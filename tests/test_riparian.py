"""Riparian habitat: credit curves, distance sampling, and a synthetic run."""

import json

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box

from foraging.config import load_config
from foraging.grid import build_grid
from foraging.stages import riparian
from foraging.stages.riparian import (
    CLASS_DRY,
    CLASS_LAKE_RIVER,
    CLASS_STREAM,
    CLASS_WATER,
    CLASS_WETLAND,
    stream_order_credit,
    water_credit,
)

KNOTS = [[1, 0.15], [3, 0.7], [5, 1.0]]


class TestStreamOrderCredit:
    def test_interpolates_between_knots(self):
        c = stream_order_credit(np.array([1, 2, 3, 5]), KNOTS)
        assert c[0] == pytest.approx(0.15)
        assert c[1] == pytest.approx(0.425)
        assert c[2] == pytest.approx(0.7)
        assert c[3] == pytest.approx(1.0)

    def test_high_orders_clamp_rather_than_extrapolate(self):
        assert stream_order_credit(np.array([9]), KNOTS)[0] == pytest.approx(1.0)

    def test_missing_order_gets_the_lowest_credit(self):
        """A stream with no recorded order is not assumed to be a mainstem."""
        assert stream_order_credit(np.array([np.nan]), KNOTS)[0] == pytest.approx(0.15)


class TestWaterCredit:
    def test_full_credit_inside_the_optimal_band(self):
        assert water_credit(np.array([0.0, 60.0]), 60.0, 300.0).tolist() == [1.0, 1.0]

    def test_falls_to_zero_at_hard_max(self):
        c = water_credit(np.array([180.0, 300.0, 900.0]), 60.0, 300.0)
        assert c[0] == pytest.approx(0.5)
        assert c[1] == 0.0
        assert c[2] == 0.0


class TestDistanceTo:
    def test_measures_in_metres_from_the_masked_cells(self):
        mask = np.zeros((3, 3), dtype=bool)
        mask[1, 1] = True
        dist, (ri, ci) = riparian._distance_to(mask, res=30.0)
        assert dist[1, 2] == pytest.approx(30.0)
        assert dist[0, 0] == pytest.approx(np.hypot(30.0, 30.0))
        # Indices point back at the source cell, which is what carries credit.
        assert (ri[1, 2], ci[1, 2]) == (1, 1)

    def test_no_mask_returns_nothing(self):
        assert riparian._distance_to(np.zeros((2, 2), dtype=bool), 30.0) == (None, None)


def _aoi(tmp_path):
    """A ~2.2 km square AOI in the West Kootenays, as a one-feature file."""
    geom = box(-117.80, 49.06, -117.77, 49.08)
    path = tmp_path / "aoi.geojson"
    gpd.GeoDataFrame({"id": ["test"], "label": ["test"]}, geometry=[geom],
                     crs="EPSG:4326").to_file(path, driver="GeoJSON")
    return path


PROFILE = {
    "id": "test_riparian",
    "common_name": "Test plant",
    "scientific_name": "Testus riparia",
    "habitat_model": "riparian",
    "habitat_note": "test",
    "folk_magic": {
        "folk_names": ["test plant"],
        "traditions": ["nowhere"],
        "associations": [{"theme": "protection", "note": "n.", "origin": "o."}],
        "safety": {"level": "none", "note": "no known hazard at all for this test plant."},
        "sources": [],
    },
    "observations": {"taxon_name": "Testus riparia", "months": [7], "no_observation_baseline": 0.5},
    "terrain": {
        "elevation_m": {"hard_min": 0, "optimal_min": 400, "optimal_max": 1500, "hard_max": 2500},
        "slope_deg": {"optimal_min": 0, "optimal_max": 20, "hard_max": 40},
        "aspect": {"optimal_bearing_deg": 0.0, "tolerance_deg": 180.0, "flat_slope_deg": 3.0},
    },
    "riparian": {
        "distance_m": {"optimal_max": 60, "hard_max": 300},
        "slope_deg": {"optimal_max": 10, "hard_max": 30},
        "stream_order_credit": {"knots": KNOTS},
        "water_type_credit": {"lake_river": 1.0, "wetland": 0.9},
    },
    "weights_override": {
        "weights": {"terrain": 0.2, "riparian": 0.5, "access": 0.15, "observations": 0.15},
        "terrain_subweights": {"elevation": 0.7, "slope": 0.3, "aspect": 0.0},
    },
    "imagery_window_override": None,
}


@pytest.fixture
def cfg(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(PROFILE))
    c = load_config("config/pipeline.json", root=".", aoi=_aoi(tmp_path), species=profile)
    c.pipeline["paths"] = {k: str(tmp_path / k) for k in ("cache", "interim", "output")}
    return c


@pytest.fixture
def water(cfg):
    """A lake in the middle of the AOI and a stream running north-south."""
    crs = build_grid(cfg).crs
    lake = gpd.GeoDataFrame(
        {"WATERBODY_TYPE": ["lake"], "AREA_HA": [40.0]},
        geometry=[Polygon([(-117.792, 49.068), (-117.788, 49.068),
                           (-117.788, 49.072), (-117.792, 49.072)])],
        crs="EPSG:4326",
    ).to_crs(crs)
    stream = gpd.GeoDataFrame(
        {"STREAM_ORDER": [4], "GNIS_NAME": ["Test Creek"]},
        geometry=[LineString([(-117.798, 49.06), (-117.798, 49.08)])], crs="EPSG:4326",
    ).to_crs(crs)
    return {"water_lakes": lake, "water_rivers": None, "water_wetlands": None, "water_streams": stream}


