"""Held-out validation of the suitability model against real occurrences.

The question this answers: **does the model actually pick out habitat, or does
it just pick out places people go?**

Two confounds have to be removed before iNaturalist records can test anything:

1. **Circularity.** Observations feed the score, so validating the full score
   against them is partly self-fulfilling.
2. **Sampling bias.** Foragers and botanists walk near roads and trails, and the
   access layer rewards exactly that. A model that only learned "near a road"
   would look good against observation locations.

Both are avoided by validating the **habitat-only** score - terrain plus the
species' habitat layer (Sentinel-2 vegetation or VRI forest), with access and
observations dropped. Nothing in that surface has seen an occurrence record or
a road.

The target group is compared against:

* a **random null** drawn from the extent - does the model rank real
  occurrences above chance?
* a **contrast taxon** sharing the target's collectors, season and access bias
  but not its habitat (the species profile's ``validation`` block) - the
  sharper test, since sampling bias applies to both equally.

Samples are small and spatially clustered, so every AUC carries a bootstrap
CI and a one-sided Mann-Whitney p-value, and is reported twice: per record, and
per *site* after merging same-group records within ``--decluster-m``. Records
the hard filters reject are counted, not silently dropped: the "all" rows
score them 0, which is what the model actually says about them. See
``foraging/stats.py``.

Run with:
    python scripts/validate.py
    python scripts/validate.py --species config/species/cantharellus_formosus.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from foraging.config import load_config  # noqa: E402
from foraging.curves import aspect_score, trapezoid, weighted_mean  # noqa: E402
from foraging.grid import Grid  # noqa: E402
from foraging.sources.inaturalist import fetch_observations, in_taxon  # noqa: E402
from foraging.stats import (  # noqa: E402
    auc,
    bootstrap_auc,
    mann_whitney_p,
    midrank_percentile,
    paired_bootstrap_delta,
    per_cluster,
    spatial_clusters,
)

CONFIG = "config/pipeline.validation.json"
# iNaturalist positional accuracy varies from 2 m to several kilometres. Point
# sampling a 30-90 m cell is meaningless past a certain radius, so imprecise
# records are excluded rather than quietly compared.
MAX_ACCURACY_M = 100.0
# Records of the same group closer than this are treated as one site.
DECLUSTER_M = 1000.0
N_NULL_RASTER = 40000
# A VRI-scored null costs a WFS round trip per 150 points.
N_NULL_POINTS = 2000
N_BOOT = 2000
SEED = 20240802


# ------------------------------------------------------------------ records
def load_records(cfg, vcfg: dict, max_accuracy_m: float) -> gpd.GeoDataFrame:
    """Target and contrast records in the extent, labelled by group.

    Fetched independently of the pipeline's own boost input and across all
    months, so seasonality is not doing the work.
    """
    frames = [
        fetch_observations(q, tuple(cfg.aoi.total_bounds), cfg.cache_dir,
                           quality_grade="research", months=None, log=lambda *a: None)
        for q in vcfg["query_taxa"]
    ]
    frames = [f for f in frames if len(f)]
    if not frames:
        return gpd.GeoDataFrame({"group": [], "taxon": []}, geometry=[], crs="EPSG:4326")
    obs = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    obs = obs.drop_duplicates(subset="id")
    obs = obs[obs.geometry.within(cfg.aoi_geom)]

    def group(taxon):
        # Matched on returned names, not query names: a genus query also
        # returns synonym look-alikes from other genera.
        if any(in_taxon(taxon, n) for n in vcfg["target_taxa"]):
            return "target"
        if any(in_taxon(taxon, n) for n in vcfg["contrast_taxa"]):
            return "contrast"
        return None

    obs["group"] = obs["taxon"].map(group)
    obs = obs[obs["group"].notna()]
    precise = obs["accuracy_m"].notna() & (obs["accuracy_m"] <= max_accuracy_m)
    for g in ("target", "contrast"):
        n_all, n_ok = int((obs["group"] == g).sum()), int((precise & (obs["group"] == g)).sum())
        print(f"  {g}: {n_all} research-grade record(s), {n_ok} within "
              f"{max_accuracy_m:g} m positional accuracy")
    return obs[precise].reset_index(drop=True)


def null_points(grid: Grid, eligible: np.ndarray, n: int, rng) -> gpd.GeoDataFrame:
    """Random cell centres from the extent (inside the AOI, with elevation)."""
    rows, cols = np.nonzero(eligible)
    pick = rng.choice(len(rows), size=min(n, len(rows)), replace=False)
    xs, ys = grid.transform * (cols[pick] + 0.5, rows[pick] + 0.5)
    return gpd.GeoDataFrame({"group": ["null"] * len(pick)}, geometry=gpd.points_from_xy(xs, ys),
                            crs=grid.crs)


# -------------------------------------------------------------- evaluation
class Surfaces:
    """The habitat-only model, evaluated at arbitrary points."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.terrain, self.grid = Grid.read(cfg.interim("score_terrain.tif"))
        self.rasters = {n: Grid.read(cfg.interim(f"{n}.tif"))[0]
                        for n in ("elevation", "slope", "aspect")}
        self.habitat = cfg.habitat_model
        hab_path = cfg.interim(f"score_{self.habitat}.tif")
        self.habitat_raster = Grid.read(hab_path)[0] if hab_path.exists() else None
        if self.habitat_raster is not None:
            self.mode = f"terrain + {self.habitat}"
        elif self.habitat == "forest":
            # VRI over the whole validation extent is ~317,000 stands; sampling
            # it at the evaluated points is the same score for a fraction of
            # the download. Stand age is VRI's own here - no cutblock override.
            self.mode = "terrain + forest (VRI sampled at points)"
        else:
            self.mode = "terrain only"

    @property
    def eligible(self) -> np.ndarray:
        return np.isfinite(self.rasters["elevation"])

    def evaluate(self, pts: gpd.GeoDataFrame) -> pd.DataFrame:
        """Per point: terrain sub-layers, habitat, combined score, and scored_all."""
        proj = pts.to_crs(self.grid.crs)
        inv = ~self.grid.transform
        rc = np.array([inv * (g.x, g.y) for g in proj.geometry]).reshape(-1, 2)
        r, c = np.floor(rc[:, 1]).astype(int), np.floor(rc[:, 0]).astype(int)
        on = (r >= 0) & (r < self.grid.height) & (c >= 0) & (c < self.grid.width)

        def take(arr):
            out = np.full(len(pts), np.nan)
            out[on] = arr[r[on], c[on]]
            return out

        t = self.cfg.species["terrain"]
        e, s, a = (take(self.rasters[k]) for k in ("elevation", "slope", "aspect"))
        df = pd.DataFrame({
            "elevation": e,
            "elevation fit": trapezoid(e, t["elevation_m"]["hard_min"], t["elevation_m"]["optimal_min"],
                                       t["elevation_m"]["optimal_max"], t["elevation_m"]["hard_max"]),
            "slope fit": trapezoid(s, None, t["slope_deg"]["optimal_min"],
                                   t["slope_deg"]["optimal_max"], t["slope_deg"]["hard_max"]),
            "aspect fit": aspect_score(a, s, t["aspect"]["optimal_bearing_deg"],
                                       t["aspect"]["tolerance_deg"], t["aspect"]["flat_slope_deg"]),
            "terrain": take(self.terrain),
        }, index=pts.index)

        if self.habitat_raster is not None:
            df[f"{self.habitat} fit"] = take(self.habitat_raster)
        elif self.habitat == "forest":
            df = df.join(self._forest_at(proj))

        hab_col = f"{self.habitat} fit"
        if hab_col in df:
            w = self.cfg.weights["weights"]
            combined = weighted_mean({"terrain": df["terrain"].to_numpy(), "habitat": df[hab_col].to_numpy()},
                                     {"terrain": w["terrain"], "habitat": w[self.habitat]})
            # A cell rejected by either hard filter is not habitat at all.
            ok = np.isfinite(df["terrain"]) & np.isfinite(df[hab_col])
            df["score"] = np.where(ok, combined, np.nan)
        else:
            df["score"] = df["terrain"]
        df["terrain_all"] = np.where(np.isfinite(e), np.nan_to_num(df["terrain"], nan=0.0), np.nan)
        df["score_all"] = np.where(np.isfinite(e), np.nan_to_num(df["score"], nan=0.0), np.nan)
        return df

    def _forest_at(self, proj: gpd.GeoDataFrame) -> pd.DataFrame:
        from foraging.sources.bcdata import sample_at_points
        from foraging.stages.forest import (
            VRI_PROPERTIES,
            score_stands,
            soft_factor,
            stand_attributes,
        )

        fcfg = self.cfg.species["forest"]
        stands = sample_at_points("vri", proj.geometry, self.cfg.cache_dir, VRI_PROPERTIES,
                                  log=lambda *a: None)
        at = stand_attributes(stands, fcfg)
        enforce = self.cfg.weights["hard_filters"].get("enforce_treed", True)
        return pd.DataFrame({
            "host share %": at["host_pct"].to_numpy(),
            "stand age fit": soft_factor(at["age"].to_numpy(), fcfg["stand_age_yr"]),
            "crown closure fit": soft_factor(at["closure"].to_numpy(), fcfg["crown_closure_pct"]),
            "forest fit": score_stands(at["host_pct"], at["age"], at["closure"], at["treed"],
                                       fcfg, enforce_treed=enforce),
        }, index=proj.index)


