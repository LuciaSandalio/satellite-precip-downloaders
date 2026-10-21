from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

from ..base import BaseDownloader
from ..dates import iter_hours, iso_utc
from ..geometry import point_mask
from ..manifest import ArtifactRecord, RunManifest
from ..output import human_bytes, write_dataset


_START_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})")


def goes_time(name: str) -> datetime:
    match = _START_RE.search(name)
    if not match:
        raise ValueError(f"No GOES timestamp in {name}")
    year, day, hour, minute, second = map(int, match.groups())
    return datetime.strptime(f"{year} {day:03d} {hour:02d}{minute:02d}{second:02d}", "%Y %j %H%M%S").replace(
        tzinfo=timezone.utc
    )


class GoesDownloader(BaseDownloader):
    name = "goes"

    def _satellite(self, when: datetime) -> int:
        selected = str(self.options.extra.get("satellite", "auto")).lower().replace("goes-", "")
        if selected != "auto":
            value = int(selected)
            if value not in (16, 17, 18, 19):
                raise ValueError("GOES satellite must be auto, 16, 17, 18, or 19")
            return value
        lon, _ = self.region.centroid
        if lon <= -105:
            return 18
        transition = datetime(2025, 4, 7, 15, 10, tzinfo=timezone.utc)
        return 19 if when >= transition else 16

    def _channels(self) -> list[int]:
        value = self.options.extra.get("channels", [13])
        if isinstance(value, (str, int)):
            value = [int(part) for part in str(value).split(",")]
        channels = sorted({int(item) for item in value})
        if not channels or any(item < 1 or item > 16 for item in channels):
            raise ValueError("ABI channels must be between 1 and 16")
        return channels

    def _filesystem(self):
        import s3fs

        return s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": "us-east-1"})

    def _keys(self) -> list[tuple[datetime, str]]:
        fs = self._filesystem()
        product = str(self.options.extra.get("product", "ABI-L2-CMIPF"))
        channels = {f"C{channel:02d}" for channel in self._channels()}
        found: list[tuple[datetime, str]] = []
        for hour in iter_hours(self.options.start, self.options.end):
            satellite = self._satellite(hour)
            prefix = f"noaa-goes{satellite}/{product}/{hour:%Y}/{hour:%j}/{hour:%H}"
            try:
                keys = fs.ls(prefix, detail=False)
            except FileNotFoundError:
                continue
            for key in keys:
                name = Path(key).name
                if not any(re.search(rf"M\d{channel}", name) for channel in channels):
                    continue
                stamp = goes_time(name)
                if self.options.start <= stamp < self.options.end:
                    found.append((stamp, key))
        return sorted(set(found))

    @staticmethod
    def _projection_name(dataset: xr.Dataset) -> str:
        for name, variable in dataset.variables.items():
            if variable.attrs.get("grid_mapping_name") == "geostationary":
                return str(name)
        raise ValueError("GOES projection variable was not found")

    def _crop(self, source: Path, target: Path) -> Path:
        with xr.open_dataset(source, engine="h5netcdf") as opened:
            dataset = opened.load()
        projection_name = self._projection_name(dataset)
        attrs = dataset[projection_name].attrs
        crs = CRS.from_cf(attrs)
        forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        west, south, east, north = self.region.bounds
        boundary = np.asarray(self.region.geometry.envelope.exterior.coords)
        bx, by = forward.transform(boundary[:, 0], boundary[:, 1])
        height = float(attrs["perspective_point_height"])
        x_min, x_max = np.nanmin(bx / height), np.nanmax(bx / height)
        y_min, y_max = np.nanmin(by / height), np.nanmax(by / height)
        x_values, y_values = np.asarray(dataset.x), np.asarray(dataset.y)
        xi = np.flatnonzero((x_values >= x_min) & (x_values <= x_max))
        yi = np.flatnonzero((y_values >= y_min) & (y_values <= y_max))
        if not len(xi) or not len(yi):
            raise ValueError(f"Region {west, south, east, north} is outside the GOES scene")
        cropped = dataset.isel(x=slice(xi.min(), xi.max() + 1), y=slice(yi.min(), yi.max() + 1))
        if self.options.exact_mask:
            inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            xx, yy = np.meshgrid(np.asarray(cropped.x) * height, np.asarray(cropped.y) * height)
            lon, lat = inverse.transform(xx, yy)
            mask = point_mask(self.region, lon, lat, True)
            mask_array = xr.DataArray(mask, dims=("y", "x"))
            for variable_name in list(cropped.data_vars):
                variable = cropped[variable_name]
                if {"x", "y"}.issubset(variable.dims):
                    cropped[variable_name] = variable.where(mask_array)
            cropped = cropped.assign_coords(longitude=(("y", "x"), lon), latitude=(("y", "x"), lat))
        cropped.attrs.update(region_geometry_sha256=self.region.fingerprint)
        return write_dataset(cropped, target, self.options.output_format)

    def estimate(self) -> dict[str, object]:
        keys = self._keys()
        estimated = len(keys) * 3_000_000
        return {"provider": self.name, "files": len(keys), "estimated_bytes": estimated, "estimated_size": human_bytes(estimated)}

    def download(self) -> RunManifest:
        manifest = self.new_manifest()
        fs = self._filesystem()
        keys = self._keys()
        if not keys:
            manifest.add(ArtifactRecord(None, "NOAA S3 search", None, "no_data", message="No matching GOES ABI objects"))
        for stamp, key in keys:
            raw = self.raw_dir / Path(key).name
            stem = raw.name.rsplit(".nc", 1)[0]
            suffix = ".zarr" if self.options.output_format == "zarr" else ".nc"
            expected = self.cropped_dir / f"{stem}{suffix}"
            if self.should_skip(expected):
                output = expected
                manifest.add(ArtifactRecord(iso_utc(stamp), key, str(output), "skipped", output.stat().st_size))
                continue
            try:
                temporary = self.atomic_target(raw)
                self.retry(lambda: fs.get(key, str(temporary)), f"download {key}")
                self.commit(temporary, raw)
                output = self._crop(raw, expected)
                manifest.add(ArtifactRecord(iso_utc(stamp), key, str(output), "downloaded", output.stat().st_size))
                if not self.options.keep_raw:
                    raw.unlink(missing_ok=True)
            except Exception as exc:
                manifest.add(ArtifactRecord(iso_utc(stamp), key, None, "failed", message=str(exc)))
        manifest.finish()
        manifest.write(self.manifest_path())
        return manifest
