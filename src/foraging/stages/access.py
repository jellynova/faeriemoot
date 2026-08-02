"""Stage 3 - access.

Answers two questions per candidate cell, kept deliberately separate because
they trade off differently for different people:

* **Drive minutes** - how long to get from the configured origin to the road
  access point, over a routed network built from the BC Digital Road Atlas plus
  forest tenure roads.
* **Hike minutes / km** - how long from that road to the cell on foot, as a
  least-cost path over real terrain using Tobler's hiking function, so a
  kilometre up a headwall costs far more than a kilometre along a bench.

The two are surfaced separately in the UI and only combined for the ranking
number.

Snowpack and seasonal road closures are explicitly *not* modelled - the user
tracks those from local sources.
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from rasterio.features import rasterize
from shapely.geometry import LineString, MultiLineString

from ..config import Config
from ..curves import ramp_down, weighted_mean
from ..grid import Grid
from ..sources.bcdata import aoi_bbox_albers, fetch_layer
from ..sources.osm import fetch_trails

# DRA road classes that cannot be driven to a trailhead.
NON_DRIVABLE_CLASSES = {"trail", "pedestrian", "runway", "water", "ferry", "boat"}
NON_DRIVABLE_SURFACES = {"decommissioned", "overgrown"}

# Roads outside the AOI can still be the right way in.
ROAD_BUFFER_M = 8000.0

TOBLER_REF_SPEED = 5.0  # km/h on the flat, the speed Tobler's curve is scaled from


def tobler_speed_kmh(slope_deg: np.ndarray, base_speed_kmh: float = 5.0) -> np.ndarray:
    """Walking speed from terrain slope (Tobler's hiking function).

    The classic form peaks at ~6 km/h on a slight downhill and falls off sharply
    with steepness; ``base_speed_kmh`` rescales the whole curve to the walker.
    """
    grade = np.tan(np.radians(np.asarray(slope_deg, dtype="float64")))
    speed = 6.0 * np.exp(-3.5 * np.abs(grade + 0.05))
    return speed * (base_speed_kmh / TOBLER_REF_SPEED)


def _explode_lines(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return []


def _build_drive_graph(roads, speeds: dict, log=print) -> tuple[nx.Graph, dict]:
    """Node-per-endpoint graph weighted by minutes.

    The DRA is already noded at intersections, so segment endpoints are enough
    to reproduce network connectivity without exploding every vertex into a node.
    """
    g = nx.Graph()
    for row in roads.itertuples():
        cls = str(getattr(row, "road_class", "unknown") or "unknown").lower()
        speed = float(speeds.get(cls, speeds.get("unknown", 30.0)))
        for line in _explode_lines(row.geometry):
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            a = (round(coords[0][0], 0), round(coords[0][1], 0))
            b = (round(coords[-1][0], 0), round(coords[-1][1], 0))
            if a == b:
                continue
            minutes = (line.length / 1000.0) / max(speed, 1e-6) * 60.0
            if g.has_edge(a, b) and g[a][b]["minutes"] <= minutes:
                continue
            g.add_edge(a, b, minutes=minutes)
    log(f"    drive graph: {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")
    return g, {}


def _origin_drive_minutes(g: nx.Graph, origin_xy: tuple[float, float], log=print) -> dict:
    nodes = np.array(g.nodes)
    if len(nodes) == 0:
        raise RuntimeError("no road network found in the AOI")
    d = np.hypot(nodes[:, 0] - origin_xy[0], nodes[:, 1] - origin_xy[1])
    start = tuple(nodes[int(np.argmin(d))])
    log(f"    origin snapped to road network, {d.min():.0f} m away")

    lengths = nx.single_source_dijkstra_path_length(g, start, weight="minutes")
    reach = len(lengths) / g.number_of_nodes()
    log(f"    {len(lengths):,} nodes reachable by road ({reach:.0%} of network)")
    return lengths


def _rasterise_drive_time(grid: Grid, roads, node_minutes: dict, speeds: dict) -> np.ndarray:
    """Burn per-segment drive time onto the grid, lowest value winning."""
    shapes = []
    for row in roads.itertuples():
        for line in _explode_lines(row.geometry):
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            a = (round(coords[0][0], 0), round(coords[0][1], 0))
            b = (round(coords[-1][0], 0), round(coords[-1][1], 0))
            va, vb = node_minutes.get(a), node_minutes.get(b)
            vals = [v for v in (va, vb) if v is not None]
            if not vals:
                continue
            shapes.append((line, float(min(vals))))

    if not shapes:
        return np.full(grid.shape, np.nan, dtype="float32")

    # rasterize lets later shapes overwrite earlier ones, so painting in
    # descending time order leaves the quickest access on top.
    shapes.sort(key=lambda s: -s[1])
    out = rasterize(
        shapes,
        out_shape=grid.shape,
        transform=grid.transform,
        fill=np.nan,
        dtype="float32",
        all_touched=True,
    )
    return out


def _trace_to_source(traceback: np.ndarray, offsets: np.ndarray, res: float) -> tuple[np.ndarray, np.ndarray]:
    """Resolve, for every cell, which start cell its least-cost path came from.

    Walking each path one step at a time would mean a Python loop over millions
    of cells. Instead the predecessor pointers are doubled - ``pred = pred[pred]``
    - which collapses every path to its root in O(log n) vectorised passes,
    accumulating path length along the way.
    """
    h, w = traceback.shape
    n = h * w
    flat_tb = traceback.ravel()

    idx = np.arange(n, dtype="int64")
    rows, cols = np.divmod(idx, w)

    is_start = flat_tb < 0
    valid = flat_tb >= 0

    off = offsets[np.clip(flat_tb, 0, len(offsets) - 1)]
    pr = np.where(valid, rows - off[:, 0], rows)
    pc = np.where(valid, cols - off[:, 1], cols)
    np.clip(pr, 0, h - 1, out=pr)
    np.clip(pc, 0, w - 1, out=pc)

    pred = (pr * w + pc).astype("int64")
    pred[is_start] = idx[is_start]

    step = np.where(valid, np.hypot(off[:, 0], off[:, 1]) * res, 0.0).astype("float64")
    step[is_start] = 0.0

    # Pointer doubling: distance to root accumulates as the pointers collapse.
    for _ in range(32):
        nxt = pred[pred]
        step = step + step[pred]
        if np.array_equal(nxt, pred):
            pred = nxt
            break
        pred = nxt

    return pred, step


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))
    slope, _ = Grid.read(cfg.interim("slope.tif"))

    acc = cfg.pipeline["access"]
    speeds = acc["road_speeds_kmh"]
    bbox = aoi_bbox_albers(cfg.aoi, buffer_m=ROAD_BUFFER_M)

    log("[access] fetching BC road & trail layers")
    dra = fetch_layer("roads_dra", bbox, cfg.cache_dir, log=log)
    ften = fetch_layer("roads_ften", bbox, cfg.cache_dir, log=log)
    trails = fetch_layer("trails_ften", bbox, cfg.cache_dir, log=log)
    # OSM fills in the recreational trail network the tenure layer misses.
    w, s_, e, n = cfg.aoi.total_bounds
    osm_trails = fetch_trails((w, s_, e, n), cfg.cache_dir, log=log)

    # ---- assemble a single drivable network -----------------------------
    dra = dra.to_crs(grid.crs)
    if len(dra):
        dra["road_class"] = dra.get("ROAD_CLASS", "unknown").fillna("unknown").str.lower()
        surface = dra.get("ROAD_SURFACE")
        keep = ~dra["road_class"].isin(NON_DRIVABLE_CLASSES)
        if surface is not None:
            keep &= ~surface.fillna("unknown").str.lower().isin(NON_DRIVABLE_SURFACES)
        dropped = int((~keep).sum())
        dra = dra[keep]
        log(f"    dropped {dropped:,} non-drivable DRA segments")

    ften = ften.to_crs(grid.crs)
    if len(ften):
        # Forest tenure roads carry no class; they are resource roads by nature.
        ften = ften.assign(road_class="resource")
        if "LIFE_CYCLE_STATUS_CODE" in ften.columns:
            ften = ften[ften["LIFE_CYCLE_STATUS_CODE"].fillna("ACTIVE").str.upper() == "ACTIVE"]

    cols = ["road_class", "geometry"]
    roads = gpd.GeoDataFrame(
        pd.concat([d[cols] for d in (dra, ften) if len(d)], ignore_index=True), crs=grid.crs
    )
    log(f"    {len(roads):,} drivable road segments")

    graph, _ = _build_drive_graph(roads, speeds, log=log)

    origin = acc["origin"]
    ox, oy = (
        gpd.GeoSeries.from_xy([origin["lon"]], [origin["lat"]], crs="EPSG:4326")
        .to_crs(grid.crs)
        .iloc[0]
        .coords[0]
    )
    log(f"[access] routing from {origin['name']}")
    node_minutes = _origin_drive_minutes(graph, (ox, oy), log=log)

    drive_on_road = _rasterise_drive_time(grid, roads, node_minutes, speeds)
    road_mask = np.isfinite(drive_on_road)
    log(f"    {road_mask.sum():,} road cells on the grid")

    # ---- least-cost hike from the road ----------------------------------
    log("[access] least-cost hike surface (Tobler)")
    speed = tobler_speed_kmh(slope, float(acc.get("hike_base_speed_kmh", 5.0)))
    speed = np.where(np.isfinite(speed), speed, 0.05)
    speed = np.clip(speed, 0.05, 8.0)
    cost_min_per_m = 60.0 / (speed * 1000.0)

    # Off-trail travel is slower than the same grade on a trail.
    trail_mask = np.zeros(grid.shape, dtype=bool)
    trail_frames = [t.to_crs(grid.crs)[["geometry"]] for t in (trails, osm_trails) if len(t)]
    if trail_frames:
        all_trails = gpd.GeoDataFrame(pd.concat(trail_frames, ignore_index=True), crs=grid.crs)
        trail_mask = grid.mask_from(all_trails, all_touched=True)
        log(f"    {len(all_trails):,} trail segments, {trail_mask.sum():,} trail cells")
    penalty = float(acc.get("offtrail_penalty", 1.0))
    cost_min_per_m = np.where(trail_mask | road_mask, cost_min_per_m, cost_min_per_m * penalty)

    # Cliffs are treated as barriers rather than merely slow.
    impassable = ~np.isfinite(slope) | (slope > 45.0)
    cost_min_per_m = np.where(impassable, 1e4, cost_min_per_m)
    cost_min_per_m[road_mask] = 1e-6

    from skimage.graph import MCP_Geometric

    starts = np.argwhere(road_mask)
    mcp = MCP_Geometric(cost_min_per_m.astype("float64"), sampling=(grid.resolution, grid.resolution),
                        fully_connected=True)
    hike_minutes, traceback = mcp.find_costs(starts)
    hike_minutes = np.asarray(hike_minutes, dtype="float32")
    hike_minutes[~np.isfinite(hike_minutes)] = np.nan

    pred, path_len_m = _trace_to_source(
        np.asarray(traceback), np.asarray(mcp.offsets, dtype="int64"), grid.resolution
    )
    hike_km = (path_len_m.reshape(grid.shape) / 1000.0).astype("float32")
    # Drive time of the road cell each path actually traces back to - not the
    # euclidean-nearest road, which can be over a ridge.
    drive_minutes = drive_on_road.ravel()[pred].reshape(grid.shape).astype("float32")

    inside = grid.mask_from(cfg.aoi, all_touched=True)
    for arr in (hike_minutes, hike_km, drive_minutes):
        arr[~inside] = np.nan
    hike_minutes[hike_minutes > 24 * 60] = np.nan

    # ---- score -----------------------------------------------------------
    max_drive = float(acc.get("max_drive_minutes", 90))
    max_hike = float(acc.get("max_hike_km", 6.0))
    s_drive = ramp_down(drive_minutes, best=max_drive * 0.25, worst=max_drive)
    s_hike = ramp_down(hike_km, best=0.25, worst=max_hike)

    sub = cfg.weights["access_subweights"]
    score = weighted_mean({"drive": s_drive, "hike": s_hike}, sub).astype("float32")

    hard = cfg.weights["hard_filters"]
    if hard.get("enforce_max_drive_minutes", False):
        score = np.where(drive_minutes <= max_drive, score, np.nan)
    if hard.get("enforce_max_hike_km", False):
        score = np.where(hike_km <= max_hike, score, np.nan)

    grid.write(cfg.interim("drive_minutes.tif"), drive_minutes)
    grid.write(cfg.interim("hike_minutes.tif"), hike_minutes)
    grid.write(cfg.interim("hike_km.tif"), hike_km)
    grid.write(cfg.interim("score_access.tif"), score)
    grid.write(cfg.interim("road_mask.tif"), road_mask.astype("uint8"), dtype="uint8")

    reachable = np.isfinite(hike_minutes) & (hike_km <= max_hike)
    log(f"[access] median drive {np.nanmedian(drive_minutes):.0f} min, "
        f"median hike {np.nanmedian(hike_km):.1f} km")
    log(f"[access] {reachable.sum():,} cells within {max_hike:g} km of a road")
    return {"roads": len(roads), "road_cells": int(road_mask.sum())}
