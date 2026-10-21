# Satellite precipitation downloaders

A reusable command-line package for downloading and spatially subsetting GOES ABI,
GOES GLM, NASA IMERG, NOAA CMORPH, and JAXA GSMaP data. Every run accepts a vector
study area and UTC period, supports restart/resume, and writes a JSON manifest.

## What each downloader creates

| Source | Remote service | Cropped output |
|---|---|---|
| GOES ABI | NOAA public S3 | Native grid, NetCDF or Zarr |
| GOES GLM | NOAA public S3 | Daily flash events, Parquet or CSV |
| IMERG | NASA Earthdata | One point table per granule, Parquet or CSV |
| CMORPH | NOAA CPC HTTPS | Daily 30-minute point tables, Parquet or CSV |
| GSMaP | JAXA FTP | Daily hourly point tables, Parquet or CSV |

The default exact mode first crops to the geometry bounding box and then removes
pixels/events outside the polygon. `--bbox-only` skips the second step.

## Installation

Use a fresh virtual environment from this directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For Zarr output, install the optional dependency with `pip install -e '.[zarr]'`.

## Credentials

GOES and CMORPH need no credentials. Keep credentials outside project files:

```bash
export EARTHDATA_USERNAME='your_nasa_user'
export EARTHDATA_PASSWORD='your_nasa_password'
# Alternatively: export EARTHDATA_TOKEN='your_nasa_token'
export GSMAP_USER='your_jaxa_user'
export GSMAP_PASSWORD='your_jaxa_password'
satprecip credentials-check
```

NASA Earthdata may alternatively use a standard `~/.netrc`. The program never
writes passwords to manifests. `.env.example` contains variable names only.

## One provider

```bash
satprecip download goes \
  --shape ../shapes/study_area.gpkg \
  --start 2025-03-01 --end 2025-03-02 \
  --output ./downloads --channels 8,13 --satellite auto

satprecip download glm --shape area.geojson --start 2025-03-01 --end 2025-03-01 --output ./downloads
satprecip download imerg --shape area.geojson --start 2025-03-01 --end 2025-03-01 --output ./downloads
satprecip download cmorph --shape area.geojson --start 2025-03-01 --end 2025-03-01 --output ./downloads
satprecip download gsmap --shape area.geojson --start 2025-03-01 --end 2025-03-01 --output ./downloads
```

The five `download_*.py` files are standalone entry points with the same options,
for example `python download_gsmap.py --help`.

`--satellite auto` uses GOES-West for study-area centroids west of 105°W. For
GOES-East it uses GOES-16 before the operational transition at 15:10 UTC on
2025-04-07 and GOES-19 thereafter. Specify the satellite explicitly when reproducibility or a
non-operational platform matters.

## Run a project

Copy `project.example.yml`, edit its region, period, output directory, and enabled
sources, then run:

```bash
satprecip estimate project.yml
satprecip download-all project.yml
```

Set a source to `false` to disable it. Size estimates are approximate for services
that do not expose sizes cheaply; GOES/GLM estimates first list matching objects.

## Outputs, resuming, and validation

The output root contains `raw/<provider>`, `cropped/<provider>`, and `manifests`.
Raw downloads are removed after successful cropping unless `--keep-raw` is set.
Existing non-empty cropped files are skipped by default. Use `--overwrite` to
replace them or `--no-resume` to process them again.

```bash
satprecip inventory ./downloads
satprecip inventory ./downloads --checksum
```

Inventory validates gzip streams, Parquet metadata, NetCDF/HDF5 structure, and
JSON syntax. A checksum inventory reads every byte and can be slow.

## Notes

- Dates and times are interpreted as UTC; the interval end is exclusive for an
  exact timestamp, while a date-only end includes the full calendar day.
- IMERG searches `GPM_3IMERGHH` version 07 by default. Both are configurable.
- CMORPH uses the V0.x RAW 8 km, 30-minute archive.
- GSMaP uses the standard v8 hourly MVK product.
- Table outputs contain `time`, `lon`, `lat`, and provider variables. Missing data
  stay as NaN or the documented source fill value where applicable.
