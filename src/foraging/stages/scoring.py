"""Stage 6 - weighted scoring and site extraction.

Combines the per-layer scores into one suitability surface, then turns that
surface into discrete, clickable *sites*: connected patches of good ground,
each summarised by the cell that scores best within it.

Reporting the peak cell rather than the polygon centroid matters - a meadow
patch wrapping around a bluff has a centroid that can land on the bluff, which
is neither the best ground nor somewhere you would want to be sent.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy import ndimage
from shapely.geometry import Point

from ..config import Config
from ..curves import weighted_mean
from ..grid import Grid
from .landstatus import CLASS_KEYS, CLASS_LABELS
from .vegetation import CLASS_NAMES as VEG_CLASS_NAMES

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass_label(bearing_deg: float) -> str:
    if not np.isfinite(bearing_deg):
        return "n/a"
    return COMPASS[int((bearing_deg % 360.0) / 22.5 + 0.5) % 16]


def _read(cfg: Config, name: str):
    path = cfg.interim(f"{name}.tif")
    if not path.exists():
        return None
    return Grid.read(path)[0]


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    components = {
        "terrain": _read(cfg, "score_terrain"),
        "vegetation": _read(cfg, "score_vegetation"),
        "access": _read(cfg, "score_access"),
        "observations": _read(cfg, "score_observations"),
    }
    missing = [k for k, v in components.items() if v is None]
    if missing:
        raise RuntimeError(f"missing stage output(s): {', '.join(missing)} - run those stages first")

    weights = cfg.weights["weights"]
    score = weighted_mean(components, weights).astype("float32")

    # Terrain and vegetation carry the hard filters; a cell rejected by either
    # is not a candidate at all, regardless of how it scores elsewhere.
    for key in ("terrain", "vegetation"):
        score = np.where(np.isfinite(components[key]), score, np.nan)

    excluded = _read(cfg, "land_excluded")
    if excluded is not None:
        score = np.where(excluded.astype(bool), np.nan, score)

    grid.write(cfg.interim("score_total.tif"), score)
    grid.write(cfg.output("suitability.tif"), score)

    finite = np.isfinite(score)
    log(f"[scoring] {finite.sum():,} scored cells, "
        f"max {np.nanmax(score):.3f}, p99 {np.nanpercentile(score[finite], 99):.3f}")

    sites = extract_sites(cfg, grid, score, components, log=log)
    return {"scored_cells": int(finite.sum()), "sites": len(sites)}


def extract_sites(cfg, grid, score, components, log=print) -> gpd.GeoDataFrame:
    """Cluster high-scoring cells into discrete ranked sites."""
    scfg = cfg.pipeline["sites"]
    min_area_ha = float(scfg["min_area_ha"])
    max_sites = int(scfg["max_sites"])
    separation_m = float(scfg.get("separation_m", 600.0))

    cell_ha = (grid.resolution**2) / 10000.0
    min_cells = max(int(round(min_area_ha / cell_ha)), 1)

    finite = np.isfinite(score)
    # Thresholding on an absolute score does not transfer between regions: in
    # this AOI 95% of scored cells clear 0.45, which merges the whole massif
    # into one "site". Selecting a top percentile of whatever the local score
    # distribution looks like keeps the cut meaningful when the AOI changes.
    floor = float(scfg.get("min_score", 0.0))
    if str(scfg.get("selection", "percentile")).lower() == "percentile":
        pct = float(scfg.get("score_percentile", 98.0))
        min_score = max(float(np.nanpercentile(score[finite], pct)), floor)
        log(f"[scoring] selecting top {100 - pct:g}% of scored cells -> score >= {min_score:.3f}")
    else:
        min_score = floor
        log(f"[scoring] selecting cells with score >= {min_score:.3f}")

    good = finite & (score >= min_score)

    labels, n = ndimage.label(good, structure=np.ones((3, 3), dtype=int))
    log(f"[scoring] {n:,} connected patches")

    # Most patches are already site-sized. Only the few that sprawl past
    # max_area_ha get split, at their local maxima, so genuinely distinct high
    # points become separate pins without shattering the small patches.
    max_area_ha = float(scfg.get("max_area_ha", 0)) or float("inf")
    labels, n = _split_oversized(
        labels, n, score, good,
        max_cells=int(max_area_ha / cell_ha),
        min_distance=max(int(round(separation_m / grid.resolution)), 1),
        log=log,
    )
    if n == 0:
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
        empty.to_file(cfg.output("sites.geojson"), driver="GeoJSON")
        return empty

    layers = {
        "elevation": _read(cfg, "elevation"),
        "slope": _read(cfg, "slope"),
        "aspect": _read(cfg, "aspect"),
        "ndvi": _read(cfg, "ndvi"),
        "canopy_closure": _read(cfg, "canopy_closure"),
        "veg_class": _read(cfg, "veg_class"),
        "drive_minutes": _read(cfg, "drive_minutes"),
        "hike_minutes": _read(cfg, "hike_minutes"),
        "hike_km": _read(cfg, "hike_km"),
        "land_tenure": _read(cfg, "land_tenure"),
    }

    idx = np.arange(1, n + 1)
    sizes = ndimage.sum_labels(np.ones_like(score, dtype="float32"), labels, idx)
    keep = idx[sizes >= min_cells]
    log(f"[scoring] {len(keep):,} patches at or above {min_area_ha:g} ha")
    if len(keep) == 0:
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
        empty.to_file(cfg.output("sites.geojson"), driver="GeoJSON")
        return empty

    # Peak cell per surviving patch.
    peak_flat = ndimage.maximum_position(np.nan_to_num(score, nan=-1.0), labels, keep)
    peak_flat = np.atleast_2d(np.array(peak_flat))
    mean_score = ndimage.mean(np.nan_to_num(score, nan=0.0), labels, keep)
    area_ha = sizes[keep - 1] * cell_ha

    obs = _load_observations(cfg)
    obs_radius = float(cfg.species["observations"].get("boost_radius_m", 1200.0))

    rows = []
    for (r, c), lbl, msc, ash in zip(peak_flat, keep, mean_score, area_ha):
        r, c = int(r), int(c)
        x, y = grid.transform * (c + 0.5, r + 0.5)

        def at(name, default=None):
            arr = layers.get(name)
            if arr is None:
                return default
            v = arr[r, c]
            return None if isinstance(v, float) and not np.isfinite(v) else v

        tenure_code = int(at("land_tenure", 0) or 0)
        tenure_key = CLASS_KEYS.get(tenure_code, "crown_land")
        veg_code = int(at("veg_class", 0) or 0)
        aspect = at("aspect")

        rows.append({
            "rank": 0,
            "score": round(float(score[r, c]), 4),
            "mean_score": round(float(msc), 4),
            "area_ha": round(float(ash), 2),
            "elevation_m": None if at("elevation") is None else round(float(at("elevation"))),
            "slope_deg": None if at("slope") is None else round(float(at("slope")), 1),
            "aspect_deg": None if aspect is None else round(float(aspect)),
            "aspect_compass": compass_label(float(aspect)) if aspect is not None else "n/a",
            "ndvi": None if at("ndvi") is None else round(float(at("ndvi")), 3),
            "canopy_closure": None if at("canopy_closure") is None else round(float(at("canopy_closure")), 3),
            "veg_class": VEG_CLASS_NAMES.get(veg_code, "unknown"),
            "drive_minutes": None if at("drive_minutes") is None else round(float(at("drive_minutes")), 1),
            "hike_minutes": None if at("hike_minutes") is None else round(float(at("hike_minutes")), 1),
            "hike_km": None if at("hike_km") is None else round(float(at("hike_km")), 2),
            "land_status": tenure_key,
            "land_status_label": CLASS_LABELS.get(tenure_key, tenure_key),
            "land_flagged": tenure_key != cfg.weights["legality"].get("unflagged_default", "crown_land"),
            "score_terrain": _round_at(components["terrain"], r, c),
            "score_vegetation": _round_at(components["vegetation"], r, c),
            "score_access": _round_at(components["access"], r, c),
            "score_observations": _round_at(components["observations"], r, c),
            "_x": x,
            "_y": y,
        })

    gdf = gpd.GeoDataFrame(rows, geometry=[Point(r["_x"], r["_y"]) for r in rows], crs=grid.crs)
    gdf = gdf.drop(columns=["_x", "_y"])
    gdf = gdf.sort_values("score", ascending=False).head(max_sites).reset_index(drop=True)
    gdf["rank"] = np.arange(1, len(gdf) + 1)

    # Observation counts are measured on the real points, not the smoothed
    # boost surface, so the popup can state a defensible number.
    gdf = _attach_observation_counts(gdf, obs, obs_radius)

    gdf = gdf.to_crs("EPSG:4326")
    gdf["lon"] = gdf.geometry.x.round(6)
    gdf["lat"] = gdf.geometry.y.round(6)

    out = cfg.output("sites.geojson")
    gdf.to_file(out, driver="GeoJSON")
    _write_manifest(cfg, gdf, grid)

    flagged = int(gdf["land_flagged"].sum())
    log(f"[scoring] {len(gdf)} ranked sites written to {out.relative_to(cfg.root)}")
    log(f"[scoring] top score {gdf['score'].iloc[0]:.3f}, {flagged} carry a land-status flag")
    return gdf


def _split_oversized(labels, n, score, good, max_cells, min_distance, log=print):
    """Break patches larger than ``max_cells`` at their local maxima."""
    if n == 0 or not np.isfinite(max_cells) or max_cells <= 0:
        return labels, n

    sizes = ndimage.sum_labels(np.ones_like(score, dtype="float32"), labels, np.arange(1, n + 1))
    oversized = np.nonzero(sizes > max_cells)[0] + 1
    if not len(oversized):
        return labels, n

    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed

    objects = ndimage.find_objects(labels)
    out = labels.copy()
    next_label = n + 1
    split_count = 0

    for lbl in oversized:
        sl = objects[lbl - 1]
        if sl is None:
            continue
        sub_mask = out[sl] == lbl
        sub_score = np.where(sub_mask, np.nan_to_num(score[sl], nan=0.0), 0.0).astype("float32")

        peaks = peak_local_max(sub_score, min_distance=min_distance, labels=sub_mask,
                               exclude_border=False)
        if len(peaks) < 2:
            continue

        markers = np.zeros(sub_mask.shape, dtype="int32")
        markers[tuple(peaks.T)] = np.arange(1, len(peaks) + 1)
        parts = watershed(-sub_score, markers=markers, mask=sub_mask)

        # First part keeps the original label; the rest get fresh ones.
        region = out[sl]
        for part_id in range(2, len(peaks) + 1):
            region[(parts == part_id) & sub_mask] = next_label
            next_label += 1
        out[sl] = region
        split_count += 1

    total = next_label - 1
    log(f"[scoring] split {split_count} oversized patch(es) -> {total:,} candidate sites")
    return out, total


def _round_at(arr, r, c, nd=3):
    if arr is None:
        return None
    v = float(arr[r, c])
    return round(v, nd) if np.isfinite(v) else None


def _load_observations(cfg) -> gpd.GeoDataFrame | None:
    path = cfg.output("observations.geojson")
    if not path.exists():
        return None
    try:
        return gpd.read_file(path)
    except Exception:
        return None


def _attach_observation_counts(gdf, obs, radius_m: float):
    if obs is None or not len(obs):
        gdf["inat_nearby"] = 0
        gdf["inat_nearby_exact"] = 0
        return gdf

    obs_proj = obs.to_crs(gdf.crs)
    pts = np.array([[g.x, g.y] for g in obs_proj.geometry])
    exact = (obs_proj["weight"].to_numpy() >= 1.0) if "weight" in obs_proj.columns else np.zeros(len(pts), bool)

    counts, counts_exact = [], []
    for geom in gdf.geometry:
        d = np.hypot(pts[:, 0] - geom.x, pts[:, 1] - geom.y)
        within = d <= radius_m
        counts.append(int(within.sum()))
        counts_exact.append(int((within & exact).sum()))
    gdf["inat_nearby"] = counts
    gdf["inat_nearby_exact"] = counts_exact
    return gdf


def _write_manifest(cfg, gdf, grid) -> None:
    """Everything the web UI needs to configure itself for this AOI."""
    bounds = cfg.aoi.total_bounds
    manifest = {
        "aoi": {
            "id": cfg.aoi_id,
            "label": cfg.aoi_label,
            "bounds": [float(b) for b in bounds],
        },
        "species": {
            "id": cfg.species_id,
            "common_name": cfg.species.get("common_name"),
            "scientific_name": cfg.species.get("scientific_name"),
            "habitat_note": cfg.species.get("habitat_note"),
        },
        "origin": cfg.pipeline["access"]["origin"],
        "weights": cfg.weights["weights"],
        "imagery_window": {
            k: cfg.imagery_window().get(k) for k in ("window_start", "window_end", "years")
        },
        "filters": {
            "max_drive_minutes": cfg.pipeline["access"]["max_drive_minutes"],
            "max_hike_km": cfg.pipeline["access"]["max_hike_km"],
            "elevation_m": cfg.species["terrain"]["elevation_m"],
            "min_score": cfg.pipeline["sites"]["min_score"],
        },
        "counts": {
            "sites": int(len(gdf)),
            "flagged": int(gdf["land_flagged"].sum()) if len(gdf) else 0,
        },
        "ranges": {
            "score": [float(gdf["score"].min()), float(gdf["score"].max())] if len(gdf) else [0, 1],
            "elevation_m": [float(gdf["elevation_m"].min()), float(gdf["elevation_m"].max())] if len(gdf) else [0, 0],
            "drive_minutes": [float(gdf["drive_minutes"].min()), float(gdf["drive_minutes"].max())] if len(gdf) else [0, 0],
            "hike_km": [float(gdf["hike_km"].min()), float(gdf["hike_km"].max())] if len(gdf) else [0, 0],
        },
        "land_status_labels": CLASS_LABELS,
    }
    with open(cfg.output("manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
