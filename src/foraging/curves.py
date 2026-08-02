"""Membership curves used to turn raw measurements into 0..1 suitability.

Every layer scores the same way: a raw value (metres, degrees, NDVI, minutes)
is mapped through a curve into 0..1, where 1 means "ideal for this target".
Keeping these in one place means the scoring behaviour is consistent and
tunable from config rather than reimplemented per stage.
"""

from __future__ import annotations

import numpy as np


def trapezoid(
    x: np.ndarray,
    hard_min: float | None,
    optimal_min: float,
    optimal_max: float,
    hard_max: float | None,
) -> np.ndarray:
    """Ramp up to 1 across the optimal band, ramp back down to 0 at the hard edges.

    ``None`` on either hard edge means "no falloff on that side" - the score
    stays at 1 beyond the optimal band rather than declining.
    """
    x = np.asarray(x, dtype="float64")
    out = np.zeros_like(x)

    with np.errstate(invalid="ignore", divide="ignore"):
        if hard_min is None:
            rising = x <= optimal_min
            out[rising] = 1.0
        else:
            span = max(optimal_min - hard_min, 1e-9)
            rising = (x > hard_min) & (x < optimal_min)
            out[rising] = (x[rising] - hard_min) / span

        plateau = (x >= optimal_min) & (x <= optimal_max)
        out[plateau] = 1.0

        if hard_max is None:
            out[x > optimal_max] = 1.0
        else:
            span = max(hard_max - optimal_max, 1e-9)
            falling = (x > optimal_max) & (x < hard_max)
            out[falling] = (hard_max - x[falling]) / span

    out = np.clip(out, 0.0, 1.0)
    out[~np.isfinite(x)] = np.nan
    return out


def ramp_down(x: np.ndarray, best: float, worst: float) -> np.ndarray:
    """1 at or below ``best``, falling linearly to 0 at ``worst``.

    Used for cost-like quantities (drive minutes, hike kilometres) where less
    is always better.
    """
    x = np.asarray(x, dtype="float64")
    span = max(worst - best, 1e-9)
    out = np.clip((worst - x) / span, 0.0, 1.0)
    out[~np.isfinite(x)] = np.nan
    return out


def aspect_score(
    aspect_deg: np.ndarray,
    slope_deg: np.ndarray,
    optimal_bearing_deg: float,
    tolerance_deg: float,
    flat_slope_deg: float,
) -> np.ndarray:
    """Score compass aspect against a preferred bearing.

    Full credit inside ``tolerance_deg`` of the preferred bearing, then a
    cosine falloff to 0 at 180 degrees away. Cells flatter than
    ``flat_slope_deg`` get neutral credit, since aspect is meaningless on flat
    ground and would otherwise inject noise from a near-arbitrary gradient
    direction.
    """
    aspect_deg = np.asarray(aspect_deg, dtype="float64")
    slope_deg = np.asarray(slope_deg, dtype="float64")

    # Smallest absolute angular difference, 0..180.
    delta = np.abs(((aspect_deg - optimal_bearing_deg + 180.0) % 360.0) - 180.0)

    out = np.ones_like(delta)
    beyond = delta > tolerance_deg
    span = max(180.0 - tolerance_deg, 1e-9)
    # Cosine falloff so the penalty is gentle just outside the window and
    # steep for genuinely wrong-facing (north) slopes.
    out[beyond] = 0.5 * (1.0 + np.cos(np.pi * (delta[beyond] - tolerance_deg) / span))

    out = np.clip(out, 0.0, 1.0)
    flat = slope_deg < flat_slope_deg
    out[flat] = 0.5
    out[~np.isfinite(aspect_deg)] = np.nan
    return out


def weighted_mean(components: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    """Weighted mean over whichever components are present and finite.

    Weights are renormalised per-cell over the non-NaN components, so a
    missing layer degrades the score's confidence rather than zeroing it.
    """
    total = None
    wsum = None
    for name, arr in components.items():
        w = float(weights.get(name, 0.0))
        if w <= 0:
            continue
        a = np.asarray(arr, dtype="float64")
        valid = np.isfinite(a)
        contrib = np.where(valid, a * w, 0.0)
        wcontrib = np.where(valid, w, 0.0)
        total = contrib if total is None else total + contrib
        wsum = wcontrib if wsum is None else wsum + wcontrib

    if total is None:
        raise ValueError("no components with positive weight")

    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(wsum > 0, total / np.maximum(wsum, 1e-9), np.nan)
    return out
