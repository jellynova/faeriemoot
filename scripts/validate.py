"""Held-out validation of the suitability model against real occurrences.

The question this answers: **does the model actually pick out habitat, or does
it just pick out places people go?**

Two confounds have to be removed before iNaturalist records can test anything:

1. **Circularity.** Observations feed the score, so validating the full score
   against them is partly self-fulfilling.
2. **Sampling bias.** Botanists walk near roads and trails, and the access
   layer rewards exactly that. A model that only learned "near a road" would
   look good against observation locations.

Both are avoided by validating the **habitat-only** score - terrain and
vegetation, with access and observations dropped. Nothing in that surface has
seen an occurrence record or a road.

Three comparisons are reported:

* target species vs a **random null** drawn from scored cells - does the model
  rank real occurrences above chance?
* target species vs its **forest congener** (A. cordifolia) - the sharper test.
  Both are Arnica, both are collected by the same people in the same places, so
  sampling bias applies equally. If the model is modelling *habitat* rather
  than *access*, the subalpine target should score above the forest congener.
* elevation of each group, as a plain sanity check.

Run with:  python scripts/validate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from foraging.config import load_config  # noqa: E402
from foraging.curves import weighted_mean  # noqa: E402
from foraging.grid import Grid  # noqa: E402
from foraging.sources.inaturalist import fetch_observations  # noqa: E402

TARGET = "Arnica latifolia"
CONGENER = "Arnica cordifolia"
CONFIGS = ["config/pipeline.validation.json"]
N_NULL = 40000
# iNaturalist positional accuracy varies from 2 m to several kilometres. Point
# sampling a 30-90 m cell is meaningless past a certain radius, so imprecise
# records are excluded rather than quietly compared.
MAX_ACCURACY_M = 100.0
RNG = np.random.default_rng(20240802)


def habitat_score(cfg) -> tuple[np.ndarray, Grid, str]:
    """Terrain + vegetation only - no access, no observations.

    Falls back to terrain alone if the vegetation stage has not been run for
    this extent, so the terrain model can be tested without waiting on imagery.
    """
    terrain, grid = Grid.read(cfg.interim("score_terrain.tif"))
    veg_path = cfg.interim("score_vegetation.tif")
    w = cfg.weights["weights"]
    if not veg_path.exists():
        return terrain.astype("float32"), grid, "terrain only"
    veg = Grid.read(veg_path)[0]
    score = weighted_mean(
        {"terrain": terrain, "vegetation": veg},
        {"terrain": w["terrain"], "vegetation": w["vegetation"]},
    )
    # A cell rejected by either hard filter is not habitat at all.
    score = np.where(np.isfinite(terrain) & np.isfinite(veg), score, np.nan)
    return score.astype("float32"), grid, "terrain + vegetation"


def sample_at(arrays: dict, grid, gdf, require: str):
    """Sample several aligned rasters at each point.

    A point is kept only when ``require``'s raster is finite there, and every
    other raster is read at the same kept points, so the returned columns stay
    row-aligned. Sampling each raster independently would not: the habitat
    score is NaN wherever a hard filter rejects a cell, while elevation is
    finite almost everywhere.
    """
    proj = gdf.to_crs(grid.crs)
    inv = ~grid.transform
    out = {k: [] for k in arrays}
    kept = []
    for i, geom in zip(gdf.index, proj.geometry, strict=True):
        col, row = inv * (geom.x, geom.y)
        r, c = int(row), int(col)
        if not (0 <= r < grid.height and 0 <= c < grid.width):
            continue
        if not np.isfinite(arrays[require][r, c]):
            continue
        for k, arr in arrays.items():
            out[k].append(float(arr[r, c]))
        kept.append(i)
    return {k: np.array(v) for k, v in out.items()}, kept


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(a random positive outranks a random negative). 0.5 = no signal."""
    if not len(pos) or not len(neg):
        return float("nan")
    # Rank-based Mann-Whitney statistic, ties counted as half.
    allv = np.concatenate([pos, neg])
    order = allv.argsort()
    ranks = np.empty(len(allv), dtype="float64")
    ranks[order] = np.arange(1, len(allv) + 1)
    # Average ranks over ties.
    _, inv, counts = np.unique(allv, return_inverse=True, return_counts=True)
    mean_rank = np.zeros(len(counts))
    np.add.at(mean_rank, inv, ranks)
    mean_rank /= counts
    ranks = mean_rank[inv]
    r_pos = ranks[: len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def percentile_of(values, reference) -> np.ndarray:
    """Where each value sits in the reference distribution, as 0..1."""
    ref = np.sort(reference)
    return np.searchsorted(ref, values, side="left") / max(len(ref), 1)


def _component_auc(configs):
    """AUC of each individual layer, target vs congener.

    Shows whether the discrimination comes from the elevation band alone or
    whether slope, aspect and vegetation contribute anything on their own.
    """
    from foraging.curves import aspect_score, trapezoid

    out = []
    for conf in configs:
        cfg = load_config(conf)
        if not cfg.interim("elevation.tif").exists():
            continue
        elev, grid = Grid.read(cfg.interim("elevation.tif"))
        slope = Grid.read(cfg.interim("slope.tif"))[0]
        aspect = Grid.read(cfg.interim("aspect.tif"))[0]
        t = cfg.species["terrain"]
        layers = {
            "elevation fit": trapezoid(elev, t["elevation_m"]["hard_min"], t["elevation_m"]["optimal_min"],
                                       t["elevation_m"]["optimal_max"], t["elevation_m"]["hard_max"]),
            "slope fit": trapezoid(slope, None, t["slope_deg"]["optimal_min"],
                                   t["slope_deg"]["optimal_max"], t["slope_deg"]["hard_max"]),
            "aspect fit": aspect_score(aspect, slope, t["aspect"]["optimal_bearing_deg"],
                                       t["aspect"]["tolerance_deg"], t["aspect"]["flat_slope_deg"]),
        }
        veg_path = cfg.interim("score_vegetation.tif")
        if veg_path.exists():
            layers["vegetation fit"] = Grid.read(veg_path)[0]

        obs = fetch_observations("Arnica", tuple(cfg.aoi.total_bounds), cfg.cache_dir,
                                 quality_grade="research", months=None, log=lambda *a: None)
        obs = obs[obs.geometry.within(cfg.aoi_geom)]
        obs = obs[obs["accuracy_m"].notna() & (obs["accuracy_m"] <= MAX_ACCURACY_M)]

        for name, arr in layers.items():
            a = np.asarray(arr, dtype="float32")
            cols, kept = sample_at({"v": a}, grid, obs, require="v")
            taxa = obs.loc[kept, "taxon"].to_numpy()
            pos = cols["v"][taxa == TARGET]
            neg = cols["v"][taxa == CONGENER]
            if len(pos) and len(neg):
                out.append((name, auc(pos, neg)))
    return out


def main() -> int:
    rows_target, rows_congener, rows_other, null_pool, elev_by_taxon = [], [], [], [], {}

    model_desc = "?"
    for conf in CONFIGS:
        cfg = load_config(conf)
        if not cfg.interim("score_terrain.tif").exists():
            print(f"! {cfg.aoi_id}: no pipeline output, skipping")
            continue

        score, grid, model_desc = habitat_score(cfg)
        elevation = Grid.read(cfg.interim("elevation.tif"))[0]
        finite = np.isfinite(score)

        # Validation records are fetched independently of the pipeline's own
        # boost input: all months, so seasonality is not doing the work.
        obs = fetch_observations(
            "Arnica", tuple(cfg.aoi.total_bounds), cfg.cache_dir,
            quality_grade="research", months=None, log=lambda *a: None,
        )
        if len(obs):
            obs = obs[obs.geometry.within(cfg.aoi_geom)]
            before = len(obs)
            obs = obs[obs["accuracy_m"].notna() & (obs["accuracy_m"] <= MAX_ACCURACY_M)]
            print(f"  dropped {before - len(obs)} record(s) coarser than "
                  f"{MAX_ACCURACY_M:g} m positional accuracy")

        cols, kept = sample_at({"score": score, "elev": elevation}, grid, obs, require="score")
        vals, elevs = cols["score"], cols["elev"]
        taxa = obs.loc[kept, "taxon"].to_numpy()

        pool = score[finite]
        null = RNG.choice(pool, size=min(N_NULL, len(pool)), replace=False)
        null_pool.append(null)

        pct = percentile_of(vals, pool)
        for t, p, e in zip(taxa, pct, elevs, strict=True):
            bucket = rows_target if t == TARGET else rows_congener if t == CONGENER else rows_other
            bucket.append(p)
            elev_by_taxon.setdefault(t, []).append(e)

        print(f"{cfg.aoi_label}: {len(vals)} Arnica records on scored ground "
              f"({int(finite.sum()):,} scored cells)")

    component_auc = _component_auc(CONFIGS)
    null = np.concatenate(null_pool) if null_pool else np.array([])
    null_pct = percentile_of(null, null)

    def summarise(name, arr):
        a = np.asarray(arr, dtype="float64")
        if not len(a):
            print(f"  {name:28s}      n=0")
            return
        print(f"  {name:28s} n={len(a):3d}   median percentile {np.median(a):.2f}   "
              f"mean {a.mean():.2f}   >=0.75: {(a >= 0.75).mean():.0%}")

    print(f"\n--- habitat-only score percentile ({model_desc}; no access, no records) ---")
    summarise(f"{TARGET} (target)", rows_target)
    summarise(f"{CONGENER} (forest congener)", rows_congener)
    summarise("other Arnica", rows_other)
    summarise("random null", null_pct)

    print("\n--- discrimination (AUC, 0.5 = no signal) ---")
    t = np.asarray(rows_target, dtype="float64")
    c = np.asarray(rows_congener, dtype="float64")
    print(f"  target vs random null        {auc(t, null_pct):.3f}   "
          f"(does the model beat chance?)")
    print(f"  target vs forest congener    {auc(t, c):.3f}   "
          f"(habitat, or just where people walk?)")

    if component_auc:
        print("\n--- which layer carries the signal? (AUC target vs congener) ---")
        for name, val in component_auc:
            print(f"  {name:28s} {val:.3f}")

    print("\n--- elevation of records (m) ---")
    for taxon, es in sorted(elev_by_taxon.items(), key=lambda kv: -len(kv[1])):
        e = np.asarray(es)
        print(f"  {taxon:28s} n={len(e):3d}   median {np.median(e):6.0f}   "
              f"range {e.min():.0f}-{e.max():.0f}")

    print(f"\nSample: {len(t)} target and {len(c)} congener records passing the "
          f"{MAX_ACCURACY_M:g} m accuracy filter. iNaturalist coverage is thin, so")
    print("treat the exact figures as directional; the sign and rough size are the point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
