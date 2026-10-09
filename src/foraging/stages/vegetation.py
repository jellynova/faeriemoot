"""Stage 2 - vegetation.

Confirms that terrain-plausible ground is *actually open meadow or open forest*
rather than closed conifer canopy, bare rock or clearcut.

Rather than a single NDVI cut, three signals are computed and two are used:

* **NDVI** separates vegetated ground from bare rock, scree and fresh cutblock.
  It does *not* separate meadow from forest here - mid-summer conifer and lush
  sward both saturate around 0.82.
* **NDMI** (NIR vs SWIR) carries the canopy-closure signal. Conifer canopies
  hold water and self-shadow, suppressing SWIR. Measured against West Kootenays
  reference populations, valley forest medians 0.303 against 0.105 for open
  high ground - the strongest separator of the three.
* **Sub-pixel texture** - the standard deviation of 10 m NDVI *inside* each 30 m
  cell - is computed and written as a diagnostic layer but is **not** used for
  closure by default. Against the same reference populations it ran backwards
  from the usual assumption (open ground 0.0353, closed canopy 0.0195: alpine is
  a rock/heath/krummholz mosaic while closed canopy is uniform at 10 m), and it
  separated weakly. ``closure_texture_weight`` in the species profile folds it
  back in, correctly signed, for anyone who wants it.

NDMI defines a continuous *canopy closure* value and the species profile assigns
credit along that continuum. For Arnica latifolia open forest scores nearly as
well as meadow, so the stage deliberately avoids a binary meadow/forest cut.
"""

from __future__ import annotations

import warnings

import numpy as np
from affine import Affine

from ..config import Config
from ..curves import trapezoid
from ..grid import Grid
from ..sources.sentinel import (
    SCL_KEEP,
    BandReadError,
    read_band,
    read_scl,
    search_scenes,
    select_per_tile,
)

# Categorical output for the UI's vegetation layer.
CLASS_NODATA, CLASS_BARE, CLASS_MEADOW, CLASS_OPEN_FOREST, CLASS_CLOSED_FOREST = 0, 1, 2, 3, 4
CLASS_CUTBLOCK = 5
CLASS_NAMES = {
    CLASS_BARE: "bare / rock / scree",
    CLASS_MEADOW: "open meadow",
    CLASS_OPEN_FOREST: "open forest",
    CLASS_CLOSED_FOREST: "closed forest",
    CLASS_CUTBLOCK: "regenerating cutblock",
}

FINE_FACTOR = 3  # 30 m analysis cell = 3x3 block of 10 m Sentinel-2 pixels


def _fine_grid(grid: Grid) -> tuple[Affine, tuple[int, int]]:
    """A 10 m grid exactly nested inside the analysis grid."""
    res = grid.resolution / FINE_FACTOR
    t = Affine(res, 0.0, grid.transform.c, 0.0, -res, grid.transform.f)
    return t, (grid.height * FINE_FACTOR, grid.width * FINE_FACTOR)


