from __future__ import annotations

import re
import netrc
import os
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

from ..base import BaseDownloader
from ..dates import iso_utc
from ..geometry import point_mask
from ..errors import ConfigurationError
from ..manifest import ArtifactRecord, RunManifest
from ..output import human_bytes, write_frame


_STAMP_RE = re.compile(r"\.(\d{8})-S(\d{6})")


def imerg_time(name: str) -> datetime:
    match = _STAMP_RE.search(name)
    if not match:
        raise ValueError(f"No IMERG timestamp in {name}")
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


class ImergDownloader(BaseDownloader):
    name = "imerg"

    def _granules(self):
        import earthaccess

        west, south, east, north = self.region.bounds
        return earthaccess.search_data(
            short_name=str(self.options.extra.get("short_name", "GPM_3IMERGHH")),
            version=str(self.options.extra.get("version", "07")),
            temporal=(iso_utc(self.options.start), iso_utc(self.options.end)),
            bounding_box=(west, south, east, north),
        )

    def _login_strategy(self) -> str:
        requested = str(self.options.extra.get("login_strategy", "auto"))
        if requested != "auto":
            return requested
        if os.environ.get("EARTHDATA_TOKEN") or (
            os.environ.get("EARTHDATA_USERNAME") and os.environ.get("EARTHDATA_PASSWORD")
        ):
            return "environment"
        try:
            if netrc.netrc().authenticators("urs.earthdata.nasa.gov") is not None:
                return "netrc"
        except (FileNotFoundError, netrc.NetrcParseError, OSError):
            pass
        raise ConfigurationError(
            "IMERG requires EARTHDATA_TOKEN, EARTHDATA_USERNAME/EARTHDATA_PASSWORD, or a NASA entry in ~/.netrc"
        )

    @staticmethod
    def _name(granule) -> str:
        for attribute in ("data_links",):
            method = getattr(granule, attribute, None)
            if callable(method):
                links = method()
                if links:
                    return Path(str(links[0]).split("?")[0]).name
        return Path(str(granule)).name

    def _read(self, source: Path, stamp: datetime, source_name: str) -> pd.DataFrame:
        requested = self.options.extra.get("variables", ["precipitation", "precipitationCal"])
        if isinstance(requested, str):
            requested = [part.strip() for part in requested.split(",") if part.strip()]
        with h5py.File(source, "r") as handle:
            grid = handle["Grid"]
            lon = np.asarray(grid["lon"])
            lat = np.asarray(grid["lat"])
            west, south, east, north = self.region.bounds
            lon_idx = np.flatnonzero((lon >= west) & (lon <= east))
            lat_idx = np.flatnonzero((lat >= south) & (lat <= north))
            if not len(lon_idx) or not len(lat_idx):
                return pd.DataFrame(columns=["time", "lon", "lat", *requested, "source_file"])
            lon_slice = slice(int(lon_idx.min()), int(lon_idx.max()) + 1)
            lat_slice = slice(int(lat_idx.min()), int(lat_idx.max()) + 1)
            local_lon, local_lat = lon[lon_slice], lat[lat_slice]
            xx, yy = np.meshgrid(local_lon, local_lat)
            keep = point_mask(self.region, xx, yy, self.options.exact_mask)
            frame = pd.DataFrame({"time": stamp, "lon": xx[keep], "lat": yy[keep]})
            for variable in requested:
                if variable not in grid:
                    continue
                source_data = grid[variable]
                shape = source_data.shape
                if len(shape) == 3 and shape[0] == 1 and shape[1:] == (lon.size, lat.size):
                    data = np.asarray(source_data[0, lon_slice, lat_slice]).T
                elif len(shape) == 3 and shape[0] == 1 and shape[1:] == (lat.size, lon.size):
                    data = np.asarray(source_data[0, lat_slice, lon_slice])
                elif shape == (lon.size, lat.size):
                    data = np.asarray(source_data[lon_slice, lat_slice]).T
                elif shape == (lat.size, lon.size):
                    data = np.asarray(source_data[lat_slice, lon_slice])
                else:
                    raise ValueError(f"IMERG variable {variable} has unexpected shape {shape}")
                if data.shape != keep.shape:
                    raise ValueError(f"IMERG variable {variable} has unexpected shape {data.shape}")
                fill = source_data.attrs.get("_FillValue")
                values = data.astype("float32", copy=False)
                if fill is not None:
                    values = np.where(values == fill, np.nan, values)
                frame[variable] = values[keep]
        frame["source_file"] = source_name
        return frame

    def estimate(self) -> dict[str, object]:
        granules = self._granules()
        estimated = len(granules) * 8_000_000
        return {"provider": self.name, "files": len(granules), "estimated_bytes": estimated, "estimated_size": human_bytes(estimated)}

    def download(self) -> RunManifest:
        import earthaccess

        manifest = self.new_manifest()
        earthaccess.login(strategy=self._login_strategy(), persist=False)
        granules = self._granules()
        matched = 0
        for granule in granules:
            name = self._name(granule)
            try:
                stamp = imerg_time(name)
            except ValueError:
                stamp = self.options.start
            if not (self.options.start <= stamp < self.options.end):
                continue
            matched += 1
            target = self.cropped_dir / f"imerg_{stamp:%Y%m%dT%H%M%S}.{self.options.output_format}"
            if self.should_skip(target):
                manifest.add(ArtifactRecord(iso_utc(stamp), name, str(target), "skipped", target.stat().st_size))
                continue
            try:
                paths = self.retry(lambda: earthaccess.download([granule], local_path=str(self.raw_dir)), f"download {name}")
                if not paths:
                    raise RuntimeError("earthaccess returned no downloaded path")
                raw = Path(paths[0])
                frame = self._read(raw, stamp, name)
                output = write_frame(frame, target, self.options.output_format)
                manifest.add(ArtifactRecord(iso_utc(stamp), name, str(output), "downloaded", output.stat().st_size))
                if not self.options.keep_raw:
                    raw.unlink(missing_ok=True)
            except Exception as exc:
                manifest.add(ArtifactRecord(iso_utc(stamp), name, None, "failed", message=str(exc)))
        if not matched:
            manifest.add(ArtifactRecord(None, "NASA CMR search", None, "no_data", message="No matching IMERG granules"))
        manifest.finish()
        manifest.write(self.manifest_path())
        return manifest
