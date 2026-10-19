"""Synthetic terrain fixtures only; no private survey data or network access."""

import numpy as np
import pytest

rio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin

from dynacity.flood_terrain import TerrainGrid, TerrainProvenance, load_legacy_copernicus, load_terrain_raster


def provenance(**overrides):
    fields = dict(source="synthetic survey fixture", kind="bare_earth",
                  vertical_reference="synthetic local benchmark", evidence="generated test raster",
                  vertical_accuracy_m=0.05)
    return TerrainProvenance(**(fields | overrides))


def raster(tmp_path, values=None, *, crs="EPSG:32636", transform=None, nodata=-9999, unit="m"):
    path = tmp_path / "synthetic.tif"
    if values is None:
        values = np.arange(16, dtype=float).reshape(4, 4) - 2
    with rio.open(path, "w", driver="GTiff", count=1, dtype="float64", crs=crs,
                  width=values.shape[1], height=values.shape[0], nodata=nodata,
                  transform=transform or from_origin(726000, 3756000, 1, 1)) as dst:
        dst.write(values, 1)
        if unit:
            dst.set_band_unit(1, unit)
    return path


def load(path, **kwargs):
    return load_terrain_raster(path, provenance=provenance(), target_crs="EPSG:32636", **kwargs)


def test_native_ground_heights_extent_negative_values_and_sampling(tmp_path):
    grid = load(raster(tmp_path), cell_m=1)
    assert grid.bounds == (726000, 3755996, 726004, 3756000)
    np.testing.assert_array_equal(grid.elevation_m, np.arange(16).reshape(4, 4) - 2)
    assert grid.sample(726000.5, 3755999.5) == -2  # below datum is not automatically sea
    samples = grid.sample([726003.9, 726004, 726000, np.nan], [3755996.1, 3756000, 3755996, 3756000])
    assert samples[0] == 13
    assert np.isnan(samples[1:]).all()
    assert grid.quality().valid_fraction == 1
    assert not grid.elevation_m.flags.writeable


def test_resampling_retains_native_and_prior_source_detail(tmp_path):
    path = raster(tmp_path, transform=from_origin(726000, 3756000, 30, 30))
    grid = load(path, cell_m=10)
    assert grid.elevation_m.shape == (12, 12)
    assert grid.source_resolution == (30, 30)
    assert grid.provenance.source_resolution_m == pytest.approx(30)
    assert grid.quality().effective_resolution_m == pytest.approx(30)
    assert any("cannot resolve" in w for w in grid.quality().warnings)
    coarser = load_terrain_raster(path, provenance=provenance(source_resolution_m=90), target_crs="EPSG:32636", cell_m=10)
    assert coarser.provenance.source_resolution_m == 90


@pytest.mark.parametrize("method", ["nearest", "bilinear"])
def test_nodata_mask_is_not_interpolated_or_set_to_zero(tmp_path, method):
    z = np.ones((4, 4)) * 7
    z[1, 1] = -9999
    z[2, 2] = np.nan
    grid = load(raster(tmp_path, z), cell_m=0.5, resampling=method)
    assert np.isnan(grid.sample(726001.25, 3755998.75))
    assert np.isnan(grid.sample(726002.25, 3755997.75))
    assert grid.quality().valid_fraction == pytest.approx(14 / 16)
    assert np.nanmin(grid.elevation_m) == pytest.approx(7)


def test_partial_coverage_is_explicit_and_can_be_rejected(tmp_path):
    path = raster(tmp_path)
    box = (725998, 3755996, 726004, 3756000)
    grid = load(path, cell_m=1, bounds=box)
    assert grid.bounds == box
    assert grid.quality().valid_fraction == pytest.approx(2 / 3)
    assert not grid.valid[:, :2].any()
    with pytest.raises(ValueError, match="coverage"):
        load(path, cell_m=1, bounds=box, min_valid_fraction=0.95)


def test_geographic_source_reprojects_but_output_must_be_metric(tmp_path):
    path = raster(tmp_path, crs="EPSG:4326", transform=from_origin(35.5, 33.9, 1 / 3600, 1 / 3600))
    grid = load(path, cell_m=10)
    assert grid.source_crs == "EPSG:4326"
    assert grid.source_resolution_unit == "degree"
    assert grid.source_resolution[0] == pytest.approx(1 / 3600)
    assert 30 < grid.provenance.source_resolution_m < 32
    with pytest.raises(ValueError, match="metre"):
        load_terrain_raster(path, provenance=provenance(), target_crs="EPSG:4326")
    with pytest.raises(ValueError, match="metre"):
        load_terrain_raster(path, provenance=provenance(), target_crs="EPSG:2263")