def _block_stats(fine: np.ndarray, factor: int = FINE_FACTOR) -> tuple[np.ndarray, np.ndarray]:
    """Mean and standard deviation of each ``factor`` x ``factor`` block.

    Computed as E[x^2] - E[x]^2 in one pass, which is what gives us within-cell
    texture without ever holding a second full-resolution array.
    """
    h, w = fine.shape
    blocks = fine.reshape(h // factor, factor, w // factor, factor)
    # Blocks fully masked by cloud are legitimately empty; NaN is the intended
    # result, so the all-NaN warnings are suppressed rather than worked around.
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(blocks, axis=(1, 3))
        mean_sq = np.nanmean(blocks * blocks, axis=(1, 3))
        var = np.maximum(mean_sq - mean * mean, 0.0)
    return mean.astype("float32"), np.sqrt(var).astype("float32")


def _normalise(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Map lo->0, hi->1, clipped."""
    return np.clip((x - lo) / max(hi - lo, 1e-9), 0.0, 1.0)


def logging_age(cfg: Config, grid: Grid, log=print) -> np.ndarray:
    """Years since harvest per cell; NaN where never logged (on record).

    Needed because a regenerating clearcut is spectrally almost identical to
    subalpine meadow - open canopy, high summer NDVI - so the vegetation stage
    scores it as prime habitat. Checking the top-ranked sites against satellite
    imagery showed them sitting on hard-edged cutblocks along logging roads,
    which is what prompted this layer.
    """
    from ..sources.bcdata import aoi_bbox_albers, fetch_layer

    blocks = fetch_layer("cutblocks", aoi_bbox_albers(cfg.aoi), cfg.cache_dir, log=log)
    age = np.full(grid.shape, np.nan, dtype="float32")
    if not len(blocks):
        return age

    year_col = next((c for c in ("HARVEST_MID_YEAR_CALENDAR", "HARVEST_START_YEAR_CALENDAR")
                     if c in blocks.columns), None)
    if year_col is None:
        log("    ! cutblock layer has no harvest year column; skipping")
        return age

    from datetime import date

    this_year = date.today().year
    blocks = blocks[blocks[year_col].notna()].copy()
    blocks["_age"] = (this_year - blocks[year_col]).clip(lower=0)
    # WFS hands these back in BC Albers; the analysis grid is UTM.
    blocks = blocks.to_crs(grid.crs)

    # Youngest block wins where cuts overlap, so re-logged ground reads as recent.
    shapes = sorted(
        ((geom, float(a)) for geom, a in zip(blocks.geometry, blocks["_age"], strict=True)
         if geom is not None and not geom.is_empty),
        key=lambda s: -s[1],
    )
    if not shapes:
        return age

    from rasterio.features import rasterize

    burned = rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                       fill=np.nan, dtype="float32", all_touched=False)
    if np.isfinite(burned).any():
        log(f"    {len(shapes):,} cutblocks on grid, most recent "
            f"{np.nanmin(burned):.0f} years ago, {np.isfinite(burned).mean():.1%} of cells")
    else:
        log(f"    {len(shapes):,} cutblocks fetched but none intersect the grid")
    return burned


def run(cfg: Config, grid: Grid | None = None, log=print, reuse_indices: bool = False) -> dict:
    if cfg.habitat_layer != "vegetation":
        # Not an error: a host-tree species gets its habitat signal from the
        # forest stage, and the Sentinel-2 download is the slowest thing in the
        # pipeline, so it is not done for nothing.
        log(f"[vegetation] skipped - {cfg.species_id} uses habitat_model "
            f"'{cfg.habitat_model}' (see the forest stage)")
        return {"skipped": True}
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    if reuse_indices and cfg.interim("ndvi.tif").exists():
        log("[vegetation] reusing cached index composites")
        ndvi = Grid.read(cfg.interim("ndvi.tif"))[0]
        ndmi = Grid.read(cfg.interim("ndmi.tif"))[0]
        texture = Grid.read(cfg.interim("ndvi_texture.tif"))[0]
        return classify(cfg, grid, ndvi, ndmi, texture, log=log)

    max_scenes = int(cfg.pipeline["imagery"].get("max_scenes", 8))

    log("[vegetation] searching Sentinel-2")
    scenes = search_scenes(cfg, log=log)
    if not scenes:
        raise RuntimeError(
            "no Sentinel-2 scenes in the seasonal window - widen imagery.window_start/"
            "window_end, add years, or raise max_cloud_cover"
        )
    # max_scenes is per MGRS tile, so a multi-tile AOI keeps coverage everywhere.
    scenes = select_per_tile(scenes, max_scenes, log=log)

    fine_transform, fine_shape = _fine_grid(grid)
    ndvi_stack, tex_stack, ndmi_stack = [], [], []
    failed: list = []

    for i, sc in enumerate(scenes, 1):
        log(f"  [{i}/{len(scenes)}] {sc.date} {sc.tile}  cloud={sc.cloud:.1f}%")

        try:
            red = read_band(sc, "B04", grid.crs, fine_transform, fine_shape, log=log)
            nir = read_band(sc, "B08", grid.crs, fine_transform, fine_shape, log=log)
            scl = read_scl(sc, grid.crs, fine_transform, fine_shape, log=log)
        except BandReadError as exc:
            # One unreadable scene should cost that scene, not the whole run -
            # the composite is a median over several dates and tolerates a gap.
            log(f"    ! skipping {sc.date} {sc.tile}: {exc}")
            failed.append(sc)
            continue

        keep = np.isin(scl, SCL_KEEP)
        del scl

        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi_fine = (nir - red) / (nir + red)
        del red
        ndvi_fine[~keep | ~np.isfinite(ndvi_fine)] = np.nan
        ndvi_fine[(ndvi_fine < -1) | (ndvi_fine > 1)] = np.nan

        ndvi_mean, ndvi_std = _block_stats(ndvi_fine)
        del ndvi_fine
        ndvi_stack.append(ndvi_mean)
        tex_stack.append(ndvi_std)

        # NDMI needs SWIR, which is native 20 m - compute it straight on the
        # 30 m analysis grid rather than pretending to 10 m detail. NIR is
        # cloud-masked first so a partly clouded block does not average cloud
        # reflectance into the NDMI numerator.
        nir[~keep] = np.nan
        nir30 = _block_stats(nir)[0]
        del nir
        try:
            swir30 = read_band(sc, "B11", grid.crs, grid.transform, grid.shape, log=log)
        except BandReadError as exc:
            # NDVI for this scene is already banked; only NDMI is lost.
            log(f"    ! no SWIR for {sc.date} {sc.tile}: {exc}")
            del keep
            continue
        keep30 = _block_stats(keep.astype("float32"))[0] > 0.5
        del keep
        with np.errstate(invalid="ignore", divide="ignore"):
            ndmi = (nir30 - swir30) / (nir30 + swir30)
        ndmi[~keep30 | ~np.isfinite(ndmi)] = np.nan
        ndmi_stack.append(ndmi.astype("float32"))

    if failed:
        log(f"[vegetation] {len(failed)} scene(s) skipped after repeated read errors")
    if not ndvi_stack:
        raise RuntimeError(
            "every Sentinel-2 scene failed to read. This is normally a network "
            "problem rather than a data one - re-run `forage run --only vegetation`, "
            "which resumes from the cached DEM and road layers."
        )
    if not ndmi_stack:
        raise RuntimeError(
            "no SWIR band could be read, so canopy closure cannot be computed. "
            "Re-run `forage run --only vegetation`."
        )
    # The NDMI stack can be shorter than the NDVI one when a scene's SWIR read
    # failed after its NDVI was already banked; each index is composited
    # independently, so a mismatch is fine.
    log(f"[vegetation] compositing {len(ndvi_stack)} scene(s) NDVI, "
        f"{len(ndmi_stack)} NDMI (median)")
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ndvi = np.nanmedian(np.stack(ndvi_stack), axis=0)
        texture = np.nanmedian(np.stack(tex_stack), axis=0)
        ndmi = np.nanmedian(np.stack(ndmi_stack), axis=0)
    del ndvi_stack, tex_stack, ndmi_stack

    inside = grid.mask_from(cfg.aoi, all_touched=True)
    for arr in (ndvi, texture, ndmi):
        arr[~inside] = np.nan

    coverage = np.isfinite(ndvi)[inside].mean()
    log(f"[vegetation] cloud-free coverage {coverage:.1%} of AOI")

    return classify(cfg, grid, ndvi, ndmi, texture, log=log)


def classify(cfg: Config, grid: Grid, ndvi, ndmi, texture, log=print) -> dict:
    """Turn the index composites into a closure continuum, score and classes.

    Split out from the download so thresholds can be retuned against cached
    indices without re-fetching gigabytes of imagery.
    """
    vcfg = cfg.species["vegetation"]

    # ---- canopy closure continuum ---------------------------------------
    nd = vcfg["ndmi"]
    tx = vcfg["texture"]
    closure = _normalise(ndmi, nd["open_max"], nd["closed_min"])

    tex_w = float(vcfg.get("closure_texture_weight", 0.0))
    if tex_w > 0:
        # Inverted: measured against reference populations, open ground is the
        # rougher of the two, so high texture argues for openness.
        closure_tex = 1.0 - _normalise(texture, tx["smooth_max"], tx["rough_min"])
        closure = (1.0 - tex_w) * closure + tex_w * closure_tex
    closure[~np.isfinite(ndmi)] = np.nan

    ndvi_cfg = vcfg["ndvi"]
    bare = ndvi < ndvi_cfg["hard_min"]

    pref = vcfg["openness_preference"]
    # Piecewise-linear credit along the closure continuum, so the meadow ->
    # open forest -> closed forest transition is graded rather than stepped.
    openness_credit = np.interp(
        np.nan_to_num(closure, nan=0.5),
        [0.0, 0.5, 1.0],
        [pref["meadow"], pref["open_forest"], pref["closed_forest"]],
    ).astype("float32")
    openness_credit[bare] = pref["bare"]
    openness_credit[~np.isfinite(closure)] = np.nan

    # hard_max is optional: a dry-ground species (mullein, sage) is wrong on
    # lush ground, so its fit can fall off above optimal_max. Without one, high
    # NDVI keeps full credit, which is right for a meadow plant.
    ndvi_fit = trapezoid(ndvi, ndvi_cfg["hard_min"], ndvi_cfg["optimal_min"], ndvi_cfg["optimal_max"],
                         ndvi_cfg.get("hard_max"))
    score = (ndvi_fit * openness_credit).astype("float32")

    if cfg.weights["hard_filters"].get("enforce_ndvi_hard_min", True):
        score = np.where(bare, np.nan, score)

    # ---- clearcut penalty ------------------------------------------------
    lcfg = vcfg.get("logging", {})
    age = logging_age(cfg, grid, log=log)
    penalty = np.ones(grid.shape, dtype="float32")
    recovery = float(lcfg.get("recovery_years", 45.0))
    floor = float(lcfg.get("penalty", 1.0))
    logged = np.isfinite(age)
    if floor < 1.0 and logged.any():
        # Full penalty on fresh ground, fading linearly back to no penalty once
        # the block reaches recovery_years.
        fade = np.clip(age / max(recovery, 1e-9), 0.0, 1.0)
        penalty = np.where(logged, floor + (1.0 - floor) * fade, 1.0).astype("float32")
        score = (score * penalty).astype("float32")
        hit = logged & (age < recovery)
        log(f"    clearcut penalty applied to {int(hit.sum()):,} cells "
            f"({hit.mean():.1%} of grid) logged within {recovery:g} years")

    # ---- categorical layer for the UI -----------------------------------
    veg_class = np.full(grid.shape, CLASS_NODATA, dtype="uint8")
    valid = np.isfinite(ndvi) & np.isfinite(closure)
    veg_class[valid & (closure >= 2 / 3)] = CLASS_CLOSED_FOREST
    veg_class[valid & (closure < 2 / 3)] = CLASS_OPEN_FOREST
    veg_class[valid & (closure < 1 / 3)] = CLASS_MEADOW
    veg_class[valid & bare] = CLASS_BARE
    # Regenerating cutblocks are called what they are, not "open meadow".
    veg_class[valid & logged & (age < recovery) & (veg_class == CLASS_MEADOW)] = CLASS_CUTBLOCK

    grid.write(cfg.interim("logging_age.tif"), age)

    grid.write(cfg.interim("ndvi.tif"), ndvi)
    grid.write(cfg.interim("ndmi.tif"), ndmi)
    grid.write(cfg.interim("ndvi_texture.tif"), texture)
    grid.write(cfg.species_interim("canopy_closure.tif"), closure.astype("float32"))
    grid.write(cfg.species_interim("score_vegetation.tif"), score)
    grid.write(cfg.species_interim("veg_class.tif"), veg_class, dtype="uint8")

    counts = {CLASS_NAMES[k]: int((veg_class == k).sum()) for k in CLASS_NAMES}
    total = max(sum(counts.values()), 1)
    log("[vegetation] class mix: " + ", ".join(f"{k} {v / total:.1%}" for k, v in counts.items()))
    return {"classes": counts}
