"""Stage 2 (alternative) - forest host trees.

For a mycorrhizal fungus the habitat signal is the *host tree*, not anything
the target itself does to reflectance. A chanterelle has no spectral signature
at 10 m, and NDVI/NDMI cannot tell a Douglas-fir stand from an equally green,
equally closed cedar or spruce one - which is the distinction that matters,
since the fungus only fruits where its ectomycorrhizal partner's roots are.

So this stage replaces the vegetation stage for species profiles with
``"habitat_model": "host_trees"``, and reads tree species composition from BC's
**Vegetation Resources Inventory** (VRI, rank-1 layer). Each VRI polygon lists
up to six species with their percentage of the stand. The habitat score is:

    host credit  x  stand-age credit  x  crown-closure credit

* **Host credit.** Percentage-weighted sum of per-species affinities from the
  profile, ``sum(pct_i / 100 * affinity(species_i))``, saturating at
  ``host_fraction.saturation``. Species codes match by longest prefix, so
  ``FDI`` (interior Douglas-fir) picks up the ``FD`` entry and ``PLI`` the
  ``PL`` one. Non-treed polygons have no hosts.
* **Stand-age credit.** Piecewise-linear over ``stand_age_credit`` knots. The
  effective age is the younger of the inventory's projected age and years
  since harvest from the consolidated cutblock layer, so a block logged after
  the inventory was last projected still reads as young.
* **Crown-closure credit.** A trapezoid on VRI crown closure (percent).

A cell whose host fraction is below ``host_fraction.hard_min`` is rejected
outright (NaN): with no host there is no fungus, however good the rest is.

Cells with no inventory polygon at all - mostly private land, which VRI covers
patchily - cannot be scored and are left NaN. That fraction is logged, because
it is a data gap, not a habitat judgement.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd

from ..config import Config
from ..curves import trapezoid
from ..grid import Grid

N_SLOTS = 6
VRI_FIELDS = (
    ["FEATURE_ID", "BCLCS_LEVEL_2", "PROJ_AGE_1", "CROWN_CLOSURE"]
    + [f"SPECIES_CD_{i}" for i in range(1, N_SLOTS + 1)]
    + [f"SPECIES_PCT_{i}" for i in range(1, N_SLOTS + 1)]
)

# Categorical output for the UI's forest layer.
CLASS_NODATA, CLASS_NON_FOREST, CLASS_NON_HOST, CLASS_LOW_HOST, CLASS_HOST_RICH = 0, 1, 2, 3, 4
CLASS_YOUNG = 5


def class_names(fcfg: dict | None = None) -> dict[int, str]:
    """Forest class labels, worded with the profile's ``host_label``.

    "Host" is the right word for a mycorrhizal fungus. For a profile that uses
    stand composition as an *indicator* - cedar for devil's club, or the target
    tree itself - the profile names what it is scoring instead, e.g.
    ``"host_label": "cedar"`` gives "cedar-rich forest".
    """
    term = (fcfg or {}).get("host_label") or "host"
    plural = (fcfg or {}).get("host_label_plural") or ("hosts" if term == "host" else term)
    return {
        CLASS_NON_FOREST: "non-forest",
        CLASS_NON_HOST: f"forest, no {plural}",
        CLASS_LOW_HOST: f"forest, some {plural}",
        CLASS_HOST_RICH: f"{term}-rich forest",
        CLASS_YOUNG: "young / recently harvested",
    }


CLASS_NAMES = class_names()

# VRI species codes -> common names, for the leading-species attribute. Matched
# by longest prefix like the affinity table, so variety codes (FDI, PLI) fold
# into their species.
SPECIES_NAMES = {
    "FD": "Douglas-fir", "HW": "western hemlock", "HM": "mountain hemlock",
    "CW": "western redcedar", "YC": "yellow-cedar", "TW": "western yew",
    "PL": "lodgepole pine", "PW": "western white pine", "PY": "ponderosa pine",
    "PA": "whitebark pine", "SE": "Engelmann spruce", "SX": "hybrid spruce",
    "SS": "Sitka spruce", "S": "spruce", "BL": "subalpine fir", "BG": "grand fir",
    "BA": "amabilis fir", "B": "true fir", "LW": "western larch", "LA": "alpine larch",
    "L": "larch", "AT": "trembling aspen", "AC": "cottonwood", "EP": "paper birch",
    "DR": "red alder", "MB": "bigleaf maple",
}
SPECIES_KEYS = sorted(SPECIES_NAMES)  # index + 1 is the raster code; 0 = none
OTHER_SPECIES = 255


def longest_prefix(code, table: dict):
    """The ``table`` key that is the longest prefix of ``code``, or None."""
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    for n in range(len(code), 0, -1):
        if code[:n] in table:
            return code[:n]
    return None


def host_fraction(vri: pd.DataFrame, affinity: dict[str, float], default: float = 0.0) -> np.ndarray:
    """Affinity-weighted host share of each stand, 0..1.

    Non-treed polygons (BCLCS level 2 other than ``T``) score 0 regardless of
    any species listed, since a species list on a non-treed polygon describes
    scattered stems, not a stand.
    """
    table = {k.upper(): float(v) for k, v in affinity.items()}
    total = np.zeros(len(vri), dtype="float64")
    for i in range(1, N_SLOTS + 1):
        codes = vri.get(f"SPECIES_CD_{i}")
        pcts = vri.get(f"SPECIES_PCT_{i}")
        if codes is None or pcts is None:
            continue
        aff = np.array([table.get(longest_prefix(c, table), default) if isinstance(c, str) else 0.0
                        for c in codes], dtype="float64")
        pct = np.nan_to_num(pd.to_numeric(pcts, errors="coerce").to_numpy(dtype="float64"), nan=0.0)
        total += pct / 100.0 * aff
    treed = _treed(vri)
    return np.where(treed, np.clip(total, 0.0, 1.0), 0.0)


def _treed(vri: pd.DataFrame) -> np.ndarray:
    """BCLCS level 2 'T'; where the code is missing, fall back to 'has species'."""
    level2 = vri.get("BCLCS_LEVEL_2")
    has_species = vri.get("SPECIES_CD_1", pd.Series([None] * len(vri))).notna().to_numpy()
    if level2 is None:
        return has_species
    code = level2.astype("string").str.upper()
    return np.where(code.isna().to_numpy(), has_species, (code == "T").fillna(False).to_numpy())


def age_credit(age: np.ndarray, knots: list[list[float]], unknown: float) -> np.ndarray:
    """Piecewise-linear credit over (age, credit) knots; ``unknown`` where age is NaN."""
    xs = [float(k[0]) for k in knots]
    ys = [float(k[1]) for k in knots]
    if any(b < a for a, b in pairwise(xs)):
        raise ValueError("stand_age_credit knots must be in increasing age order")
    a = np.asarray(age, dtype="float64")
    out = np.interp(np.nan_to_num(a, nan=xs[0]), xs, ys)
    return np.where(np.isfinite(a), out, unknown)


def leading_codes(vri: pd.DataFrame) -> np.ndarray:
    """Raster code of each stand's leading species (0 = none, 255 = unlisted)."""
    out = np.zeros(len(vri), dtype="uint8")
    for j, code in enumerate(vri.get("SPECIES_CD_1", pd.Series([None] * len(vri)))):
        key = longest_prefix(code, SPECIES_NAMES)
        if key is not None:
            out[j] = SPECIES_KEYS.index(key) + 1
        elif isinstance(code, str) and code.strip():
            out[j] = OTHER_SPECIES
    return out


