"""Slope and aspect derivation.

Aspect convention is easy to get subtly wrong (and wrong by 90 or 180 degrees
is still "plausible looking"), so it is pinned against synthetic planes whose
answers are known exactly.
"""

import numpy as np
import pytest

from foraging.stages.terrain import slope_aspect

RES = 30.0


def plane(bearing_deg: float, slope_deg: float, n: int = 9) -> np.ndarray:
    """A plane descending toward ``bearing_deg`` at ``slope_deg``."""
    rows, cols = np.mgrid[0:n, 0:n]
    east = cols * RES
    north = (n - 1 - rows) * RES  # row 0 is the north edge
    b = np.radians(bearing_deg)
    return 1000.0 - np.tan(np.radians(slope_deg)) * (east * np.sin(b) + north * np.cos(b))


@pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 202.5, 225, 270, 315])
def test_aspect_matches_downslope_bearing(bearing):
    slope, aspect = slope_aspect(plane(bearing, 20.0), RES)
    assert aspect[4, 4] == pytest.approx(bearing, abs=0.01)


@pytest.mark.parametrize("angle", [5, 15, 25, 35])
def test_slope_matches_plane_angle(angle):
    slope, _ = slope_aspect(plane(180.0, angle), RES)
    assert slope[4, 4] == pytest.approx(angle, abs=0.01)


def test_flat_ground_has_zero_slope():
    slope, _ = slope_aspect(np.full((9, 9), 1500.0), RES)
    assert slope[4, 4] == pytest.approx(0.0, abs=1e-9)


def test_nodata_propagates_but_does_not_smear():
    dem = plane(180.0, 20.0)
    dem[4, 4] = np.nan
    slope, aspect = slope_aspect(dem, RES)
    assert np.isnan(slope[4, 4]) and np.isnan(aspect[4, 4])
    # A hole must not poison its neighbours - they still resolve to the plane.
    assert aspect[2, 2] == pytest.approx(180.0, abs=0.01)


def test_smoothing_preserves_planar_aspect():
    slope, aspect = slope_aspect(plane(202.5, 15.0), RES, smooth_sigma=1.0)
    assert aspect[4, 4] == pytest.approx(202.5, abs=0.5)
    assert slope[4, 4] == pytest.approx(15.0, abs=0.5)
