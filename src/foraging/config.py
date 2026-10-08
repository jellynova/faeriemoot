"""Configuration loading.

Everything region- or species-specific lives in ``config/``. Nothing in the
pipeline hard-codes an extent, a species or a weight, so re-targeting at a new
area is a matter of dropping in a new AOI polygon and pointing
``pipeline.json`` at it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from .folk_magic import validate as validate_folk_magic


def _strip_comments(obj: Any) -> Any:
    """Drop ``$comment`` keys so config files can be self-documenting."""
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("$comment")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return _strip_comments(json.load(fh))


# Which stage supplies the habitat component, by species ``habitat_model``.
# Each model answers "what actually limits this species?", which differs:
# "spectral" reads the target's own signature off Sentinel-2 (right for a
# meadow plant); "host_trees" scores tree composition from the forest inventory
# (right for a mycorrhizal fungus, a target tree, or an epiphytic lichen);
# "riparian" scores proximity to streams, lakes and wetlands (right for a
# moisture-obligate plant, whose binding constraint is water, not greenness).
HABITAT_LAYERS = {
    "spectral": "vegetation",     # the target's own reflectance (Sentinel-2)
    "host_trees": "forest",       # tree composition from the forest inventory (VRI)
    "riparian": "riparian",       # proximity to water (BC Freshwater Atlas)
}

# Only these weights sections may be overridden per species. Access and land
# status are computed once per AOI and shared between species, so letting a
# profile change access_subweights or legality would silently disagree with
# the shared layers.
OVERRIDABLE_WEIGHTS = ("weights", "terrain_subweights")


def _apply_weights_override(weights: dict, override: dict | None, species_id: str) -> dict:
    if not override:
        return weights
    bad = set(override) - set(OVERRIDABLE_WEIGHTS)
    if bad:
        raise ValueError(
            f"species {species_id}: weights_override may only set "
            f"{', '.join(OVERRIDABLE_WEIGHTS)}, not {', '.join(sorted(bad))}"
        )
    out = dict(weights)
    for key, sub in override.items():
        # Replace the section wholesale: a partial merge would leave stale
        # component weights in place (e.g. a "vegetation" weight on a species
        # that has no vegetation layer).
        out[key] = dict(sub)
    return out


@dataclass
class Config:
    """Resolved pipeline configuration."""

    root: Path
    pipeline: dict
    species: dict
    weights: dict
    aoi: gpd.GeoDataFrame
    aoi_path: Path

    # ---- derived ---------------------------------------------------------
    @property
    def aoi_geom(self) -> BaseGeometry:
        return self.aoi.union_all()

    @property
    def aoi_id(self) -> str:
        props = self.aoi.iloc[0]
        return str(props.get("id") or self.aoi_path.stem)

    @property
    def aoi_label(self) -> str:
        props = self.aoi.iloc[0]
        return str(props.get("label") or self.aoi_id)

    @property
    def species_id(self) -> str:
        return self.species["id"]

    @property
    def habitat_model(self) -> str:
        model = self.species.get("habitat_model", "spectral")
        if model not in HABITAT_LAYERS:
            raise ValueError(f"unknown habitat_model {model!r}; expected one of {', '.join(HABITAT_LAYERS)}")
        return model

    @property
    def habitat_layer(self) -> str:
        """Score component that carries the habitat signal: 'vegetation' or 'forest'."""
        return HABITAT_LAYERS[self.habitat_model]

    @property
    def run_id(self) -> str:
        """``<aoi>/<species>`` - one built map per AOI and target."""
        return f"{self.aoi_id}/{self.species_id}"

    @property
    def resolution(self) -> float:
        return float(self.pipeline["grid"]["resolution_m"])

    def imagery_window(self) -> dict:
        """Species-level override wins over the pipeline default."""
        return self.species.get("imagery_window_override") or self.pipeline["imagery"]

    # ---- paths -----------------------------------------------------------
    def _dir(self, key: str) -> Path:
        p = self.root / self.pipeline["paths"][key]
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_dir(self) -> Path:
        return self._dir("cache")

    @property
    def interim_dir(self) -> Path:
        """Per-AOI so two regions can be built side by side without clobbering."""
        p = self._dir("interim") / self.aoi_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def species_interim_dir(self) -> Path:
        """Per-AOI *and* per-species.

        Anything that depends on the species profile (scores, thresholds,
        classes) lives here, so two targets on one AOI do not overwrite each
        other. Species-independent layers - DEM, Sentinel-2 composites, drive
        times, tenure, raw inventory attributes - stay in ``interim_dir`` and
        are shared, which is what makes a second species cheap to add.
        """
        p = self.interim_dir / self.species_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_dir(self) -> Path:
        p = self._dir("output") / self.aoi_id / self.species_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def interim(self, name: str) -> Path:
        """Shared, species-independent layer."""
        return self.interim_dir / name

    def species_interim(self, name: str) -> Path:
        """Species-dependent layer."""
        return self.species_interim_dir / name

    def output(self, name: str) -> Path:
        return self.output_dir / name


def load_config(
    pipeline_path: str | Path = "config/pipeline.json",
    root: str | Path | None = None,
    aoi: str | Path | None = None,
    species: str | Path | None = None,
) -> Config:
    """Load the pipeline config.

    ``aoi`` and ``species`` override the paths named in the config file, so a
    different region or target can be run without editing anything on disk.
    """
    pipeline_path = Path(pipeline_path)
    root = Path(root) if root else _find_root(pipeline_path)
    pipeline = load_json(root / pipeline_path if not pipeline_path.is_absolute() else pipeline_path)

    if aoi:
        pipeline["aoi"] = str(aoi)
    if species:
        pipeline["species"] = str(species)

    species = load_json(root / pipeline["species"])
    # Every profile carries a folk-magic block; validate it here so a typo
    # fails at startup with the species named, rather than rendering an empty
    # panel in the map a pipeline run later.
    validate_folk_magic(species)
    weights = load_json(root / pipeline["weights"])
    weights = _apply_weights_override(weights, species.get("weights_override"), species["id"])
    aoi_path = root / pipeline["aoi"]
    aoi = gpd.read_file(aoi_path)
    if aoi.crs is None:
        aoi = aoi.set_crs("EPSG:4326")
    aoi = aoi.to_crs("EPSG:4326")

    return Config(root=root, pipeline=pipeline, species=species, weights=weights, aoi=aoi, aoi_path=aoi_path)


def _find_root(pipeline_path: Path) -> Path:
    """Walk up from cwd looking for the config file, so the CLI works from anywhere."""
    if pipeline_path.is_absolute():
        return pipeline_path.parent.parent
    here = Path.cwd().resolve()
    for cand in [here, *here.parents]:
        if (cand / pipeline_path).exists():
            return cand
    return here
