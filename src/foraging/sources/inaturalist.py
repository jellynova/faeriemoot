"""iNaturalist observation access.

Research-grade, species-filtered, clipped to the AOI. No API key is needed for
read access; the API asks callers to identify themselves and to stay under
about one request per second.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import geopandas as gpd
import requests

API = "https://api.inaturalist.org/v1/observations"
USER_AGENT = "foraging-suitability-mapper/0.1 (+https://github.com/jellynova/faeriemoot)"
PER_PAGE = 200


def fetch_observations(
    taxon_name: str,
    bbox_wgs84: tuple[float, float, float, float],
    cache_dir: Path,
    quality_grade: str = "research",
    months: list[int] | None = None,
    log=print,
) -> gpd.GeoDataFrame:
    """All matching observations for ``taxon_name`` inside ``bbox_wgs84``."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = taxon_name.replace(" ", "_").lower()
    # Every query parameter has to be in the key. Keying on taxon alone meant a
    # second region silently reused the first region's records and then filtered
    # them all out as being outside its own AOI, reporting zero observations for
    # an area that genuinely has some.
    key = hashlib.sha1(
        f"{bbox_wgs84}|{quality_grade}|{sorted(months) if months else None}".encode()
    ).hexdigest()[:12]
    cache = cache_dir / f"inat_{safe}_{key}.gpkg"
    if cache.exists():
        gdf = gpd.read_file(cache)
        log(f"    {taxon_name}: {len(gdf)} observations (cached)")
        return gdf

    w, s, e, n = bbox_wgs84
    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "taxon_name": taxon_name,
            "quality_grade": quality_grade,
            "geo": "true",
            "swlat": s, "swlng": w, "nelat": n, "nelng": e,
            "per_page": PER_PAGE,
            "page": page,
        }
        if months:
            params["month"] = ",".join(str(m) for m in months)

        r = requests.get(API, params=params, headers={"User-Agent": USER_AGENT}, timeout=120)
        if r.status_code != 200:
            log(f"    ! iNaturalist HTTP {r.status_code} for {taxon_name}")
            break
        payload = r.json()
        results = payload.get("results", [])
        for o in results:
            loc = o.get("geojson") or {}
            coords = loc.get("coordinates")
            if not coords:
                continue
            taxon = o.get("taxon") or {}
            rows.append({
                "id": o.get("id"),
                "taxon": taxon.get("name"),
                "rank": taxon.get("rank"),
                "observed_on": o.get("observed_on"),
                # Public coordinates are fuzzed for sensitive taxa; the accuracy
                # figure matters when deciding how far to spread the boost.
                "accuracy_m": o.get("positional_accuracy"),
                "obscured": bool(o.get("obscured")),
                "url": f"https://www.inaturalist.org/observations/{o.get('id')}",
                "lon": coords[0],
                "lat": coords[1],
            })
        if len(results) < PER_PAGE or page * PER_PAGE >= payload.get("total_results", 0):
            break
        page += 1
        time.sleep(1.0)  # be polite to a free API

    if not rows:
        gdf = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    else:
        gdf = gpd.GeoDataFrame(
            rows,
            geometry=gpd.points_from_xy([r["lon"] for r in rows], [r["lat"] for r in rows]),
            crs="EPSG:4326",
        )
    log(f"    {taxon_name}: {len(gdf)} observations")
    if len(gdf):
        gdf.to_file(cache, driver="GPKG")
    return gdf
