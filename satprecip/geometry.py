from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from shapely import contains_xy
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .errors import ConfigurationError


@dataclass(frozen=True)
class Region:
    source: Path
    geometry: BaseGeometry
    bounds: tuple[float, float, float, float]
    fingerprint: str
    layer: str | None = None

    @property
    def centroid(self) -> tuple[float, float]:
        point = self.geometry.centroid
        return float(point.x), float(point.y)

    def metadata(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "layer": self.layer,
            "crs": "EPSG:4326",
            "bounds": list(self.bounds),
            "geometry_sha256": self.fingerprint,
        }


def load_region(path: str | Path, layer: str | None = None) -> Region:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ConfigurationError(f"Region file does not exist: {source}")
    try:
        frame = gpd.read_file(source, layer=layer)
    except Exception as exc:
        raise ConfigurationError(f"Could not read region file {source}: {exc}") from exc
    if frame.empty:
        raise ConfigurationError(f"Region file contains no features: {source}")
    if frame.crs is None:
        raise ConfigurationError(f"Region file has no CRS: {source}")
    frame = frame.to_crs("EPSG:4326")
    geometries = frame.geometry.dropna()
    # ``union_all`` is the current API; ``unary_union`` keeps compatibility
    # with the GeoPandas version shipped by several scientific distributions.
    geometry = geometries.union_all() if hasattr(geometries, "union_all") else geometries.unary_union
    if geometry.is_empty:
        raise ConfigurationError(f"Region geometry is empty: {source}")
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    if geometry.is_empty or not geometry.is_valid:
        raise ConfigurationError(f"Region geometry is invalid and could not be repaired: {source}")
    min_lon, min_lat, max_lon, max_lat = map(float, geometry.bounds)
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ConfigurationError("Region longitudes must be in the [-180, 180] range")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ConfigurationError("Region latitudes must be in the [-90, 90] range")
    fingerprint = hashlib.sha256(geometry.wkb).hexdigest()
    return Region(source, geometry, (min_lon, min_lat, max_lon, max_lat), fingerprint, layer)


def bbox_region(bounds: tuple[float, float, float, float]) -> BaseGeometry:
    return box(*bounds)


def point_mask(region: Region, lons: np.ndarray, lats: np.ndarray, exact: bool = True) -> np.ndarray:
    """Return a boolean mask for points in the region, including polygon boundaries."""
    min_lon, min_lat, max_lon, max_lat = region.bounds
    mask = (lons >= min_lon) & (lons <= max_lon) & (lats >= min_lat) & (lats <= max_lat)
    if not exact or not np.any(mask):
        return mask
    selected = np.flatnonzero(mask)
    # A tiny buffer includes points exactly on polygon boundaries.
    inside = contains_xy(region.geometry.buffer(1e-12), lons.flat[selected], lats.flat[selected])
    result = np.zeros(mask.size, dtype=bool)
    result[selected] = inside
    return result.reshape(mask.shape)


def longitude_intervals_360(bounds: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    """Convert a conventional lon bbox into one or two [0, 360) intervals."""
    west, _, east, _ = bounds
    west360, east360 = west % 360.0, east % 360.0
    if west <= east and west360 <= east360:
        return [(west360, east360)]
    return [(west360, 360.0), (0.0, east360)]
