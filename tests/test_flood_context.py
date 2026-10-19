"""Use acquired source records for optional integration checks; invent no city assets."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dynacity.flood_context import load_flood_context, LAYERS
from dynacity.service import create_app


@pytest.fixture
def acquired_package(tmp_path):
    value = os.environ.get("DYNACITY_TEST_FLOOD_HANDOFF")
    if not value:
        pytest.skip("private acquired survey package not configured")
    original = Path(value)
    handoff = json.loads(original.read_text())
    # Copies of genuine source bytes, kept in temporary storage, never Git.
    for layer in handoff["context_layers"]:
        old = (original.parent / layer["path"]).resolve()
        new = tmp_path / old.name
        new.write_bytes(old.read_bytes())
        for entry in handoff["files"]:
            if (original.parent / entry["path"]).resolve() == old:
                entry["path"] = str(new)
        layer["path"] = str(new)
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(handoff))
    return path


def rewrite_layer(path, name, mutate):
    handoff = json.loads(path.read_text())
    layer = next(r for r in handoff["context_layers"] if Path(r["path"]).name == name)
    file = Path(layer["path"])
    data = json.loads(file.read_text())
    mutate(data)
    file.write_text(json.dumps(data))
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    layer["sha256"] = digest
    next(r for r in handoff["files"] if r["path"] == str(file))["sha256"] = digest
    path.write_text(json.dumps(handoff))


def test_real_package_preserves_observations_and_unknowns(acquired_package):
    result = load_flood_context(acquired_package)
    assert result["simulation_ready"] is False
    assert all(not r["hydraulic_asset"] for r in result["points"] + result["streets"])
    assert all(r["survey_year"] is None for r in result["points"])
    assert all(len(r["position"]) == 2 for r in result["points"])
    assert all(len(p) == 2 for r in result["streets"] for line in r["paths"] for p in line)
    raw = json.loads((acquired_package.parent / "unhabitat-beirut-street-condition.json").read_text())
    counts = Counter(f["attributes"]["StormW_Dra"].strip() or None for f in raw["features"])
    assert result["counts"]["conditions"]["unknown"] == counts[None]
    assert result["counts"]["conditions"]["blocked"] == counts["Blocked drains"]
    assert result["counts"]["street_records"] == len(raw["features"])
    assert all(status == "unavailable" for status in result["model_inputs"].values())
    response = json.dumps(result)
    assert str(acquired_package.parent) not in response
    assert '"z"' not in response and '"depth_m"' not in response


@pytest.mark.parametrize("mutation", ["checksum", "crs", "partial", "count", "missing_geometry", "duplicate_id"])
def test_incomplete_or_modified_survey_package_is_rejected(acquired_package, mutation):
    name = "unhabitat-beirut-street-condition.json"
    file = acquired_package.parent / name
    if mutation == "checksum":
        file.write_text(file.read_text() + " ")
    else:
        def mutate(data):
            if mutation == "crs": data["spatialReference"] = {"wkid":4326}
            if mutation == "partial": data["exceededTransferLimit"] = True
            if mutation == "count": data["features"].pop()
            if mutation == "missing_geometry": data["features"][0]["geometry"]["paths"] = []
            if mutation == "duplicate_id": data["features"][1]["attributes"]["FID"] = data["features"][0]["attributes"]["FID"]
        rewrite_layer(acquired_package, name, mutate)
    with pytest.raises(ValueError): load_flood_context(acquired_package)


def test_api_serves_real_context_locally_without_enabling_simulation(acquired_package):
    context = load_flood_context(acquired_package)
    client = TestClient(create_app(object(), flood_context=context))
    assert client.get("/v1/flood/context").json() == context
    assert client.get("/v1/flood/pilot").json()["status"] == "unavailable"
    remote = TestClient(create_app(object(), flood_context=context), client=("192.0.2.1", 1234))
    assert remote.get("/v1/flood/context").status_code == 403


def test_api_without_package_explicitly_stays_unavailable():
    result = TestClient(create_app(object())).get("/v1/flood/context").json()
    assert result["status"] == "unavailable"
    assert result["simulation_ready"] is False


def test_missing_context_layers_cannot_become_empty_success(tmp_path):
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps({"schema":"dynacity.real-input-acquisition.v1","context_layers":[]}))
    with pytest.raises(ValueError,match="exactly"): load_flood_context(path)
