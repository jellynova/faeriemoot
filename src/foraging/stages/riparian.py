"""Stage 2 (alternative) - riparian habitat.

Some targets are neither a spectral signature nor a host tree: they are
moisture-obligate plants that grow *along water*. Devil's club, stinging nettle
and red elderberry all want the same thing - ground within a stone's throw of a
stream, seep, lake edge or wetland, on a flat valley floor rather than a
gradient - and none of that is legible in NDVI, which sees only "green".

So this stage replaces the vegetation and forest stages for species profiles
with ``"habitat_model": "riparian"``, and scores proximity to water from BC's
Freshwater Atlas (FWA), which maps the province's streams, rivers, lakes and
wetlands as vector geometry.

The habitat score is:

    water credit  x  slope credit

* **Water credit.** Distance to the nearest water feature, full credit within
  ``distance_m.optimal_max`` and falling to nothing at ``distance_m.hard_max``.
  Streams are weighted by **stream order**, because a first-order headwater
  gully that runs dry in August is not the same habitat as a fourth-order
  mainstem: each cell takes the credit of the *nearest* stream, so a seep two
  cells away beats a mainstem across the valley. Lakes, rivers and wetlands
  carry their own credit from the profile.
* **Slope credit.** Flat ground, from the DEM the terrain stage already built.
  Riparian plants sit on floodplain and bench, not on the cutbank above it.

Cells further than ``distance_m.hard_max`` from any water are rejected (NaN)
rather than scored low: with no water there is no habitat, however flat the
ground. So are cells of open lake/river water, which are not ground at all.
Wetlands are deliberately *kept* - a skunk-cabbage swamp is prime habitat for
several of these plants.

The distance surface is written to the shared interim directory, since it is
the same for every species on the AOI; only the thresholds and credits in the
profile are species-specific.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..config import Config
from ..curves import ramp_down
from ..grid import Grid

# Water feature classes, and the raster code each gets in the UI's categorical
# layer. Streams are one class regardless of order - the order is a credit, not
# a category - but lakes/rivers and wetlands are visually distinct.
CLASS_NODATA, CLASS_WATER, CLASS_WETLAND, CLASS_LAKE_RIVER, CLASS_STREAM, CLASS_DRY = 0, 1, 2, 3, 4, 5
CLASS_NAMES = {
    CLASS_WATER: "open water",
    CLASS_WETLAND: "wetland",
    CLASS_LAKE_RIVER: "lake / river shore",
    CLASS_STREAM: "stream corridor",
    CLASS_DRY: "beyond water range",
}

# FWA layers, and the profile key each is credited by.
LAYERS = {
    "lake_river": ("water_lakes", "water_rivers"),
    "wetland": ("water_wetlands",),
    "stream": ("water_streams",),
}

# Stream order is a small integer; orders above the last knot take the last
# knot's credit, so an eighth-order mainstem is not extrapolated past the
# profile's intent. The clamp is load-bearing for FWA's order 9, which is not a
# real order at all: it marks the *areal* representation of a major river (the
# Columbia appears this way, FEATURE_SOURCE "areal stream sk"), so it belongs
# at full credit rather than being treated as unknown.
DEFAULT_STREAM_ORDER_KNOTS = [[1, 0.15], [3, 0.7], [5, 1.0]]


def stream_order_credit(order: np.ndarray, knots: list[list[float]]) -> np.ndarray:
    """Piecewise-linear credit by stream order; orders off the table are clamped.

    A first-order stream is a gully that may be dry by midsummer; a third- or
    fourth-order stream is water you can count on. NaN order (the FWA
    occasionally has none) gets the lowest credit rather than a guess.
    """
    xs = [float(k[0]) for k in knots]
    ys = [float(k[1]) for k in knots]
    o = np.asarray(order, dtype="float64")
    out = np.interp(np.nan_to_num(o, nan=xs[0]), xs, ys)
    return np.where(np.isfinite(o), out, float(ys[0]))


def water_credit(distance_m: np.ndarray, optimal_max: float, hard_max: float) -> np.ndarray:
    """1 on the water's edge, 0 at ``hard_max``, linear between."""
    return ramp_down(distance_m, best=optimal_max, worst=hard_max)


