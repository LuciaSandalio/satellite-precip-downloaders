from __future__ import annotations

import argparse
import json
import netrc
import os
import sys
from typing import Any, Sequence

from .base import DownloadOptions
from .config import SUPPORTED_SOURCES, enabled_sources, load_project
from .errors import SatPrecipError
from .geometry import load_region
from .output import human_bytes
from .providers import provider_class
from .validation import inventory, inventory_summary


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--shape", required=True, help="Shapefile, GeoPackage, or GeoJSON region")
    parser.add_argument("--layer", help="Optional vector layer name")
    parser.add_argument("--start", required=True, help="UTC ISO date/time; a date includes the full day")
    parser.add_argument("--end", required=True, help="UTC ISO date/time; a date includes the full day")
    parser.add_argument("--output", required=True, help="Output root directory")
    parser.add_argument("--format", choices=("parquet", "csv", "netcdf", "zarr"), default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--bbox-only", action="store_true", help="Crop to bbox without polygon point mask")


def _add_provider_options(parser: argparse.ArgumentParser, provider: str) -> None:
    if provider in {"goes", "glm"}:
        parser.add_argument("--satellite", default="auto", help="auto, 16, 17, 18, or 19")
    if provider == "goes":
        parser.add_argument("--product", default="ABI-L2-CMIPF")
        parser.add_argument("--channels", default="13", help="Comma-separated ABI channels")
    elif provider == "imerg":
        parser.add_argument("--short-name", default="GPM_3IMERGHH")
        parser.add_argument("--version", default="07")
        parser.add_argument("--variables", default="precipitation,precipitationCal")
        parser.add_argument("--login-strategy", choices=("auto", "environment", "netrc", "interactive", "all"), default="auto")
    elif provider == "gsmap":
        parser.add_argument("--host", default="hokusai.eorc.jaxa.jp")


def _default_format(provider: str) -> str:
    return "netcdf" if provider == "goes" else "parquet"


def _direct_downloader(provider: str, args: argparse.Namespace):
    reserved = {
        "command", "provider", "shape", "layer", "start", "end", "output", "format", "workers",
        "retries", "overwrite", "no_resume", "keep_raw", "bbox_only", "handler",
    }
    extra = {key: value for key, value in vars(args).items() if key not in reserved and value is not None}
    output_format = args.format or _default_format(provider)
    if provider == "goes" and output_format not in {"netcdf", "zarr"}:
        raise ValueError("GOES output format must be netcdf or zarr")
    if provider != "goes" and output_format not in {"parquet", "csv"}:
        raise ValueError(f"{provider} output format must be parquet or csv")
    region = load_region(args.shape, args.layer)
    options = DownloadOptions.create(
        args.start, args.end, args.output, workers=args.workers, retries=args.retries,
        resume=not args.no_resume, overwrite=args.overwrite, keep_raw=args.keep_raw,
        exact_mask=not args.bbox_only, output_format=output_format, extra=extra,
    )
    return provider_class(provider)(region, options)


def _project_downloader(config: dict[str, Any], name: str, source: dict[str, Any]):
    region_config = config["region"]
    region = load_region(region_config["shape"], region_config.get("layer"))
    output = config["output"]
    common = config.get("defaults", {})
    output_format = source.get("format", output.get("format", _default_format(name)))
    if name == "goes" and output_format not in {"netcdf", "zarr"}:
        raise ValueError("GOES output format must be netcdf or zarr")
    if name != "goes" and output_format not in {"parquet", "csv"}:
        raise ValueError(f"{name} output format must be parquet or csv")
    options = DownloadOptions.create(
        config["period"]["start"], config["period"]["end"], output["directory"],
        workers=int(source.get("workers", common.get("workers", 4))),
        retries=int(source.get("retries", common.get("retries", 3))),
        resume=bool(source.get("resume", common.get("resume", True))),
        overwrite=bool(source.get("overwrite", common.get("overwrite", False))),
        keep_raw=bool(source.get("keep_raw", output.get("keep_raw", False))),
        exact_mask=bool(source.get("exact_mask", region_config.get("exact_mask", True))),
        output_format=output_format,
        extra={key: value for key, value in source.items() if key not in {"format", "workers", "retries", "resume", "overwrite", "keep_raw", "exact_mask"}},
    )
    return provider_class(name)(region, options)


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _download(args: argparse.Namespace) -> int:
    downloader = _direct_downloader(args.provider, args)
    manifest = downloader.download()
    _print({"manifest": str(downloader.manifest_path()), "summary": manifest.summary()})
    summary = manifest.summary()
    return 2 if summary.get("failed", 0) or summary.get("partial", 0) else 0


def _download_all(args: argparse.Namespace) -> int:
    config = load_project(args.project)
    summaries: dict[str, Any] = {}
    exit_code = 0
    for name, source in enabled_sources(config):
        try:
            downloader = _project_downloader(config, name, source)
            manifest = downloader.download()
            summaries[name] = {"manifest": str(downloader.manifest_path()), "summary": manifest.summary()}
            if manifest.summary().get("failed", 0) or manifest.summary().get("partial", 0):
                exit_code = 2
        except Exception as exc:
            summaries[name] = {"error": str(exc)}
            exit_code = 2
    _print(summaries)
    return exit_code


def _estimate(args: argparse.Namespace) -> int:
    config = load_project(args.project)
    results = []
    errors: dict[str, str] = {}
    for name, source in enabled_sources(config):
        try:
            results.append(_project_downloader(config, name, source).estimate())
        except Exception as exc:
            errors[name] = str(exc)
    total = sum(int(item.get("estimated_bytes", 0)) for item in results)
    _print({"providers": results, "errors": errors, "estimated_total_bytes": total, "estimated_total_size": human_bytes(total)})
    return 2 if errors else 0


def _inventory(args: argparse.Namespace) -> int:
    report = inventory_summary(inventory(args.path, checksum=args.checksum))
    _print(report)
    return 2 if report["invalid"] else 0


def _credentials(_: argparse.Namespace) -> int:
    earthdata_netrc = False
    try:
        earthdata_netrc = netrc.netrc().authenticators("urs.earthdata.nasa.gov") is not None
    except (FileNotFoundError, netrc.NetrcParseError, OSError):
        pass
    state = {
        "NASA_Earthdata": bool(os.environ.get("EARTHDATA_TOKEN")) or bool(os.environ.get("EARTHDATA_USERNAME") and os.environ.get("EARTHDATA_PASSWORD")) or earthdata_netrc,
        "GSMaP": bool(os.environ.get("GSMAP_USER") and os.environ.get("GSMAP_PASSWORD")),
        "NOAA_GOES": "not required (anonymous public S3)",
        "CMORPH": "not required (public HTTPS)",
    }
    _print(state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="satprecip", description="Reusable satellite precipitation downloaders")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="Download one provider")
    providers = download.add_subparsers(dest="provider", required=True)
    for name in SUPPORTED_SOURCES:
        child = providers.add_parser(name)
        _add_common(child)
        _add_provider_options(child, name)
        child.set_defaults(handler=_download)
    all_parser = commands.add_parser("download-all", help="Run all enabled sources in a project YAML")
    all_parser.add_argument("project")
    all_parser.set_defaults(handler=_download_all)
    estimate = commands.add_parser("estimate", help="Estimate download size for a project YAML")
    estimate.add_argument("project")
    estimate.set_defaults(handler=_estimate)
    inspect = commands.add_parser("inventory", help="Validate and catalogue existing files")
    inspect.add_argument("path")
    inspect.add_argument("--checksum", action="store_true")
    inspect.set_defaults(handler=_inventory)
    credentials = commands.add_parser("credentials-check", help="Report credential availability without showing secrets")
    credentials.set_defaults(handler=_credentials)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.handler(args))
    except (SatPrecipError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2


def provider_main(provider: str) -> int:
    return main(["download", provider, *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
