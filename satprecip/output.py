from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd
import xarray as xr


def write_frame(frame: pd.DataFrame, target: Path, output_format: str = "parquet") -> Path:
    """Write a tabular result atomically as Parquet or CSV."""
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = ".csv" if output_format == "csv" else ".parquet"
    target = target.with_suffix(suffix)
    temporary = target.with_name(target.name + ".part")
    if output_format == "csv":
        frame.to_csv(temporary, index=False)
    elif output_format == "parquet":
        frame.to_parquet(temporary, index=False)
    else:
        raise ValueError(f"Unsupported tabular format: {output_format}")
    os.replace(temporary, target)
    return target


def write_dataset(dataset: xr.Dataset, target: Path, output_format: str = "netcdf") -> Path:
    """Write a gridded result atomically as NetCDF or Zarr."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "zarr":
        target = target.with_suffix(".zarr")
        temporary = target.with_name(target.name + ".part")
        if temporary.exists():
            shutil.rmtree(temporary)
        dataset.to_zarr(temporary, mode="w")
        if target.exists():
            shutil.rmtree(target)
        os.replace(temporary, target)
        return target
    if output_format != "netcdf":
        raise ValueError(f"Unsupported gridded format: {output_format}")
    target = target.with_suffix(".nc")
    temporary = target.with_name(target.name + ".part")
    dataset.to_netcdf(temporary, engine="h5netcdf")
    os.replace(temporary, target)
    return target


def human_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"
