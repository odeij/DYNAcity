# Planning terrain handoff

Integration status: ready

Implemented 16 October 2026 in `src/dynacity/flood_terrain.py`; synthetic checks in `tests/test_flood_terrain.py`. Existing `terrain.py`, shared APIs, dependencies, viewer and other workstreams were not changed. Ready means an ingestion/quality component is available; it does **not** mean Beirut has surveyed or calibrated planning terrain.

## Public callable interface

The existing optional `terrain` extra supplies Rasterio; NumPy is already a core dependency. Grid creation/loading validates the metric CRS through Rasterio, so consumers of the NPZ adapter also need this extra. No additional dependency declarations are required.

- `TerrainProvenance(source, kind, vertical_reference, evidence, elevation_unit="m", source_resolution_m=None, acquisition_date=None, vertical_accuracy_m=None)`: explicit producer declarations. `kind` is `bare_earth`, `surface`, `estimated_ground` or `unknown`. Source, evidence and vertical reference must be declared; elevations must be metres. Optional accuracy is a supplied positive value, not estimated by this module. An acquisition date remains a producer string and is not derived from file timestamps. Unknown acquisition date/accuracy remains `None`. `bare_earth` requires external evidence: the loader cannot verify classification from pixels.
- `load_terrain_raster(path, *, provenance, target_crs, cell_m=None, bounds=None, resampling="nearest", min_valid_fraction=0.0, max_cells=10_000_000) -> TerrainGrid`: local single-band north-up raster ingestion. Geographic sources are allowed; the destination must be projected with metre units. Non-metre elevation band units are rejected. Scales and offsets are applied. `cell_m` is a positive square-cell spacing in metres; when omitted, Rasterio's suggested destination spacing is used. `bounds=(west,south,east,north)` is in the destination CRS and must span integer multiples of `cell_m`; without bounds, the destination rounds outward to cover the source. Rotated rasters, absent source CRS, empty/no-overlap domains, all-nodata results, and invalid size/unit inputs fail clearly. Both source and destination are guarded by `max_cells`; larger sources need a bounded local crop first. Rasterio/GDAL performs horizontal reprojection, with no vertical datum conversion requested by this module.
- `load_legacy_copernicus(path) -> TerrainGrid`: explicitly reads this project's legacy `GroundModel` NPZ, requiring its Copernicus GLO-30 attribution. Annotates the result as `estimated_ground`, EGM2008, nominal 30 m source detail, with unrecoverable legacy masking/filtering disclosed. The nominal one-arcsecond source posting is a product/pipeline annotation, not recovered file metadata. This is not a generic NPZ importer.
- `TerrainGrid`: fields `crs`, `x0`, `y0`, `cell_m`, read-only `elevation_m`, `provenance`, `source_crs`, native `source_resolution=(dx,dy)`, `source_resolution_unit`, `source_nodata`, `source_sha256`, `resampling`. `bounds` gives west/south/east/north outer edges. `valid` is a boolean array; invalid elevations are NaN. In row/column order, cell centres are `x0+(col+0.5)*cell_m`, `y0-(row+0.5)*cell_m`. `source_sha256` fingerprints the principal input file, not any external mask/sidecar files; retain those files and their provenance separately when supplied. It does not authenticate the producer's declarations.
- `grid.sample(x, y) -> ndarray`: nearest-cell metre elevations at broadcastable grid-CRS coordinates. No CRS conversion or gap interpolation. Nonfinite/outside/nodata queries return NaN. Bounds are half-open: west ≤ x < east and south < y ≤ north.
- `grid.quality(street_width_m=6.0) -> TerrainQuality`: valid fraction/counts, elevation range, effective resolution and tuple of warnings. Three cells across the requested street width is a **screening heuristic**, not an established Beirut accuracy criterion. Even fine terrain warns that underpasses/culverts require surveyed connectivity, and undeclared vertical accuracy is disclosed. `effective_resolution_m` is the coarser of output spacing and retained source detail. A raster's native posting is a lower bound if the original sensor/parent resolution is unknown.
- `grid.save(path)` / `TerrainGrid.load(path)`: compressed NPZ with schema-versioned JSON metadata and elevations; no pickle. Write private rasters and derived grids outside Git, under the configured private data/artifact roots.

