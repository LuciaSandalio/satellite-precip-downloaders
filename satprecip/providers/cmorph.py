from __future__ import annotations

import gzip
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..base import BaseDownloader
from ..dates import iter_days, iter_hours
from ..manifest import ArtifactRecord, RunManifest
from ..output import human_bytes, write_frame
from ._grid import append_grid_rows, crop_regular_grid


NLAT, NLON = 1649, 4948
LATS = -59.963614 + np.arange(NLAT) * 0.072771377
LONS = 0.036378335 + np.arange(NLON) * 0.072771377
BASE_URL = "https://ftp.cpc.ncep.noaa.gov/precip/CMORPH_V0.x/RAW/8km-30min"


class CmorphDownloader(BaseDownloader):
    name = "cmorph"

    @staticmethod
    def _url(hour) -> str:
        return f"{BASE_URL}/{hour:%Y}/{hour:%Y%m}/CMORPH_V0.x_RAW_8km-30min_{hour:%Y%m%d%H}.gz"

    @staticmethod
    def _read(path: Path) -> np.ndarray:
        with gzip.open(path, "rb") as stream:
            values = np.frombuffer(stream.read(), dtype="<f4")
        expected = 2 * NLAT * NLON
        if values.size != expected:
            raise ValueError(f"CMORPH file has {values.size} values; expected {expected}")
        return values.reshape(2, NLAT, NLON)

    def estimate(self) -> dict[str, object]:
        count = sum(1 for _ in iter_hours(self.options.start, self.options.end))
        estimated = count * 12_000_000
        return {"provider": self.name, "files": count, "estimated_bytes": estimated, "estimated_size": human_bytes(estimated), "note": "Approximation; remote sizes are not queried."}

    def download(self) -> RunManifest:
        manifest = self.new_manifest()
        for day in iter_days(self.options.start, self.options.end):
            hours = [hour for hour in iter_hours(max(day, self.options.start), min(day + timedelta(days=1), self.options.end))]
            if not hours:
                continue
            target = self.cropped_dir / f"cmorph_{day:%Y%m%d}.{self.options.output_format}"
            if self.should_skip(target):
                manifest.add(ArtifactRecord(day.isoformat(), f"{len(hours)} hourly files", str(target), "skipped", target.stat().st_size))
                continue
            rows: list[pd.DataFrame] = []
            failures: list[str] = []
            for hour in hours:
                url = self._url(hour)
                raw = self.raw_dir / Path(url).name
                try:
                    temporary = self.atomic_target(raw)
                    def fetch(url: str = url, temporary: Path = temporary) -> None:
                        urllib.request.urlretrieve(url, temporary)
                    self.retry(fetch, f"download {url}")
                    self.commit(temporary, raw)
                    arrays = self._read(raw)
                    for offset, array in enumerate(arrays):
                        timestamp = hour + timedelta(minutes=30 * offset)
                        if not (self.options.start <= timestamp < self.options.end):
                            continue
                        array = np.where(array < 0, np.nan, array)
                        lon, lat, values = crop_regular_grid(array, LATS, LONS, self.region, self.options.exact_mask)
                        append_grid_rows(rows, timestamp, lon, lat, values)
                    if not self.options.keep_raw:
                        raw.unlink(missing_ok=True)
                except Exception as exc:
                    failures.append(f"{hour:%Y%m%d%H}: {exc}")
            try:
                frame = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["time", "lon", "lat", "precipitation"])
                output = write_frame(frame, target, self.options.output_format)
                status = "failed" if failures and not rows else ("partial" if failures else "downloaded")
                manifest.add(ArtifactRecord(day.isoformat(), f"{len(hours)} hourly files", str(output), status, output.stat().st_size, "; ".join(failures) or None))
            except Exception as exc:
                manifest.add(ArtifactRecord(day.isoformat(), f"{len(hours)} hourly files", None, "failed", message=str(exc)))
        manifest.finish()
        manifest.write(self.manifest_path())
        return manifest
