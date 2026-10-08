"""Held-out validation of the suitability model against real occurrences.

The question this answers: **does the model actually pick out habitat, or does
it just pick out places people go?**

Two confounds have to be removed before iNaturalist records can test anything:

1. **Circularity.** Observations feed the score, so validating the full score
   against them is partly self-fulfilling.
2. **Sampling bias.** Collectors walk near roads and trails, and the access
   layer rewards exactly that. A model that only learned "near a road" would
   look good against observation locations.

Both are avoided by validating the **habitat-only** score - terrain and the
species' habitat layer (vegetation or forest), with access and observations
dropped. Nothing in that surface has seen an occurrence record or a road.

Two comparisons are reported, each with a 95% interval:

* target vs a **random null** drawn from AOI cells - does the model rank real
  occurrences above chance?
* target vs a **contrast taxon** named in the species profile - the sharper
  test. The contrast is collected by the same people in the same places, so
  sampling bias applies equally; if the model is modelling *habitat* rather
  than *access*, the target should score above it.

How the small sample is handled (see ``foraging.validation`` for the why):

* Records are grouped into spatial clusters (``--cluster-m``) and the bootstrap
  resamples clusters, so near-duplicate records count once.
* Every layer is scored on the **same** records, and differences between
  layers come from paired replicates.
* A record on ground the hard filters reject is a miss, ranked last - not
  silently dropped.
* Obscured records, whose public coordinates are randomised over ~20 km, are
  excluded along with imprecise ones.
* Below a minimum number of independent clusters, the script reports the
  counts and stops rather than print an AUC that means nothing.

Run with:  python scripts/validate.py [--species config/species/<id>.json]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from foraging.config import load_config, load_json  # noqa: E402
from foraging.curves import aspect_score, trapezoid, weighted_mean  # noqa: E402
from foraging.grid import Grid  # noqa: E402
from foraging.sources.inaturalist import fetch_observations  # noqa: E402
from foraging.validation import (  # noqa: E402
    auc,
    bootstrap_aucs,
    cluster_means,
    interval,
    n_units,
    percentile_rank,
    permutation_pvalue,
    spatial_clusters,
)

DEFAULT_SPECIES = "config/species/arnica_latifolia.json"
N_NULL = 40000
# iNaturalist positional accuracy varies from 2 m to several kilometres. Point
# sampling a 30-90 m cell is meaningless past a certain radius, so imprecise
# records - and records that state no accuracy at all - are excluded rather
# than quietly compared.
MAX_ACCURACY_M = 100.0
# Below these many independent clusters an AUC interval spans most of 0..1 and
# the point estimate is noise. The thresholds are a judgement call, not a
# derivation; they are set so that the arnica sample clears them and a handful
# of records does not.
MIN_TARGET_UNITS = 10
MIN_CONTRAST_UNITS = 5
SEED = 20240802


# ---------------------------------------------------------------- records
def load_records(cfg, vcfg, log=print):
    """Usable target and contrast records, with a per-group accounting of losses."""
    import geopandas as gpd
    import pandas as pd

    frames = [
        fetch_observations(t, tuple(cfg.aoi.total_bounds), cfg.cache_dir,
                           quality_grade="research", months=None, log=lambda *a: None)
        for t in vcfg["query_taxa"]
    ]
    frames = [f for f in frames if len(f)]
    if not frames:
        return None, {}
    obs = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    obs = obs.drop_duplicates(subset="id")
    obs = obs[obs.geometry.within(cfg.aoi_geom)]

    groups = {"target": vcfg["target"], "contrast": vcfg["contrast"]}
    obscured = obs["obscured"].fillna(False).astype(bool)
    precise = obs["accuracy_m"].notna() & (obs["accuracy_m"] <= MAX_ACCURACY_M)
    accounting = {}
    for role, taxon in groups.items():
        m = obs["taxon"] == taxon
        accounting[role] = {
            "taxon": taxon,
            "in AOI": int(m.sum()),
            "obscured": int((m & obscured).sum()),
            "imprecise or no accuracy": int((m & ~obscured & ~precise).sum()),
            "usable": int((m & ~obscured & precise).sum()),
        }
    keep = obs["taxon"].isin(groups.values()) & ~obscured & precise
    return obs[keep], accounting


# --------------------------------------------------------------- surfaces
def candidate_surfaces(cfg):
    """Every score worth testing, keyed by label. NaN = rejected by a hard filter."""
    habitat = cfg.habitat_layer
    terrain = Grid.read(cfg.species_interim("score_terrain.tif"))[0]
    elev, grid = Grid.read(cfg.interim("elevation.tif"))
    slope = Grid.read(cfg.interim("slope.tif"))[0]
    aspect = Grid.read(cfg.interim("aspect.tif"))[0]
    t = cfg.species["terrain"]
    e, s, a = t["elevation_m"], t["slope_deg"], t["aspect"]

    surfaces = {}
    hab_path = cfg.species_interim(f"score_{habitat}.tif")
    # Optional layers (moisture) describe habitat too, not access or sightings,
    # so they belong in the habitat-only surface.
    extras = {k: Grid.read(cfg.species_interim(f"score_{k}.tif"))[0] for k in cfg.optional_layers
              if cfg.species_interim(f"score_{k}.tif").exists()}
    if hab_path.exists():
        hab = Grid.read(hab_path)[0]
        w = cfg.weights["weights"]
        parts = {"terrain": terrain, habitat: hab, **extras}
        combined = weighted_mean(parts, {k: w[k] for k in parts})
        ok = np.logical_and.reduce([np.isfinite(v) for v in parts.values()])
        surfaces[f"habitat score ({' + '.join(parts)})"] = np.where(ok, combined, np.nan)
    surfaces["terrain"] = terrain
    surfaces["  elevation fit"] = trapezoid(elev, e["hard_min"], e["optimal_min"], e["optimal_max"], e["hard_max"])
    surfaces["  slope fit"] = trapezoid(slope, None, s["optimal_min"], s["optimal_max"], s["hard_max"])
    surfaces["  aspect fit"] = aspect_score(aspect, slope, a["optimal_bearing_deg"],
                                            a["tolerance_deg"], a["flat_slope_deg"])
    if hab_path.exists():
        surfaces[f"{habitat} layer"] = hab
    for k, arr in extras.items():
        surfaces[f"{k} layer"] = arr
    return surfaces, elev, grid


def sample_cells(grid, gdf, valid):
    """Row/col of each record's cell, keeping only records on valid AOI ground."""
    proj = gdf.to_crs(grid.crs)
    inv = ~grid.transform
    rows, cols, xs, ys, kept = [], [], [], [], []
    for i, geom in zip(gdf.index, proj.geometry, strict=True):
        col, row = inv * (geom.x, geom.y)
        r, c = int(row), int(col)
        if 0 <= r < grid.height and 0 <= c < grid.width and valid[r, c]:
            rows.append(r)
            cols.append(c)
            xs.append(geom.x)
            ys.append(geom.y)
            kept.append(i)
    return np.array(rows, int), np.array(cols, int), np.array(xs), np.array(ys), kept


