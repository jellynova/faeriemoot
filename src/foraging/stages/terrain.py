"""Stage 1 - terrain.

Derives elevation, slope and aspect on the analysis grid and scores each
against the target species' terrain preferences. This is the stage that
narrows *where meadows should be*.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..config import Config
from ..curves import aspect_score, trapezoid, weighted_mean
from ..grid import Grid, build_grid
from ..sources.dem import fetch_dem

# Horn's 3x3 kernels. Written for scipy.ndimage.correlate (no kernel flip),
# with row 0 at the north edge of a north-up raster.
_KX = np.array([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]])  # -> dz/d(east)
_KY = np.array([[1.0, 2.0, 1.0], [0.0, 0.0, 0.0], [-1.0, -2.0, -1.0]])  # -> dz/d(north)


def slope_aspect(dem: np.ndarray, res: float, smooth_sigma: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and aspect as a compass bearing of the downslope direction.

    A light Gaussian pre-smooth suppresses the stair-stepping that DEM
    quantisation puts into aspect on gentle ground - without it, meadow-angle
    slopes produce noisy aspect that the aspect filter would then act on.
    """
    z = dem.astype("float64")
    holes = ~np.isfinite(z)
    if holes.any():
        # Fill holes with nearest finite value so the gradient kernel does not
        # smear NaN across the neighbourhood; re-masked afterwards.
        idx = ndimage.distance_transform_edt(holes, return_distances=False, return_indices=True)
        z = z[tuple(idx)]

    if smooth_sigma and smooth_sigma > 0:
        z = ndimage.gaussian_filter(z, sigma=smooth_sigma, mode="nearest")

    denom = 8.0 * res
    g_east = ndimage.correlate(z, _KX, mode="nearest") / denom
    g_north = ndimage.correlate(z, _KY, mode="nearest") / denom

    slope = np.degrees(np.arctan(np.hypot(g_east, g_north)))
    # Downslope vector is the negated uphill gradient; compass bearing from
    # its east/north components.
    aspect = np.degrees(np.arctan2(-g_east, -g_north)) % 360.0

    slope[holes] = np.nan
    aspect[holes] = np.nan
    return slope.astype("float32"), aspect.astype("float32")


def run(cfg: Config, log=print) -> dict:
    grid = build_grid(cfg)
    log(f"[terrain] grid {grid.width}x{grid.height} @ {grid.resolution:g} m  {grid.crs.to_string()}")

    dem = fetch_dem(cfg, grid, log=log)
    sigma = float(cfg.pipeline["terrain"].get("smooth_sigma", 0.0))
    slope, aspect = slope_aspect(dem, grid.resolution, smooth_sigma=sigma)

    # Clip to the AOI polygon - the grid is a rectangle, the AOI need not be.
    inside = grid.mask_from(cfg.aoi, all_touched=True)
    for arr in (dem, slope, aspect):
        arr[~inside] = np.nan

    tcfg = cfg.species["terrain"]
    ecfg, scfg, acfg = tcfg["elevation_m"], tcfg["slope_deg"], tcfg["aspect"]

    s_elev = trapezoid(dem, ecfg["hard_min"], ecfg["optimal_min"], ecfg["optimal_max"], ecfg["hard_max"])
    s_slope = trapezoid(slope, None, scfg["optimal_min"], scfg["optimal_max"], scfg["hard_max"])
    s_aspect = aspect_score(
        aspect, slope,
        optimal_bearing_deg=acfg["optimal_bearing_deg"],
        tolerance_deg=acfg["tolerance_deg"],
        flat_slope_deg=acfg["flat_slope_deg"],
    )

    score = weighted_mean(
        {"elevation": s_elev, "slope": s_slope, "aspect": s_aspect},
        cfg.weights["terrain_subweights"],
    )

    hard = cfg.weights["hard_filters"]
    ok = np.isfinite(dem)
    if hard.get("enforce_elevation_hard_bounds", True):
        ok &= (dem >= ecfg["hard_min"]) & (dem <= ecfg["hard_max"])
    if hard.get("enforce_slope_hard_max", True):
        ok &= slope <= scfg["hard_max"]
    score = np.where(ok, score, np.nan)

    grid.write(cfg.interim("elevation.tif"), dem)
    grid.write(cfg.interim("slope.tif"), slope)
    grid.write(cfg.interim("aspect.tif"), aspect)
    grid.write(cfg.interim("score_terrain.tif"), score.astype("float32"))

    n_ok = int(np.isfinite(score).sum())
    log(f"[terrain] elevation {np.nanmin(dem):.0f}-{np.nanmax(dem):.0f} m")
    log(f"[terrain] {n_ok:,} candidate cells pass the hard terrain filters "
        f"({n_ok / max(inside.sum(), 1):.1%} of AOI)")
    return {"grid": grid, "candidates": n_ok}
