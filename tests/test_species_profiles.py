"""Structural validation of every shipped species profile.

Seventeen profiles is enough that a typo in an affinity code or an inverted
elevation band stops being obvious by eye. These checks are the mechanical part
of the review: they do not judge whether a band is *right*, only that it is
well-formed and internally consistent.
"""

import json
from pathlib import Path

import pytest

from foraging.config import load_json
from foraging.stages.forest import SPECIES_NAMES

ROOT = Path(__file__).resolve().parent.parent
PROFILES = sorted((ROOT / "config" / "species").glob("*.json"))
IDS = [p.stem for p in PROFILES]

MODEL_BLOCK = {"spectral": "vegetation", "host_trees": "forest", "riparian": "riparian"}

REQUIRED = (
    "id", "common_name", "scientific_name", "habitat_model", "habitat_note",
    "observations", "terrain", "folk_magic",
)


def load(path):
    return load_json(path)


def ids_of(model):
    return [p.stem for p in PROFILES if load(p)["habitat_model"] == model]


@pytest.mark.parametrize("path", PROFILES, ids=IDS)
class TestEveryProfile:
    def test_has_the_required_keys(self, path):
        missing = [k for k in REQUIRED if k not in load(path)]
        assert missing == []

    def test_id_matches_the_filename(self, path):
        """The id keys the interim, output and web directories, so a mismatch
        between it and the filename makes two files for one species."""
        assert load(path)["id"] == path.stem

    def test_habitat_model_is_known(self, path):
        assert load(path)["habitat_model"] in MODEL_BLOCK

    def test_carries_its_habitat_block(self, path):
        p = load(path)
        assert MODEL_BLOCK[p["habitat_model"]] in p

    def test_elevation_band_is_ordered(self, path):
        e = load(path)["terrain"]["elevation_m"]
        assert e["hard_min"] < e["optimal_min"] < e["optimal_max"] < e["hard_max"]

    def test_slope_band_is_ordered(self, path):
        s = load(path)["terrain"]["slope_deg"]
        assert s["optimal_min"] < s["optimal_max"] < s["hard_max"]

    def test_observations_are_well_formed(self, path):
        o = load(path)["observations"]
        assert o["taxon_name"]
        assert 0.0 <= o["no_observation_baseline"] <= 1.0
        months = o.get("months")
        if months is not None:
            assert months and all(1 <= m <= 12 for m in months)

    def test_weights_override_names_its_own_habitat_layer(self, path):
        """A profile that overrides weights must weight the layer it actually
        uses, or its habitat component is silently dropped from the score."""
        p = load(path)
        override = p.get("weights_override")
        if not override:
            return
        layer = MODEL_BLOCK[p["habitat_model"]]
        assert layer in override["weights"]
        assert sum(override["weights"].values()) > 0
        for other in set(MODEL_BLOCK.values()) - {layer}:
            assert other not in override["weights"], f"stale {other} weight"


@pytest.mark.parametrize("path", [p for p in PROFILES if load(p)["habitat_model"] == "spectral"], ids=ids_of("spectral"))
class TestSpectralProfiles:
    def test_vegetation_block_is_complete(self, path):
        v = load(path)["vegetation"]
        for key in ("ndvi", "ndmi", "texture", "openness_preference", "logging"):
            assert key in v

    def test_ndvi_band_is_ordered(self, path):
        n = load(path)["vegetation"]["ndvi"]
        assert n["hard_min"] < n["optimal_min"] < n["optimal_max"]

    def test_ndmi_band_is_ordered(self, path):
        n = load(path)["vegetation"]["ndmi"]
        assert n["open_max"] < n["closed_min"]

    def test_openness_preferences_are_credits(self, path):
        pref = load(path)["vegetation"]["openness_preference"]
        assert set(pref) == {"meadow", "open_forest", "closed_forest", "bare"}
        assert all(0.0 <= v <= 1.0 for v in pref.values())

    def test_logging_penalty_is_a_multiplier(self, path):
        log = load(path)["vegetation"]["logging"]
        assert 0.0 < log["penalty"] <= 1.0
        assert log["recovery_years"] >= 1