Nearest resampling is the default. Optional bilinear resampling interpolates valid elevations while a separate nearest-neighbour source validity mask preserves holes at target-cell support. It does not guarantee conservative coverage of every subcell gap when coarsening; select a resolution and explicit model mask appropriate to the pilot. No nodata filling, sea threshold, ground filtering, depression filling, building burning or drain conditioning occurs. Valid zero and negative elevations remain valid. Consumers must supply boundaries for nodata regions and must not silently turn them into zero or sea.

`source_resolution` always retains native file spacing/units after reprojection. `provenance.source_resolution_m` retains the coarser of supplied original detail and file posting measured near its centre in the metric destination. Thus resampling a 30 m raster to 1 m cannot make it pass a street-detail screen. This centre estimate is suitable for a bounded pilot, not a citywide distortion/accuracy guarantee. If a prior-resampled raster's history is unknown, the loader cannot discover its original resolution; supply an honest declaration and retain that limitation.

## Integration example

```python
from pathlib import Path
from dynacity.flood_terrain import (
    TerrainProvenance, load_terrain_raster, load_legacy_copernicus,
)

# Values below must come from the actual survey deliverable/metadata.
# Do not label a raster bare_earth merely because it contains elevations.
def ingest_survey(path, *, producer, vertical_reference, survey_evidence,
                  native_resolution_m, pilot_bounds):
    return load_terrain_raster(
        path,
        provenance=TerrainProvenance(
            source=producer,
            kind="bare_earth",
            vertical_reference=vertical_reference,
            evidence=survey_evidence,
            source_resolution_m=native_resolution_m,
        ),
        target_crs="EPSG:32636",
        cell_m=1.0,
        bounds=pilot_bounds,  # exact metre edges, integer 1 m spans
        min_valid_fraction=0.99,  # explicit pilot assumption, not a default standard
    )

# Immediately usable fallback for ingestion/wiring, with all caveats retained:
grid = load_legacy_copernicus(Path.home() / "dynacity-private/data/terrain.npz")
quality = grid.quality(street_width_m=6.0)
z_m = grid.elevation_m
valid = grid.valid
dx_m = grid.cell_m
cell_area_m2 = dx_m ** 2
# Surface/runoff consumers receive z_m, valid, dx_m, bounds, crs and provenance.
# They must compare vertical references before attaching observed levels,
# outfalls or boundary stages, and propagate quality.warnings into results.
# Passing this fallback to a solver does not validate street-scale hydraulics.
```

No surface/runoff engine is called by this component. Solvers retain responsibility for numerical stability, conservative transfers, boundary handling, explicit connectivity, hydraulic roughness and any conditioning. Missing velocities or water-depth fields are not generated by this adapter.

## Existing Copernicus pipeline audit

Read-only audit of `terrain.py` identified these properties and limitations:

