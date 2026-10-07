"""Validation statistics: AUC, its uncertainty, and spatial declustering."""

import itertools

import numpy as np
import pytest

from foraging.stats import (
    auc,
    bootstrap_auc,
    mann_whitney_p,
    midrank_percentile,
    paired_bootstrap_delta,
    per_cluster,
    spatial_clusters,
)


def brute_force_auc(pos, neg):
    wins = [1.0 if p > n else 0.5 if p == n else 0.0 for p, n in itertools.product(pos, neg)]
    return sum(wins) / len(wins)


class TestAuc:
    @pytest.mark.parametrize("seed", range(5))
    def test_matches_pairwise_definition_with_ties(self, seed):
        rng = np.random.default_rng(seed)
        # Rounded so ties are common, as on a plateaued score surface.
        pos = rng.normal(0.6, 0.2, 13).round(1)
        neg = rng.normal(0.5, 0.2, 29).round(1)
        assert auc(pos, neg) == pytest.approx(brute_force_auc(pos, neg))

    def test_perfect_and_reversed_separation(self):
        assert auc([5, 6, 7], [1, 2, 3]) == 1.0
        assert auc([1, 2, 3], [5, 6, 7]) == 0.0

    def test_all_tied_is_no_signal(self):
        assert auc([1.0, 1.0], [1.0, 1.0, 1.0]) == 0.5

    def test_empty_group_is_nan(self):
        assert np.isnan(auc([], [1.0]))


class TestMidrankPercentile:
    def test_mean_percentile_equals_auc(self):
        rng = np.random.default_rng(3)
        pos, ref = rng.random(20).round(1), rng.random(500).round(1)
        assert midrank_percentile(pos, ref).mean() == pytest.approx(auc(pos, ref))

    def test_plateau_counts_ties_half(self):
        # Half the reference sits on the same top value. Counting only the
        # strictly-lower values puts a value on that plateau at 0.50; it is
        # level with the whole top half, so mid-rank gives 0.75.
        ref = np.array([0.1] * 50 + [1.0] * 50)
        assert midrank_percentile([1.0], ref)[0] == pytest.approx(0.75)


class TestMannWhitney:
    def test_clear_separation_is_significant(self):
        assert mann_whitney_p(np.arange(10, 20), np.arange(0, 10)) < 0.001

    def test_one_sided_in_the_right_direction(self):
        assert mann_whitney_p(np.arange(0, 10), np.arange(10, 20)) > 0.99


class TestBootstrap:
    def test_interval_brackets_estimate_and_is_reproducible(self):
        rng_data = np.random.default_rng(0)
        pos, neg = rng_data.normal(1, 1, 25), rng_data.normal(0, 1, 40)
        lo, hi = bootstrap_auc(pos, neg, np.random.default_rng(1), n_boot=500)
        assert 0.0 <= lo <= auc(pos, neg) <= hi <= 1.0
        again = bootstrap_auc(pos, neg, np.random.default_rng(1), n_boot=500)
        assert (lo, hi) == again

    def test_small_samples_give_wide_intervals(self):
        # The point of reporting a CI at all: ten records cannot pin an AUC.
        rng = np.random.default_rng(5)
        pos, neg = rng.normal(0.5, 1, 10), rng.normal(0, 1, 10)
        lo, hi = bootstrap_auc(pos, neg, np.random.default_rng(2), n_boot=1000)
        assert hi - lo > 0.3

    def test_too_few_records_is_nan(self):
        lo, hi = bootstrap_auc([1.0], [0.0, 0.5], np.random.default_rng(0))
        assert np.isnan(lo) and np.isnan(hi)


class TestPairedDelta:
    def test_identical_scorings_differ_by_exactly_zero(self):
        rng = np.random.default_rng(0)
        pos, neg = rng.random(12), rng.random(20)
        d, lo, hi, p_le0 = paired_bootstrap_delta(pos, neg, pos, neg, np.random.default_rng(1), n_boot=200)
        assert d == lo == hi == 0.0 and p_le0 == 1.0

    def test_a_strictly_better_scoring_is_positive(self):
        rng = np.random.default_rng(0)
        pos, neg = rng.random(15), rng.random(25)
        # b scrambles the positives; a keeps them and adds a clear lift.
        d, lo, _, p_le0 = paired_bootstrap_delta(pos + 1.0, neg, rng.permutation(pos) * 0.0, neg,
                                                 np.random.default_rng(1), n_boot=300)
        assert d > 0 and lo > 0 and p_le0 == 0.0

    def test_mismatched_records_are_rejected(self):
        with pytest.raises(ValueError):
            paired_bootstrap_delta([1, 2], [0], [1], [0], np.random.default_rng(0))


class TestSpatialClusters:
    def test_single_linkage_chains_neighbours(self):
        # A-B and B-C are 800 m apart (A-C 1600 m); D is far away.
        xy = [(0, 0), (800, 0), (1600, 0), (50_000, 0)]
        labels = spatial_clusters(xy, 1000.0)
        assert labels[0] == labels[1] == labels[2]
        assert labels[3] != labels[0]
        assert sorted(set(labels)) == [0, 1]

    def test_zero_distance_keeps_every_point(self):
        assert list(spatial_clusters([(0, 0), (1, 1)], 0.0)) == [0, 1]

    def test_per_cluster_takes_the_median(self):
        vals = [1.0, 2.0, 9.0, 5.0]
        labels = [0, 0, 0, 1]
        assert list(per_cluster(vals, labels)) == [2.0, 5.0]
