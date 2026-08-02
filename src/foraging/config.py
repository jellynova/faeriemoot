"""Configuration loading.

Everything region- or species-specific lives in ``config/``. Nothing in the
pipeline hard-codes an extent, a species or a weight, so re-targeting at a new
area is a matter of dropping in a new AOI polygon and pointing
``pipeline.json`` at it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry.base import BaseGeometry


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
    def output_dir(self) -> Path:
        p = self._dir("output") / self.aoi_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def interim(self, name: str) -> Path:
        return self.interim_dir / name

    def output(self, name: str) -> Path:
        return self.output_dir / name


def load_config(pipeline_path: str | Path = "config/pipeline.json", root: str | Path | None = None) -> Config:
    pipeline_path = Path(pipeline_path)
    root = Path(root) if root else _find_root(pipeline_path)
    pipeline = load_json(root / pipeline_path if not pipeline_path.is_absolute() else pipeline_path)

    species = load_json(root / pipeline["species"])
    weights = load_json(root / pipeline["weights"])
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
