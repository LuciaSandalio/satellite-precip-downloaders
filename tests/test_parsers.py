from satprecip.providers.glm import glm_time
from satprecip.providers.goes import goes_time
from satprecip.providers.gsmap import GsmapDownloader
from satprecip.providers.imerg import imerg_time


def test_noaa_timestamps():
    name = "OR_ABI-L2-CMIPF-M6C13_G16_s20250600000123_e.nc"
    assert goes_time(name).isoformat().startswith("2025-03-01T00:00:12")
    glm = "OR_GLM-L2-LCFA_G16_s20250600000000_e.nc"
    assert glm_time(glm).isoformat().startswith("2025-03-01T00:00:00")


def test_precipitation_timestamps():
    assert imerg_time("3B-HHR.MS.MRG.3IMERG.20250301-S003000-E005959.h5").hour == 0
    stamp = GsmapDownloader._timestamp("gsmap_mvk.20250301.1300.v8.0000.1.dat.gz")
    assert stamp is not None and stamp.hour == 13 and stamp.day == 1
