"""Species profiles, per-species paths and occurrence-record hygiene."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

from foraging.config import _apply_weights_override, load_config
from foraging.sources.inaturalist import pick_taxon
from foraging.stages.observations import filter_usable

ROOT = Path(__file__).resolve().parent.parent
ARNICA = "config/species/arnica_latifolia.json"
CHANTERELLE = "config/species/cantharellus_formosus.json"


@pytest.fixture
def cfgs(tmp_path):
    """Both shipped profiles on the default AOI, with paths redirected to tmp."""
    out = {}
    for name, sp in (("arnica", ARNICA), ("chanterelle", CHANTERELLE)):
        cfg = load_config("config/pipeline.json", root=ROOT, species=sp)
        cfg.pipeline["paths"] = {k: str(tmp_path / k) for k in ("cache", "interim", "output")}
        out[name] = cfg
    return out


class TestProfiles:
    def test_habitat_layer_follows_the_profile(self, cfgs):
        assert cfgs["arnica"].habitat_layer == "vegetation"
        assert cfgs["chanterelle"].habitat_layer == "forest"

    def test_chanterelle_weights_name_its_own_habitat_layer(self, cfgs):
        w = cfgs["chanterelle"].weights
        assert "forest" in w["weights"] and "vegetation" not in w["weights"]
        assert w["terrain_subweights"]["aspect"] == 0.0
        # Sections the profile does not override are inherited.
        assert w["legality"] == cfgs["arnica"].weights["legality"]

    def test_two_species_on_one_aoi_do_not_collide(self, cfgs):
        a, c = cfgs["arnica"], cfgs["chanterelle"]
        assert a.interim("elevation.tif") == c.interim("elevation.tif")
        assert a.species_interim("score_terrain.tif") != c.species_interim("score_terrain.tif")
        assert a.output("sites.geojson") != c.output("sites.geojson")
        assert a.run_id == "west_kootenays/arnica_latifolia"


class TestWeightsOverride:
    BASE = {"weights": {"terrain": 1, "vegetation": 1}, "legality": {"x": 1}}

    def test_replaces_a_section_wholesale(self):
        out = _apply_weights_override(self.BASE, {"weights": {"terrain": 1, "forest": 2}}, "sp")
        # No stale "vegetation" weight survives a partial merge.
        assert out["weights"] == {"terrain": 1, "forest": 2}
        assert self.BASE["weights"] == {"terrain": 1, "vegetation": 1}

    def test_refuses_shared_sections(self):
        with pytest.raises(ValueError, match="legality"):
            _apply_weights_override(self.BASE, {"legality": {}}, "sp")

    def test_none_is_identity(self):
        assert _apply_weights_override(self.BASE, None, "sp") is self.BASE


class TestPickTaxon:
    RESULTS = [
        {"id": 63538, "name": "Hygrophoropsis aurantiaca", "rank": "species", "observations_count": 9000},
        {"id": 1374657, "name": "Cantharellus", "rank": "subgenus", "observations_count": 100},
        {"id": 47348, "name": "Cantharellus", "rank": "genus", "observations_count": 50000},
    ]

    def test_exact_scientific_name_only(self):
        # The false chanterelle comes back from a "Cantharellus" search via
        # its common name; it must never be picked.
        assert pick_taxon(self.RESULTS, "Cantharellus")["id"] == 47348

    def test_no_exact_match(self):
        assert pick_taxon(self.RESULTS, "Craterellus") is None

    def test_inactive_taxa_skipped(self):
        res = [{"id": 1, "name": "Arnica", "is_active": False}, {"id": 2, "name": "Arnica"}]
        assert pick_taxon(res, "arnica")["id"] == 2


def test_filter_usable_drops_obscured_and_coarse_keeps_unknown():
    obs = gpd.GeoDataFrame(
        {"obscured": [False, True, False, False], "accuracy_m": [10.0, 5.0, 5000.0, None]},
        geometry=gpd.points_from_xy([0, 1, 2, 3], [0, 1, 2, 3]), crs="EPSG:4326",
    )
    kept = filter_usable(obs, max_accuracy_m=1000.0, log=lambda *a: None)
    assert list(kept.index) == [0, 3]


class TestTargetMatching:
    def test_infraspecific_records_are_the_target(self):
        from foraging.stages.observations import WEIGHT_EXACT, _weight_for, is_target

        assert is_target("Amanita muscaria flavivolvata", "Amanita muscaria")
        assert _weight_for("Amanita muscaria flavivolvata", "Amanita muscaria", set()) == WEIGHT_EXACT

    def test_genus_target_takes_its_species(self):
        from foraging.stages.observations import is_target

        assert is_target("Rosa nutkana", "Rosa")
        # A name prefix is not a genus: "Rosaceae" is not "Rosa".
        assert not is_target("Rosaceae", "Rosa")
        assert not is_target(None, "Rosa")

    def test_downweight_beats_genus_match(self):
        from foraging.stages.observations import WEIGHT_CONGENER, _weight_for

        # Matches the shipped rosa profile: genus fallback Rosa, R. nutkana down-weighted.
        assert _weight_for("Rosa nutkana", "Rosa", {"Rosa nutkana"}) == WEIGHT_CONGENER


def test_ndvi_hard_max_is_optional():
    from foraging.curves import trapezoid

    ndvi = np.array([0.3, 0.6, 0.9])
    open_top = trapezoid(ndvi, 0.1, 0.2, 0.5, None)
    capped = trapezoid(ndvi, 0.1, 0.2, 0.5, 0.9)
    assert open_top.tolist() == [1.0, 1.0, 1.0]
    assert capped.tolist() == pytest.approx([1.0, 0.75, 0.0])
