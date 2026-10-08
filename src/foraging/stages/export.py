"""Stage 7 - web export.

Turns the pipeline's UTM rasters and BC-Albers vectors into assets a browser
can use: PNG overlays reprojected to WGS84 (Leaflet's ``imageOverlay`` treats
an image as equirectangular, so a UTM PNG would sit visibly askew), simplified
GeoJSON for the vector toggles, and a manifest the UI configures itself from.

Everything lands in ``web/data/<aoi_id>/<species_id>/``, so dropping in a new
AOI or a new target produces a parallel directory and the UI picks it up
without code changes.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject

from ..config import Config
from ..grid import Grid
from ..sources.bcdata import aoi_bbox_albers, fetch_layer
from .forest import CLASS_NAMES as FOREST_CLASS_NAMES
from .riparian import CLASS_NAMES as RIPARIAN_CLASS_NAMES
from .vegetation import CLASS_NAMES as VEG_CLASS_NAMES

# Compact colour ramps as (stop, r, g, b). Hand-rolled to avoid a matplotlib
# dependency for what amounts to six lookup tables.
RAMPS = {
    "suitability": [(0.0, 68, 1, 84), (0.25, 59, 82, 139), (0.5, 33, 145, 140),
                    (0.75, 94, 201, 98), (1.0, 253, 231, 37)],
    "elevation": [(0.0, 60, 110, 70), (0.35, 150, 175, 110), (0.6, 190, 165, 120),
                  (0.8, 160, 130, 110), (1.0, 250, 250, 252)],
    "slope": [(0.0, 255, 255, 204), (0.4, 253, 176, 74), (0.7, 227, 90, 60), (1.0, 128, 0, 38)],
    "ndvi": [(0.0, 140, 100, 60), (0.4, 220, 210, 140), (0.7, 90, 170, 70), (1.0, 10, 80, 30)],
    "host": [(0.0, 245, 240, 225), (0.3, 200, 190, 110), (0.6, 120, 150, 60), (1.0, 30, 80, 40)],
    # Water distance is inverted on the way in, so the ramp reads "near" -> "far".
    "water": [(0.0, 30, 90, 160), (0.4, 70, 150, 180), (1.0, 225, 235, 220)],
}

# Cyclic ramp so north wraps cleanly; used for aspect.
ASPECT_RAMP = [(0.0, 235, 90, 90), (0.25, 235, 220, 90), (0.5, 90, 200, 235),
               (0.75, 140, 110, 220), (1.0, 235, 90, 90)]

VEG_COLOURS = {
    1: (170, 150, 130),   # bare / rock / cutblock
    2: (120, 200, 90),    # open meadow
    3: (60, 140, 80),     # open forest
    4: (25, 70, 45),      # closed forest
    5: (198, 122, 62),    # regenerating cutblock
}

FOREST_COLOURS = {
    1: (205, 195, 160),   # non-forest
    2: (120, 120, 150),   # forest, no hosts
    3: (150, 175, 90),    # forest, some hosts
    4: (35, 110, 50),     # host-rich forest
    5: (198, 122, 62),    # young / recently harvested
}

RIPARIAN_COLOURS = {
    1: (60, 120, 190),    # open water
    2: (110, 170, 160),   # wetland
    3: (80, 150, 210),    # lake / river shore
    4: (60, 175, 170),    # stream corridor
    5: (200, 190, 160),   # beyond water range
}

TENURE_COLOURS = {
    1: (200, 170, 90),    # woodlot
    2: (220, 120, 120),   # private
    3: (150, 190, 120),   # recreation area
    4: (90, 170, 110),    # provincial park
    5: (70, 150, 140),    # conservancy
    6: (170, 100, 180),   # ecological reserve
    7: (60, 130, 90),     # national park
}


def _legend(colours: dict, names: dict) -> list[list[str]]:
    """[[hex, label], ...] so the UI draws categorical legends from the manifest."""
    return [["#{:02x}{:02x}{:02x}".format(*colours[k]), names[k]] for k in colours if k in names]


def _ramp_lut(stops) -> np.ndarray:
    """Expand colour stops into a 256-entry lookup table."""
    lut = np.zeros((256, 3), dtype="uint8")
    xs = np.linspace(0.0, 1.0, 256)
    pos = np.array([s[0] for s in stops])
    cols = np.array([s[1:] for s in stops], dtype="float64")
    for ch in range(3):
        lut[:, ch] = np.interp(xs, pos, cols[:, ch]).astype("uint8")
    return lut


def _to_wgs84(array: np.ndarray, grid: Grid, bounds, resampling=Resampling.bilinear):
    """Reproject a grid array onto a plain lat/lon raster covering ``bounds``."""
    west, south, east, north = bounds
    # Keep roughly the native cell count so nothing is thrown away.
    height = grid.height
    width = int(round(height * (east - west) / max(north - south, 1e-9)))
    dst = np.full((height, width), np.nan, dtype="float32")
    transform = from_bounds(west, south, east, north, width, height)
    reproject(
        source=array.astype("float32"),
        destination=dst,
        src_transform=grid.transform,
        src_crs=grid.crs,
        src_nodata=np.nan,
        dst_transform=transform,
        dst_crs="EPSG:4326",
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return dst


def _write_png(path, rgb: np.ndarray, alpha: np.ndarray) -> None:
    rgba = np.dstack([rgb, alpha]).astype("uint8")
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)


def _continuous_overlay(path, array, grid, bounds, ramp, vmin=None, vmax=None, opacity=200):
    warped = _to_wgs84(array, grid, bounds)
    valid = np.isfinite(warped)
    if not valid.any():
        return None
    lo = float(np.nanpercentile(warped[valid], 2)) if vmin is None else vmin
    hi = float(np.nanpercentile(warped[valid], 98)) if vmax is None else vmax
    norm = np.clip((warped - lo) / max(hi - lo, 1e-9), 0, 1)
    idx = np.nan_to_num(norm * 255, nan=0).astype("uint8")
    rgb = _ramp_lut(ramp)[idx]
    alpha = np.where(valid, opacity, 0).astype("uint8")
    _write_png(path, rgb, alpha)
    return {"min": lo, "max": hi}


def _categorical_overlay(path, array, grid, bounds, colours, opacity=190):
    warped = _to_wgs84(array.astype("float32"), grid, bounds, resampling=Resampling.nearest)
    codes = np.nan_to_num(warped, nan=0).astype("int32")
    rgb = np.zeros((*codes.shape, 3), dtype="uint8")
    alpha = np.zeros(codes.shape, dtype="uint8")
    for code, colour in colours.items():
        m = codes == code
        if m.any():
            rgb[m] = colour
            alpha[m] = opacity
    _write_png(path, rgb, alpha)


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    web_dir = cfg.root / "web" / "data" / cfg.run_id
    web_dir.mkdir(parents=True, exist_ok=True)
    bounds = tuple(float(b) for b in cfg.aoi.total_bounds)
    log(f"[export] writing web assets to web/data/{cfg.run_id}/")

    layers: dict[str, dict] = {}

    def add_continuous(key, raster, ramp, label, **kw):
        arr = _safe_read(cfg, raster)
        if arr is None:
            return
        stats = _continuous_overlay(web_dir / f"{key}.png", arr, grid, bounds, ramp, **kw)
        if stats:
            layers[key] = {"label": label, "file": f"{key}.png", "type": "continuous", **stats}
            log(f"    {key}.png")

    add_continuous("suitability", "score_total", RAMPS["suitability"], "Suitability score",
                   vmin=0.0, vmax=1.0)
    add_continuous("elevation", "elevation", RAMPS["elevation"], "Elevation")
    add_continuous("slope", "slope", RAMPS["slope"], "Slope")
    add_continuous("ndvi", "ndvi", RAMPS["ndvi"], "NDVI", vmin=0.0, vmax=1.0)

    aspect = _safe_read(cfg, "aspect")
    if aspect is not None:
        _continuous_overlay(web_dir / "aspect.png", aspect, grid, bounds, ASPECT_RAMP,
                            vmin=0.0, vmax=360.0)
        layers["aspect"] = {"label": "Aspect", "file": "aspect.png", "type": "cyclic"}
        log("    aspect.png")

    veg = _safe_read(cfg, "veg_class") if cfg.habitat_layer == "vegetation" else None
    if veg is not None:
        _categorical_overlay(web_dir / "vegetation.png", veg, grid, bounds, VEG_COLOURS)
        layers["vegetation"] = {"label": "Vegetation class", "file": "vegetation.png",
                                "type": "categorical",
                                "legend": _legend(VEG_COLOURS, VEG_CLASS_NAMES)}
        log("    vegetation.png")

    if cfg.habitat_layer == "forest":
        fclass = _safe_read(cfg, "forest_class")
        if fclass is not None:
            _categorical_overlay(web_dir / "forest.png", fclass, grid, bounds, FOREST_COLOURS)
            layers["forest"] = {"label": "Forest / host trees", "file": "forest.png",
                                "type": "categorical",
                                "legend": _legend(FOREST_COLOURS, FOREST_CLASS_NAMES)}
            log("    forest.png")
        add_continuous("host_fraction", "host_fraction", RAMPS["host"], "Host-tree share",
                       vmin=0.0, vmax=1.0)
        add_continuous("stand_age", "stand_age", RAMPS["ndvi"], "Stand age (years)",
                       vmin=0.0, vmax=200.0)

    if cfg.habitat_layer == "riparian":
        rclass = _safe_read(cfg, "riparian_class")
        if rclass is not None:
            _categorical_overlay(web_dir / "riparian.png", rclass, grid, bounds, RIPARIAN_COLOURS)
            layers["riparian"] = {"label": "Riparian class", "file": "riparian.png",
                                  "type": "categorical",
                                  "legend": _legend(RIPARIAN_COLOURS, RIPARIAN_CLASS_NAMES)}
            log("    riparian.png")
        # Distance is drawn nearest-dark, so a dark pixel is a wet one.
        dist = _safe_read(cfg, "distance_to_water")
        if dist is not None and np.isfinite(dist).any():
            add_continuous("water_distance", "distance_to_water", RAMPS["water"],
                           "Distance to water (m)", vmin=0.0, vmax=500.0)

    logging_age = _safe_read(cfg, "logging_age")
    if logging_age is not None and np.isfinite(logging_age).any():
        stats = _continuous_overlay(web_dir / "logging_age.png", logging_age, grid, bounds,
                                    RAMPS["ndvi"], vmin=0.0, vmax=60.0, opacity=200)
        if stats:
            layers["logging_age"] = {"label": "Years since logging", "file": "logging_age.png",
                                     "type": "continuous", **stats}
            log("    logging_age.png")

    tenure = _safe_read(cfg, "land_tenure")
    if tenure is not None:
        _categorical_overlay(web_dir / "land_tenure.png", tenure, grid, bounds, TENURE_COLOURS)
        layers["land_tenure"] = {"label": "Land tenure", "file": "land_tenure.png",
                                 "type": "categorical", "legend_key": "land_tenure"}
        log("    land_tenure.png")

    # ---- vector layers ---------------------------------------------------
    _export_vectors(cfg, web_dir, log=log)

    for name in ("sites.geojson", "observations.geojson", "manifest.json"):
        src = cfg.output(name)
        if src.exists():
            shutil.copy(src, web_dir / name)

    # AOI outline, so the UI can frame and show the region boundary.
    cfg.aoi.to_file(web_dir / "aoi.geojson", driver="GeoJSON")

    manifest_path = web_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest["raster_layers"] = layers
    manifest["image_bounds"] = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]
    manifest_path.write_text(json.dumps(manifest, indent=2))

    _write_index(cfg, log=log)
    log(f"[export] {len(layers)} raster overlays + vector layers ready")
    return {"layers": list(layers)}


# Layers that only ever exist per species. These are never read from the
# shared directory, where a build from before species were separated may have
# left a copy made with another profile's thresholds.
SPECIES_LAYERS = {"score_total", "veg_class", "forest_class", "host_fraction", "riparian_class"}


def _safe_read(cfg: Config, name: str):
    """Species layer, then shared layer, then the species output directory."""
    paths = [cfg.species_interim(f"{name}.tif"), cfg.output(f"{name}.tif")]
    if name not in SPECIES_LAYERS:
        paths.insert(1, cfg.interim(f"{name}.tif"))
    for path in paths:
        if path.exists():
            with rasterio.open(path) as src:
                return src.read(1).astype("float32")
    return None


def _export_vectors(cfg: Config, web_dir, log=print) -> None:
    """Roads, trails and protected areas, simplified for browser delivery."""
    bbox = aoi_bbox_albers(cfg.aoi)
    aoi_geom = cfg.aoi_geom

    def dump(name, gdf, tolerance_m, keep_cols):
        if gdf is None or not len(gdf):
            return
        g = gdf.to_crs("EPSG:3005")
        g["geometry"] = g.geometry.simplify(tolerance_m, preserve_topology=False)
        g = g.to_crs("EPSG:4326")
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        g = g[g.geometry.intersects(aoi_geom)]
        cols = [c for c in keep_cols if c in g.columns] + ["geometry"]
        out = web_dir / f"{name}.geojson"
        g[cols].to_file(out, driver="GeoJSON")
        log(f"    {name}.geojson ({len(g):,} features, {out.stat().st_size / 1e6:.1f} MB)")

    try:
        roads = fetch_layer("roads_dra", bbox, cfg.cache_dir, log=lambda *a: None)
        dump("roads", roads, 40.0, ["ROAD_NAME_FULL", "ROAD_CLASS", "ROAD_SURFACE"])
    except Exception as exc:
        log(f"    ! roads export skipped: {exc}")

    try:
        from ..sources.osm import fetch_trails

        w, s, e, n = cfg.aoi.total_bounds
        trails = fetch_trails((w, s, e, n), cfg.cache_dir, log=lambda *a: None)
        dump("trails", trails, 25.0, ["name", "highway"])
    except Exception as exc:
        log(f"    ! trails export skipped: {exc}")

    try:
        parks = fetch_layer("parks_provincial", bbox, cfg.cache_dir, log=lambda *a: None)
        dump("protected_areas", parks, 30.0,
             ["PROTECTED_LANDS_NAME", "PROTECTED_LANDS_DESIGNATION"])
    except Exception as exc:
        log(f"    ! protected areas export skipped: {exc}")


def _write_index(cfg: Config, log=print) -> None:
    """List every built AOI x species so the UI can offer a switcher."""
    data_root = cfg.root / "web" / "data"
    entries = []
    # web/data/<aoi>/<species>/manifest.json. A manifest directly under <aoi>/
    # is a build from before targets were separated; it is skipped rather than
    # listed, since its layer files are laid out differently.
    for mf in sorted(data_root.glob("*/*/manifest.json")):
        d = mf.parent
        m = json.loads(mf.read_text())
        sp = m.get("species", {})
        entries.append({
            "id": f"{d.parent.name}/{d.name}",
            "label": m.get("aoi", {}).get("label", d.parent.name),
            "species": sp.get("scientific_name"),
            "common_name": sp.get("common_name"),
            "sites": m.get("counts", {}).get("sites", 0),
        })
    (data_root / "index.json").write_text(json.dumps({"areas": entries}, indent=2))
    log(f"    index.json ({len(entries)} area/species build(s))")
