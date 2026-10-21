from satprecip.base import BaseDownloader, DownloadOptions
from satprecip.geometry import Region
from pathlib import Path
from shapely.geometry import box


class DummyDownloader(BaseDownloader):
    name = "dummy"


def test_manifest_redacts_secret_options(tmp_path):
    geometry = box(0, 0, 1, 1)
    region = Region(Path("area.geojson"), geometry, geometry.bounds, "abc")
    options = DownloadOptions.create(
        "2025-01-01", "2025-01-01", tmp_path,
        extra={"api_token": "do-not-save", "product": "example"},
    )
    metadata = DummyDownloader(region, options).request_metadata()
    assert metadata["api_token"] == "<redacted>"
    assert metadata["product"] == "example"
