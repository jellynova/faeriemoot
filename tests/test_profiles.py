"""Every shipped species profile loads and is internally consistent.

Parametrised over ``config/species/*.json``, so a new profile is checked the
moment it is added - a typo'd key or an inverted band fails here rather than
an hour into a pipeline run.
"""

from pathlib import Path

import pytest

from foraging.config import HABITAT_LAYERS, load_config
from foraging.stages.forest import age_credit
from foraging.stages.moisture import WATER_FEATURES

ROOT = Path(__file__).resolve().parent.parent
PROFILES = sorted((ROOT / "config" / "species").glob("*.json"))


@pytest.fixture(params=PROFILES, ids=[p.stem for p in PROFILES])
def cfg(request):
    return load_config("config/pipeline.json", root=ROOT, species=request.param.relative_to(ROOT))


def _ordered(*vals):
    vals = [v for v in vals if v is not None]
    return all(a <= b for a, b in zip(vals, vals[1:], strict=False))


def test_id_matches_filename(cfg):
    assert cfg.species_id == Path(cfg.pipeline["species"]).stem


def test_names_present(cfg):
    sp = cfg.species
    assert sp.get("common_name") and sp.get("scientific_name") and sp.get("habitat_note")


def test_terrain_bands_ordered(cfg):
    t = cfg.species["terrain"]
    e, s, a = t["elevation_m"], t["slope_deg"], t["aspect"]
    assert _ordered(e["hard_min"], e["optimal_min"], e["optimal_max"], e["hard_max"])
    assert _ordered(s["optimal_min"], s["optimal_max"], s["hard_max"])
    assert 0 <= a["optimal_bearing_deg"] < 360 and 0 <= a["tolerance_deg"] <= 180


def test_weights_cover_the_habitat_layer(cfg):
    w = cfg.weights["weights"]
    assert w.get(cfg.habitat_layer, 0) > 0
    # The other habitat layer is never computed for this species, so a weight
    # on it would silently do nothing.
    for other in set(HABITAT_LAYERS.values()) - {cfg.habitat_layer}:
        assert other not in w
    assert set(cfg.weights["terrain_subweights"]) == {"elevation", "slope", "aspect"}


def test_habitat_block(cfg):
    if cfg.habitat_layer == "vegetation":
        v = cfg.species["vegetation"]
        n = v["ndvi"]
        assert _ordered(n["hard_min"], n["optimal_min"], n["optimal_max"], n.get("hard_max"))
        assert v["ndmi"]["open_max"] < v["ndmi"]["closed_min"]
        pref = v["openness_preference"]
        assert set(pref) == {"meadow", "open_forest", "closed_forest", "bare"}
        assert all(0 <= x <= 1 for x in pref.values())
        assert 0 < v["logging"]["penalty"] <= 1
    else:
        f = cfg.species["forest"]
        assert f["host_affinity"] and all(0 <= x <= 1 for x in f["host_affinity"].values())
        assert all(k == k.upper() for k in f["host_affinity"])
        assert 0 <= f["host_fraction"]["hard_min"] <= f["host_fraction"]["saturation"] <= 1
        knots = f["stand_age_credit"]["knots"]
        age_credit([0.0], knots, 0.5)  # raises on unordered knots
        assert all(0 <= k[1] <= 1 for k in knots)
        c = f["crown_closure_pct"]
        assert _ordered(c.get("hard_min"), c["optimal_min"], c["optimal_max"], c.get("hard_max"))


def test_moisture_block(cfg):
    m = cfg.species.get("moisture")
    if not m:
        pytest.skip("no moisture block")
    w = m["water_distance_m"]
    assert w["optimal_max"] < w["zero_at"]
    assert set(m.get("water_features", WATER_FEATURES)) <= set(WATER_FEATURES)
    if m.get("tpi"):
        assert m["tpi"]["wet_at_m"] < m["tpi"]["dry_at_m"]
    assert 0 <= m.get("min_credit", 0) < 1


def test_observations_block(cfg):
    o = cfg.species["observations"]
    assert o["taxon_name"]
    assert all(1 <= mo <= 12 for mo in o["months"])
    assert 0 <= o["no_observation_baseline"] <= 1
    assert o["taxon_name"] not in o.get("congeners_downweight", [])


def test_validation_block_names_distinct_taxa(cfg):
    v = cfg.species.get("validation")
    if not v:
        pytest.skip("no validation block")
    assert v["target"] != v["contrast"]
    assert (ROOT / v["config"]).exists()
