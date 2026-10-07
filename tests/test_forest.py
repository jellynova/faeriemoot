"""VRI forest scoring: host share, stand age, crown closure and classes."""

import numpy as np
import pandas as pd
import pytest

from foraging.stages.forest import (
    CLASS_HOST_LEADING,
    CLASS_MIXED,
    CLASS_NONFOREST,
    CLASS_NONHOST,
    CLASS_YOUNG,
    classify_stands,
    host_share,
    is_treed,
    leading_is_host,
    score_stands,
    stand_attributes,
    stand_label,
)

HOSTS = {"FDI": 1.0, "FD": 1.0, "HW": 0.5}
FCFG = {
    "hosts": HOSTS,
    "host_share_pct": {"hard_min": 0, "optimal_min": 50},
    "stand_age_yr": {"hard_min": 15, "optimal_min": 40, "optimal_max": 1000,
                     "floor": 0.0, "missing_credit": 0.5},
    "crown_closure_pct": {"hard_min": 10, "optimal_min": 40, "optimal_max": 100,
                          "floor": 0.4, "missing_credit": 0.6},
}


def stands(*rows):
    """Rows of (species..., pct..., age, closure, bclcs2)."""
    cols = {f"SPECIES_CD_{i}": [] for i in range(1, 4)} | {f"SPECIES_PCT_{i}": [] for i in range(1, 4)}
    cols |= {"PROJ_AGE_1": [], "CROWN_CLOSURE": [], "BCLCS_LEVEL_2": []}
    for spp, pcts, age, cc, lc in rows:
        for i in range(3):
            cols[f"SPECIES_CD_{i + 1}"].append(spp[i] if i < len(spp) else None)
            cols[f"SPECIES_PCT_{i + 1}"].append(pcts[i] if i < len(pcts) else np.nan)
        cols["PROJ_AGE_1"].append(age)
        cols["CROWN_CLOSURE"].append(cc)
        cols["BCLCS_LEVEL_2"].append(lc)
    return pd.DataFrame(cols)


class TestHostShare:
    def test_weights_each_species_by_its_host_credit(self):
        df = stands((["FDI", "HW", "LW"], [60, 20, 20], 100, 50, "T"))
        # 60 x 1.0 + 20 x 0.5 + 20 x 0 = 70
        assert host_share(df, HOSTS)[0] == pytest.approx(70.0)

    def test_non_forest_with_no_species_is_zero_not_nan(self):
        df = stands(([], [], np.nan, np.nan, "N"))
        assert host_share(df, HOSTS)[0] == 0.0

    def test_leading_species_must_be_a_full_credit_host(self):
        df = stands((["FDI"], [60], 90, 50, "T"), (["HW", "FDI"], [60, 40], 90, 50, "T"),
                    (["LW", "FDI"], [60, 40], 90, 50, "T"))
        assert list(leading_is_host(df, HOSTS)) == [True, False, False]


def test_treed_reads_bclcs_and_treats_missing_as_not_treed():
    df = pd.DataFrame({"BCLCS_LEVEL_2": ["T", "N", None, np.nan, "L"]})
    assert list(is_treed(df)) == [True, False, False, False, False]


class TestScoreStands:
    def score(self, host, age, closure, treed=True, enforce=True):
        return score_stands(np.array([host], float), np.array([age], float), np.array([closure], float),
                            np.array([treed]), FCFG, enforce_treed=enforce)[0]

    def test_ideal_stand_scores_one(self):
        assert self.score(80, 120, 60) == pytest.approx(1.0)

    def test_no_host_trees_means_no_habitat(self):
        assert self.score(0, 120, 60) == 0.0

    def test_fresh_clearcut_scores_zero_even_on_a_host_site(self):
        assert self.score(80, 5, 60) == 0.0

    def test_open_canopy_keeps_its_floor(self):
        assert self.score(80, 120, 0) == pytest.approx(0.4)

    def test_unknown_age_and_closure_get_partial_credit(self):
        assert self.score(80, np.nan, np.nan) == pytest.approx(0.5 * 0.6)

    def test_non_treed_is_filtered_or_zeroed(self):
        assert np.isnan(self.score(80, 120, 60, treed=False, enforce=True))
        assert self.score(80, 120, 60, treed=False, enforce=False) == 0.0

    def test_host_share_without_hard_min_does_not_saturate(self):
        # A missing hard_min must not turn into an open-ended trapezoid that
        # gives a host-free stand full credit.
        cfg = {**FCFG, "host_share_pct": {"optimal_min": 50}}
        out = score_stands(np.array([0.0]), np.array([120.0]), np.array([60.0]), np.array([True]), cfg)
        assert out[0] == 0.0


def test_classify_orders_precedence_nonforest_then_young_then_host():
    host = np.array([80.0, 30.0, 0.0, 80.0, 80.0])
    age = np.array([120.0, 120.0, 120.0, 10.0, 120.0])
    treed = np.array([True, True, True, True, False])
    lead = np.array([True, False, False, True, True])
    out = classify_stands(host, age, treed, lead, FCFG)
    assert list(out) == [CLASS_HOST_LEADING, CLASS_MIXED, CLASS_NONHOST, CLASS_YOUNG, CLASS_NONFOREST]


def test_stand_attributes_and_label_read_a_real_vri_row():
    df = stands((["FDI", "LW", "CW"], [60, 20, 20], 85, 55, "T"))
    at = stand_attributes(df, FCFG).iloc[0]
    assert at["host_pct"] == 60 and at["age"] == 85 and at["closure"] == 55
    assert bool(at["treed"]) and bool(at["lead_host"])
    assert stand_label(df.iloc[0]) == "Douglas-fir 60%, western larch 20%, western redcedar 20%"
