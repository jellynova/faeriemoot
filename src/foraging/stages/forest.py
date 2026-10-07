"""Stage 2b - forest composition, from the BC Vegetation Resources Inventory.

The habitat stage for mycorrhizal targets. A chanterelle does not live in
"open meadow" or "closed canopy" - it lives on the roots of particular trees,
so the question is *which trees are here, and how old is the stand*. Spectral
indices cannot answer that: mid-summer Douglas-fir, larch, cedar and spruce
saturate NDVI identically. BC's VRI can, because it is a photo-interpreted,
ground-calibrated inventory of every forest stand in the province, carrying up
to six tree species with their percentages, projected age and crown closure.

Per stand three things are scored and multiplied:

* **host share** - species percentages weighted by how good a host each one is
  (``forest.hosts`` in the species profile). A mycorrhizal obligate has no
  habitat at all without its host, so this one has no floor.
* **stand age** - fruiting needs an established root system; clearcuts and
  young plantations produce little. Age is cross-checked against BC's
  consolidated cutblock layer and the younger of the two wins, because VRI
  depletions can lag recent harvest by a year or more.
* **crown closure** - a soft preference with a floor, not a gate.

A species profile opts in with ``"habitat_model": "forest"``; profiles for
meadow plants keep using the Sentinel-2 vegetation stage instead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..curves import trapezoid
from ..grid import Grid

SPECIES_COLS = [f"SPECIES_CD_{i}" for i in range(1, 7)]
PCT_COLS = [f"SPECIES_PCT_{i}" for i in range(1, 7)]
VRI_PROPERTIES = [
    "FEATURE_ID", *[c for pair in zip(SPECIES_COLS, PCT_COLS, strict=True) for c in pair],
    "PROJ_AGE_1", "CROWN_CLOSURE", "BCLCS_LEVEL_1", "BCLCS_LEVEL_2", "BEC_ZONE_CODE",
]

CLASS_NODATA, CLASS_NONFOREST, CLASS_YOUNG, CLASS_NONHOST, CLASS_MIXED, CLASS_HOST_LEADING = range(6)
CLASS_NAMES = {
    CLASS_NONFOREST: "non-forest",
    CLASS_YOUNG: "young / regenerating stand",
    CLASS_NONHOST: "forest without host trees",
    CLASS_MIXED: "mixed stand with host trees",
    CLASS_HOST_LEADING: "host-leading stand",
}

# VRI species codes seen in the Kootenays, for readable popups.
TREE_NAMES = {
    "FD": "Douglas-fir", "FDI": "Douglas-fir", "FDC": "Douglas-fir",
    "LW": "western larch", "PL": "lodgepole pine", "PLI": "lodgepole pine",
    "PY": "ponderosa pine", "PW": "western white pine", "CW": "western redcedar",
    "HW": "western hemlock", "HM": "mountain hemlock", "SE": "Engelmann spruce",
    "SX": "hybrid spruce", "S": "spruce", "BL": "subalpine fir", "BG": "grand fir",
    "EP": "paper birch", "AT": "trembling aspen", "ACT": "black cottonwood",
    "AC": "cottonwood", "YC": "yellow-cedar",
}


# ---------------------------------------------------------------- scoring
def host_share(stands: pd.DataFrame, hosts: dict[str, float]) -> np.ndarray:
    """Host-weighted species percentage per stand, 0..100.

    A stand of 60% Douglas-fir (credit 1.0) and 20% hemlock (credit 0.5) scores
    70. Stands with no species recorded score 0, not NaN - VRI leaves the
    species fields empty on non-forest polygons.
    """
    total = np.zeros(len(stands), dtype="float64")
    for sc, pc in zip(SPECIES_COLS, PCT_COLS, strict=True):
        if sc not in stands.columns:
            continue
        credit = stands[sc].map(hosts).fillna(0.0).to_numpy(dtype="float64")
        pct = pd.to_numeric(stands[pc], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
        total += credit * pct
    return np.clip(total, 0.0, 100.0)


def leading_is_host(stands: pd.DataFrame, hosts: dict[str, float]) -> np.ndarray:
    """True where the stand's leading species is a full-credit host."""
    lead = stands["SPECIES_CD_1"].map(hosts).fillna(0.0).to_numpy(dtype="float64")
    return lead >= 1.0