def leading_label(code: int | None, pct: float | None) -> str | None:
    if code is None or code == 0:
        return None
    name = "other" if code == OTHER_SPECIES else SPECIES_NAMES[SPECIES_KEYS[int(code) - 1]]
    return f"{name} {pct:.0f}%" if pct is not None and np.isfinite(pct) else name


def _polygon_index(vri, grid: Grid) -> np.ndarray:
    """Row index of the VRI polygon covering each cell, -1 where none.

    Rasterising one index and gathering attributes through it is one pass over
    the geometry instead of one per attribute. Rank-1 VRI polygons tile the
    province without overlap, so burn order does not matter.
    """
    from rasterio.features import rasterize

    proj = vri.to_crs(grid.crs)
    shapes = [(geom, i) for i, geom in enumerate(proj.geometry)
              if geom is not None and not geom.is_empty]
    if not shapes:
        return np.full(grid.shape, -1, dtype="int32")
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                     fill=-1, dtype="int32", all_touched=False)


def _gather(values: np.ndarray, index: np.ndarray, fill=np.nan, dtype="float32") -> np.ndarray:
    out = np.full(index.shape, fill, dtype=dtype)
    hit = index >= 0
    out[hit] = values[index[hit]]
    return out


def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if cfg.habitat_layer != "forest":
        log(f"[forest] skipped - {cfg.species_id} uses habitat_model '{cfg.habitat_model}'")
        return {"skipped": True}
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    from ..sources.bcdata import aoi_bbox_albers, fetch_layer
    from .vegetation import logging_age

    fcfg = cfg.species["forest"]
    log("[forest] fetching VRI rank-1 forest inventory")
    vri = fetch_layer("vri", aoi_bbox_albers(cfg.aoi), cfg.cache_dir, log=log, properties=VRI_FIELDS)
    if not len(vri):
        raise RuntimeError("no VRI polygons returned for this AOI - the forest stage cannot score hosts")

    # ---- per-stand attributes --------------------------------------------
    hf = host_fraction(vri, fcfg["host_affinity"], float(fcfg.get("default_affinity", 0.0)))
    vri_age = pd.to_numeric(vri.get("PROJ_AGE_1"), errors="coerce").to_numpy(dtype="float64")
    closure = pd.to_numeric(vri.get("CROWN_CLOSURE"), errors="coerce").to_numpy(dtype="float64")
    treed = _treed(vri)
    lead = leading_codes(vri)
    lead_pct = pd.to_numeric(vri.get("SPECIES_PCT_1"), errors="coerce").to_numpy(dtype="float64")

    # ---- onto the grid ----------------------------------------------------
    index = _polygon_index(vri, grid)
    inside = grid.mask_from(cfg.aoi, all_touched=True)
    index[~inside] = -1
    inventoried = index >= 0
    log(f"[forest] {len(vri):,} stands; inventory covers {inventoried[inside].mean():.1%} of AOI")
    gap = (~inventoried[inside]).mean()
    if gap >= 0.001:
        log(f"    {gap:.1%} of the AOI has no VRI polygon (mostly "
            "private land) and cannot be scored for host trees")

    hf_grid = _gather(hf, index)
    treed_grid = _gather(treed.astype("float32"), index) > 0.5
    closure_grid = _gather(closure, index)
    lead_grid = _gather(lead, index, fill=0, dtype="uint8")
    lead_pct_grid = _gather(lead_pct, index)

    # Inventory age is projected to the inventory's reference date; a cutblock
    # logged since then is younger than the inventory says.
    harvest_age = logging_age(cfg, grid, log=log)
    stand_age = _gather(vri_age, index)
    stand_age = np.where(treed_grid, stand_age, np.nan)
    stand_age = np.fmin(stand_age, np.where(treed_grid, harvest_age, np.nan)).astype("float32")

    # ---- score ------------------------------------------------------------
    hcfg = fcfg["host_fraction"]
    unknown = float(fcfg.get("missing_attribute_credit", 0.5))
    host_credit = np.clip(hf_grid / max(float(hcfg["saturation"]), 1e-9), 0.0, 1.0)
    a_credit = age_credit(stand_age, fcfg["stand_age_credit"]["knots"], unknown)
    ccfg = fcfg["crown_closure_pct"]
    c_credit = trapezoid(closure_grid, ccfg.get("hard_min"), ccfg["optimal_min"],
                         ccfg["optimal_max"], ccfg.get("hard_max"))
    c_credit = np.where(np.isfinite(closure_grid), c_credit, unknown)

    score = (host_credit * a_credit * c_credit).astype("float32")
    score[~inventoried] = np.nan
    hard_min = float(hcfg.get("hard_min", 0.0))
    if cfg.weights["hard_filters"].get("enforce_host_min", True):
        score = np.where(hf_grid >= hard_min, score, np.nan).astype("float32")

    # ---- categorical layer for the UI -------------------------------------
    fclass = np.full(grid.shape, CLASS_NODATA, dtype="uint8")
    fclass[inventoried & ~treed_grid] = CLASS_NON_FOREST
    fclass[inventoried & treed_grid & (hf_grid < hard_min)] = CLASS_NON_HOST
    fclass[inventoried & treed_grid & (hf_grid >= hard_min)] = CLASS_LOW_HOST
    fclass[inventoried & treed_grid & (hf_grid >= float(hcfg["saturation"]))] = CLASS_HOST_RICH
    fclass[(fclass >= CLASS_LOW_HOST) & np.isfinite(stand_age) & (a_credit <= 0.0)] = CLASS_YOUNG

    # Species-independent inventory layers are shared across profiles.
    grid.write(cfg.interim("logging_age.tif"), harvest_age)
    grid.write(cfg.interim("stand_age.tif"), stand_age)
    grid.write(cfg.interim("crown_closure_pct.tif"), closure_grid)
    grid.write(cfg.interim("leading_species.tif"), lead_grid, dtype="uint8")
    grid.write(cfg.interim("leading_species_pct.tif"), lead_pct_grid)
    # These depend on the profile's host table and thresholds.
    grid.write(cfg.species_interim("host_fraction.tif"), hf_grid)
    grid.write(cfg.species_interim("score_forest.tif"), score)
    grid.write(cfg.species_interim("forest_class.tif"), fclass, dtype="uint8")

    names = class_names(fcfg)
    counts = {names[k]: int((fclass == k).sum()) for k in names}
    total = max(sum(counts.values()), 1)
    log("[forest] class mix: " + ", ".join(f"{k} {v / total:.1%}" for k, v in counts.items()))
    ok = np.isfinite(score)
    log(f"[forest] {int(ok.sum()):,} cells carry hosts above {hard_min:g} "
        f"({ok[inside].mean():.1%} of AOI), median score {np.nanmedian(score):.2f}")
    return {"classes": counts}
