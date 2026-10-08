"""Moisture component: water mask, distance, topographic position and blending."""

import numpy as np
import pytest

from foraging.config import _check_optional_weights
from foraging.stages.forest import CLASS_HOST_RICH, CLASS_NON_HOST, class_names
from foraging.stages.moisture import (
    POLY_CODES,
    distance_to_water,
    moisture_score,
    topographic_position,
    water_mask,
)

CFG = {
    "water_distance_m": {"optimal_max": 30, "zero_at": 230},
    "tpi": {"radius_m": 90, "wet_at_m": -5, "dry_at_m": 5},
    "blend": {"water": 1.0, "tpi": 0.0},
    "min_credit": 0.0,
}


class TestWaterMask:
    def test_stream_order_cut(self):
        order = np.array([[0, 1, 2, 3]])
        polys = np.zeros_like(order)
        assert water_mask(order, polys, 2, ["streams"]).tolist() == [[False, False, True, True]]

    def test_polygon_features_are_selectable(self):
        order = np.zeros((1, 3), dtype=int)
        polys = np.array([[POLY_CODES["lakes"], POLY_CODES["wetlands"], 0]])
        assert water_mask(order, polys, 1, ["wetlands"]).tolist() == [[False, True, False]]
        assert water_mask(order, polys, 1, ["lakes", "wetlands"]).tolist() == [[True, True, False]]

    def test_unknown_feature_is_an_error(self):
        with pytest.raises(ValueError, match="ponds"):
            water_mask(np.zeros((1, 1)), np.zeros((1, 1)), 1, ["ponds"])


class TestDistance:
    def test_metres_not_cells(self):
        mask = np.zeros((1, 5), dtype=bool)
        mask[0, 0] = True
        assert distance_to_water(mask, 30.0).tolist() == [[0, 30, 60, 90, 120]]

    def test_no_water_reads_as_infinitely_far(self):
        d = distance_to_water(np.zeros((2, 2), dtype=bool), 30.0)
        assert np.isinf(d).all()


class TestTopographicPosition:
    def test_gully_is_negative_ridge_positive(self):
        # A V-shaped valley along the middle column.
        x = np.abs(np.arange(-10, 11, dtype="float64"))
        dem = np.tile(x * 10.0, (21, 1))
        tpi = topographic_position(dem, 30.0, 90.0)
        assert tpi[10, 10] < 0          # valley floor
        assert tpi[10, 3] == pytest.approx(0.0, abs=1e-9)  # mid-slope of a plane
        # Edge cells see only part of the window; NaN-aware mean keeps them finite.
        assert np.isfinite(tpi).all()

    def test_nan_stays_nan_and_does_not_spread(self):
        dem = np.full((9, 9), 100.0)
        dem[4, 4] = np.nan
        tpi = topographic_position(dem, 30.0, 60.0)
        assert np.isnan(tpi[4, 4])
        assert np.nanmax(np.abs(tpi)) == pytest.approx(0.0)


class TestMoistureScore:
    def test_water_ramp(self):
        d = np.array([0.0, 30.0, 130.0, 230.0, 500.0])
        s = moisture_score(d, np.zeros_like(d), CFG)
        assert s.tolist() == pytest.approx([1.0, 1.0, 0.5, 0.0, 0.0])

    def test_no_water_anywhere_is_zero_not_nan(self):
        s = moisture_score(np.array([np.inf]), np.array([0.0]), CFG)
        assert s.tolist() == [0.0]

    def test_floor_and_blend(self):
        cfg = {**CFG, "blend": {"water": 1.0, "tpi": 1.0}, "min_credit": 0.2}
        # Far from water (0) but in a hollow (1): blended 0.5, lifted by the floor.
        s = moisture_score(np.array([1000.0]), np.array([-10.0]), cfg)
        assert s[0] == pytest.approx(0.2 + 0.8 * 0.5)

    def test_hard_max_rejects(self):
        cfg = {**CFG, "water_distance_m": {**CFG["water_distance_m"], "hard_max_m": 100}}
        s = moisture_score(np.array([50.0, 150.0]), np.zeros(2), cfg)
        assert np.isfinite(s[0]) and np.isnan(s[1])

    def test_zero_blend_is_an_error(self):
        with pytest.raises(ValueError):
            moisture_score(np.zeros(1), np.zeros(1), {**CFG, "blend": {"water": 0, "tpi": 0}})


class TestOptionalWeights:
    def test_block_without_weight(self):
        with pytest.raises(ValueError, match="no weight"):
            _check_optional_weights({"id": "x", "moisture": {"a": 1}}, {"weights": {"terrain": 1}})

    def test_weight_without_block(self):
        with pytest.raises(ValueError, match="no 'moisture' block"):
            _check_optional_weights({"id": "x"}, {"weights": {"moisture": 0.2}})

    def test_paired_is_fine(self):
        _check_optional_weights({"id": "x", "moisture": {"a": 1}}, {"weights": {"moisture": 0.2}})


def test_forest_class_names_follow_host_label():
    assert class_names()[CLASS_HOST_RICH] == "host-rich forest"
    assert class_names()[CLASS_NON_HOST] == "forest, no hosts"
    cedar = class_names({"host_label": "cedar"})
    assert cedar[CLASS_HOST_RICH] == "cedar-rich forest"
    assert cedar[CLASS_NON_HOST] == "forest, no cedar"
