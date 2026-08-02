"""Stage 5 - land status and legality.

Foraging rules differ by land tenure, so this stage never quietly sinks a
site's rank. It assigns each cell a tenure class, and the weights file decides
what happens to each class:

* ``exclude_from_ranking`` - dropped from the ranked list entirely (national
  parks and ecological reserves, where harvesting is flatly prohibited).
* ``flag_only`` - stays ranked, but carries a visible flag so the user can make
  the call themselves (provincial parks, private parcels, woodlots).
* everything else falls back to ``unflagged_default`` - Crown land, the default
  tenure across most of the BC backcountry.

This is a guide to *what tenure the ground is*, not legal advice about what may
be harvested there.
"""

from __future__ import annotations

import numpy as np

from ..config import Config
from ..grid import Grid
from ..sources.bcdata import aoi_bbox_albers, fetch_layer

# Rasterised in ascending order of precedence: later classes overwrite earlier
# ones where polygons overlap, so the most restrictive tenure wins the cell.
CLASS_CROWN = 0
CLASS_WOODLOT = 1
CLASS_PRIVATE = 2
CLASS_RECREATION_AREA = 3
CLASS_PROVINCIAL_PARK = 4
CLASS_CONSERVANCY = 5
CLASS_ECOLOGICAL_RESERVE = 6
CLASS_NATIONAL_PARK = 7

CLASS_KEYS = {
    CLASS_CROWN: "crown_land",
    CLASS_WOODLOT: "woodlot",
    CLASS_PRIVATE: "private",
    CLASS_RECREATION_AREA: "recreation_area",
    CLASS_PROVINCIAL_PARK: "provincial_park",
    CLASS_CONSERVANCY: "conservancy",
    CLASS_ECOLOGICAL_RESERVE: "ecological_reserve",
    CLASS_NATIONAL_PARK: "national_park",
}

CLASS_LABELS = {
    "crown_land": "Crown land",
    "woodlot": "Woodlot licence",
    "private": "Private property",
    "recreation_area": "Recreation area",
    "provincial_park": "Provincial park",
    "conservancy": "Conservancy",
    "ecological_reserve": "Ecological reserve",
    "national_park": "National park",
}


def _designation_class(designation: str | None) -> int:
    """Map a BC protected-lands designation onto a tenure class."""
    d = (designation or "").upper()
    if "ECOLOGICAL RESERVE" in d:
        return CLASS_ECOLOGICAL_RESERVE
    if "CONSERVANCY" in d:
        return CLASS_CONSERVANCY
    if "RECREATION AREA" in d:
        return CLASS_RECREATION_AREA
    return CLASS_PROVINCIAL_PARK


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    bbox = aoi_bbox_albers(cfg.aoi)
    log("[landstatus] fetching tenure & protected-area layers")

    tenure = np.full(grid.shape, CLASS_CROWN, dtype="uint8")

    # --- woodlots (lowest precedence) ------------------------------------
    woodlots = fetch_layer("woodlots", bbox, cfg.cache_dir, log=log)
    if len(woodlots):
        tenure[grid.mask_from(woodlots)] = CLASS_WOODLOT

    # --- private parcels --------------------------------------------------
    parcels = fetch_layer("parcels", bbox, cfg.cache_dir, log=log, cql_extra="OWNER_TYPE='Private'")
    if len(parcels):
        tenure[grid.mask_from(parcels)] = CLASS_PRIVATE

    # --- provincial protected areas --------------------------------------
    parks = fetch_layer("parks_provincial", bbox, cfg.cache_dir, log=log)
    if len(parks):
        col = "PROTECTED_LANDS_DESIGNATION"
        for designation, sub in parks.groupby(parks[col] if col in parks.columns else None):
            tenure[grid.mask_from(sub)] = _designation_class(designation)

    conservancies = fetch_layer("conservancies", bbox, cfg.cache_dir, log=log)
    if len(conservancies):
        tenure[grid.mask_from(conservancies)] = CLASS_CONSERVANCY

    # --- national parks (highest precedence) ------------------------------
    national = fetch_layer("parks_national", bbox, cfg.cache_dir, log=log)
    if len(national):
        tenure[grid.mask_from(national)] = CLASS_NATIONAL_PARK

    inside = grid.mask_from(cfg.aoi, all_touched=True)
    tenure[~inside] = CLASS_CROWN

    legality = cfg.weights["legality"]
    excluded = set(legality.get("exclude_from_ranking", []))
    flagged = set(legality.get("flag_only", []))

    exclude_mask = np.zeros(grid.shape, dtype=bool)
    flag_mask = np.zeros(grid.shape, dtype=bool)
    for code, key in CLASS_KEYS.items():
        if key in excluded:
            exclude_mask |= tenure == code
        elif key in flagged:
            flag_mask |= tenure == code

    grid.write(cfg.interim("land_tenure.tif"), tenure, dtype="uint8")
    grid.write(cfg.interim("land_excluded.tif"), exclude_mask.astype("uint8"), dtype="uint8")

    total = max(int(inside.sum()), 1)
    mix = {CLASS_KEYS[c]: int(((tenure == c) & inside).sum()) for c in CLASS_KEYS}
    log("[landstatus] tenure mix: " +
        ", ".join(f"{k} {v / total:.1%}" for k, v in mix.items() if v))
    log(f"[landstatus] {exclude_mask.sum():,} cells excluded from ranking, "
        f"{flag_mask.sum():,} flagged")
    return {"mix": mix, "excluded": int(exclude_mask.sum())}
