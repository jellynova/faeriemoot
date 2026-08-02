"""Suitability membership curves."""

import numpy as np
import pytest

from foraging.curves import aspect_score, ramp_down, trapezoid, weighted_mean


def test_trapezoid_plateau_and_edges():
    x = np.array([1200, 1300, 1400, 1500, 1800, 2100, 2300, 2500, 2600], dtype=float)
    out = trapezoid(x, hard_min=1300, optimal_min=1500, optimal_max=2100, hard_max=2500)
    assert out[0] == 0.0            # below hard_min
    assert out[1] == 0.0            # exactly at hard_min
    assert out[2] == pytest.approx(0.5)   # halfway up the rising ramp
    assert out[3] == 1.0 and out[4] == 1.0 and out[5] == 1.0
    assert out[6] == pytest.approx(0.5)   # halfway down the falling ramp
    assert out[7] == 0.0 and out[8] == 0.0


def test_trapezoid_open_ended_sides_stay_saturated():
    x = np.array([0.0, 5.0, 50.0])
    # No hard_min: everything at or below optimal_min scores 1.
    out = trapezoid(x, None, 4.0, 25.0, None)
    assert out[0] == 1.0 and out[2] == 1.0


def test_trapezoid_preserves_nan():
    out = trapezoid(np.array([np.nan, 1600.0]), 1300, 1500, 2100, 2500)
    assert np.isnan(out[0]) and out[1] == 1.0


def test_ramp_down_is_monotonic_and_clipped():
    x = np.array([0.0, 10.0, 22.5, 45.0, 90.0])
    out = ramp_down(x, best=10.0, worst=45.0)
    assert out[0] == 1.0 and out[1] == 1.0
    assert out[2] == pytest.approx(0.642857, abs=1e-5)
    assert out[3] == 0.0 and out[4] == 0.0
    assert np.all(np.diff(out) <= 0)


class TestAspectScore:
    PREF, TOL, FLAT = 202.5, 45.0, 3.0

    def score(self, bearings, slope=15.0):
        b = np.asarray(bearings, dtype=float)
        return aspect_score(b, np.full(b.shape, slope), self.PREF, self.TOL, self.FLAT)

    def test_full_credit_inside_tolerance(self):
        # 202.5 +/- 45 spans SSE through WSW.
        assert np.allclose(self.score([157.5, 180.0, 202.5, 225.0, 247.5]), 1.0)

    def test_north_is_heavily_penalised(self):
        assert self.score([0.0])[0] < 0.1

    def test_falloff_is_monotonic_away_from_preference(self):
        out = self.score([202.5, 247.5, 270.0, 300.0, 330.0, 22.5])
        assert np.all(np.diff(out) <= 1e-9)

    @pytest.mark.parametrize("delta", [30.0, 90.0, 150.0, 170.0])
    def test_symmetric_about_preference_across_the_wrap(self, delta):
        # Bearings equidistant either side of the preferred bearing must score
        # the same, including when one of them wraps past 360.
        left = (self.PREF - delta) % 360.0
        right = (self.PREF + delta) % 360.0
        assert self.score([left])[0] == pytest.approx(self.score([right])[0], abs=1e-9)

    def test_opposite_bearing_scores_zero(self):
        opposite = (self.PREF + 180.0) % 360.0
        assert self.score([opposite])[0] == pytest.approx(0.0, abs=1e-9)

    def test_flat_ground_is_neutral_not_penalised(self):
        # Aspect is meaningless below the flat threshold; north-facing flat
        # ground must not be punished for a near-arbitrary gradient direction.
        assert self.score([0.0], slope=1.0)[0] == 0.5


class TestWeightedMean:
    def test_matches_hand_computation(self):
        comps = {"a": np.array([1.0]), "b": np.array([0.0])}
        out = weighted_mean(comps, {"a": 0.75, "b": 0.25})
        assert out[0] == pytest.approx(0.75)

    def test_renormalises_over_present_components(self):
        # b missing entirely: the result is a's value, not a's value halved.
        comps = {"a": np.array([0.8]), "b": np.array([np.nan])}
        out = weighted_mean(comps, {"a": 0.5, "b": 0.5})
        assert out[0] == pytest.approx(0.8)

    def test_zero_weight_component_is_ignored(self):
        comps = {"a": np.array([1.0]), "b": np.array([0.0])}
        out = weighted_mean(comps, {"a": 1.0, "b": 0.0})
        assert out[0] == pytest.approx(1.0)

    def test_all_nan_cell_is_nan(self):
        comps = {"a": np.array([np.nan]), "b": np.array([np.nan])}
        assert np.isnan(weighted_mean(comps, {"a": 1.0, "b": 1.0})[0])

    def test_raises_when_nothing_has_weight(self):
        with pytest.raises(ValueError):
            weighted_mean({"a": np.array([1.0])}, {"a": 0.0})
