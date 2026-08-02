"""OpenStreetMap trail access via Overpass.

BC's forest-tenure recreation-trail layer only covers trails under an active
tenure - it returned 28 features for the whole 2,900 km2 West Kootenays AOI,
missing essentially every well-known hiking trail in the region. OSM has far
better coverage of actual walked trails, so the two are merged.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import LineString

# Overpass rejects the default python-requests User-Agent with HTTP 406, and
# asks API users to identify themselves regardless.
USER_AGENT = "foraging-suitability-mapper/0.1 (+https://github.com/jellynova/faeriemoot)"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Ways people actually walk on. Roads are handled by the drive network, so
# only foot-usable ways matter here.
TRAIL_QUERY = """
[out:json][timeout:180];
(
  way["highway"~"^(path|footway|track|bridleway|cycleway)$"]({bbox});
  way["route"="hiking"]({bbox});
);
out geom;
"""


def fetch_trails(bbox_wgs84: tuple[float, float, float, float], cache_dir: Path,
                 log=print, retries: int = 3) -> gpd.GeoDataFrame:
    """Fetch walkable ways in ``bbox_wgs84`` (west, south, east, north)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(",".join(f"{v:.4f}" for v in bbox_wgs84).encode()).hexdigest()[:16]
    cache = cache_dir / f"osm_trails_{key}.gpkg"

    if cache.exists():
        gdf = gpd.read_file(cache)
        log(f"    osm_trails: {len(gdf):,} features (cached)")
        return gdf

    w, s, e, n = bbox_wgs84
    query = TRAIL_QUERY.format(bbox=f"{s},{w},{n},{e}")

    data = None
    delay = 3.0
    for attempt in range(retries):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": query},
                                  headers={"User-Agent": USER_AGENT}, timeout=300)
                if r.status_code == 200:
                    data = r.json()
                    break
                log(f"    overpass {url.split('/')[2]} -> HTTP {r.status_code}")
            except (requests.RequestException, ValueError) as exc:
                log(f"    overpass {url.split('/')[2]} -> {type(exc).__name__}")
        if data is not None:
            break
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2

    if data is None:
        # Trails are an enhancement to the hike model, not a requirement -
        # degrade to the tenure layer alone rather than failing the stage.
        log("    ! Overpass unavailable, continuing without OSM trails")
        return gpd.GeoDataFrame({"geometry": [], "highway": []}, geometry="geometry", crs="EPSG:4326")

    rows = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        rows.append({
            "highway": el.get("tags", {}).get("highway", "path"),
            "name": el.get("tags", {}).get("name"),
            "geometry": LineString([(p["lon"], p["lat"]) for p in geom]),
        })

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    log(f"    osm_trails: {len(gdf):,} features")
    if len(gdf):
        gdf.to_file(cache, driver="GPKG")
    return gdf
