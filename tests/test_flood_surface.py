"""Synthetic import fixtures, not hydraulic-engine validation."""
import copy
import hashlib

import numpy as np
import pytest

from dynacity.flood_surface import (
    HydraulicOutput, import_depth_rasters, load_hydraulic_output, solver_runtime_status,
)


def metadata():
    return {
        "schema": "dynacity.hydraulic-grid.v1", "output_kind": "precomputed_hydraulic",
        "crs": "EPSG:32636", "horizontal_units": "m", "depth_units": "m",
        "grid": [700000, 3750000, 2, 3], "vertical_datum": "unknown",
        "terrain_id": "synthetic-flat", "terrain_provenance": "Synthetic fixture; no Beirut survey",
        "timestamps": ["2026-10-16T00:00:00Z", "2026-10-16T00:01:00Z"], "nodata": -9999,
        "model": {"name": "synthetic fixture", "version": "1", "run_id": "test-1",
                  "source": "unit-test analytical storage arrays", "evidence": "synthetic"},
        "forcing": {key: {"status": "none", "description": "synthetic closed-domain fixture"}
                    for key in ("rainfall", "boundary", "drainage_exchange")},
    }


def test_storage_nodata_velocity_and_roundtrip(tmp_path):
    depth = np.array([[[0, 1], [2, -9999]], [[1, 2], [3, -9999]]])
    m = metadata()
    m["velocity_units"] = "m/s"
    velocity = np.ones((*depth.shape, 2))
    output = HydraulicOutput(depth, m, velocity)
    assert output.volumes_m3() == pytest.approx([18, 36])
    assert output.mass_balance_residual_m3([18]) == pytest.approx([0])
    assert output.mass_balance_residual_m3([0]) == pytest.approx([18])
    assert output.valid[0].tolist() == [[True, True], [True, False]]
    assert np.isnan(output.velocity_m_s[:, 1, 1]).all()
    path = tmp_path / "private-result.npz"
    output.save(path)
    restored = load_hydraulic_output(path)
    np.testing.assert_equal(restored.depth_m, output.depth_m)
    np.testing.assert_equal(restored.velocity_m_s, output.velocity_m_s)
    assert restored.metadata["artifact_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert m == metadata() | {"velocity_units": "m/s"}  # input untouched


def test_missing_velocity_is_unknown_and_source_warning_preserved():
    m = metadata()
    m["warnings"] = ["Not calibrated"]
    m["forcing"]["boundary"]["status"] = "unknown"
    output = HydraulicOutput(np.zeros((2, 1, 1)), m)
    assert output.velocity_m_s is None
    assert "Not calibrated" in output.metadata["warnings"]
    assert any("boundary forcing is unknown" in w for w in output.metadata["warnings"])
    assert any("Velocity unavailable" in w for w in output.metadata["warnings"])
    assert any("Vertical datum unknown" in w for w in output.metadata["warnings"])


@pytest.mark.parametrize("key,value", [
    ("output_kind", "visual_flood"), ("schema", "bogus"), ("grid", [0, 0, -1, 1]),
    ("grid", [0, 0, 1]), ("crs", ""), ("terrain_id", ""), ("depth_units", "ft"),
    ("timestamps", ["2026-10-16", "2026-10-17"]),
    ("timestamps", ["2026-10-16T00:00:00Z"] * 2), ("nodata", 0),
])
def test_rejects_ambiguous_metadata(key, value):
    m = metadata()
    m[key] = value
    with pytest.raises(ValueError):
        HydraulicOutput(np.zeros((2, 1, 1)), m)


@pytest.mark.parametrize("depth", [np.zeros((2, 2)), np.empty((0, 2, 2)),
    np.full((2, 1, 1), -1), np.full((2, 1, 1), np.inf), np.full((2, 1, 1), np.nan)])
def test_invalid_depth(depth):
    with pytest.raises(ValueError):
        HydraulicOutput(depth, metadata())


def test_velocity_and_forcing_contracts():
    m = metadata()
    m["velocity_units"] = "m/s"
    with pytest.raises(ValueError, match="velocity requires"):
        HydraulicOutput(np.zeros((2, 1, 1)), m, np.zeros((2, 1, 1)))
    m["forcing"]["rainfall"] = {"status": "supplied", "description": "rain", "source": "rain.csv", "units": "mm/h"}
    with pytest.raises(ValueError, match="units"):
        HydraulicOutput(np.zeros((2, 1, 1)), m)


def test_mass_balance_refuses_changing_coverage_and_invalid_net():
    depth = np.zeros((2, 2, 2))
    output = HydraulicOutput(depth, metadata())
    for net in ([], [float("nan")], [1, 2]):
        with pytest.raises(ValueError, match="net volume"):
            output.mass_balance_residual_m3(net)
    depth[1, 0, 0] = np.nan
    with pytest.raises(ValueError, match="constant validity"):
        HydraulicOutput(depth, metadata()).mass_balance_residual_m3([0])


def raster(path, values, transform=None, crs="EPSG:32636"):
    rio = pytest.importorskip("rasterio")
    transform = transform or rio.transform.from_origin(700000, 3750000, 2, 3)
    with rio.open(path, "w", driver="GTiff", height=2, width=2, count=1,
                  dtype="float32", transform=transform, crs=crs, nodata=-9999) as dst:
        dst.write(np.array(values, dtype="float32"), 1)


def test_geotiff_series_and_grid_boundary_preservation(tmp_path):
    paths = [tmp_path / "t0.tif", tmp_path / "t1.tif"]
    for path in paths:
        raster(path, [[0, -9999], [1, 2]])
    output = import_depth_rasters(paths, metadata())
    assert output.depth_m[:, 0, 0].tolist() == [0, 0]  # dry edge stays dry
    assert np.isnan(output.depth_m[:, 0, 1]).all()  # missing edge stays missing
    assert output.volumes_m3() == pytest.approx([18, 18])
    assert output.mass_balance_residual_m3([0]) == pytest.approx([0])
    assert len(output.metadata["imported_rasters"]) == 2
    import rasterio
    raster(paths[1], [[0, 0], [0, 0]], rasterio.transform.from_origin(700001, 3750000, 2, 3))
    with pytest.raises(ValueError, match="identical"):
        import_depth_rasters(paths, metadata())


def test_raster_metadata_mismatch_and_geographic_crs(tmp_path):
    path = tmp_path / "t.tif"
    raster(path, [[0, 0], [0, 0]])
    m = metadata()
    m["grid"][0] += 1
    with pytest.raises(ValueError, match="grid disagrees"):
        import_depth_rasters([path], m)
    raster(path, [[0, 0], [0, 0]], crs="EPSG:4326")
    with pytest.raises(ValueError, match="projected metre"):
        import_depth_rasters([path], metadata())


def test_runtime_distinguishes_implementation_from_real_data_readiness():
    status = solver_runtime_status()
    assert status["engine"] == "ANUGA"
    assert status["runner_implemented"] is True
    assert status["real_data_ready"] is False
    assert status["reason"]
