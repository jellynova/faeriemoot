"""iNaturalist observation access.

Research-grade, species-filtered, clipped to the AOI. No API key is needed for
read access; the API asks callers to identify themselves and to stay under
about one request per second.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import geopandas as gpd
import requests

API = "https://api.inaturalist.org/v1/observations"
TAXA_API = "https://api.inaturalist.org/v1/taxa"
USER_AGENT = "foraging-suitability-mapper/0.1 (+https://github.com/jellynova/faeriemoot)"
PER_PAGE = 200


def pick_taxon(results: list[dict], name: str) -> dict | None:
    """The active taxon whose scientific name is exactly ``name``.

    Several ranks can share a name (genus *Cantharellus* and subgenus
    *Cantharellus*); the one with the most observations is the one people
    mean, and for a genus it is the genus.
    """
    want = name.strip().lower()
    exact = [r for r in results
             if str(r.get("name", "")).lower() == want and r.get("is_active", True)]
    if not exact:
        return None
    return max(exact, key=lambda r: r.get("observations_count") or 0)


def resolve_taxon_id(name: str, cache_dir: Path, log=print) -> int:
    """Map a scientific name to an iNaturalist taxon ID.

    The observations endpoint's ``taxon_name`` parameter also matches
    *common* names, so querying it is not a taxonomic filter. "Cantharellus"
    returns false chanterelle (*Hygrophoropsis*, a different order) and
    *Hygrocybe cantharellus*; "Arnica" returns *Erigeron divergens*. Querying
    by ID returns the taxon and its descendants and nothing else.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "inat_taxon_ids.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if name in known:
        return int(known[name])

    r = requests.get(TAXA_API, params={"q": name, "per_page": 30},
                     headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    results = r.json().get("results", [])
    hit = pick_taxon(results, name)
    if hit is None:
        near = ", ".join(str(x.get("name")) for x in results[:5]) or "nothing"
        raise ValueError(f"iNaturalist has no taxon named exactly {name!r} (closest: {near})")
    known[name] = int(hit["id"])
    cache.write_text(json.dumps(known, indent=2, sort_keys=True))
    log(f"    {name}: iNaturalist taxon {hit['id']} ({hit.get('rank')})")
    return int(hit["id"])


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
    taxon_id = resolve_taxon_id(taxon_name, cache_dir, log=log)
    # Every query parameter has to be in the key. Keying on taxon alone meant a
    # second region silently reused the first region's records and then filtered
    # them all out as being outside its own AOI, reporting zero observations for
    # an area that genuinely has some. The taxon ID is in it too, so caches made
    # by the old name-matched query are not reused.
    key = hashlib.sha1(
        f"{taxon_id}|{bbox_wgs84}|{quality_grade}|{sorted(months) if months else None}".encode()
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
            "taxon_id": taxon_id,
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
