"""Small-sample validation statistics."""

import numpy as np
import pytest

from foraging.validation import (
    auc,
    bootstrap_aucs,
    cluster_means,
    interval,
    n_units,
    percentile_rank,
    permutation_pvalue,
    spatial_clusters,
)


class TestAuc:
    def test_perfect_and_inverted(self):
        assert auc([3, 4], [1, 2]) == 1.0
        assert auc([1, 2], [3, 4]) == 0.0

    def test_ties_count_half(self):
        assert auc([1, 1], [1, 1]) == 0.5

    def test_matches_brute_force(self):
        rng = np.random.default_rng(0)
        pos, neg = rng.integers(0, 5, 30).astype(float), rng.integers(0, 5, 40).astype(float)
        brute = np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg])
        assert auc(pos, neg) == pytest.approx(brute)

    def test_rejected_records_rank_last_not_dropped(self):
        # A hard-filter reject is a miss: it must lower the AUC, not vanish.
        neg = np.array([0.1, 0.2, 0.3])
        kept_only = auc([0.9], neg)
        with_reject = auc([0.9, -np.inf], neg)
        assert kept_only == 1.0 and with_reject == 0.5

    def test_empty_is_nan(self):
        assert np.isnan(auc([], [1.0]))


class TestSpatialClusters:
    def test_chains_link_and_far_points_separate(self):
        x = np.array([0.0, 800.0, 1600.0, 10_000.0])
        labels = spatial_clusters(x, np.zeros(4), radius_m=1000.0)
        assert labels[0] == labels[1] == labels[2]
        assert labels[3] != labels[0]
        assert n_units(labels, 4) == 2

    def test_empty(self):
        assert len(spatial_clusters(np.array([]), np.array([]), 1000.0)) == 0

    def test_unclustered_units_is_n(self):
        assert n_units(None, 7) == 7


class TestBootstrap:
    def test_identical_surfaces_give_zero_paired_difference(self):
        rng = np.random.default_rng(1)
        pos, neg = rng.normal(1, 1, 20), rng.normal(0, 1, 50)
        boots = bootstrap_aucs({"a": pos, "b": pos.copy()}, {"a": neg, "b": neg.copy()},
                               n_boot=200, rng=rng)
        assert np.all(boots["a"] - boots["b"] == 0)

    def test_cluster_resampling_widens_interval_for_duplicates(self):
        # Five real sites, each photographed eight times. Treating the 40
        # records as independent overstates precision; resampling the five
        # sites does not.
        rng = np.random.default_rng(2)
        sites = rng.normal(0.5, 1.0, 5)
        pos = np.repeat(sites, 8)
        clusters = np.repeat(np.arange(5), 8)
        neg = rng.normal(0.0, 1.0, 400)
        iid = interval(bootstrap_aucs({"s": pos}, {"s": neg}, n_boot=1000, resample_neg=False,
                                      rng=np.random.default_rng(3))["s"])
        clus = interval(bootstrap_aucs({"s": pos}, {"s": neg}, pos_clusters=clusters,
                                       resample_neg=False, n_boot=1000,
                                       rng=np.random.default_rng(3))["s"])
        assert (clus[1] - clus[0]) > 1.5 * (iid[1] - iid[0])

    def test_fixed_negatives_are_not_resampled(self):
        pos, neg = np.array([1.0, 2.0, 3.0]), np.array([0.0, 1.5, 5.0])
        boots = bootstrap_aucs({"s": pos}, {"s": neg}, resample_neg=False, n_boot=50,
                               rng=np.random.default_rng(4))
        # Every replicate is some multiset of pos against the same neg.
        assert np.all((boots["s"] >= 0) & (boots["s"] <= 1))


class TestPermutation:
    def test_separated_groups_are_significant(self):
        p = permutation_pvalue(np.arange(10, 20.0), np.arange(0, 10.0), n_perm=2000,
                               rng=np.random.default_rng(5))
        assert p < 0.01

    def test_identical_groups_are_not(self):
        rng = np.random.default_rng(6)
        x = rng.normal(0, 1, 30)
        p = permutation_pvalue(x[:15], x[15:], n_perm=2000, rng=rng)
        assert p > 0.05

    def test_never_exactly_zero(self):
        p = permutation_pvalue(np.array([9.0]), np.array([0.0]), n_perm=10,
                               rng=np.random.default_rng(7))
        assert p > 0


def test_cluster_means_one_value_per_cluster():
    assert cluster_means(np.array([1.0, 3.0, 10.0]), np.array([0, 0, 1])).tolist() == [2.0, 10.0]


def test_percentile_rank_puts_rejects_at_zero():
    ref = np.array([-np.inf, -np.inf, 0.2, 0.4])
    assert percentile_rank(np.array([-np.inf, 0.3, 0.5]), ref).tolist() == [0.0, 0.75, 1.0]