def _fmt_ci(point, ci):
    return f"{point:.2f} [{ci[0]:.2f}, {ci[1]:.2f}]"


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--species", default=DEFAULT_SPECIES, help="species profile to validate")
    ap.add_argument("--boot", type=int, default=2000, help="bootstrap replicates")
    ap.add_argument("--perm", type=int, default=10000, help="permutations for the contrast p-value")
    ap.add_argument("--cluster-m", type=float, default=1000.0,
                    help="records closer than this (chained) form one cluster")
    args = ap.parse_args(argv)

    profile = load_json(Path(args.species) if Path(args.species).is_absolute()
                        else Path(__file__).resolve().parent.parent / args.species)
    vcfg = profile.get("validation")
    if not vcfg:
        print(f"! {args.species} has no 'validation' block; nothing to validate against")
        return 1
    cfg = load_config(vcfg["config"], species=args.species)
    rng = np.random.default_rng(SEED)
    target, contrast, clabel = vcfg["target"], vcfg["contrast"], vcfg.get("contrast_label", "contrast")

    # ---- records first: cheap, and decides whether the rest is worth doing
    obs, accounting = load_records(cfg, vcfg)
    print(f"{cfg.aoi_label}: records with <= {MAX_ACCURACY_M:g} m stated accuracy, not obscured")
    for role, a in accounting.items():
        print(f"  {role:9s} {a['taxon']:28s} in AOI {a['in AOI']:3d}   obscured {a['obscured']:3d}   "
              f"imprecise {a['imprecise or no accuracy']:3d}   usable {a['usable']:3d}")
    if obs is None or not len(obs):
        print("\nNo usable records - nothing to validate.")
        return 0

    # Clusters need projected coordinates; the analysis grid's CRS is metric.
    have_rasters = cfg.species_interim("score_terrain.tif").exists() and cfg.interim("elevation.tif").exists()
    proj = obs.to_crs(cfg.aoi.estimate_utm_crs())
    units = {}
    for role, taxon in (("target", target), ("contrast", contrast)):
        g = proj[proj["taxon"] == taxon]
        units[role] = n_units(spatial_clusters(g.geometry.x, g.geometry.y, args.cluster_m), len(g)) if len(g) else 0
    print(f"  independent clusters at {args.cluster_m:g} m: target {units['target']}, "
          f"contrast {units['contrast']}")

    if units["target"] < MIN_TARGET_UNITS:
        print(f"\nStopping: {units['target']} independent target cluster(s), below the "
              f"{MIN_TARGET_UNITS} needed for an interval narrower than most of 0..1.")
        print("Any AUC from this sample would be noise. See README, Validation.")
        return 0
    if not have_rasters:
        print(f"\n! no habitat rasters for {cfg.run_id}; run `forage run -c {vcfg['config']} "
              f"--species {args.species} --only "
              f"{','.join(['terrain', cfg.habitat_layer, *cfg.optional_layers])}` first")
        return 1

    # ---- surfaces, sampled at a common record set --------------------------
    surfaces, elev, grid = candidate_surfaces(cfg)
    aoi_cells = np.isfinite(elev)
    rows, cols, xs, ys, kept = sample_cells(grid, obs, aoi_cells)
    taxa = obs.loc[kept, "taxon"].to_numpy()
    is_t, is_c = taxa == target, taxa == contrast
    clusters_t = spatial_clusters(xs[is_t], ys[is_t], args.cluster_m)
    clusters_c = spatial_clusters(xs[is_c], ys[is_c], args.cluster_m)
    contrast_ok = n_units(clusters_c, int(is_c.sum())) >= MIN_CONTRAST_UNITS

    # Same null cells for every surface, so null comparisons are paired too.
    flat = np.flatnonzero(aoi_cells.ravel())
    null_idx = rng.choice(flat, size=min(N_NULL, len(flat)), replace=False)

    pos, neg_c, neg_n, ref = {}, {}, {}, {}
    rejected = {}
    for name, arr in surfaces.items():
        a = np.where(aoi_cells & np.isfinite(arr), arr, -np.inf)
        vals = a[rows, cols]
        pos[name], neg_c[name] = vals[is_t], vals[is_c]
        neg_n[name] = a.ravel()[null_idx]
        ref[name] = neg_n[name]
        rejected[name] = (int(np.isinf(vals[is_t]).sum()), int(np.isinf(vals[is_c]).sum()))

    headline = next(iter(surfaces))
    print(f"\n{int(is_t.sum())} target and {int(is_c.sum())} contrast records on AOI ground "
          f"({n_units(clusters_t, int(is_t.sum()))} and {n_units(clusters_c, int(is_c.sum()))} clusters)")
    print(f"  on ground the hard filters reject ({headline}): target {rejected[headline][0]}, "
          f"contrast {rejected[headline][1]} - counted as misses, not dropped")

    # ---- percentiles of the headline surface ------------------------------
    def summarise(label, vals):
        p = percentile_rank(vals, ref[headline])
        if not len(p):
            print(f"  {label:34s} n=  0")
            return
        print(f"  {label:34s} n={len(p):3d}   median percentile {np.median(p):.2f}   "
              f">=0.75: {(p >= 0.75).mean():.0%}")

    print(f"\n--- {headline}: percentile among all AOI cells (rejected ground ranks last) ---")
    summarise(f"{target} (target)", pos[headline])
    summarise(f"{contrast} ({clabel})", neg_c[headline])
    summarise("random null", neg_n[headline])

    # ---- AUCs with intervals ----------------------------------------------
    boot_n = bootstrap_aucs(pos, neg_n, pos_clusters=clusters_t, resample_neg=False,
                            n_boot=args.boot, rng=rng)
    boot_c = bootstrap_aucs(pos, neg_c, pos_clusters=clusters_t, neg_clusters=clusters_c,
                            n_boot=args.boot, rng=rng) if contrast_ok else None

    print("\n--- discrimination: AUC [95% cluster-bootstrap interval]; 0.5 = no signal ---")
    print(f"  {'':34s} {'vs random null':22s} {'vs ' + clabel:22s} p (vs contrast)")
    for name in surfaces:
        a_n = _fmt_ci(auc(pos[name], neg_n[name]), interval(boot_n[name]))
        if boot_c is not None:
            a_c = _fmt_ci(auc(pos[name], neg_c[name]), interval(boot_c[name]))
            # Permutation on cluster means: clusters are the exchangeable units.
            pt = cluster_means(percentile_rank(pos[name], ref[name]), clusters_t)
            pc = cluster_means(percentile_rank(neg_c[name], ref[name]), clusters_c)
            pv = permutation_pvalue(pt, pc, n_perm=args.perm, rng=rng)
            pval = "<0.001" if pv < 0.001 else f"{pv:.3f}"
        else:
            a_c, pval = "too few records", ""
        print(f"  {name:34s} {a_n:22s} {a_c:22s} {pval}")
    if boot_c is None:
        print(f"  (contrast has fewer than {MIN_CONTRAST_UNITS} independent clusters; not compared)")

    # ---- does the habitat layer add anything? -----------------------------
    hab_key = next((k for k in surfaces if k.startswith("habitat score")), None)
    if hab_key:
        print(f"\n--- paired difference: AUC({hab_key}) - AUC(terrain) ---")
        for label, boots, negs in (("vs random null", boot_n, neg_n), (f"vs {clabel}", boot_c, neg_c)):
            if boots is None:
                continue
            d = auc(pos[hab_key], negs[hab_key]) - auc(pos["terrain"], negs["terrain"])
            ci = interval(boots[hab_key] - boots["terrain"])
            verdict = "distinguishable from zero" if ci[0] > 0 or ci[1] < 0 else "not distinguishable from zero"
            # Three decimals: an interval edge near zero must not round onto it.
            print(f"  {label:22s} {d:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]   {verdict}")

    # ---- elevation sanity check -------------------------------------------
    print("\n--- elevation of records (m) ---")
    for label, m in ((target, is_t), (contrast, is_c)):
        ev = elev[rows[m], cols[m]]
        if len(ev):
            print(f"  {label:28s} n={len(ev):3d}   median {np.median(ev):6.0f}   "
                  f"range {ev.min():.0f}-{ev.max():.0f}")

    print(f"\nIntervals resample {args.cluster_m:g} m spatial clusters ({args.boot} replicates), so")
    print("near-duplicate records do not narrow them. Read the interval, not the third decimal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
