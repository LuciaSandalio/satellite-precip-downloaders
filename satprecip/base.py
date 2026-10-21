from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from .dates import iso_utc, normalize_interval
from .geometry import Region
from .manifest import RunManifest


T = TypeVar("T")


@dataclass
class DownloadOptions:
    start: datetime
    end: datetime
    output: Path
    workers: int = 4
    retries: int = 3
    resume: bool = True
    overwrite: bool = False
    keep_raw: bool = False
    exact_mask: bool = True
    output_format: str = "parquet"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, start: str, end: str, output: str | Path, **kwargs: Any) -> "DownloadOptions":
        start_dt, end_dt = normalize_interval(start, end)
        return cls(start_dt, end_dt, Path(output).expanduser().resolve(), **kwargs)


class BaseDownloader:
    name = "base"

    def __init__(self, region: Region, options: DownloadOptions):
        self.region = region
        self.options = options
        self.raw_dir = options.output / "raw" / self.name
        self.cropped_dir = options.output / "cropped" / self.name
        self.manifest_dir = options.output / "manifests"
        for directory in (self.raw_dir, self.cropped_dir, self.manifest_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def request_metadata(self) -> dict[str, Any]:
        sensitive_tokens = ("password", "passwd", "secret", "token", "credential", "api_key")
        safe_extra = {
            key: ("<redacted>" if any(token in key.lower() for token in sensitive_tokens) else value)
            for key, value in self.options.extra.items()
        }
        return {
            "start": iso_utc(self.options.start),
            "end": iso_utc(self.options.end),
            "workers": self.options.workers,
            "retries": self.options.retries,
            "resume": self.options.resume,
            "overwrite": self.options.overwrite,
            "keep_raw": self.options.keep_raw,
            "exact_mask": self.options.exact_mask,
            "format": self.options.output_format,
            **safe_extra,
        }

    def new_manifest(self) -> RunManifest:
        return RunManifest(self.name, self.request_metadata(), self.region.metadata())

    def manifest_path(self) -> Path:
        stamp = f"{self.options.start:%Y%m%dT%H%M%S}_{self.options.end:%Y%m%dT%H%M%S}"
        return self.manifest_dir / f"{self.name}_{stamp}.json"

    def should_skip(self, path: Path) -> bool:
        if self.options.overwrite:
            return False
        if not self.options.resume or not path.exists():
            return False
        if path.is_dir():
            return any(path.iterdir())
        return path.stat().st_size > 0

    def retry(self, function: Callable[[], T], description: str) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self.options.retries + 1):
            try:
                return function()
            except Exception as exc:
                last_error = exc
                if attempt < self.options.retries:
                    time.sleep(min(30, 2 ** (attempt - 1)))
        assert last_error is not None
        raise RuntimeError(f"{description} failed after {self.options.retries} attempts: {last_error}") from last_error

    @staticmethod
    def atomic_target(path: Path) -> Path:
        return path.with_name(path.name + ".part")

    @staticmethod
    def commit(temporary: Path, target: Path) -> None:
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise ValueError(f"Temporary output is empty: {temporary}")
        os.replace(temporary, target)

    def estimate(self) -> dict[str, Any]:
        raise NotImplementedError

    def download(self) -> RunManifest:
        raise NotImplementedError
