"""The analysis grid.

Every raster layer in the pipeline is resampled onto one common grid so that
terrain, vegetation and access values line up cell-for-cell. The grid is
derived from the AOI, in the AOI's local UTM zone, snapped outward to whole
multiples of the cell size.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS
from rasterio.features import rasterize

from .config import Config


@dataclass
class Grid:
    crs: CRS
    transform: Affine
    width: int
    height: int
    resolution: float

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        left, top = self.transform * (0, 0)
        right, bottom = self.transform * (self.width, self.height)
        return (left, bottom, right, top)

    def profile(self, dtype="float32", nodata="auto", count=1) -> dict:
        if nodata == "auto":
            # Integer rasters cannot carry NaN; 0 is the nodata code for the
            # categorical layers this pipeline writes.
            nodata = np.nan if str(dtype).startswith("float") else 0
        return {
            "driver": "GTiff",
            "dtype": dtype,
            "nodata": nodata,
            "width": self.width,
            "height": self.height,
            "count": count,
            "crs": self.crs,
            "transform": self.transform,
            "compress": "deflate",
            "predictor": 2 if dtype.startswith("float") else 1,
            "tiled": True,
        }

    def xy_centres(self) -> tuple[np.ndarray, np.ndarray]:
        """Projected x/y coordinates of every cell centre."""
        cols = np.arange(self.width) + 0.5
        rows = np.arange(self.height) + 0.5
        xs = self.transform.c + cols * self.transform.a
        ys = self.transform.f + rows * self.transform.e
        return xs, ys

    def lonlat_centres(self) -> tuple[np.ndarray, np.ndarray]:
        """Cell-centre longitude/latitude arrays, full grid shape."""
        from pyproj import Transformer

        xs, ys = self.xy_centres()
        xx, yy = np.meshgrid(xs, ys)
        tf = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        lon, lat = tf.transform(xx, yy)
        return lon, lat

    def mask_from(self, gdf: gpd.GeoDataFrame, all_touched: bool = False) -> np.ndarray:
        """Boolean raster of where ``gdf`` covers the grid."""
        g = gdf.to_crs(self.crs)
        geoms = [geom for geom in g.geometry if geom is not None and not geom.is_empty]
        if not geoms:
            return np.zeros(self.shape, dtype=bool)
        out = rasterize(
            ((geom, 1) for geom in geoms),
            out_shape=self.shape,
            transform=self.transform,
            fill=0,
            dtype="uint8",
            all_touched=all_touched,
        )
        return out.astype(bool)

    def write(self, path, array: np.ndarray, dtype: str = "float32") -> None:
        prof = self.profile(dtype=dtype)
        with rasterio.open(path, "w", **prof) as dst:
            dst.write(array.astype(dtype), 1)

    @staticmethod
    def read(path) -> tuple[np.ndarray, "Grid"]:
        with rasterio.open(path) as src:
            arr = src.read(1)
            grid = Grid(
                crs=CRS.from_user_input(src.crs),
                transform=src.transform,
                width=src.width,
                height=src.height,
                resolution=abs(src.transform.a),
            )
        return arr, grid


def build_grid(cfg: Config) -> Grid:
    """Snap a grid to the AOI bounds in the local UTM zone."""
    res = cfg.resolution
    crs_cfg = cfg.pipeline["grid"].get("crs")
    crs = CRS.from_user_input(crs_cfg) if crs_cfg else cfg.aoi.estimate_utm_crs()

    proj = cfg.aoi.to_crs(crs)
    left, bottom, right, top = proj.total_bounds

    # Snap outward to whole multiples of the cell size for clean tile alignment.
    left = np.floor(left / res) * res
    bottom = np.floor(bottom / res) * res
    right = np.ceil(right / res) * res
    top = np.ceil(top / res) * res

    width = int(round((right - left) / res))
    height = int(round((top - bottom) / res))
    transform = Affine(res, 0.0, left, 0.0, -res, top)

    return Grid(crs=CRS.from_user_input(crs), transform=transform, width=width, height=height, resolution=res)
