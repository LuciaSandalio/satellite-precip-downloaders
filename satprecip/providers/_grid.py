from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..geometry import Region, point_mask
from ..output import write_frame


def crop_regular_grid(
    values: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    region: Region,
    exact: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return flattened lon, lat, value vectors inside a region."""
    west, south, east, north = region.bounds
    lon180 = ((np.asarray(lons) + 180.0) % 360.0) - 180.0
    lat_idx = np.flatnonzero((lats >= south) & (lats <= north))
    lon_idx = np.flatnonzero((lon180 >= west) & (lon180 <= east))
    if not len(lat_idx) or not len(lon_idx):
        return np.array([]), np.array([]), np.array([])
    sub = values[np.ix_(lat_idx, lon_idx)]
    xx, yy = np.meshgrid(lon180[lon_idx], lats[lat_idx])
    keep = point_mask(region, xx, yy, exact)
    return xx[keep], yy[keep], sub[keep]


def append_grid_rows(
    rows: list[pd.DataFrame], timestamp: datetime, lons: np.ndarray, lats: np.ndarray,
    values: np.ndarray, variable: str = "precipitation"
) -> None:
    rows.append(pd.DataFrame({"time": timestamp, "lon": lons, "lat": lats, variable: values}))


def write_daily(rows: list[pd.DataFrame], target: Path, output_format: str) -> Path:
    frame = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["time", "lon", "lat", "precipitation"]
    )
    return write_frame(frame, target, output_format)