def is_treed(stands: pd.DataFrame) -> np.ndarray:
    """VRI's own land-cover call: BCLCS level 2 ``T`` is treed."""
    return (stands["BCLCS_LEVEL_2"].astype("string") == "T").fillna(False).to_numpy(dtype=bool)


def soft_factor(x: np.ndarray, spec: dict) -> np.ndarray:
    """One trapezoid factor, lifted onto ``floor`` and with a credit for gaps."""
    fit = trapezoid(x, spec.get("hard_min"), spec["optimal_min"],
                    spec.get("optimal_max", np.inf), spec.get("hard_max"))
    floor = float(spec.get("floor", 0.0))
    out = floor + (1.0 - floor) * fit
    return np.where(np.isfinite(out), out, float(spec.get("missing_credit", 0.5)))


def score_stands(host_pct, age, closure, treed, fcfg: dict, enforce_treed: bool = True) -> np.ndarray:
    """Forest suitability, 0..1, from per-stand (or per-cell) attributes.

    ``host_fit x age_fit x closure_fit``. Non-treed ground is NaN when the
    hard filter is on - not habitat at all, like bare rock in the vegetation
    stage - and 0 otherwise.
    """
    # hard_min defaults to 0 rather than None: an open-ended trapezoid would
    # give a stand with no host trees full credit.
    host_fit = trapezoid(np.asarray(host_pct, dtype="float64"),
                         fcfg["host_share_pct"].get("hard_min") or 0.0,
                         fcfg["host_share_pct"]["optimal_min"], 100.0, None)
    host_fit = np.nan_to_num(host_fit, nan=0.0)
    age_fit = soft_factor(np.asarray(age, dtype="float64"), fcfg["stand_age_yr"])
    closure_fit = soft_factor(np.asarray(closure, dtype="float64"), fcfg["crown_closure_pct"])
    score = host_fit * age_fit * closure_fit
    treed = np.asarray(treed, dtype=bool)
    return np.where(treed, score, np.nan if enforce_treed else 0.0).astype("float32")


def classify_stands(host_pct, age, treed, lead_host, fcfg: dict) -> np.ndarray:
    """Categorical forest class for the map and site popups."""
    host_pct = np.asarray(host_pct, dtype="float64")
    age = np.asarray(age, dtype="float64")
    young_below = float(fcfg["stand_age_yr"]["optimal_min"])
    host_min = float(fcfg["host_share_pct"].get("hard_min") or 0.0)

    out = np.full(host_pct.shape, CLASS_NONHOST, dtype="uint8")
    out[host_pct > host_min] = CLASS_MIXED
    out[np.asarray(lead_host, dtype=bool)] = CLASS_HOST_LEADING
    out[np.isfinite(age) & (age < young_below)] = CLASS_YOUNG
    out[~np.asarray(treed, dtype=bool)] = CLASS_NONFOREST
    return out


def stand_label(row) -> str:
    """``Douglas-fir 60%, western larch 20%`` - the stand as a forager reads it."""
    parts = []
    for sc, pc in zip(SPECIES_COLS, PCT_COLS, strict=True):
        code, pct = row.get(sc), row.get(pc)
        if not isinstance(code, str) or not code or pd.isna(pct):
            continue
        parts.append(f"{TREE_NAMES.get(code, code)} {pct:.0f}%")
    return ", ".join(parts[:3])


def stand_attributes(stands: pd.DataFrame, fcfg: dict) -> pd.DataFrame:
    """Per-stand host share, age, closure, treed flag and leading-host flag."""
    hosts = fcfg["hosts"]
    return pd.DataFrame({
        "host_pct": host_share(stands, hosts),
        "age": pd.to_numeric(stands["PROJ_AGE_1"], errors="coerce").to_numpy(dtype="float64"),
        "closure": pd.to_numeric(stands["CROWN_CLOSURE"], errors="coerce").to_numpy(dtype="float64"),
        "treed": is_treed(stands),
        "lead_host": leading_is_host(stands, hosts),
    }, index=stands.index)


