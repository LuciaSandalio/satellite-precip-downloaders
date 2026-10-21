from pathlib import Path

import numpy as np
from shapely.geometry import box

from satprecip.geometry import Region, point_mask
from satprecip.providers._grid import crop_regular_grid


def region():
    geometry = box(-1, -1, 1, 1)
    return Region(Path("test.geojson"), geometry, geometry.bounds, "abc")


def test_exact_point_mask():
    lons = np.array([-2.0, 0.0, 1.0, 2.0])
    lats = np.zeros(4)
    assert point_mask(region(), lons, lats, True).tolist() == [False, True, True, False]


def test_regular_grid_crop_converts_360_longitudes():
    geometry = box(-2, -1, 0, 1)
    area = Region(Path("test.geojson"), geometry, geometry.bounds, "abc")
    lats = np.array([-1.0, 0.0, 1.0])
    lons = np.array([0.0, 1.0, 358.0, 359.0])
    values = np.arange(12).reshape(3, 4)
    out_lon, out_lat, out_value = crop_regular_grid(values, lats, lons, area, True)
    assert set(out_lon.tolist()) == {-2.0, -1.0, 0.0}
    assert len(out_lon) == len(out_lat) == len(out_value) == 9
