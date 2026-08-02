"""Sentinel-2 L2A access via the Planetary Computer STAC API.

Scene selection is seasonal: only captures inside the target's bloom/peak-green
window are eligible, gathered across several years so a single bad season does
not leave holes in the composite.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from ..config import Config

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Scene Classification Layer values worth keeping: vegetated, bare, unclassified.
# Everything else is cloud, cloud shadow, cirrus, snow, water, saturation or
# topographic shadow - all of which corrupt NDVI.
SCL_KEEP = (4, 5, 7)

# Since processing baseline 04.00 the L2A products carry a +1000 DN offset.
BOA_OFFSET = 1000.0
BOA_SCALE = 10000.0


@dataclass
class Scene:
    item: object
    date: str
    cloud: float
    baseline: str

    @property
    def id(self) -> str:
        return self.item.id


def search_scenes(cfg: Config, log=print) -> list[Scene]:
    w = cfg.imagery_window()
    client = pystac_client.Client.open(PC_STAC, modifier=planetary_computer.sign_inplace)
    years = w["years"]
    items = list(
        client.search(
            collections=[w["collection"]],
            bbox=list(cfg.aoi.total_bounds),
            datetime=f"{min(years)}-01-01/{max(years)}-12-31",
            query={"eo:cloud_cover": {"lt": w["max_cloud_cover"]}},
        ).items()
    )

    in_window: dict[str, Scene] = {}
    for it in items:
        md = it.datetime.strftime("%m-%d")
        if not (w["window_start"] <= md <= w["window_end"]):
            continue
        if it.datetime.year not in years:
            continue
        date = it.datetime.strftime("%Y-%m-%d")
        sc = Scene(
            item=it,
            date=date,
            cloud=float(it.properties.get("eo:cloud_cover", 100.0)),
            baseline=str(it.properties.get("s2:processing_baseline", "05.00")),
        )
        # Same date can appear more than once across processing baselines.
        prev = in_window.get(date)
        if prev is None or sc.cloud < prev.cloud:
            in_window[date] = sc

    scenes = sorted(in_window.values(), key=lambda s: s.cloud)
    log(f"  {len(scenes)} distinct dates in the {w['window_start']}..{w['window_end']} window across {years}")
    return scenes


def _harmonise(arr: np.ndarray, baseline: str) -> np.ndarray:
    """DN -> surface reflectance, applying the post-baseline-04.00 offset."""
    out = arr.astype("float32")
    try:
        needs_offset = float(baseline) >= 4.0
    except ValueError:
        needs_offset = True
    if needs_offset:
        out -= BOA_OFFSET
    return out / BOA_SCALE


def read_band(scene: Scene, asset: str, dst_crs, dst_transform, dst_shape,
              resampling=Resampling.bilinear, harmonise: bool = True) -> np.ndarray:
    """Read one band straight onto a target grid."""
    href = scene.item.assets[asset].href
    dest = np.full(dst_shape, np.nan, dtype="float32")
    with rasterio.open(href) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dest,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=0,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )
    return _harmonise(dest, scene.baseline) if harmonise else dest


def read_scl(scene: Scene, dst_crs, dst_transform, dst_shape) -> np.ndarray:
    """Scene classification, nearest-neighbour so class codes stay intact."""
    return read_band(
        scene, "SCL", dst_crs, dst_transform, dst_shape,
        resampling=Resampling.nearest, harmonise=False,
    )
