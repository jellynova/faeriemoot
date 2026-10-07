"""Config loading: per-species run layout, weight overrides, taxon matching."""

from pathlib import Path
from typing import ClassVar

from foraging.config import apply_weights_override, load_config
from foraging.sources.inaturalist import in_taxon

ROOT = Path(__file__).resolve().parent.parent


class TestWeightsOverride:
    BASE: ClassVar[dict] = {"weights": {"terrain": 0.3, "forest": 0.3, "access": 0.2},
                           "terrain_subweights": {"elevation": 0.45, "aspect": 0.3}}

    def test_merges_one_level_deep(self):
        out = apply_weights_override(self.BASE, {"weights": {"terrain": 0.15}})
        assert out["weights"] == {"terrain": 0.15, "forest": 0.3, "access": 0.2}
        assert out["terrain_subweights"] == self.BASE["terrain_subweights"]

    def test_does_not_mutate_the_shared_weights(self):
        apply_weights_override(self.BASE, {"weights": {"terrain": 0.0}})
        assert self.BASE["weights"]["terrain"] == 0.3

    def test_no_override_is_identity(self):
        assert apply_weights_override(self.BASE, None) is self.BASE


class TestRunLayout:
    def test_species_on_the_same_aoi_get_separate_directories(self):
        arnica = load_config("config/pipeline.json", root=ROOT)
        chant = load_config("config/pipeline.json", root=ROOT,
                            species="config/species/cantharellus_formosus.json")
        assert arnica.aoi_id == chant.aoi_id
        assert arnica.run_id != chant.run_id
        assert arnica.run_id == "west_kootenays/arnica_latifolia"

    def test_habitat_model_and_species_weights(self):
        arnica = load_config("config/pipeline.json", root=ROOT)
        chant = load_config("config/pipeline.json", root=ROOT,
                            species="config/species/cantharellus_formosus.json")
        assert arnica.habitat_model == "vegetation"
        assert chant.habitat_model == "forest"
        # The override applies to chanterelle only.
        assert chant.weights["weights"]["forest"] > chant.weights["weights"]["terrain"]
        assert arnica.weights["weights"]["terrain"] == 0.30


def test_in_taxon_rejects_synonym_lookalikes():
    assert in_taxon("Cantharellus subalbidus", "Cantharellus")
    assert in_taxon("Cantharellus", "Cantharellus")
    # Returned for a "Cantharellus" query via old synonyms, but not chanterelles.
    assert not in_taxon("Hygrophoropsis aurantiaca", "Cantharellus")
    assert not in_taxon("Hygrocybe cantharellus", "Cantharellus")
    assert not in_taxon("Cantharellusoides x", "Cantharellus")
    assert not in_taxon(None, "Cantharellus")