# ------------------------------------------------------------------- stage
def run(cfg: Config, grid: Grid | None = None, log=print) -> dict:
    if cfg.habitat_model != "forest":
        log(f"[forest] not used by {cfg.species_id} (habitat_model "
            f"'{cfg.habitat_model}') - skipping")
        return {"skipped": True}
    if grid is None:
        _, grid = Grid.read(cfg.interim("elevation.tif"))

    from rasterio.features import rasterize

    from ..sources.bcdata import aoi_bbox_albers, fetch_layer
    from .vegetation import logging_age

    fcfg = cfg.species["forest"]
    log("[forest] BC Vegetation Resources Inventory")
    stands = fetch_layer("vri", aoi_bbox_albers(cfg.aoi), cfg.cache_dir, log=log,
                         properties=VRI_PROPERTIES)
    if not len(stands):
        raise RuntimeError("VRI returned no stands for this AOI - is it inside BC?")
    stands = stands.to_crs(grid.crs).reset_index(drop=True)
    attrs = stand_attributes(stands, fcfg)

    # Burn the stand index once and look every attribute up through it, rather
    # than rasterizing each attribute separately.
    shapes = ((g, i + 1) for i, g in enumerate(stands.geometry) if g is not None and not g.is_empty)
    idx = rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                    fill=0, dtype="int32", all_touched=False)
    inside = grid.mask_from(cfg.aoi, all_touched=True)
    idx[~inside] = 0
    has = idx > 0
    k = idx - 1

    def per_cell(values, fill):
        out = np.full(grid.shape, fill, dtype=np.asarray(values).dtype)
        out[has] = np.asarray(values)[k[has]]
        return out

    host_pct = per_cell(attrs["host_pct"].to_numpy("float32"), np.nan)
    age = per_cell(attrs["age"].to_numpy("float32"), np.nan)
    closure = per_cell(attrs["closure"].to_numpy("float32"), np.nan)
    treed = per_cell(attrs["treed"].to_numpy(bool), False)
    lead_host = per_cell(attrs["lead_host"].to_numpy(bool), False)

    # VRI is projected forward annually but harvest depletions lag; the
    # cutblock layer is the fresher record of where a stand was just removed.
    logged = logging_age(cfg, grid, log=log)
    stale = np.isfinite(logged) & (~np.isfinite(age) | (logged < age))
    age = np.where(stale, logged, age).astype("float32")
    if stale.any():
        log(f"    cutblock layer overrides VRI stand age on {int(stale.sum()):,} cells")

    enforce = cfg.weights["hard_filters"].get("enforce_treed", True)
    score = score_stands(host_pct, age, closure, treed, fcfg, enforce_treed=enforce)
    score[~has] = np.nan

    fclass = classify_stands(host_pct, age, treed, lead_host, fcfg)
    fclass[~has] = CLASS_NODATA

    grid.write(cfg.interim("vri_stand.tif"), idx, dtype="int32")
    pd.DataFrame({"label": [stand_label(r) for _, r in stands.iterrows()]},
                 index=pd.RangeIndex(1, len(stands) + 1, name="stand")).to_csv(
        cfg.interim("vri_stands.csv"))
    grid.write(cfg.interim("host_pct.tif"), np.where(has, host_pct, np.nan))
    grid.write(cfg.interim("stand_age.tif"), age)
    grid.write(cfg.interim("crown_closure_vri.tif"), closure)
    grid.write(cfg.interim("logging_age.tif"), logged)
    grid.write(cfg.interim("forest_class.tif"), fclass, dtype="uint8")
    grid.write(cfg.interim("score_forest.tif"), score)

    covered = has[inside].mean() if inside.any() else 0.0
    log(f"[forest] {len(stands):,} stands, VRI covers {covered:.1%} of the AOI")
    counts = {CLASS_NAMES[c]: int((fclass == c).sum()) for c in CLASS_NAMES}
    total = max(sum(counts.values()), 1)
    log("[forest] class mix: " + ", ".join(f"{n} {v / total:.1%}" for n, v in counts.items()))
    return {"stands": len(stands), "classes": counts}
