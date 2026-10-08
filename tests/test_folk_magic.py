"""The folk-magic block: schema validation and what the UI receives."""

import json
from pathlib import Path

import pytest

from foraging.config import load_config, load_json
from foraging.folk_magic import SAFETY_LEVELS, THEMES, for_manifest, problems, validate

ROOT = Path(__file__).resolve().parent.parent
PROFILES = sorted((ROOT / "config" / "species").glob("*.json"))


def profile(**overrides):
    base = {
        "id": "test",
        "folk_magic": {
            "folk_names": ["test plant"],
            "traditions": ["somewhere"],
            "associations": [
                {"theme": "protection", "note": "carried against harm.", "origin": "a tradition."}
            ],
            "safety": {"level": "none", "note": "no known hazard."},
            "sources": ["a book"],
        },
    }
    base.update(overrides)
    return base


class TestShippedProfiles:
    @pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
    def test_every_profile_validates(self, path):
        assert problems(load_json(path)) == []

    def test_the_set_covers_several_themes(self):
        """A one-theme set would mean the vocabulary is not being used."""
        themes = {
            a["theme"]
            for path in PROFILES
            for a in load_json(path).get("folk_magic", {}).get("associations", [])
        }
        assert len(themes) >= 5
        assert themes <= set(THEMES)

    def test_every_profile_has_a_safety_note(self):
        for path in PROFILES:
            block = load_json(path)["folk_magic"]
            assert block["safety"]["level"] in SAFETY_LEVELS
            assert len(block["safety"]["note"]) > 40, path.stem


class TestValidation:
    def test_accepts_a_good_block(self):
        assert problems(profile()) == []

    def test_missing_block_is_an_error(self):
        assert "no folk_magic block" in problems({"id": "x"})[0]

    def test_unknown_theme_rejected(self):
        p = profile()
        p["folk_magic"]["associations"][0]["theme"] = "necromancy"
        assert any("theme vocabulary" in m for m in problems(p))

    def test_origin_is_required(self):
        """An unsourced association is how European and Indigenous records get
        conflated, so a missing origin is a hard error."""
        p = profile()
        del p["folk_magic"]["associations"][0]["origin"]
        assert any("origin" in m for m in problems(p))

    def test_bad_safety_level_rejected(self):
        p = profile()
        p["folk_magic"]["safety"]["level"] = "spicy"
        assert any("safety.level" in m for m in problems(p))

    def test_unknown_key_rejected(self):
        p = profile()
        p["folk_magic"]["magick_power"] = 9
        assert any("unknown folk_magic key" in m for m in problems(p))

    def test_empty_folk_names_rejected(self):
        p = profile()
        p["folk_magic"]["folk_names"] = []
        assert any("folk_names" in m for m in problems(p))

    def test_validate_raises_with_the_species_named(self):
        p = profile()
        p["folk_magic"]["associations"] = []
        with pytest.raises(ValueError, match="test"):
            validate(p)


class TestManifestPayload:
    def test_resolves_display_labels_and_disclaimer(self):
        out = for_manifest(profile())
        assert out["associations"][0]["theme_label"] == "protection"
        assert out["safety"]["level_label"] == "No known hazard"
        assert "not medical or magical advice" in out["disclaimer"].lower()

    def test_none_when_absent(self):
        assert for_manifest({"id": "x"}) is None


class TestLoadConfigEnforcesIt:
    def test_profile_without_the_block_is_rejected(self, tmp_path):
        bad = tmp_path / "bare.json"
        bad.write_text(json.dumps({"id": "bare", "terrain": {}}))
        with pytest.raises(ValueError, match="folk_magic"):
            load_config("config/pipeline.json", root=ROOT, species=bad)


class TestManifestCarriesIt:
    """The manifest is the only channel to the browser, so the block has to
    survive the trip intact."""

    def _manifest(self, tmp_path, species):
        import geopandas as gpd
        from shapely.geometry import Point

        from foraging.stages.scoring import _write_manifest

        cfg = load_config("config/pipeline.json", root=ROOT, species=species)
        cfg.pipeline["paths"] = {k: str(tmp_path / k) for k in ("cache", "interim", "output")}
        gdf = gpd.GeoDataFrame(
            {
                "score": [0.8, 0.7],
                "mean_score": [0.8, 0.7],
                "area_ha": [1.0, 2.0],
                "elevation_m": [1200, 1300],
                "drive_minutes": [10.0, 20.0],
                "hike_km": [1.0, 2.0],
                "land_flagged": [False, True],
            },
            geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:4326",
        )
        _write_manifest(cfg, gdf, None)
        return json.loads(cfg.output("manifest.json").read_text())

    @pytest.mark.parametrize("species", [p for p in PROFILES], ids=lambda p: p.stem)
    def test_manifest_exposes_validated_folk_magic(self, tmp_path, species):
        fm = self._manifest(tmp_path, species)["species"]["folk_magic"]
        assert fm["folk_names"]
        assert fm["associations"]
        assert fm["safety"]["note"]
        assert "not medical or magical advice" in fm["disclaimer"].lower()

