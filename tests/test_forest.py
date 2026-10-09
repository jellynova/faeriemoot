"""Host-tree scoring from VRI attributes."""

import numpy as np
import pandas as pd
import pytest

from foraging.stages.forest import (
    OTHER_SPECIES,
    SPECIES_KEYS,
    age_credit,
    host_fraction,
    leading_codes,
    leading_label,
    longest_prefix,
)

AFFINITY = {"FD": 1.0, "HW": 0.8, "PL": 0.3, "CW": 0.0, "S": 0.2, "SX": 0.25}


def stands(*rows, level2="T"):
    """Build a VRI-like frame from (code, pct) lists, one list per stand."""
    recs = []
    for spp in rows:
        rec = {"BCLCS_LEVEL_2": level2}
        for i, (cd, pct) in enumerate(spp, 1):
            rec[f"SPECIES_CD_{i}"] = cd
            rec[f"SPECIES_PCT_{i}"] = pct
        recs.append(rec)
    return pd.DataFrame(recs)


class TestLongestPrefix:
    def test_variety_code_folds_into_species(self):
        assert longest_prefix("FDI", AFFINITY) == "FD"

    def test_most_specific_entry_wins(self):
        # SX has its own entry; SE only matches the generic spruce one.
        assert longest_prefix("SX", AFFINITY) == "SX"
        assert longest_prefix("SE", AFFINITY) == "S"

    def test_case_and_whitespace_insensitive(self):
        assert longest_prefix(" fdi ", AFFINITY) == "FD"

    def test_unknown_and_missing(self):
        assert longest_prefix("AT", AFFINITY) is None
        assert longest_prefix(None, AFFINITY) is None


class TestHostFraction:
    def test_percentage_weighted_sum(self):
        df = stands([("FDI", 60), ("CW", 40)], [("HW", 50), ("PLI", 50)])
        assert host_fraction(df, AFFINITY) == pytest.approx([0.6, 0.55])

    def test_cedar_cannot_host(self):
        # Western redcedar is arbuscular mycorrhizal: a pure cedar stand has no hosts.
        assert host_fraction(stands([("CW", 100)]), AFFINITY)[0] == 0.0

    def test_unlisted_species_take_the_default(self):
        df = stands([("AT", 100)])
        assert host_fraction(df, AFFINITY)[0] == 0.0
        assert host_fraction(df, AFFINITY, default=0.1)[0] == pytest.approx(0.1)

    def test_non_treed_polygon_has_no_hosts(self):
        # Scattered Douglas-fir on a shrub polygon is not a stand.
        df = stands([("FD", 100)], level2="N")
        assert host_fraction(df, AFFINITY)[0] == 0.0

    def test_missing_cover_class_falls_back_to_species_presence(self):
        df = stands([("FD", 100)], level2=None)
        assert host_fraction(df, AFFINITY)[0] == pytest.approx(1.0)

    def test_null_percentages_are_ignored(self):
        df = stands([("FD", 70), (None, None)])
        assert host_fraction(df, AFFINITY)[0] == pytest.approx(0.7)


class TestAgeCredit:
    KNOTS = [[0, 0.0], [15, 0.0], [40, 1.0], [150, 1.0], [300, 0.7]]

    def test_knots_interpolate(self):
        out = age_credit(np.array([5.0, 27.5, 100.0, 225.0, 500.0]), self.KNOTS, unknown=0.5)
        assert out == pytest.approx([0.0, 0.5, 1.0, 0.85, 0.7])

    def test_unknown_age_gets_neutral_credit(self):
        assert age_credit(np.array([np.nan]), self.KNOTS, unknown=0.5)[0] == 0.5

    def test_rejects_unsorted_knots(self):
        with pytest.raises(ValueError):
            age_credit(np.array([10.0]), [[0, 0], [40, 1], [20, 1]], unknown=0.5)


def test_leading_species_codes_and_labels():
    df = stands([("FDI", 60)], [("ZZ", 100)], [(None, None)])
    codes = leading_codes(df)
    assert codes[0] == SPECIES_KEYS.index("FD") + 1
    assert codes[1] == OTHER_SPECIES
    assert codes[2] == 0
    assert leading_label(int(codes[0]), 60.0) == "Douglas-fir 60%"
    assert leading_label(0, None) is None