def test_roundtrip_retains_metadata_mask_and_hash(tmp_path):
    z = np.ones((4, 4))
    z[0, 0] = -9999
    grid = load(raster(tmp_path, z), cell_m=1)
    saved = tmp_path / "planning.npz"
    grid.save(saved)
    restored = TerrainGrid.load(saved)
    np.testing.assert_equal(restored.elevation_m, grid.elevation_m)
    assert restored.provenance == grid.provenance
    assert restored.source_sha256 == grid.source_sha256
    assert len(restored.source_sha256) == 64
    assert restored.source_nodata == -9999
    assert restored.quality() == grid.quality()


def test_apply_raster_scale_offset_and_internal_mask(tmp_path):
    path = raster(tmp_path, np.ones((4, 4)), nodata=None)
    with rio.open(path, "r+") as dst:
        dst.scales = (0.1,)
        dst.offsets = (-3,)
        valid = np.ones((4, 4), dtype=np.uint8) * 255
        valid[0, 0] = 0
        dst.write_mask(valid)
    grid = load(path, cell_m=1)
    assert np.isnan(grid.elevation_m[0, 0])
    assert grid.elevation_m[1, 1] == pytest.approx(-2.9)


@pytest.mark.parametrize("options, message", [
    ({"cell_m": 0}, "cell_m"),
    ({"cell_m": np.nan}, "cell_m"),
    ({"cell_m": 1, "bounds": (0, 0, 0, 1)}, "positive"),
    ({"cell_m": 1, "bounds": (726000, 3755996, 726004.5, 3756000)}, "integer"),
    ({"cell_m": 1, "bounds": (0, 0, 2, 2)}, "overlap"),
    ({"cell_m": 0.01, "max_cells": 100}, "max_cells"),
    ({"min_valid_fraction": 1.1}, "min_valid_fraction"),
    ({"resampling": "cubic"}, "resampling"),
])
def test_reject_invalid_domain_requests(tmp_path, options, message):
    with pytest.raises(ValueError, match=message):
        load(raster(tmp_path), **options)


@pytest.mark.parametrize("options, message", [
    ({"vertical_reference": "unknown"}, "vertical_reference"),
    ({"evidence": ""}, "evidence"),
    ({"elevation_unit": "ft"}, "elevation_unit"),
    ({"source_resolution_m": -1}, "source_resolution_m"),
    ({"kind": "lidar"}, "kind"),
])
def test_require_declared_vertical_units_and_provenance(options, message):
    with pytest.raises(ValueError, match=message):
        provenance(**options)


def test_reject_unusable_sources(tmp_path):
    with pytest.raises(ValueError, match="embedded source CRS"):
        load(raster(tmp_path, crs=None))
    with pytest.raises(ValueError, match="unit"):
        load(raster(tmp_path, unit="ft"))
    with pytest.raises(ValueError, match="empty"):
        load(raster(tmp_path, np.full((4, 4), -9999.0)))
    with pytest.raises(ValueError, match="north-up"):
        load(raster(tmp_path, transform=rio.Affine(1, 0.1, 726000, 0, -1, 3756000)))


def test_surface_and_underpasses_are_never_silently_verified(tmp_path):
    grid = load_terrain_raster(raster(tmp_path), target_crs="EPSG:32636", provenance=provenance(kind="surface"))
    warnings = " ".join(grid.quality().warnings)
    assert "not verified bare earth" in warnings and "Underpasses" in warnings
    with pytest.raises(ValueError, match="street_width_m"):
        grid.quality(street_width_m=-1)


def test_explicit_legacy_adapter_keeps_estimated_ground_limitations(tmp_path):
    path = tmp_path / "legacy.npz"
    np.savez(path, crs="EPSG:32636", x0=726000, y0=3756000, cell_m=10,
             ground=np.array([[5., np.nan], [2., 3.]]), source="Elevation: Copernicus DEM GLO-30")
    grid = load_legacy_copernicus(path)
    assert grid.provenance.kind == "estimated_ground"
    assert grid.provenance.vertical_reference == "EGM2008 (EPSG:3855)"
    assert grid.provenance.source_resolution_m == 30
    assert grid.quality().valid_fraction == 0.75
    assert "<=0 m masked" in grid.provenance.evidence
    np.savez(path, crs="EPSG:32636", x0=0, y0=0, cell_m=10, ground=np.ones((2, 2)), source="unknown")
    with pytest.raises(ValueError, match="identified Copernicus"):
        load_legacy_copernicus(path)
