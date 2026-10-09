"""Small-sample statistics for validating the model against occurrence records.

With a couple of dozen records the question is less "what is the AUC" than
"what range of AUCs is this sample compatible with", so everything here
reports an interval alongside the point estimate. Three things drive the
design:

* **Records are not independent.** iNaturalist records cluster: one hiker
  photographs the same meadow on the same day, a popular trail yields a dozen
  records of one population. Resampling records as if independent understates
  the uncertainty. Records are grouped into spatial clusters and the
  bootstrap resamples *clusters*, so a run of near-duplicates counts once
  toward the effective sample size.
* **Comparisons between layers must be paired.** "Terrain alone beats terrain
  plus vegetation" is a claim about a difference measured on the same
  records. Each bootstrap replicate draws one set of clusters and scores every
  candidate surface on it, so the interval on the difference reflects the
  correlation between the two scores rather than ignoring it.
* **Hard-filter rejections are misses, not missing.** A record on a cell the
  model rejects outright (NaN score) is the model saying "not habitat".
  Dropping such records conditions the test on the model having already
  succeeded. Callers map NaN to ``-inf`` so these records rank last.
"""

from __future__ import annotations

import numpy as np


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(random positive outranks random negative), ties counting half. 0.5 = no signal.

    This is the Mann-Whitney U statistic over ``len(pos) * len(neg)``. It
    accepts ``-inf`` (hard-filter rejects), which tie with each other.
    """
    pos = np.asarray(pos, dtype="float64")
    neg = np.sort(np.asarray(neg, dtype="float64"))
    if not len(pos) or not len(neg):
        return float("nan")
    below = np.searchsorted(neg, pos, side="left")
    at_or_below = np.searchsorted(neg, pos, side="right")
    return float((below + 0.5 * (at_or_below - below)).sum() / (len(pos) * len(neg)))


def spatial_clusters(x: np.ndarray, y: np.ndarray, radius_m: float) -> np.ndarray:
    """Single-linkage cluster labels: records within ``radius_m`` of a chain share one.

    ``x``/``y`` must be in a metric CRS.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    pts = np.column_stack([np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")])
    n = len(pts)
    if n == 0:
        return np.zeros(0, dtype="int64")
    pairs = np.array(sorted(cKDTree(pts).query_pairs(radius_m)), dtype="int64").reshape(-1, 2)
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    return labels.astype("int64")


def n_units(clusters: np.ndarray | None, n: int) -> int:
    """Effective sample size: distinct clusters, or ``n`` when unclustered."""
    return n if clusters is None else len(np.unique(clusters))


class _Resampler:
    """Draws bootstrap index sets, by cluster when clusters are given."""

    def __init__(self, n: int, clusters: np.ndarray | None):
        self.n = n
        if clusters is None:
            self.members = None
        else:
            clusters = np.asarray(clusters)
            self.members = [np.flatnonzero(clusters == c) for c in np.unique(clusters)]

    def draw(self, rng: np.random.Generator) -> np.ndarray:
        if self.members is None:
            return rng.integers(0, self.n, self.n)
        pick = rng.integers(0, len(self.members), len(self.members))
        return np.concatenate([self.members[i] for i in pick])


def bootstrap_aucs(
    pos: dict[str, np.ndarray],
    neg: dict[str, np.ndarray],
    pos_clusters: np.ndarray | None = None,
    neg_clusters: np.ndarray | None = None,
    resample_neg: bool = True,
    n_boot: int = 2000,
    rng: np.random.Generator | None = None,
) -> dict[str, np.ndarray]:
    """Bootstrap AUC replicates for several score surfaces at once, paired.

    ``pos[name]`` and ``neg[name]`` are one surface's values at the same
    records for every name (row-aligned across names). Every replicate uses
    one shared resample, which is what makes differences between surfaces
    paired. ``resample_neg=False`` holds the negatives fixed - right for a
    random-cell null of tens of thousands, whose own sampling error is
    negligible next to that of the records.
    """
    rng = rng or np.random.default_rng()
    names = list(pos)
    n_pos = len(next(iter(pos.values())))
    n_neg = len(next(iter(neg.values())))
    rs_pos = _Resampler(n_pos, pos_clusters)
    rs_neg = _Resampler(n_neg, neg_clusters)
    out = {k: np.empty(n_boot, dtype="float64") for k in names}
    for b in range(n_boot):
        ip = rs_pos.draw(rng)
        ineg = rs_neg.draw(rng) if resample_neg else slice(None)
        for k in names:
            out[k][b] = auc(pos[k][ip], neg[k][ineg])
    return out


def interval(replicates: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    """Percentile bootstrap interval."""
    r = np.asarray(replicates, dtype="float64")
    r = r[np.isfinite(r)]
    if not len(r):
        return (float("nan"), float("nan"))
    a = (1.0 - level) / 2.0
    return (float(np.quantile(r, a)), float(np.quantile(r, 1.0 - a)))


def cluster_means(values: np.ndarray, clusters: np.ndarray) -> np.ndarray:
    """One value per cluster - the unit the permutation test exchanges."""
    values = np.asarray(values, dtype="float64")
    return np.array([values[clusters == c].mean() for c in np.unique(clusters)])


def permutation_pvalue(
    pos_units: np.ndarray,
    neg_units: np.ndarray,
    n_perm: int = 10000,
    rng: np.random.Generator | None = None,
) -> float:
    """One-sided p-value for AUC > 0.5 by permuting group labels across units.

    Units should be independent - pass cluster means, not raw records. The
    +1 in numerator and denominator keeps the estimate valid (never exactly 0)
    with a finite number of permutations.
    """
    rng = rng or np.random.default_rng()
    pos_units = np.asarray(pos_units, dtype="float64")
    neg_units = np.asarray(neg_units, dtype="float64")
    observed = auc(pos_units, neg_units)
    pooled = np.concatenate([pos_units, neg_units])
    k = len(pos_units)
    hits = 0
    for _ in range(n_perm):
        perm = rng.permutation(pooled)
        if auc(perm[:k], perm[k:]) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def percentile_rank(values: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Fraction of ``reference`` strictly below each value, 0..1.

    With rejects encoded as ``-inf`` in both, a rejected record sits at 0 and
    the reference distribution includes the rejected ground, so "0.80" means
    "better than 80% of the area", not "better than 80% of what passed".
    """
    ref = np.sort(np.asarray(reference, dtype="float64"))
    return np.searchsorted(ref, np.asarray(values, dtype="float64"), side="left") / max(len(ref), 1)
