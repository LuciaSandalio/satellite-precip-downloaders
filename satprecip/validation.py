from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


@dataclass
class ValidationResult:
    path: str
    valid: bool
    size_bytes: int
    kind: str
    message: str = ""
    sha256: str | None = None


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def directory_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        with item.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
    return digest.hexdigest()


def validate_file(path: str | Path, checksum: bool = False) -> ValidationResult:
    item = Path(path)
    if not item.exists():
        return ValidationResult(str(item), False, 0, "missing", "File does not exist")
    if item.is_dir() and item.suffix.lower() != ".zarr":
        return ValidationResult(str(item), False, 0, "directory", "Only Zarr directories are valid artifacts")
    size = (
        sum(candidate.stat().st_size for candidate in item.rglob("*") if candidate.is_file())
        if item.is_dir() else item.stat().st_size
    )
    if size == 0:
        return ValidationResult(str(item), False, 0, "empty", "File is empty")
    suffixes = "".join(item.suffixes).lower()
    kind = item.suffix.lower().lstrip(".") or "file"
    try:
        if item.is_dir() and item.suffix.lower() == ".zarr":
            import xarray as xr
            with xr.open_zarr(item) as dataset:
                _ = dict(dataset.sizes)
            kind = "zarr"
        elif suffixes.endswith(".gz"):
            with gzip.open(item, "rb") as stream:
                while stream.read(1024 * 1024):
                    pass
            kind = "gzip"
        elif item.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            metadata = pq.ParquetFile(item).metadata
            if metadata.num_rows < 0:
                raise ValueError("Invalid Parquet row count")
            kind = "parquet"
        elif item.suffix.lower() in {".nc", ".nc4"}:
            import xarray as xr
            with xr.open_dataset(item, engine="h5netcdf") as dataset:
                _ = dict(dataset.sizes)
            kind = "netcdf"
        elif item.suffix.lower() in {".h5", ".hdf5", ".he5"}:
            import h5py
            with h5py.File(item, "r") as dataset:
                _ = list(dataset.keys())
            kind = "hdf5"
        elif item.suffix.lower() == ".json":
            json.loads(item.read_text(encoding="utf-8"))
            kind = "json"
    except Exception as exc:
        return ValidationResult(str(item), False, size, kind, str(exc))
    digest = (directory_sha256(item) if item.is_dir() else file_sha256(item)) if checksum else None
    return ValidationResult(str(item), True, size, kind, sha256=digest)


def inventory(root: str | Path, checksum: bool = False) -> list[ValidationResult]:
    base = Path(root)
    if base.is_file() or (base.is_dir() and base.suffix.lower() == ".zarr"):
        paths: Iterable[Path] = [base]
    else:
        zarr_roots = sorted(path for path in base.rglob("*.zarr") if path.is_dir())
        paths = [
            *zarr_roots,
            *(path for path in sorted(base.rglob("*")) if path.is_file() and not any(root in path.parents for root in zarr_roots)),
        ]
    return [validate_file(path, checksum=checksum) for path in paths]


def inventory_summary(results: list[ValidationResult]) -> dict[str, object]:
    return {
        "files": len(results),
        "valid": sum(result.valid for result in results),
        "invalid": sum(not result.valid for result in results),
        "size_bytes": sum(result.size_bytes for result in results),
        "by_kind": {
            kind: sum(1 for result in results if result.kind == kind)
            for kind in sorted({result.kind for result in results})
        },
        "results": [asdict(result) for result in results],
    }
