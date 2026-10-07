"""Small-sample statistics for validating against occurrence records.

The validation sets here are tiny - a couple of dozen records per species - and
spatially clustered, because people photograph the same meadow from the same
trail. Point estimates alone overstate what such samples can show, so every
discrimination figure is reported with:

* a **bootstrap confidence interval**, resampling target and comparison groups
  independently (stratified), so the CI reflects both sample sizes;
* a **one-sided Mann-Whitney p-value** - the exact test of AUC > 0.5, since AUC
  *is* the normalised Mann-Whitney U;
* a **declustered** version, where records within a set distance are merged
  into one site, so the effective sample size is honest.

Two AUCs measured on the same records are compared with a **paired** bootstrap
of their difference; comparing their separate CIs would ignore the pairing.
"""

from __future__ import annotations

import numpy as np


def auc(pos, neg) -> float:
    """P(a random positive outranks a random negative); ties count half. 0.5 = no signal."""
    pos = np.asarray(pos, dtype="float64")
    neg = np.sort(np.asarray(neg, dtype="float64"))
    if not len(pos) or not len(neg):
        return float("nan")
    below = np.searchsorted(neg, pos, side="left")
    at_or_below = np.searchsorted(neg, pos, side="right")
    return float((below + at_or_below).sum() / (2.0 * len(pos) * len(neg)))


def midrank_percentile(values, reference) -> np.ndarray:
    """Where each value sits in ``reference``, 0..1, with ties counted half.

    Counting only the strictly-lower values (``searchsorted(side="left")``)
    understates anything sitting on a plateau of tied scores, which plateaued
    membership curves produce in quantity. Mid-rank keeps the mean percentile
    of a group exactly equal to its AUC against the reference, so the two
    figures cannot disagree. The *median* percentile still can: with a handful
    of records, a few low scorers pull the mean well below the median.
    """
    ref = np.sort(np.asarray(reference, dtype="float64"))
    v = np.asarray(values, dtype="float64")
    if not len(ref):
        return np.full(len(v), np.nan)
    lo = np.searchsorted(ref, v, side="left")
    hi = np.searchsorted(ref, v, side="right")
    return (lo + hi) / (2.0 * len(ref))


def mann_whitney_p(pos, neg) -> float:
    """One-sided p-value for AUC > 0.5 (exact when the samples are small and untied)."""
    from scipy.stats import mannwhitneyu

    pos, neg = np.asarray(pos, dtype="float64"), np.asarray(neg, dtype="float64")
    if len(pos) < 1 or len(neg) < 1:
        return float("nan")
    return float(mannwhitneyu(pos, neg, alternative="greater").pvalue)


def bootstrap_auc(pos, neg, rng, n_boot: int = 2000, level: float = 0.95) -> tuple[float, float]:
    """Percentile CI for the AUC, resampling each group independently.

    With fewer than ~15 per group the percentile interval runs somewhat
    narrow, so read it as a lower bound on the uncertainty, not a guarantee.
    """
    pos, neg = np.asarray(pos, dtype="float64"), np.asarray(neg, dtype="float64")
    if len(pos) < 2 or len(neg) < 2:
        return float("nan"), float("nan")
    stats = np.array([
        auc(rng.choice(pos, len(pos)), rng.choice(neg, len(neg))) for _ in range(n_boot)
    ])
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(stats, [tail, 100.0 - tail])
    return float(lo), float(hi)


def paired_bootstrap_delta(pos_a, neg_a, pos_b, neg_b, rng, n_boot: int = 2000,
                           level: float = 0.95) -> tuple[float, float, float, float]:
    """``AUC(a) - AUC(b)`` where a and b score the *same* records two ways.

    Each replicate resamples record indices once and applies them to both
    scorings, which keeps the strong positive correlation between the two
    AUCs. Returns ``(delta, ci_lo, ci_hi, share_of_replicates_at_or_below_0)``.
    """
    pos_a, neg_a = np.asarray(pos_a, "float64"), np.asarray(neg_a, "float64")
    pos_b, neg_b = np.asarray(pos_b, "float64"), np.asarray(neg_b, "float64")
    if len(pos_a) != len(pos_b) or len(neg_a) != len(neg_b):
        raise ValueError("paired scorings must cover the same records")
    delta = auc(pos_a, neg_a) - auc(pos_b, neg_b)
    if len(pos_a) < 2 or len(neg_a) < 2:
        return delta, float("nan"), float("nan"), float("nan")
    reps = np.empty(n_boot)
    for i in range(n_boot):
        ip = rng.integers(0, len(pos_a), len(pos_a))
        ineg = rng.integers(0, len(neg_a), len(neg_a))
        reps[i] = auc(pos_a[ip], neg_a[ineg]) - auc(pos_b[ip], neg_b[ineg])
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(reps, [tail, 100.0 - tail])
    return float(delta), float(lo), float(hi), float((reps <= 0).mean())


def spatial_clusters(xy, distance_m: float) -> np.ndarray:
    """Single-linkage cluster label per point: chains of points closer than ``distance_m``.

    Labels run from 0. With ``distance_m <= 0`` every point is its own cluster.
    """
    xy = np.asarray(xy, dtype="float64").reshape(-1, 2)
    if len(xy) == 0:
        return np.zeros(0, dtype=int)
    if len(xy) == 1 or distance_m <= 0:
        return np.arange(len(xy))
    from scipy.cluster.hierarchy import fcluster, linkage

    labels = fcluster(linkage(xy, method="single"), t=distance_m, criterion="distance")
    # Renumber from 0 in order of first appearance, for stable output.
    _, first = np.unique(labels, return_index=True)
    order = {lab: i for i, lab in enumerate(labels[np.sort(first)])}
    return np.array([order[lab] for lab in labels])


def per_cluster(values, labels, reducer=np.median) -> np.ndarray:
    """Collapse values to one per cluster (median by default)."""
    values, labels = np.asarray(values, dtype="float64"), np.asarray(labels)
    return np.array([reducer(values[labels == lab]) for lab in np.unique(labels)])