def _prepare(cfg, monkeypatch, water):
    """Write the shared terrain layers and stub the BC WFS fetch."""
    grid = build_grid(cfg)
    cfg.interim_dir.mkdir(parents=True, exist_ok=True)
    grid.write(cfg.interim("elevation.tif"), np.full(grid.shape, 700.0, dtype="float32"))
    grid.write(cfg.interim("slope.tif"), np.full(grid.shape, 2.0, dtype="float32"))

    def fake_fetch(alias, *a, **kw):
        return water.get(alias)

    monkeypatch.setattr("foraging.sources.bcdata.fetch_layer", fake_fetch)
    return grid


class TestSyntheticRun:
    def test_water_is_excluded_and_corridors_are_scored(self, cfg, monkeypatch, water):
        grid = _prepare(cfg, monkeypatch, water)
        riparian.run(cfg, log=lambda *a: None)

        score = riparian.Grid.read(cfg.species_interim("score_riparian.tif"))[0]
        rclass = riparian.Grid.read(cfg.species_interim("riparian_class.tif"))[0]

        assert np.isfinite(score).any(), "nothing scored"
        # Open water is not ground, so it must not be scored as habitat.
        assert not np.isfinite(score[rclass == CLASS_WATER]).any()
        # Both a lake shore and a stream corridor should exist and score well.
        assert (rclass == CLASS_LAKE_RIVER).any()
        assert (rclass == CLASS_STREAM).any()
        assert np.nanmax(score) == pytest.approx(1.0, abs=0.05)

    def test_beyond_hard_max_is_rejected_not_downed(self, cfg, monkeypatch, water):
        grid = _prepare(cfg, monkeypatch, water)
        riparian.run(cfg, log=lambda *a: None)
        score = riparian.Grid.read(cfg.species_interim("score_riparian.tif"))[0]
        rclass = riparian.Grid.read(cfg.species_interim("riparian_class.tif"))[0]
        far = rclass == CLASS_DRY
        assert far.any(), "expected some ground beyond the water range"
        assert not np.isfinite(score[far]).any()

    def test_slope_reduces_credit(self, cfg, monkeypatch, water):
        grid = _prepare(cfg, monkeypatch, water)
        riparian.run(cfg, log=lambda *a: None)
        flat = riparian.Grid.read(cfg.species_interim("score_riparian.tif"))[0].copy()

        # Re-run with a steep AOI: the same water, but no flat ground.
        grid.write(cfg.interim("slope.tif"), np.full(grid.shape, 35.0, dtype="float32"))
        riparian.run(cfg, log=lambda *a: None)
        steep = riparian.Grid.read(cfg.species_interim("score_riparian.tif"))[0]
        assert np.nanmax(steep) < np.nanmax(flat)

    def test_shared_distance_layer_is_written(self, cfg, monkeypatch, water):
        _prepare(cfg, monkeypatch, water)
        riparian.run(cfg, log=lambda *a: None)
        # Species-independent, so it lives with the other shared layers.
        assert cfg.interim("distance_to_water.tif").exists()
        assert not (cfg.species_interim_dir / "distance_to_water.tif").exists()

    def test_wetland_only_aoi_still_scores(self, cfg, monkeypatch, water):
        """Wetlands count as habitat, not as water to exclude."""
        crs = build_grid(cfg).crs
        wet = gpd.GeoDataFrame(
            {"WATERBODY_TYPE": ["wetland"], "AREA_HA": [10.0]},
            geometry=[Polygon([(-117.79, 49.07), (-117.785, 49.07),
                               (-117.785, 49.075), (-117.79, 49.075)])],
            crs="EPSG:4326",
        ).to_crs(crs)
        _prepare(cfg, monkeypatch, {"water_wetlands": wet, "water_lakes": None,
                                    "water_rivers": None, "water_streams": None})
        riparian.run(cfg, log=lambda *a: None)
        rclass = riparian.Grid.read(cfg.species_interim("riparian_class.tif"))[0]
        score = riparian.Grid.read(cfg.species_interim("score_riparian.tif"))[0]
        assert (rclass == CLASS_WETLAND).any()
        assert np.isfinite(score[rclass == CLASS_WETLAND]).any()

    def test_no_water_at_all_is_an_error_not_a_blank_map(self, cfg, monkeypatch):
        _prepare(cfg, monkeypatch, {})
        with pytest.raises(RuntimeError, match="no FWA water features"):
            riparian.run(cfg, log=lambda *a: None)


class TestSkipsOtherModels:
    def test_spectral_species_skips_the_stage(self, tmp_path):
        cfg = load_config("config/pipeline.json", root=".",
                          species="config/species/arnica_latifolia.json")
        cfg.pipeline["paths"] = {k: str(tmp_path / k) for k in ("cache", "interim", "output")}
        assert riparian.run(cfg, log=lambda *a: None) == {"skipped": True}
