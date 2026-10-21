import json

from satprecip.config import enabled_sources, load_project
from satprecip.validation import inventory_summary, validate_file


def test_project_paths_are_relative_to_yaml(tmp_path):
    shape = tmp_path / "area.geojson"
    shape.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    project = tmp_path / "project.yml"
    project.write_text(
        "region:\n  shape: area.geojson\nperiod:\n  start: '2025-01-01'\n  end: '2025-01-02'\n"
        "output:\n  directory: out\nsources:\n  goes: true\n  gsmap: false\n"
    )
    config = load_project(project)
    assert config["region"]["shape"] == str(shape)
    assert config["output"]["directory"] == str(tmp_path / "out")
    assert enabled_sources(config) == [("goes", {})]


def test_json_validation(tmp_path):
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    valid.write_text('{"ok": true}')
    invalid.write_text("{")
    assert validate_file(valid).valid
    results = [validate_file(valid), validate_file(invalid)]
    summary = inventory_summary(results)
    assert summary["valid"] == 1
    assert summary["invalid"] == 1
