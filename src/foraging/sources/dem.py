"""Elevation model fetching.

``copernicus`` (GLO-30) is the default and covers the globe, so a new AOI works
without any per-region setup. ``lidarbc`` is a hook for BC's 1 m LiDAR, which
has partial coverage and is far too large to blanket a whole region - the
intended use is refining already-shortlisted sites.
"""

from __future__ import annotations

import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from ..config import Config
from ..grid import Grid

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


def fetch_dem(cfg: Config, grid: Grid, log=print) -> np.ndarray:
    source = cfg.pipeline["terrain"].get("dem_source", "copernicus")
    if source == "copernicus":
        return _fetch_copernicus(cfg, grid, log=log)
    if source == "lidarbc":
        raise NotImplementedError(
            "LiDAR BC ingest is not wired up. It is intended for refining shortlisted "
            "sites, not blanket coverage; set terrain.dem_source to 'copernicus'."
        )
    raise ValueError(f"unknown dem_source: {source!r}")


def _fetch_copernicus(cfg: Config, grid: Grid, log=print) -> np.ndarray:
    collection = cfg.pipeline["terrain"].get("dem_collection", "cop-dem-glo-30")
    bbox = list(cfg.aoi.total_bounds)

    client = pystac_client.Client.open(PC_STAC, modifier=planetary_computer.sign_inplace)
    items = list(client.search(collections=[collection], bbox=bbox).items())
    if not items:
        raise RuntimeError(f"no {collection} tiles intersect the AOI {bbox}")
    log(f"  DEM: {len(items)} {collection} tile(s)")

    mosaic = np.full(grid.shape, np.nan, dtype="float32")
    for item in items:
        href = item.assets["data"].href
        with rasterio.open(href) as src:
            dest = np.full(grid.shape, np.nan, dtype="float32")
            reproject(
                source=rasterio.band(src, 1),
                destination=dest,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=grid.transform,
                dst_crs=grid.crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
        # Copernicus DEM uses 0 over ocean; treat exact 0 as nodata inland.
        fill = np.isnan(mosaic) & np.isfinite(dest)
        mosaic[fill] = dest[fill]
        log(f"    + {item.id}")

    covered = np.isfinite(mosaic).mean()
    if covered < 0.99:
        log(f"  ! DEM covers only {covered:.1%} of the grid")
    return mosaic