1. Fetches one public GLO-30 COG tile over building centroids plus margin, rejecting an extent crossing a one-degree tile boundary. Source remains a DSM containing structures/vegetation, per the [Copernicus product description](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM) and [COG distribution documentation](https://copernicus-dem-30m.s3.amazonaws.com/readme.html).
2. Applies a 10th-percentile filter over an approximately 150 m window and Gaussian smoothing. This is a coarse estimated-ground transformation, not a measured DTM, and can erase depressions/embankments. Source cell size in the filtering calculation uses latitude degrees times a fixed metres-per-degree factor, rather than independent east/west spacing.
3. Marks all nonfinite and ≤0 m cells as sea, then clamps remaining elevations above 0.1 m. This can discard real low ground. The old saved NaN mask cannot reveal which missing cells were sea, bad source data or low ground. The source read does not explicitly apply the dataset mask/nodata metadata before filtering; positive finite nodata could therefore contaminate it.
4. Resamples to a default 10 m UTM grid. Integer truncation in the old dimensions can omit a fractional edge strip. Its saved NPZ retains only CRS, origin, output cell spacing, heights and attribution; original CRS/resolution, vertical reference, source nodata, source identity and filtering history are absent.
5. EGM2008 is stated in its code documentation and in the [Copernicus product reference](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM). It must not be equated to a surveyed local tide/sea-level datum. No alignment to local benchmarks or independent checkpoints is evidenced.

The new module does not modify that legacy workflow. Its fallback adapter makes the provenance limitations explicit; a freshly supplied source GeoTIFF can instead use the new ingestion path. Nodata-aware reprojection follows [Rasterio's reprojection API](https://rasterio.readthedocs.io/en/stable/topics/reproject.html); resolution changes follow its [resampling conventions](https://rasterio.readthedocs.io/en/stable/topics/resampling.html).

## Focused local suitability audit

Read-only inspection on 16 October 2026; no source data was copied into this repository:

- The configured private project's data directory contains one legacy filtered terrain NPZ and no terrain GeoTIFF. It has a 790 × 921 grid at 10 m output spacing, EPSG:32636, and 422,760 valid cells out of 727,590 (58.10%). This is coverage of the saved rectangular domain, **not** a land-only completeness estimate. It lacks source/vertical accuracy metadata. Adapter output was inspected successfully against the actual file; this does not validate its elevations.
- A focused search of the project/private data and locally available Documents/Downloads project assets found no survey DTM GeoTIFF. This is not an exhaustive claim that no such survey exists elsewhere.
- A nearby AlMasar point-cloud segmentation project contains `work/terrain.npy` and `terrain_distance.npy`, both 1442 × 867. The script uses a 0.3 m array posting, draft digitized roads/open-ground masks, 40th-percentile point heights, colour/exclusion rules, outlier rejection, seed thinning, 16-neighbour inverse-distance interpolation and Gaussian smoothing. It fills the entire rectangle, including areas far from seeds. Its row orientation follows increasing y (south-up), unlike the north-up interface here. The arrays do not embed CRS, vertical reference, acquisition/accuracy or validated ground classification. **0.3 m posting is not demonstrated 0.3 m independent terrain detail.** The distance array could support future exclusion of unsupported interpolation, but no evidence-based maximum seed distance has been established.
- The corresponding available original LAS tile's header reports LAS 1.2, point format 2, PDAL export software and zero VLRs (no embedded CRS). Prior project research documents the cloud as photogrammetric, with horizontal EPSG:32636 assigned via alignment to BBED. Draft labelled LAS derivatives explicitly state they are not verified ground truth. Neither a `.las` container, draft class 2 labels nor the interpolated array establishes bare earth or a vertical datum. These assets are potential material for **survey-reviewed extraction and checkpoint comparison**, not automatically accepted planning terrain. No full cloud reconstruction or classification was performed in this workstream.
- The currently loaded building snapshot has no point-cloud features per the workstream contract; discovering separate local files does not attach or validate them in that snapshot.

## Missing survey inputs and empirical limits

No independently verified high-resolution bare-earth raster was supplied. Before street/underpass planning claims, obtain a bounded DTM with actual original resolution, acquisition date, documented ground classification/removal of roofs/vegetation, coverage and nodata masks; a declared vertical reference tied to local benchmarks (including any geoid/local offset transformations); independent checkpoint elevations and stated vertical uncertainty; surveyed kerbs, walls, embankment/breakline geometry and bridge/underpass floor levels; culvert/inlet/outfall invert levels and connectivity; and consistent coastal boundary datum/bathymetry where relevant. Drainage ownership covers network behaviour; terrain cannot infer missing connections from the DTM alone. Photogrammetry can miss occluded ground, and no interpolation repairs that evidentiary gap.

Neither this audit nor synthetic fixtures calibrate Beirut elevations or flood performance. The fine-resolution loader is functional and tested with generated rasters; real higher-resolution DTM ingestion remains unverified until an actual documented deliverable is supplied. Acquisition date, survey precision, datum offsets and missing breaklines were not invented.

## Verification

Command: `.venv/bin/python -m pytest tests/test_flood_terrain.py tests/test_terrain.py`

Result: **30 passed** (24 new terrain fixture checks, 6 existing terrain/viewer checks), Rasterio 1.5.2. Fixtures cover native/retained resolution, projected metric CRS, geographic reprojection, rejection of foot-unit output CRS and vertical units, declared vertical reference, native source nodata and internal mask, bilinear hole preservation, scaling/offsets, partial/no coverage and coverage thresholds, negative elevations, bounds alignment and allocation guard, bad inputs, sampling, serialization/provenance retention, DSM warnings and explicit legacy conversion. No network or private data is required by tests. The actual private fallback was separately loaded read-only and its aggregate quality report checked. This is component verification, not hydraulic or observed-data validation.
