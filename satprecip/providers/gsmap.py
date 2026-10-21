from __future__ import annotations

import ftplib
import gzip
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..base import BaseDownloader
from ..dates import iter_days
from ..errors import ConfigurationError
from ..manifest import ArtifactRecord, RunManifest
from ..output import human_bytes, write_frame
from ._grid import append_grid_rows, crop_regular_grid


NLAT, NLON = 1200, 3600
LATS = 59.95 - np.arange(NLAT) * 0.1
LONS = 0.05 + np.arange(NLON) * 0.1
_STAMP_RE = re.compile(r"(\d{8})\.(\d{2})\d{2}(?:\D|$)")


class GsmapDownloader(BaseDownloader):
    name = "gsmap"

    def _credentials(self) -> tuple[str, str]:
        user = os.environ.get("GSMAP_USER")
        password = os.environ.get("GSMAP_PASSWORD")
        if not user or not password:
            raise ConfigurationError("GSMaP requires GSMAP_USER and GSMAP_PASSWORD environment variables")
        return user, password

    def _connect(self) -> ftplib.FTP:
        user, password = self._credentials()
        ftp = ftplib.FTP(str(self.options.extra.get("host", "hokusai.eorc.jaxa.jp")), timeout=60)
        ftp.login(user, password)
        return ftp

    @staticmethod
    def _timestamp(name: str) -> datetime | None:
        match = _STAMP_RE.search(name)
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H").replace(tzinfo=timezone.utc) if match else None

    @staticmethod
    def _read(path: Path) -> np.ndarray:
        with gzip.open(path, "rb") as stream:
            values = np.frombuffer(stream.read(), dtype="<f4")
        if values.size != NLAT * NLON:
            raise ValueError(f"GSMaP file has {values.size} values; expected {NLAT * NLON}")
        return values.reshape(NLAT, NLON)

    def estimate(self) -> dict[str, object]:
        hours = sum(1 for day in iter_days(self.options.start, self.options.end) for hour in range(24)
                    if self.options.start <= day.replace(hour=hour) < self.options.end)
        estimated = max(0, hours) * 6_000_000
        return {"provider": self.name, "files": max(0, hours), "estimated_bytes": estimated, "estimated_size": human_bytes(estimated), "note": "Approximation; no FTP login was performed."}

    def download(self) -> RunManifest:
        manifest = self.new_manifest()
        ftp = self._connect()
        try:
            for day in iter_days(self.options.start, self.options.end):
                target = self.cropped_dir / f"gsmap_{day:%Y%m%d}.{self.options.output_format}"
                if self.should_skip(target):
                    manifest.add(ArtifactRecord(day.isoformat(), "GSMaP hourly files", str(target), "skipped", target.stat().st_size))
                    continue
                remote = f"/standard/v8/hourly/{day:%Y/%m/%d}"
                rows: list[pd.DataFrame] = []
                failures: list[str] = []
                try:
                    ftp.cwd(remote)
                    names = [
                        name for name in ftp.nlst()
                        if name.startswith("gsmap_mvk") and name.endswith(".dat.gz")
                        and "sateinfo" not in name and "timeinfo" not in name
                    ]
                except Exception as exc:
                    manifest.add(ArtifactRecord(day.isoformat(), remote, None, "failed", message=str(exc)))
                    continue
                selected = []
                for name in names:
                    stamp = self._timestamp(name)
                    if stamp and self.options.start <= stamp < self.options.end:
                        selected.append((stamp, name))
                for stamp, name in sorted(selected):
                    local_name = Path(name).name
                    raw = self.raw_dir / local_name
                    try:
                        temporary = self.atomic_target(raw)
                        def fetch() -> None:
                            with temporary.open("wb") as stream:
                                ftp.retrbinary(f"RETR {name}", stream.write)
                        self.retry(fetch, f"download {remote}/{name}")
                        self.commit(temporary, raw)
                        array = self._read(raw)
                        array = np.where(array <= -99, np.nan, array)
                        lon, lat, values = crop_regular_grid(array, LATS, LONS, self.region, self.options.exact_mask)
                        append_grid_rows(rows, stamp, lon, lat, values)
                        if not self.options.keep_raw:
                            raw.unlink(missing_ok=True)
                    except Exception as exc:
                        failures.append(f"{local_name}: {exc}")
                try:
                    frame = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["time", "lon", "lat", "precipitation"])
                    output = write_frame(frame, target, self.options.output_format)
                    status = "no_data" if not selected else ("failed" if failures and not rows else ("partial" if failures else "downloaded"))
                    manifest.add(ArtifactRecord(day.isoformat(), f"{len(selected)} GSMaP files", str(output), status, output.stat().st_size, "; ".join(failures) or None))
                except Exception as exc:
                    manifest.add(ArtifactRecord(day.isoformat(), f"{len(selected)} GSMaP files", None, "failed", message=str(exc)))
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
        manifest.finish()
        manifest.write(self.manifest_path())
        return manifest
