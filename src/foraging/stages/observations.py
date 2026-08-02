"""Stage 4 - observations.

Satellite can confirm "open meadow" but never "this species grows here", so
real sightings sharpen the guess. This stage turns research-grade iNaturalist
records into a proximity boost.

Two deliberate design choices, both following from what the data for this AOI
actually looks like:

* **It is a boost, not a filter.** Cells with no nearby record are not
  penalised - they fall back to ``no_observation_baseline`` rather than zero.
  Absence of an observation is absence of a *botanist*, not of the plant, and
  in this AOI there are only a couple of dozen records across ~2,900 km2.

* **Congeners are down-weighted.** The West Kootenays' Arnica records are
  dominated by A. cordifolia, a forest-understory plant, while the target
  A. latifolia is subalpine. Weighting every Arnica record equally would drag
  the ranking downhill into low-elevation forest - exactly away from the
  terrain the rest of the pipeline is selecting for.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..config import Config
from ..grid import Grid
from ..sources.inaturalist import fetch_observations

# Weight applied to a record depending on how well it matches the target.
WEIGHT_EXACT = 1.0
WEIGHT_CONGENER = 0.2
WEIGHT_OTHER = 0.5


def _weight_for(taxon: str | None, target: str, downweight: set[str]) -> float:
    if not taxon:
        return WEIGHT_OTHER
    if taxon == target:
        return WEIGHT_EXACT
    if taxon in downweight:
        return WEIGHT_CONGENER
    return WEIGHT_OTHER


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    ocfg = cfg.species["observations"]
    target = ocfg["taxon_name"]
    genus = ocfg.get("genus_fallback")
    downweight = set(ocfg.get("congeners_downweight", []))
    radius_m = float(ocfg.get("boost_radius_m", 1000.0))
    baseline = float(ocfg.get("no_observation_baseline", 0.5))

    log(f"[observations] iNaturalist, target {target}")
    bbox = tuple(cfg.aoi.total_bounds)
    frames = []
    # The genus query is a superset of the species query, so it is the only
    # fetch strictly needed; the species query is kept for a clear per-taxon count.
    for name in filter(None, {target, genus}):
        frames.append(
            fetch_observations(
                name, bbox, cfg.cache_dir,
                quality_grade=ocfg.get("quality_grade", "research"),
                months=ocfg.get("months"),
                log=log,
            )
        )

    import pandas as pd
    import geopandas as gpd

    obs = gpd.GeoDataFrame(
        pd.concat([f for f in frames if len(f)], ignore_index=True), crs="EPSG:4326"
    ) if any(len(f) for f in frames) else gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")

    if len(obs):
        obs = obs.drop_duplicates(subset="id")
        obs = obs[obs.geometry.within(cfg.aoi_geom)]

    if not len(obs):
        log("[observations] none found - the layer contributes a flat baseline")
        boost = np.full(grid.shape, baseline, dtype="float32")
        grid.write(cfg.interim("score_observations.tif"), boost)
        return {"observations": 0}

    obs["weight"] = [_weight_for(t, target, downweight) for t in obs["taxon"]]
    counts = obs["taxon"].value_counts().to_dict()
    log(f"[observations] {len(obs)} records in AOI: " +
        ", ".join(f"{k} {v}" for k, v in list(counts.items())[:6]))
    n_exact = int((obs["weight"] == WEIGHT_EXACT).sum())
    n_down = int((obs["weight"] == WEIGHT_CONGENER).sum())
    log(f"[observations] {n_exact} exact-match, {n_down} down-weighted congener")

    # ---- weighted proximity surface --------------------------------------
    proj = obs.to_crs(grid.crs)
    seeds = np.zeros(grid.shape, dtype="float64")
    inv = ~grid.transform
    for pt, w in zip(proj.geometry, proj["weight"], strict=True):
        col, row = inv * (pt.x, pt.y)
        r, c = int(row), int(col)
        if 0 <= r < grid.height and 0 <= c < grid.width:
            seeds[r, c] += float(w)

    sigma_cells = max(radius_m / grid.resolution, 1e-6)
    spread = ndimage.gaussian_filter(seeds, sigma=sigma_cells, mode="constant")
    # A lone unit observation spreads to a peak of 1/(2*pi*sigma^2); rescaling
    # by that puts a single record at ~1.0 right on top of itself, so the
    # surface reads as "confidence", not as an arbitrary density.
    spread *= 2.0 * np.pi * sigma_cells**2
    proximity = np.clip(spread, 0.0, 1.0)

    score = (baseline + (1.0 - baseline) * proximity).astype("float32")
    inside = grid.mask_from(cfg.aoi, all_touched=True)
    score[~inside] = np.nan

    grid.write(cfg.interim("score_observations.tif"), score)
    obs_out = obs[["id", "taxon", "observed_on", "accuracy_m", "obscured", "url", "weight", "geometry"]]
    obs_out.to_file(cfg.output("observations.geojson"), driver="GeoJSON")

    boosted = int((proximity > 0.1).sum())
    log(f"[observations] {boosted:,} cells receive a meaningful boost "
        f"({boosted / max(inside.sum(), 1):.1%} of AOI)")
    return {"observations": len(obs), "exact": n_exact}