@pytest.mark.parametrize("path", [p for p in PROFILES if load(p)["habitat_model"] == "host_trees"], ids=ids_of("host_trees"))
class TestHostTreeProfiles:
    def test_affinity_codes_are_real_vri_codes(self, path):
        """Codes are matched by longest prefix, so a key must either be a known
        code or a prefix of one. A typo here silently scores every stand as
        zero host, which looks like 'no habitat anywhere' rather than an error.
        """
        affinity = load(path)["forest"]["host_affinity"]
        known = set(SPECIES_NAMES)
        bad = [k for k in affinity
               if k not in known and not any(c.startswith(k) for c in known)]
        assert bad == [], f"unknown VRI code(s): {bad}"

    def test_affinities_are_credits(self, path):
        affinity = load(path)["forest"]["host_affinity"]
        assert affinity, "an empty affinity table can never score a cell"
        assert all(0.0 <= v <= 1.0 for v in affinity.values())
        assert max(affinity.values()) > 0.0

    def test_host_fraction_band_is_ordered(self, path):
        h = load(path)["forest"]["host_fraction"]
        assert 0.0 < h["hard_min"] < h["saturation"] <= 1.0

    def test_stand_age_knots_increase(self, path):
        knots = load(path)["forest"]["stand_age_credit"]["knots"]
        ages = [k[0] for k in knots]
        assert ages == sorted(ages)
        assert all(0.0 <= k[1] <= 1.0 for k in knots)
        assert any(k[1] > 0 for k in knots), "no age ever scores"

    def test_crown_closure_band_is_ordered(self, path):
        c = load(path)["forest"]["crown_closure_pct"]
        assert c["optimal_min"] < c["optimal_max"]
        assert c["hard_min"] < c["optimal_min"]
        assert c["hard_max"] is None or c["hard_max"] >= c["optimal_max"]


@pytest.mark.parametrize("path", [p for p in PROFILES if load(p)["habitat_model"] == "riparian"], ids=ids_of("riparian"))
class TestRiparianProfiles:
    def test_distance_band_is_ordered(self, path):
        d = load(path)["riparian"]["distance_m"]
        assert 0 < d["optimal_max"] < d["hard_max"]

    def test_slope_band_is_ordered(self, path):
        s = load(path)["riparian"]["slope_deg"]
        assert s["optimal_max"] < s["hard_max"]

    def test_stream_order_knots_increase(self, path):
        knots = load(path)["riparian"]["stream_order_credit"]["knots"]
        orders = [k[0] for k in knots]
        assert orders == sorted(orders)
        assert all(0.0 <= k[1] <= 1.0 for k in knots)
        assert knots[-1][1] > knots[0][1], "a first-order stream must not score as high as a mainstem"

    def test_water_type_credits_are_credits(self, path):
        credits = load(path)["riparian"]["water_type_credit"]
        assert set(credits) <= {"lake_river", "wetland"}
        assert all(0.0 <= v <= 1.0 for v in credits.values())


class TestSetCoverage:
    def test_every_model_is_used(self):
        """A model with no species is dead code, and a set that only exercises
        one model does not test the dispatch."""
        used = {load(p)["habitat_model"] for p in PROFILES}
        assert used == set(MODEL_BLOCK)

    def test_labels_are_set_for_non_mushroom_forest_targets(self):
        """'Host share' is right for a fungus and wrong for a tree, so a tree
        or lichen profile has to relabel it."""
        for p in PROFILES:
            profile = load(p)
            if profile["habitat_model"] != "host_trees":
                continue
            if profile.get("forest_labels", {}).get("share"):
                assert profile.get("habitat_label")
