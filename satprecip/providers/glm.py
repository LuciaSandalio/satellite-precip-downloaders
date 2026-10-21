from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from ..base import BaseDownloader
from ..dates import iter_hours, iso_utc
from ..geometry import point_mask
from ..manifest import ArtifactRecord, RunManifest
from ..output import human_bytes, write_frame


_START_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})")


def glm_time(name: str) -> datetime:
    match = _START_RE.search(name)
    if not match:
        raise ValueError(f"No GLM timestamp in {name}")
    year, day, hour, minute, second = map(int, match.groups())
    return datetime.strptime(f"{year} {day:03d} {hour:02d}{minute:02d}{second:02d}", "%Y %j %H%M%S").replace(tzinfo=timezone.utc)


class GlmDownloader(BaseDownloader):
    name = "glm"

    def _satellite(self, when: datetime) -> int:
        value = str(self.options.extra.get("satellite", "auto")).lower().replace("goes-", "")
        if value != "auto":
            return int(value)
        return 18 if self.region.centroid[0] <= -105 else (19 if when >= datetime(2025, 4, 7, 15, 10, tzinfo=timezone.utc) else 16)

    @staticmethod
    def _filesystem():
        import s3fs

        return s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": "us-east-1"})

    def _keys(self) -> list[tuple[datetime, str]]:
        fs = self._filesystem()
        found: list[tuple[datetime, str]] = []
        for hour in iter_hours(self.options.start, self.options.end):
            prefix = f"noaa-goes{self._satellite(hour)}/GLM-L2-LCFA/{hour:%Y}/{hour:%j}/{hour:%H}"
            try:
                keys = fs.ls(prefix, detail=False)
            except FileNotFoundError:
                continue
            for key in keys:
                stamp = glm_time(Path(key).name)
                if self.options.start <= stamp < self.options.end:
                    found.append((stamp, key))
        return sorted(set(found))

    def _read(self, raw: Path, stamp: datetime, source: str) -> pd.DataFrame:
        with xr.open_dataset(raw, engine="h5netcdf") as dataset:
            lon = np.asarray(dataset["flash_lon"]).ravel()
            lat = np.asarray(dataset["flash_lat"]).ravel()
            energy = np.asarray(dataset["flash_energy"]).ravel() if "flash_energy" in dataset else np.full(lon.size, np.nan)
            offset = np.asarray(dataset["flash_time_offset_of_first_event"]).ravel() if "flash_time_offset_of_first_event" in dataset else None
            ids = np.asarray(dataset["flash_id"]).ravel() if "flash_id" in dataset else np.arange(lon.size)
        keep = point_mask(self.region, lon, lat, self.options.exact_mask)
        if offset is None:
            times = np.full(lon.size, np.datetime64(stamp.replace(tzinfo=None)))
        elif np.issubdtype(offset.dtype, np.datetime64):
            times = offset
        elif np.issubdtype(offset.dtype, np.timedelta64):
            times = np.datetime64(stamp.replace(tzinfo=None)) + offset.astype("timedelta64[ns]")
        else:
            times = np.datetime64(stamp.replace(tzinfo=None)) + (offset * 1e9).astype("timedelta64[ns]")
        return pd.DataFrame({
            "time": pd.to_datetime(times[keep], utc=True), "lon": lon[keep], "lat": lat[keep],
            "flash_energy": energy[keep], "flash_id": ids[keep], "source_file": Path(source).name,
        })

    def estimate(self) -> dict[str, object]:
        keys = self._keys()
        estimated = len(keys) * 1_500_000
        return {"provider": self.name, "files": len(keys), "estimated_bytes": estimated, "estimated_size": human_bytes(estimated)}

    def download(self) -> RunManifest:
        manifest = self.new_manifest()
        fs = self._filesystem()
        grouped: dict[str, list[tuple[datetime, str]]] = {}
        for item in self._keys():
            grouped.setdefault(item[0].strftime("%Y%m%d"), []).append(item)
        if not grouped:
            manifest.add(ArtifactRecord(None, "NOAA S3 search", None, "no_data", message="No matching GLM objects"))
        for day, items in grouped.items():
            target = self.cropped_dir / f"glm_flashes_{day}.{self.options.output_format}"
            if self.should_skip(target):
                manifest.add(ArtifactRecord(day, f"{len(items)} GLM files", str(target), "skipped", target.stat().st_size))
                continue
            frames: list[pd.DataFrame] = []
            failures: list[str] = []
            for stamp, key in items:
                raw = self.raw_dir / Path(key).name
                try:
                    temporary = self.atomic_target(raw)
                    def fetch(key: str = key, temporary: Path = temporary) -> None:
                        fs.get(key, str(temporary))
                    self.retry(fetch, f"download {key}")
                    self.commit(temporary, raw)
                    frames.append(self._read(raw, stamp, key))
                    if not self.options.keep_raw:
                        raw.unlink(missing_ok=True)
                except Exception as exc:
                    failures.append(f"{Path(key).name}: {exc}")
            try:
                frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["time", "lon", "lat", "flash_energy", "flash_id", "source_file"])
                output = write_frame(frame, target, self.options.output_format)
                status = "failed" if failures and not frames else ("partial" if failures else "downloaded")
                manifest.add(ArtifactRecord(day, f"{len(items)} GLM files", str(output), status, output.stat().st_size, "; ".join(failures) or None))
            except Exception as exc:
                manifest.add(ArtifactRecord(day, f"{len(items)} GLM files", None, "failed", message=str(exc)))
        manifest.finish()
        manifest.write(self.manifest_path())
        return manifest
