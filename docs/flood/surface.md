# Surface-water output ingestion

Integration status: ready

Scope of this module: tested **precomputed hydraulic output import**. The runnable ANUGA backend is now documented in [solver.md](solver.md); neither module establishes calibration or a Beirut flood prediction. Owned implementation: `src/dynacity/flood_surface.py`; tests: `tests/test_flood_surface.py`. No shared viewer/API files or dependencies changed.

## Engine/interface choice and primary evidence

Researched 16 October 2026. ANUGA is the selected surface engine for this Python project: its documented finite-volume shallow-water domain supports wetting/drying on a triangular mesh, with geometry, initial stage, boundary tags, friction and source operators. This is an interface selection, not a claim of locally verified engine performance. [Official ANUGA background](https://anuga.readthedocs.io/en/stable/background.html).

ANUGA stores mesh and time-dependent quantities in SWW/NetCDF; `sww2dem` exports depth to ASC/ERS. Use one explicitly selected output time per imported raster; preserve the exporter configuration. This importer accepts those GDAL-readable rasters with declared CRS, and never reads SWW mesh arrays directly. Mesh-to-grid interpolation is an external preprocessing step with potential storage error. [Official ANUGA file formats and conversions](https://anuga.readthedocs.io/en/stable/reference/file_formats.html).

HEC-RAS is another established upstream engine. RAS Mapper distinguishes dynamic display maps from stored output rasters. Supply stored depth exports for specific timestamps, not a maximum-depth map presented as a time series. Native HDF face-normal velocity cannot be treated as grid x/y velocity without an explicit reconstruction. Direct HDF ingestion is outside this component. [Official RAS Mapper results maps](https://www.hec.usace.army.mil/confluence/rasdocs/rmum/6.3/mapping-results/creating-results-maps), [official HEC-RAS 2D manual](https://www.hec.usace.army.mil/software/hec-ras/documentation/HEC-RAS_6.0_2D_Modeling_Users_Manual.pdf).

## Runtime and implementation boundary

Updated 16 October 2026: ANUGA 3.3.7 is installed in the project interpreter and the separate `flood_solver` runner executes native shallow-water calculations. See [solver.md](solver.md) for the tested numerical benchmarks, required real inputs and limits. `solver_runtime_status()` distinguishes runner implementation and package discovery from real-data readiness; no verified Beirut input bundle is included. This importer remains an importer and never fabricates a fallback surface.

Implemented path: explicitly label upstream output, validate and import it, retain source SHA-256 hashes, expose validity and warnings, and calculate grid storage diagnostics. No private measurements or simulation artifacts have been added to Git.

## Public API

```python
HydraulicOutput(depth_m, metadata, velocity_m_s=None)
load_hydraulic_output(path) -> HydraulicOutput
import_depth_rasters(paths, metadata) -> HydraulicOutput
solver_runtime_status() -> dict
```

`depth_m` is `[time, row, column]`, nonnegative metres; `0` is valid dry, NaN or the declared negative sentinel is missing. Infinity and entirely invalid frames are rejected. Velocity is optional `[time, row, column, 2]` projected x/y components in m/s. Missing velocity remains `None`; it is never inferred as zero. Invalid depth masks velocity. Raster import supports depth only; velocity may be attached by constructing another validated `HydraulicOutput` from independently prepared aligned components.

`output.valid` is a boolean depth mask. `output.metadata` contains source metadata plus warnings. `output.volumes_m3()` returns sum(depth × projected cell area) over valid cells at each timestamp. `output.mass_balance_residual_m3(net_interval_volume_m3)` returns Δstorage minus net external input volume for each interval. Net input must include **all** boundary inflows/outflows, rainfall, infiltration and exchange. It refuses changing validity masks; it cannot determine unknown fluxes. A zero residual tests imported raster accounting, not native engine conservation or calibration.

`output.save(path)` writes a compressed NPZ containing `depth_m`, optional `velocity_m_s`, and scalar `metadata_json`. `load_hydraulic_output` uses `allow_pickle=False`, validates again and adds the bundle hash. Choose storage outside the checkout for real outputs. NPZ horizontal metre CRS is a producer declaration; the core NumPy loader does not parse/reproject CRS. Raster ingestion additionally checks projected metre CRS using rasterio and matches the metadata/grid exactly.

Raster import uses optional existing `terrain` extra (`rasterio`). It rejects geographic/foot CRS, rotated grids, multiband rasters, misalignment and metadata disagreement; it performs no resampling or unit conversion. Every file's raster mask/nodata is respected. Input order must match declared timestamps.

## Metadata contract and integration example

All metadata is JSON-serializable; numerical metadata must be finite. Schema example below is explicitly synthetic. Real imports must identify their actual upstream model/version, run, forcing, terrain and uncertainty.

```python
from dynacity.flood_surface import import_depth_rasters

metadata = {
    "schema": "dynacity.hydraulic-grid.v1",
    "output_kind": "precomputed_hydraulic",
    "crs": "EPSG:32636", "horizontal_units": "m", "depth_units": "m",
    # Upper-left OUTER corner, positive x/y cell sizes; rows run south.
    "grid": [700000, 3750000, 2, 3],
    "vertical_datum": "unknown",
    "terrain_id": "synthetic-flat-v1",
    "terrain_provenance": "Synthetic fixture; no Beirut measurements",
    "timestamps": ["2026-10-16T00:00:00Z", "2026-10-16T00:01:00Z"],
    "nodata": -9999,  # or null/None for NaN; zero cannot be nodata
    "model": {
        "name": "external hydraulic engine", "version": "actual-version",
        "run_id": "fixture-1", "source": "external run manifest identifier",
        "evidence": "synthetic",  # synthetic/estimated/measured/mixed
    },
    "forcing": {
        "rainfall": {"status": "none", "description": "No rain in this fixture"},
        "boundary": {"status": "none", "description": "Closed reflective fixture boundary"},
        "drainage_exchange": {"status": "none", "description": "No exchange in this fixture"},
    },
    "warnings": ["Synthetic fixture; not calibrated"],
}
result = import_depth_rasters(["/private/model/t0.tif", "/private/model/t60.tif"], metadata)
# Integration layer consumes result.depth_m, result.valid, result.metadata,
# result.velocity_m_s; preserve the warnings and output_kind in API responses.
result.save("/private/model/result.npz")
```

Timestamps require explicit timezone and strictly increasing instants; unequal output intervals are permitted. Time step stability is the upstream engine's responsibility. The importer does not invent solver time steps. Grid CRS, vertical datum and terrain identifier/provenance are mandatory, with `unknown` allowed but warned for vertical datum. Depth is relative to the upstream bed; absolute stage/terrain combinations require reconciled vertical references. Grid resolution describes the output, not source terrain detail; record native terrain resolution, filtering, vertical uncertainty and any mesh/export interpolation in `terrain_provenance` or extra metadata. Resampling cannot improve source detail.

## Rainfall, boundaries and drainage exchange

The external engine must receive the actual input series and geometry before computing the depth exports. This module records those references but does **not** execute them or couple drainage:

- Each `forcing.rainfall`, `forcing.boundary`, `forcing.drainage_exchange` requires status `supplied`, `none`, or `unknown` and a description. `unknown` raises a visible warning; it is not zero forcing.
- For `supplied`, require `source` (external immutable input/run manifest reference) and `units`. Rainfall units are `m/s`, with actual series times, extent, timestep and measurement/estimate classification in the referenced manifest. Convert mm/h upstream by dividing by 3,600,000. Distinguish gross rain from effective runoff and avoid counting both.
- Boundary units field is literally `m or m3/s`; the source manifest must distinguish stage (m, same datum), integrated discharge (m3/s), wall/reflective or transmissive segments and provide edge geometry, normals, initial conditions, interpolation and time coverage. This metadata label accommodates mixed boundary types; it is not an executable boundary specification.
- Drainage exchange units are `m3/s`; the manifest must specify inlet locations, time series, sign (positive into surface, negative to network), capacity and model identity. Dynamic bidirectional sewer coupling, surcharge, inlet capacity enforcement and infiltration computation are **unimplemented**. External run must handle its own nonnegative-depth/capacity rules.
- Net interval volume diagnostic uses positive into surface and negative out of surface in m³; use integrated interval volumes, not instantaneous rates. Same forcing, terrain and baseline identifiers must be retained when comparing interventions; the pilot/planning layer owns this comparison.

## Verification and limitations

Command: `.venv/bin/python -m pytest tests/test_flood_surface.py`

Result: **22 passed** (16 October 2026). Fixtures test explicit schema/units, ordered timezone timestamps, nonnegative depths, nodata preservation, optional velocity, source hashing, safe bundle roundtrip, aligned GeoTIFF ingestion, geographic CRS and shifted grid rejection, raster edge dry/missing behavior and exact storage/net-input accounting, including nonzero residual and changing-mask refusal.

These are synthetic importer and accounting tests. They do not establish wall/open-boundary solver behavior, lake-at-rest balance, wetting/drying accuracy, rainfall-response accuracy or native numerical conservation. This importer does not itself run a solver. The separate runner has mathematical numerical tests, but observed calibration, independent Beirut validation, subsurface coupling and surveyed fine street terrain remain unavailable. The current city snapshot has no point-cloud features. The pilot must present imported outputs as externally computed and preserve their evidence classification and warnings.
