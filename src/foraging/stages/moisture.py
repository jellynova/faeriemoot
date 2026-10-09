"""Stage 2b (optional) - moisture.

Riparian and seepage plants - devil's club, stinging nettle, black hawthorn -
are defined less by canopy or host trees than by *water*. Neither habitat stage
sees that: NDVI cannot tell a streamside thicket from an equally green dry
slope, and VRI describes the overstory, not the soil. This stage adds a separate
``moisture`` score component for profiles that carry a ``moisture`` block, and
skips itself for everyone else.

Two signals, blended by the profile:

* **Distance to mapped water** from BC's Freshwater Atlas: streams (filtered by
  Strahler order), lakes, rivers and wetlands. Credit is full within
  ``water_distance_m.optimal_max`` of water and falls linearly to zero at
  ``water_distance_m.zero_at``.
* **Topographic position** (TPI): a cell's elevation minus the mean elevation
  within ``tpi.radius_m``. Negative TPI is a hollow, gully or toe slope, where
  water collects whether or not a stream is mapped there - which matters for
  seepage species that grow well away from any blue line. Credit is full at
  ``tpi.wet_at_m`` and zero at ``tpi.dry_at_m``.

``min_credit`` keeps dry ground from scoring exactly zero (a profile may want
moisture to be a preference, not a requirement); ``hard_max_m`` under
``water_distance_m`` turns it into a requirement by rejecting cells further
than that from water.

The water rasters are species-independent and shared per AOI; distances depend
on the profile's stream-order cut and which water features count, so they are
recomputed per species. That is one distance transform - about a second.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..config import Config
from ..curves import ramp_down
from ..grid import Grid

# Codes in the shared water-polygon raster.
POLY_CODES = {"lakes": 1, "rivers": 2, "wetlands": 3}
WATER_FEATURES = ("streams", *POLY_CODES)


def water_mask(stream_order: np.ndarray, polys: np.ndarray, min_order: int,
               features=WATER_FEATURES) -> np.ndarray:
    """Cells that count as water for this profile."""
    features = set(features)
    unknown = features - set(WATER_FEATURES)
    if unknown:
        raise ValueError(f"unknown water feature(s) {', '.join(sorted(unknown))}; "
                         f"expected some of {', '.join(WATER_FEATURES)}")
    mask = np.zeros(stream_order.shape, dtype=bool)
    if "streams" in features:
        mask |= stream_order >= max(int(min_order), 1)
    for name, code in POLY_CODES.items():
        if name in features:
            mask |= polys == code
    return mask


def distance_to_water(mask: np.ndarray, resolution: float) -> np.ndarray:
    """Euclidean distance in metres from each cell to the nearest water cell.

    All-inf when there is no water at all, so the credit curve reads it as
    "very far" rather than the transform's meaningless all-zero answer.
    """
    if not mask.any():
        return np.full(mask.shape, np.inf, dtype="float32")
    return ndimage.distance_transform_edt(~mask, sampling=resolution).astype("float32")


def topographic_position(dem: np.ndarray, resolution: float, radius_m: float) -> np.ndarray:
    """Elevation minus the NaN-aware mean elevation of a square window.

    The window is ``2 * radius_m`` wide. A square is a close enough stand-in for
    a disc at this scale and lets ``uniform_filter`` do the work in O(n).
    """
    size = max(round(2 * radius_m / resolution) | 1, 3)  # odd, at least 3
    valid = np.isfinite(dem)
    z = np.where(valid, dem, 0.0).astype("float64")
    num = ndimage.uniform_filter(z, size=size, mode="constant")
    den = ndimage.uniform_filter(valid.astype("float64"), size=size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(den > 0, num / den, np.nan)
    tpi = np.where(valid, dem - mean, np.nan)
    return tpi.astype("float32")


def moisture_score(dist_m: np.ndarray, tpi_m: np.ndarray, mcfg: dict) -> np.ndarray:
    """Blend water-distance and topographic-position credit into 0..1."""
    wcfg = mcfg["water_distance_m"]
    tcfg = mcfg.get("tpi") or {}
    blend = mcfg.get("blend", {"water": 1.0, "tpi": 0.0})
    w_water = float(blend.get("water", 0.0))
    w_tpi = float(blend.get("tpi", 0.0)) if tcfg else 0.0
    if w_water + w_tpi <= 0:
        raise ValueError("moisture.blend must give water or tpi a positive weight")

    d = np.asarray(dist_m, dtype="float64")
    # ramp_down maps inf to NaN; "no water anywhere" is a credit of 0, not unknown.
    water = np.where(np.isinf(d), 0.0, ramp_down(d, float(wcfg["optimal_max"]), float(wcfg["zero_at"])))
    combined = w_water * water
    if w_tpi > 0:
        tpi = ramp_down(tpi_m, float(tcfg["wet_at_m"]), float(tcfg["dry_at_m"]))
        combined = combined + w_tpi * tpi
    combined = combined / (w_water + w_tpi)

    floor = float(mcfg.get("min_credit", 0.0))
    score = floor + (1.0 - floor) * combined

    hard_max = wcfg.get("hard_max_m")
    if hard_max is not None:
        score = np.where(d <= float(hard_max), score, np.nan)
    return score.astype("float32")


def _rasterize_water(cfg: Config, grid: Grid, log=print) -> tuple[np.ndarray, np.ndarray]:
    """Shared stream-order and water-polygon rasters, cached per AOI."""
    order_path, poly_path = cfg.interim("water_stream_order.tif"), cfg.interim("water_polys.tif")
    if order_path.exists() and poly_path.exists():
        return Grid.read(order_path)[0], Grid.read(poly_path)[0]

    from rasterio.features import rasterize

    from ..sources.bcdata import aoi_bbox_albers, fetch_layer

    bbox = aoi_bbox_albers(cfg.aoi)
    streams = fetch_layer("fwa_streams", bbox, cfg.cache_dir, log=log,
                          properties=["STREAM_ORDER", "EDGE_TYPE"])
    order = np.zeros(grid.shape, dtype="uint8")
    if len(streams):
        s = streams.to_crs(grid.crs)
        so = s["STREAM_ORDER"].fillna(1).clip(1, 254).astype(int)
        # Burned in ascending order so a confluence keeps the larger stream.
        shapes = sorted(((g, int(o)) for g, o in zip(s.geometry, so, strict=True)
                         if g is not None and not g.is_empty), key=lambda t: t[1])
        if shapes:
            # all_touched for lines, or a diagonal stream rasterises as a
            # dotted line with gaps the distance transform would see through.
            order = rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                              fill=0, dtype="uint8", all_touched=True)

    polys = np.zeros(grid.shape, dtype="uint8")
    for name, code in POLY_CODES.items():
        gdf = fetch_layer(f"fwa_{name}", bbox, cfg.cache_dir, log=log, properties=["WATERBODY_TYPE"])
        if not len(gdf):
            continue
        shapes = [(g, code) for g in gdf.to_crs(grid.crs).geometry if g is not None and not g.is_empty]
        if shapes:
            burned = rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                               fill=0, dtype="uint8", all_touched=False)
            polys = np.where(burned > 0, burned, polys).astype("uint8")

    grid.write(order_path, order, dtype="uint8")
    grid.write(poly_path, polys, dtype="uint8")
    return order.astype("float32"), polys.astype("float32")


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    mcfg = cfg.species.get("moisture")
    if not mcfg:
        log(f"[moisture] skipped - {cfg.species_id} has no moisture block")
        return {"skipped": True}
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    log("[moisture] Freshwater Atlas streams, lakes, rivers and wetlands")
    order, polys = _rasterize_water(cfg, grid, log=log)
    features = mcfg.get("water_features", list(WATER_FEATURES))
    mask = water_mask(np.nan_to_num(order), np.nan_to_num(polys),
                      int(mcfg.get("min_stream_order", 1)), features)
    dist = distance_to_water(mask, grid.resolution)

    dem = Grid.read(cfg.interim("elevation.tif"))[0]
    tcfg = mcfg.get("tpi")
    tpi = (topographic_position(dem, grid.resolution, float(tcfg["radius_m"]))
           if tcfg else np.full(grid.shape, np.nan, dtype="float32"))

    score = moisture_score(dist, tpi, mcfg)
    inside = grid.mask_from(cfg.aoi, all_touched=True)
    score[~inside] = np.nan
    dist_out = np.where(inside & np.isfinite(dist), dist, np.nan).astype("float32")

    grid.write(cfg.species_interim("water_distance.tif"), dist_out)
    grid.write(cfg.species_interim("tpi.tif"), tpi)
    grid.write(cfg.species_interim("score_moisture.tif"), score)

    near = np.isfinite(dist_out) & (dist_out <= float(mcfg["water_distance_m"]["optimal_max"]))
    log(f"[moisture] {mask[inside].mean():.1%} of AOI is mapped water "
        f"({', '.join(features)}); {near[inside].mean():.1%} within "
        f"{float(mcfg['water_distance_m']['optimal_max']):g} m of it")
    ok = np.isfinite(score)
    log(f"[moisture] median credit {np.nanmedian(score[ok & inside]) if ok.any() else float('nan'):.2f}")
    return {"water_fraction": float(mask[inside].mean())}
