"""Access-layer internals: hiking speed and least-cost path attribution."""

import numpy as np
import pytest
from skimage.graph import MCP_Geometric

from foraging.stages.access import _trace_to_source, tobler_speed_kmh


class TestToblerSpeed:
    def test_peaks_just_below_flat(self):
        # Tobler's curve peaks on a gentle downhill; on flat ground the walker
        # is slightly off peak but still near the base speed.
        flat = tobler_speed_kmh(np.array([0.0]))[0]
        assert flat == pytest.approx(6.0 * np.exp(-3.5 * 0.05), rel=1e-6)

    def test_falls_off_with_steepness(self):
        speeds = tobler_speed_kmh(np.array([0.0, 10.0, 20.0, 30.0, 40.0]))
        assert np.all(np.diff(speeds) < 0)

    def test_base_speed_rescales_the_curve(self):
        slow = tobler_speed_kmh(np.array([10.0]), base_speed_kmh=2.5)
        fast = tobler_speed_kmh(np.array([10.0]), base_speed_kmh=5.0)
        assert slow[0] == pytest.approx(fast[0] / 2.0)


class TestTraceToSource:
    """Pointer doubling must agree with walking the traceback one step at a time."""

    @staticmethod
    def _walk(traceback, offsets, start_rc, shape):
        """Reference implementation: follow predecessors until reaching a start."""
        h, w = shape
        r, c = start_rc
        dist = 0.0
        while traceback[r, c] >= 0:
            dr, dc = offsets[traceback[r, c]]
            dist += float(np.hypot(dr, dc))
            r, c = r - dr, c - dc
        return (r, c), dist

    def _setup(self, cost, starts):
        mcp = MCP_Geometric(cost.astype("float64"), sampling=(1.0, 1.0), fully_connected=True)
        _, tb = mcp.find_costs(starts)
        offsets = np.asarray(mcp.offsets, dtype="int64")
        pred, step = _trace_to_source(np.asarray(tb), offsets, res=1.0)
        return np.asarray(tb), offsets, pred, step

    def test_single_source_uniform_cost(self):
        cost = np.ones((12, 12))
        tb, offsets, pred, step = self._setup(cost, [(0, 0)])
        w = cost.shape[1]
        for rc in [(5, 5), (11, 11), (0, 7), (9, 2)]:
            root, dist = self._walk(tb, offsets, rc, cost.shape)
            flat = rc[0] * w + rc[1]
            assert pred[flat] == root[0] * w + root[1]
            assert step[flat] == pytest.approx(dist, abs=1e-9)

    def test_multiple_sources_attribute_to_the_nearer_one(self):
        cost = np.ones((10, 20))
        starts = [(5, 0), (5, 19)]
        tb, offsets, pred, step = self._setup(cost, starts)
        w = cost.shape[1]
        # A cell near the left source must trace back to it, not the right one.
        flat = 5 * w + 2
        assert pred[flat] == 5 * w + 0
        flat = 5 * w + 17
        assert pred[flat] == 5 * w + 19

    def test_barrier_forces_the_longer_way_round(self):
        cost = np.ones((11, 11))
        cost[5, 0:9] = 1e6  # wall with a gap at the right
        tb, offsets, pred, step = self._setup(cost, [(0, 0)])
        w = cost.shape[1]
        target = (10, 0)
        root, dist = self._walk(tb, offsets, target, cost.shape)
        flat = target[0] * w + target[1]
        assert step[flat] == pytest.approx(dist, abs=1e-9)
        # Going around costs more than the straight-line 10 cells.
        assert step[flat] > 10.0

    def test_start_cells_have_zero_distance_and_self_reference(self):
        cost = np.ones((8, 8))
        tb, offsets, pred, step = self._setup(cost, [(3, 3)])
        flat = 3 * 8 + 3
        assert pred[flat] == flat
        assert step[flat] == 0.0

    def test_resolution_scales_distance(self):
        cost = np.ones((8, 8))
        mcp = MCP_Geometric(cost, sampling=(30.0, 30.0), fully_connected=True)
        _, tb = mcp.find_costs([(0, 0)])
        _, step = _trace_to_source(np.asarray(tb), np.asarray(mcp.offsets, dtype="int64"), res=30.0)
        # Four cells due east at 30 m each.
        assert step[0 * 8 + 4] == pytest.approx(120.0, abs=1e-6)