# ---------------------------------------------------------------- reporting
def fmt_auc(pos, neg, rng, n_boot) -> str:
    if len(pos) < 2 or len(neg) < 2:
        return f"{'n/a':>24s}  n={len(pos)}"
    lo, hi = bootstrap_auc(pos, neg, rng, n_boot=n_boot)
    return (f"{auc(pos, neg):.2f} [{lo:.2f}-{hi:.2f}] p={mann_whitney_p(pos, neg):<6.2g}"
            f" n={len(pos):<3d}")


def site_values(df: pd.DataFrame, col: str, xy: np.ndarray, distance_m: float) -> np.ndarray:
    """One value per declustered site: the median of its records."""
    v = df[col].to_numpy()
    ok = np.isfinite(v)
    if not ok.any():
        return np.array([])
    return per_cluster(v[ok], spatial_clusters(xy[ok], distance_m))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--species", help="Species profile (default: the one in the validation config).")
    ap.add_argument("--max-accuracy-m", type=float, default=MAX_ACCURACY_M)
    ap.add_argument("--decluster-m", type=float, default=DECLUSTER_M)
    ap.add_argument("--boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(SEED)

    cfg = load_config(CONFIG, species=args.species)
    vcfg = cfg.species.get("validation")
    if not vcfg:
        print(f"! {cfg.species_id} has no 'validation' block in its species profile")
        return 1
    if not cfg.interim("score_terrain.tif").exists():
        stages = "terrain" + (",vegetation" if cfg.habitat_model == "vegetation" else "")
        print(f"! no terrain layer for {cfg.run_id}. Build it with:\n"
              f"  forage run -c {CONFIG} --only {stages}"
              + (f" --species {args.species}" if args.species else ""))
        return 1

    target_name = ", ".join(vcfg["target_taxa"])
    contrast_name = f"{', '.join(vcfg['contrast_taxa'])} ({vcfg.get('contrast_label', 'contrast')})"
    print(f"{cfg.aoi_label} - {cfg.species.get('scientific_name')}")

    surf = Surfaces(cfg)
    obs = load_records(cfg, vcfg, args.max_accuracy_m)
    n_null = N_NULL_RASTER if "sampled at points" not in surf.mode else N_NULL_POINTS
    null = null_points(surf.grid, surf.eligible, n_null, rng)

    rec = surf.evaluate(obs)
    nul = surf.evaluate(null)
    xy = np.array([[g.x, g.y] for g in obs.to_crs(surf.grid.crs).geometry]).reshape(-1, 2)
    groups = {g: (obs["group"] == g).to_numpy() for g in ("target", "contrast")}

    # ---- accounting ----------------------------------------------------
    print(f"\n--- records on the {surf.mode} surface (no access, no records) ---")
    for g, label in (("target", target_name), ("contrast", contrast_name)):
        m = groups[g]
        on = m & np.isfinite(rec["elevation"].to_numpy())
        rejected = on & ~np.isfinite(rec["score"].to_numpy())
        n_sites = len(np.unique(spatial_clusters(xy[on], args.decluster_m))) if on.any() else 0
        print(f"  {label:46s} {int(on.sum()):3d} records at {n_sites:3d} sites; "
              f"{int(rejected.sum())} rejected by the hard filters")
    print(f"  {'random null':46s} {len(nul):,} cells")

    null_admitted = nul["score"].dropna().to_numpy()

    def pct_line(name, vals):
        v = vals[np.isfinite(vals)]
        if not len(v):
            print(f"  {name:46s}   n=0")
            return
        p = midrank_percentile(v, null_admitted)
        print(f"  {name:46s}   n={len(v):3d}   median percentile {np.median(p):.2f}   "
              f"mean {p.mean():.2f}")

    print("\n--- habitat-score percentile among admitted null cells (ties count half) ---")
    pct_line(f"{target_name} (target)", rec.loc[groups["target"], "score"].to_numpy())
    pct_line(contrast_name, rec.loc[groups["contrast"], "score"].to_numpy())

    # ---- discrimination ------------------------------------------------
    print("\n--- discrimination: AUC [95% CI] one-sided p, n = target count "
          "(0.5 = no signal) ---")
    print(f"  {'':33s} {'per record':38s} per site ({args.decluster_m:g} m declustered)")
    for col, how in (("score", "admitted only"), ("score_all", "rejected = 0")):
        t = rec.loc[groups["target"], col].dropna().to_numpy()
        c = rec.loc[groups["contrast"], col].dropna().to_numpy()
        n = nul[col].dropna().to_numpy()
        ts = site_values(rec[groups["target"]], col, xy[groups["target"]], args.decluster_m)
        cs = site_values(rec[groups["contrast"]], col, xy[groups["contrast"]], args.decluster_m)
        print(f"  target vs null      {how:13s} {fmt_auc(t, n, rng, args.boot):38s} "
              f"{fmt_auc(ts, n, rng, args.boot)}")
        print(f"  target vs contrast  {how:13s} {fmt_auc(t, c, rng, args.boot):38s} "
              f"{fmt_auc(ts, cs, rng, args.boot)}")

    # ---- does the habitat layer add anything? --------------------------
    if "score_all" in rec and surf.mode != "terrain only":
        print("\n--- does the habitat layer add discrimination? paired, same records ---")
        print("  AUC(terrain + habitat) - AUC(terrain alone), rejected cells = 0")
        both = np.isfinite(rec["score_all"]) & np.isfinite(rec["terrain_all"])
        nboth = np.isfinite(nul["score_all"]) & np.isfinite(nul["terrain_all"])
        for name, neg_mask, neg_df in (("vs null", nboth, nul), ("vs contrast", None, None)):
            pm = groups["target"] & both
            if neg_df is None:
                nm = groups["contrast"] & both
                neg_a, neg_b = rec.loc[nm, "score_all"], rec.loc[nm, "terrain_all"]
            else:
                neg_a, neg_b = neg_df.loc[neg_mask, "score_all"], neg_df.loc[neg_mask, "terrain_all"]
            if pm.sum() < 2 or len(neg_a) < 2:
                print(f"  {name:12s} n/a")
                continue
            d, lo, hi, p_le0 = paired_bootstrap_delta(
                rec.loc[pm, "score_all"].to_numpy(), neg_a.to_numpy(),
                rec.loc[pm, "terrain_all"].to_numpy(), neg_b.to_numpy(), rng, n_boot=args.boot)
            print(f"  {name:12s} {d:+.2f} [{lo:+.2f} to {hi:+.2f}]   "
                  f"{p_le0:.0%} of bootstrap replicates <= 0")

    # ---- which layer carries it? ---------------------------------------
    layer_cols = [c for c in rec.columns if c.endswith(" fit") or c == "host share %"]
    print("\n--- which layer carries the signal? AUC [95% CI], target vs contrast, per site ---")
    for col in layer_cols:
        ts = site_values(rec[groups["target"]], col, xy[groups["target"]], args.decluster_m)
        cs = site_values(rec[groups["contrast"]], col, xy[groups["contrast"]], args.decluster_m)
        ns = nul[col].dropna().to_numpy()
        print(f"  {col:20s} vs contrast {fmt_auc(ts, cs, rng, args.boot):38s} "
              f"vs null {auc(ts, ns):.2f}")

    print("\n--- elevation of records (m) ---")
    for g, label in (("target", target_name), ("contrast", contrast_name)):
        e = rec.loc[groups[g], "elevation"].dropna().to_numpy()
        if len(e):
            print(f"  {label:46s} n={len(e):3d}   median {np.median(e):6.0f}   "
                  f"range {e.min():.0f}-{e.max():.0f}")
    e = nul["elevation"].dropna().to_numpy()
    print(f"  {'random null':46s} n={len(e):,}   median {np.median(e):6.0f}")

    print("\nCIs are stratified percentile bootstraps; with fewer than ~15 sites per group")
    print("they run narrow, so read them as the least uncertainty there is, not the most.")
    print("Per-site rows are the honest sample size: neighbouring records are not")
    print("independent evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
