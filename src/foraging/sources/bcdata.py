"""BC Data Catalogue WFS access.

All layers used here are on the province's public WFS endpoint and need no
account or API key. Requests are made in BC Albers (EPSG:3005) with a CQL
``BBOX`` filter rather than the WFS ``bbox`` parameter, because the latter's
axis order for EPSG:4326 is ambiguous between WFS versions.

Responses are cached to disk keyed by layer + bbox, so re-running a stage does
not re-download.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

WFS_URL = "https://openmaps.gov.bc.ca/geo/pub/wfs"
USER_AGENT = "foraging-suitability-mapper/0.1 (+https://github.com/jellynova/faeriemoot)"
PAGE_SIZE = 10000
ALBERS = "EPSG:3005"

# Layer aliases so callers do not carry the full warehouse table names around.
LAYERS = {
    "roads_dra": "WHSE_BASEMAPPING.DRA_DGTL_ROAD_ATLAS_MPAR_SP",
    "roads_ften": "WHSE_FOREST_TENURE.FTEN_ROAD_SECTION_LINES_SVW",
    "trails_ften": "WHSE_FOREST_TENURE.FTEN_REC_TRAILS_SVW",
    "parks_provincial": "WHSE_TANTALIS.TA_PARK_ECORES_PA_SVW",
    "parks_national": "WHSE_ADMIN_BOUNDARIES.CLAB_NATIONAL_PARKS",
    "conservancies": "WHSE_TANTALIS.TA_CONSERVANCY_AREAS_SVW",
    "parcels": "WHSE_CADASTRE.PMBC_PARCEL_FABRIC_POLY_SVW",
    "woodlots": "WHSE_FOREST_TENURE.FTEN_MANAGED_LICENCE_POLY_SVW",
}


def _cache_key(layer: str, bbox: tuple[float, float, float, float]) -> str:
    raw = f"{layer}|{','.join(f'{v:.1f}' for v in bbox)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


_GEOM_COL_CACHE: dict[str, str] = {}
_GEOM_RE = re.compile(r'name="(\w+)"[^>]*type="gml:\w*(?:Geometry|Surface|Curve|Point)\w*PropertyType"')


def geometry_column(layer: str, log=print) -> str:
    """Discover a layer's geometry attribute name.

    BC's WFS is not consistent here - roads and national parks use ``GEOMETRY``
    while provincial parks, conservancies and ParcelMap use ``SHAPE`` - and a
    CQL ``BBOX`` against the wrong name is a hard 400.
    """
    if layer in _GEOM_COL_CACHE:
        return _GEOM_COL_CACHE[layer]
    try:
        r = requests.get(
            WFS_URL,
            params={"service": "WFS", "version": "2.0.0", "request": "DescribeFeatureType",
                    "typeNames": f"pub:{layer}"},
            timeout=120,
        )
        match = _GEOM_RE.search(r.text)
        col = match.group(1) if match else "GEOMETRY"
    except requests.RequestException:
        col = "GEOMETRY"
    _GEOM_COL_CACHE[layer] = col
    return col


def fetch_layer(
    alias_or_name: str,
    bbox_albers: tuple[float, float, float, float],
    cache_dir: Path,
    log=print,
    retries: int = 4,
) -> gpd.GeoDataFrame:
    """Fetch a WFS layer clipped to ``bbox_albers``, paging until exhausted."""
    layer = LAYERS.get(alias_or_name, alias_or_name)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{alias_or_name}_{_cache_key(layer, bbox_albers)}.gpkg"

    if cache.exists():
        gdf = gpd.read_file(cache)
        log(f"    {alias_or_name}: {len(gdf):,} features (cached)")
        return gdf

    x0, y0, x1, y1 = bbox_albers
    geom_col = geometry_column(layer, log=log)
    frames: list[gpd.GeoDataFrame] = []
    start = 0
    needs_sort = False
    while True:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": f"pub:{layer}",
            "outputFormat": "application/json",
            "srsName": ALBERS,
            "count": str(PAGE_SIZE),
            "CQL_FILTER": f"BBOX({geom_col},{x0},{y0},{x1},{y1})",
        }
        # startIndex is omitted on the first page: sending it forces the server
        # into a sorted read, which times out on the larger layers.
        if start:
            params["startIndex"] = str(start)
            if needs_sort:
                params["sortBy"] = "OBJECTID"

        try:
            content = _get_with_retry(params, retries=retries, log=log)
        except NaturalOrderError:
            # Layer has no primary key, so paging needs an explicit sort.
            needs_sort = True
            params["sortBy"] = "OBJECTID"
            content = _get_with_retry(params, retries=retries, log=log)

        page = gpd.read_file(io.BytesIO(content))
        if len(page) == 0:
            break
        frames.append(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    if not frames:
        gdf = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=ALBERS)
    else:
        gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)

    log(f"    {alias_or_name}: {len(gdf):,} features")
    if len(gdf):
        # Timestamp columns round-trip badly through GPKG; they are not used.
        drop = [c for c in gdf.columns if str(gdf[c].dtype).startswith("datetime")]
        gdf.drop(columns=drop).to_file(cache, driver="GPKG")
    return gdf


def _readable(text: str, limit: int = 200) -> str:
    """Strip XML tags so WFS exception reports are legible in a log line."""
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())[:limit]


class NaturalOrderError(RuntimeError):
    """Server refused a paged read because the layer has no primary key."""


def _get_with_retry(params: dict, retries: int, log) -> bytes:
    delay = 2.0
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(WFS_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=300)
            if r.status_code == 200:
                return r.content
            if r.status_code == 400 and "natural order" in r.text.lower():
                raise NaturalOrderError(r.text[:200])
            last = f"HTTP {r.status_code}: {_readable(r.text)}"
        except requests.RequestException as exc:  # network flake
            last = str(exc)
        if attempt < retries - 1:
            log(f"    retry in {delay:.0f}s ({last})")
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"WFS request failed after {retries} attempts: {last}")


def aoi_bbox_albers(aoi: gpd.GeoDataFrame, buffer_m: float = 0.0) -> tuple[float, float, float, float]:
    """AOI bounds in BC Albers, optionally buffered.

    The buffer matters for the access layer: a road just outside the AOI can
    still be the right way in, so the road fetch is deliberately wider than the
    scoring extent.
    """
    b = aoi.to_crs(ALBERS)
    if buffer_m:
        b = b.buffer(buffer_m)
    x0, y0, x1, y1 = b.total_bounds
    return (float(x0), float(y0), float(x1), float(y1))