def _distance_to(mask: np.ndarray, res: float) -> tuple[np.ndarray | None, tuple | None]:
    """Metres to the nearest True cell, plus the indices of that nearest cell.

    ``distance_transform_edt`` measures from the non-zero cells to the nearest
    zero, so the mask is inverted on the way in. The indices are what let a
    cell inherit the credit of the feature it is nearest to - the mechanism
    that makes "nearest stream order" work in one pass instead of one pass per
    order class.
    """
    if not mask.any():
        return None, None
    dist, (ri, ci) = ndimage.distance_transform_edt(
        ~mask, sampling=(res, res), return_indices=True
    )
    return dist.astype("float32"), (ri, ci)


def _rasterise_polys(gdf, grid: Grid) -> np.ndarray:
    from rasterio.features import rasterize

    if gdf is None or not len(gdf):
        return np.zeros(grid.shape, dtype=bool)
    proj = gdf.to_crs(grid.crs)
    shapes = [(g, 1) for g in proj.geometry if g is not None and not g.is_empty]
    if not shapes:
        return np.zeros(grid.shape, dtype=bool)
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                     fill=0, dtype="uint8", all_touched=True).astype(bool)


def _rasterise_lines(gdf, values: np.ndarray, grid: Grid) -> np.ndarray:
    """Burn a per-feature credit onto the cells each line touches (0 elsewhere)."""
    from rasterio.features import rasterize

    if gdf is None or not len(gdf):
        return np.zeros(grid.shape, dtype="float32")
    proj = gdf.to_crs(grid.crs)
    pairs = [(g, float(v)) for g, v in zip(proj.geometry, values, strict=True)
             if g is not None and not g.is_empty]
    if not pairs:
        return np.zeros(grid.shape, dtype="float32")
    # Ascending credit, so where a mainstem and a headwater share a cell the
    # higher credit is the one burned last and kept.
    pairs.sort(key=lambda p: p[1])
    # all_touched, so a stream thinner than a cell still registers in it.
    return rasterize(pairs, out_shape=grid.shape, transform=grid.transform,
                     fill=0.0, dtype="float32", all_touched=True)


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if cfg.habitat_layer != "riparian":
        log(f"[riparian] skipped - {cfg.species_id} uses habitat_model '{cfg.habitat_model}'")
        return {"skipped": True}
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    from ..sources.bcdata import aoi_bbox_albers, fetch_layer

    rcfg = cfg.species["riparian"]
    bbox = aoi_bbox_albers(cfg.aoi)
    res = grid.resolution
    inside = grid.mask_from(cfg.aoi, all_touched=True)

    def fetch(alias, properties):
        try:
            return fetch_layer(alias, bbox, cfg.cache_dir, log=log, properties=properties)
        except Exception as exc:  # a missing layer degrades the score, not the run
            log(f"    ! {alias} unavailable: {exc}")
            return None

    log("[riparian] fetching Freshwater Atlas water features")
    lakes = fetch("water_lakes", ["WATERBODY_TYPE", "AREA_HA", "GNIS_NAME_1"])
    rivers = fetch("water_rivers", ["WATERBODY_TYPE", "AREA_HA", "GNIS_NAME_1"])
    wetlands = fetch("water_wetlands", ["WATERBODY_TYPE", "AREA_HA", "GNIS_NAME_1"])
    streams = fetch("water_streams", ["STREAM_ORDER", "GNIS_NAME", "FEATURE_SOURCE"])

    # ---- masks and distances ---------------------------------------------
    open_water = _rasterise_polys(lakes, grid) | _rasterise_polys(rivers, grid)
    wet = _rasterise_polys(wetlands, grid)

    # Streams carry a per-feature credit from stream order, so the credit has to
    # be burned onto the grid before the distance transform can hand each cell
    # the credit of the stream it is actually nearest to.
    stream_credit_raster = np.zeros(grid.shape, dtype="float32")
    if streams is not None and len(streams):
        import pandas as pd

        order = pd.to_numeric(streams.get("STREAM_ORDER"), errors="coerce").to_numpy(dtype="float64")
        knots = rcfg.get("stream_order_credit", {}).get("knots", DEFAULT_STREAM_ORDER_KNOTS)
        stream_credit_raster = _rasterise_lines(streams, stream_order_credit(order, knots), grid)
    stream_mask = stream_credit_raster > 0.0
    if not (open_water.any() or wet.any() or stream_mask.any()):
        raise RuntimeError(
            "no FWA water features intersect this AOI - the riparian stage cannot score water "
            "proximity. Check the AOI polygon and the water_* layers in src/foraging/sources/bcdata.py"
        )

    d_cfg = rcfg["distance_m"]
    optimal_max, hard_max = float(d_cfg["optimal_max"]), float(d_cfg["hard_max"])
    type_credit = rcfg.get("water_type_credit", {})
    wet_scale = float(type_credit.get("wetland", 1.0))

    credits, distances = [], []

    d_open, _ = _distance_to(open_water, res)
    if d_open is not None:
        credits.append(water_credit(d_open, optimal_max, hard_max) * float(type_credit.get("lake_river", 1.0)))
        distances.append(d_open)

    d_wet, _ = _distance_to(wet, res)
    if d_wet is not None:
        credits.append(water_credit(d_wet, optimal_max, hard_max) * wet_scale)
        distances.append(d_wet)

    d_stream = None
    if stream_mask.any():
        d_stream, (ri, ci) = _distance_to(stream_mask, res)
        nearest_order_credit = stream_credit_raster[ri, ci]
        credits.append(water_credit(d_stream, optimal_max, hard_max) * nearest_order_credit)
        distances.append(d_stream)

    water = np.maximum.reduce(credits) if len(credits) > 1 else credits[0]
    dist_any = np.minimum.reduce(distances) if len(distances) > 1 else distances[0]

    # ---- slope ------------------------------------------------------------
    slope_path = cfg.interim("slope.tif")
    if slope_path.exists():
        slope = Grid.read(slope_path)[0]
        scfg = rcfg.get("slope_deg", {})
        slope_credit = ramp_down(slope, best=float(scfg.get("optimal_max", 10.0)),
                                 worst=float(scfg.get("hard_max", 25.0)))
        slope_credit = np.where(np.isfinite(slope), slope_credit, 0.0)
    else:
        log("    ! slope layer missing (run the terrain stage) - scoring water proximity alone")
        slope_credit = np.ones(grid.shape, dtype="float32")

    score = (water * slope_credit).astype("float32")

    # ---- hard filters -----------------------------------------------------
    score[open_water] = np.nan  # open water is not ground
    if rcfg.get("hard_filters", {}).get("enforce_water_max", True):
        score[~np.isfinite(dist_any) | (dist_any > hard_max)] = np.nan
    score[~inside] = np.nan

    # ---- categorical layer for the UI -------------------------------------
    rclass = np.full(grid.shape, CLASS_NODATA, dtype="uint8")
    in_range = np.isfinite(dist_any) & (dist_any <= hard_max)
    rclass[inside & ~in_range] = CLASS_DRY
    if d_stream is not None:
        near_stream = in_range & (d_stream <= hard_max)
        rclass[inside & near_stream] = CLASS_STREAM
    if d_wet is not None:
        rclass[inside & (d_wet <= hard_max)] = CLASS_WETLAND
    if d_open is not None:
        rclass[inside & (d_open <= hard_max)] = CLASS_LAKE_RIVER
    rclass[open_water] = CLASS_WATER
    rclass[~inside] = CLASS_NODATA

    # Species-independent diagnostics are shared across profiles.
    grid.write(cfg.interim("distance_to_water.tif"), np.where(inside, dist_any, np.nan).astype("float32"))
    # Thresholds and credits are the profile's, so these stay species-specific.
    grid.write(cfg.species_interim("score_riparian.tif"), score)
    grid.write(cfg.species_interim("riparian_class.tif"), rclass, dtype="uint8")

    counts = {CLASS_NAMES[k]: int((rclass == k).sum()) for k in CLASS_NAMES}
    total = max(sum(counts.values()), 1)
    log("[riparian] class mix: " + ", ".join(f"{k} {v / total:.1%}" for k, v in counts.items()))
    ok = np.isfinite(score)
    log(f"[riparian] {int(ok.sum()):,} cells within {hard_max:g} m of water "
        f"({ok[inside].mean():.1%} of AOI), median score {np.nanmedian(score):.2f}")
    return {"classes": counts}
