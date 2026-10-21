from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError


SUPPORTED_SOURCES = ("goes", "glm", "imerg", "cmorph", "gsmap")


def load_project(path: str | Path) -> dict[str, Any]:
    project_path = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(f"Could not read project configuration {project_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("The project YAML root must be a mapping")
    for key in ("region", "period", "sources", "output"):
        if key not in raw:
            raise ConfigurationError(f"Missing required project section: {key}")
    region = raw["region"]
    period = raw["period"]
    sources = raw["sources"]
    output = raw["output"]
    if not isinstance(region, dict) or "shape" not in region:
        raise ConfigurationError("region.shape is required")
    if not isinstance(period, dict) or not {"start", "end"} <= set(period):
        raise ConfigurationError("period.start and period.end are required")
    if not isinstance(sources, dict):
        raise ConfigurationError("sources must be a mapping")
    unknown = set(sources) - set(SUPPORTED_SOURCES)
    if unknown:
        raise ConfigurationError(f"Unsupported sources: {', '.join(sorted(unknown))}")
    if not isinstance(output, dict) or "directory" not in output:
        raise ConfigurationError("output.directory is required")

    config = deepcopy(raw)
    base = project_path.parent
    shape = Path(config["region"]["shape"]).expanduser()
    if not shape.is_absolute():
        shape = base / shape
    config["region"]["shape"] = str(shape.resolve())
    out = Path(config["output"]["directory"]).expanduser()
    if not out.is_absolute():
        out = base / out
    config["output"]["directory"] = str(out.resolve())
    config["project_file"] = str(project_path)
    return config


def enabled_sources(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for name in SUPPORTED_SOURCES:
        value = config["sources"].get(name)
        if value is False or value is None:
            continue
        if value is True:
            value = {}
        if not isinstance(value, dict):
            raise ConfigurationError(f"sources.{name} must be a mapping, true, or false")
        result.append((name, value))
    return result

