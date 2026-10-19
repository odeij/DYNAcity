# Flood observation validation workflow

Integration status: ready

Implemented `src/dynacity/flood_validation.py` and `tests/test_flood_validation.py`. This is a callable evaluator of timestamped, precomputed depth fields against supplied independent observations. It neither runs nor calibrates a hydraulic model. The current Beirut model has **not** been empirically validated or calibrated by this work. No dependencies or shared API/viewer files were changed.

## Available evidence and search boundary

Checked 16 October 2026: relevant project documentation/configuration, project observation/flood/depth/event filenames, and the existing `/Users/johnkofdarelli/dynacity-private/data` input inventory and JSON artifact filenames under its sibling `artifacts` directory. The private input inventory contains building/panel/snapshot data, terrain, traffic/roads/bus routes, traffic velocities and scenario requests. Artifacts contain forecasts, transitions, evidence and planning outputs. **No usable recorded flood depths, independently observed flood extent or dated flood onset records were found in those locations.** This is a bounded inventory finding, not a claim that no such data exist elsewhere or that Beirut did not flood. Private contents were not copied into Git. No household, email, cloud-drive or unrelated directories were searched.

The current snapshot has no loaded point-cloud features. Terrain, building footprints, traffic velocity records, illustrative flood water levels and forecast outputs cannot serve as independent inundation observations. Public sources consulted below inform the workflow; they are not Beirut ground truth. No public event data were imported or manufactured.

## Public interface

```python
ValidationGrid(crs, vertical_datum, x0, y0, cell_m, rows, cols,
               coordinate_unit="m", cell_y_m=None)
DepthCube(grid, times, depth_m, valid, event_id, model_id, provenance,
          nodata=None, calibration_event_ids=(), calibration_group_ids=())
DepthObservation(**common, x_m=..., y_m=..., time=..., depth_m=...,
                 uncertainty_m=..., window_end=None)
ExtentObservation(**common, grid=..., time=..., flooded=..., valid=...,
                  uncertain=..., wet_threshold_m=...)
TimingObservation(**common, x_m=..., y_m=..., earliest=..., latest=...,
                  wet_threshold_m=...)
evaluate_flood(cube, observations, *, time_tolerance_s=0.0) -> dict
load_validation_inputs(path) -> (DepthCube, list[Observation])
from_hydraulic_output(output, *, event_id, calibration_event_ids=(),
                      calibration_group_ids=()) -> DepthCube
```

`common` requires `observation_id`, `event_id`, `group_id`, `split` (`calibration` or `holdout`), `provenance` (source/method/version and uncertainty derivation), `evidence` (`measured`, `estimated`, `synthetic`), `independent` (boolean), `crs` and `vertical_datum`. Observation constructors use keyword arguments. Missing classification/provenance/independence cannot silently default to measured evidence. An independently generated remote-sensing estimate should normally be `estimated`, retaining its detection limitations. All record types retain their provenance and evidence in the report.

`load_validation_inputs` reads a JSON object with `model` (DepthCube fields; nested `grid` object) and `observations` (observation fields plus `kind: depth|extent|timing`). Arrays are nested JSON arrays, boolean masks must contain actual booleans, and timestamps are ISO 8601 strings with offsets. `null` model depths become invalid NaN; `null` observed depths are excluded. Extent nodata is encoded with `valid=false`, not a false flooded label. Unknown observation fields fail at construction. No pickle, external file references or executable expressions are loaded. For large models, prefer the surface module's NPZ importer plus the adapter; the JSON adapter targets bounded pilots and reproducible fixtures.

## Spatial, vertical, temporal and missing-data contract

- Model depth is metres **above the local ground reference**, shape `[time, row, column]`. Valid `0` explicitly means dry. NaN/infinity, a supplied finite negative nodata sentinel or `valid=false` mean unavailable and cannot enter metrics as zero. Negative valid depths, zero/positive nodata sentinels and non-boolean masks are rejected. Depth inputs and validity masks are copied and made read-only.
- `x0,y0` are the west/north **outer** grid edges, with positive cell dimensions and rows increasing south. `cell_m` is x resolution; `cell_y_m` defaults to the same value. Coordinates and dimensions use metres in a caller-declared projected CRS. The NumPy core does not parse arbitrary CRS definitions; raster ingestion in `flood_surface` performs additional projected/metre checks. No rotated grids or axis swapping. Each point uses its containing cell; north/west edges are included, south/east outer edges excluded.
- CRS and vertical datum strings must match exactly. No implicit reprojection, CRS aliases, datum offsets, resampling or ground interpolation. `unknown`, `unspecified` or `missing` vertical datums are excluded even if their labels match. Extents additionally require identical grid metadata/shape. Prepare reprojection/rasterization externally and record source grid, algorithm, acquisition coverage, exclusion masks and spatial uncertainty in provenance. Preserve unknown areas during rasterization. Resampling cannot improve source detail.
- A water-level/stage measurement is not directly a depth. Before ingestion, subtract an independently surveyed co-located ground elevation in the same reconciled vertical datum; retain both survey sources, transformation and uncertainty budget. A datum label match alone does not prove that terrain/observed ground references agree. This evaluator supplies no geoid conversion or surveyed ground. Relative-depth observations still require an explicitly established ground/datum comparison context.
- Times require timezone-aware datetimes or ISO timestamps with offsets and normalize to UTC. The model series must strictly increase. Match observations by event ID; supply actual event times, not a fictitious time inferred from rainfall duration. Instantaneous depth and extent use the nearest model snapshot within `time_tolerance_s` (default exact), with an earlier-sample tie break. Extrapolation is always excluded. Selected model timestamps are reported.
- A peak depth/high-water mark uses `time` and `window_end` as a closed observation window. Both endpoints must be actual model timestamps; every model sample in that window must be valid. The comparison is to the maximum **sampled** depth. It does not recover a peak between outputs. Do not compare a maximum extent/high-water mark to an arbitrary snapshot; extent input here is instantaneous only.
- Timing means the **first** crossing of `depth > wet_threshold_m` in the event series. It requires valid samples from model start through first wet, with a preceding dry sample. Initially wet, never wet and pre-onset missing records are separately excluded as left-censored, right-censored or missing; they are not assigned a fabricated time. The observed onset interval must lie within model coverage. Recurring flood episodes need separately delimited event windows.

## Uncertainty, independence and holdout discipline

Depth uncertainty is a required nonnegative bound in metres: compare absolute depth error to this bound. Timing uses required earliest/latest onset bounds, with an additional reported model dry/wet sampling bracket and interval-overlap flag. These bounds are supplied assumptions/measurement assessments, not inferred standard deviations or confidence probabilities. Extent `uncertain=true` cells are omitted along with observation nodata; the report counts both. Extent labels use the supplied strict wet threshold; record how sensor detection relates to it. Sensor flood/no-flood products with an unknown physical depth threshold require a justified conversion or a documented conditional evaluation before ingestion.

There is no positional uncertainty propagation, boundary buffer optimization, weighting by observation quality, probabilistic likelihood or bootstrap confidence interval. Record location accuracy/representativeness in provenance; use externally prepared uncertainty masks/sensitivity runs and report them separately. Do not reduce uncertainty bounds to improve scores. Samples are equally weighted; dense gauges can dominate a depth summary, and cell counts are not independent statistical replicates.

Mark a record `independent=false` when it is derived from the evaluated depth field or a model-assisted product that cannot support independent testing; it is excluded. Independence is a caller declaration, not something code can establish from a source string. Record upstream derivation and shared terrain/forcing dependencies in the provenance review.

Assign calibration and holdout **before** fitting. IDs must be unique; every event and every group must belong to one split across the supplied observations. `group_id` should capture shared survey/product/site lineage that must not cross splits. One event cannot be split into random training/testing cells here. Supply the model's complete `calibration_event_ids` and `calibration_group_ids`: holdout records intersecting either fail before scoring. Empty lineage tuples are an explicit caller assertion of no prior calibration use, not automatic discovery. This function evaluates one model event per call; maintain one frozen split manifest and full lineage across all event calls. Do not tune parameters, thresholds or exclusions after inspecting holdout results. A final unseen event/product is needed after holdout-driven tuning.

Calibration/holdout metrics are never combined. Within each split, metrics include all supplied evidence classes and extent thresholds; `evaluated_evidence_counts`, record evidence, source, threshold and exclusions are exposed. Run measured, estimated, synthetic and threshold-specific evaluations separately for empirical reporting. Do not report mixed/synthetic metrics as measured validation. `calibrated` is always `False`: this evaluator cannot establish parameter calibration from a score.

## Metrics and reproducibility

Signed error is **model minus observation**. Depth metrics (m) and timing metrics (s) report `n`, bias, MAE and RMSE; all are unweighted. For onset, observed time is the midpoint of its supplied interval, and prediction is the first wet output timestamp; positive error is late arrival. This point error is resolution-dependent. `within_observation_uncertainty` is a fraction within supplied bounds, distinct from the model/observation onset-interval overlap flag.

Extent counts are computed only on cells with observed valid/unambiguous labels and valid model depth. TP/FP/FN/TN are relative to the observed wet class:

| Metric | Formula |
| --- | --- |
| CSI / IoU | TP / (TP + FP + FN) |
| Precision | TP / (TP + FP) |
| Recall | TP / (TP + FN) |
| False alarm ratio | FP / (TP + FP) |
| F1 | 2 TP / (2 TP + FP + FN) |
| Accuracy | (TP + TN) / paired cells |

Zero denominators return JSON `null`, including CSI on all-dry paired cells. Accuracy can be dominated by dry area. Report counts and coverage alongside the scores. Areas are planar projected cell areas (dx × dy), not geodesic areas. Multiple extent snapshots pool cell-time counts/area; the area sum is not a unique event footprint. Spatial or temporal correlation does not provide extra independent samples.

Each split includes total/evaluated observation counts, evaluated fraction and exclusion reasons. Extents count total cells, observed valid, uncertain, eligible, paired and missing model cells, plus paired/eligible coverage and paired area. Mismatched grids/CRSs cannot supply comparable cell denominators and remain record-level exclusions. A time-misaligned extent retains its known eligible denominator and zero paired coverage. Empty datasets/denominators give null coverage, never a successful validation claim.

The report is JSON-safe (`json.dumps(report, allow_nan=False)`), contains schema version, model provenance/identity, grid, UTC model times, calibration lineage, tolerance, records and an input SHA-256 of canonical model/observation content. Identical inputs and configuration reproduce identical reports; no clock-dependent metadata is added. Hashing serializes the bounded pilot inputs in memory and is not intended for citywide cubes. Preserve source files and preprocessing/run/split manifests separately; a hash is not proof of source authenticity. Keep real inputs and reports under configured private roots outside Git.

## Integration examples

Complete synthetic arithmetic example (no Beirut data):

```python
import numpy as np
from dynacity.flood_validation import (
    ValidationGrid, DepthCube, DepthObservation, evaluate_flood,
)

grid = ValidationGrid("EPSG:32636", "synthetic-local-ground", 0, 10, 10, 1, 1)
cube = DepthCube(
    grid, ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"],
    np.array([[[0.0]], [[0.7]]]), np.ones((2, 1, 1), dtype=bool),
    "synthetic-event", "arithmetic-fixture-v1", "Synthetic example; no field data",
)
observed = DepthObservation(
    observation_id="fixture-depth", event_id="synthetic-event", group_id="fixture-survey",
    split="holdout", provenance="Synthetic 0.5 m fixture with supplied 0.1 m bound",
    evidence="synthetic", independent=True, crs=grid.crs,
    vertical_datum=grid.vertical_datum, x_m=5, y_m=5,
    time="2026-01-01T00:01:00Z", depth_m=0.5, uncertainty_m=0.1,
)
report = evaluate_flood(cube, [observed])
assert abs(report["split_metrics"]["holdout"]["depth"]["mae"] - 0.2) < 1e-12
assert report["measured_observations_evaluated"] == 0
```

For an actual upstream hydraulic bundle, use the ready surface module:

```python
from dynacity.flood_surface import load_hydraulic_output
from dynacity.flood_validation import from_hydraulic_output, evaluate_flood

# Paths and IDs below are placeholders for actual private event/run manifests.
output = load_hydraulic_output("/private/flood/event-output.npz")
cube = from_hydraulic_output(
    output, event_id="actual-observed-event-id",
    calibration_event_ids=("previous-fitting-event-id",),
    calibration_group_ids=("previous-fitting-survey-id",),
)
# observations = [...] built from independently collected, aligned records
# report = evaluate_flood(cube, observations, time_tolerance_s=0)
```

The adapter preserves full surface metadata as model provenance, timestamps, shape, north-up grid (including rectangular cells), depth and validity. It requires `dynacity.hydraulic-grid.v1`, `precomputed_hydraulic` and metre units. Model identity is `name:version:run_id`. It does not invent an event ID or calibration history, resolve unknown datum, consume visual flood levels, validate velocities, or run a solver. The pilot can report “observations unavailable” and still import upstream output; do not present an empty comparison as zero error. Reports may include private source references, so keep them private too.

## Data still required for empirical validation

1. A dated, bounded flood event inventory with independent calibration and holdout events, immutable split/group manifest, survey/product identifiers, permissions and source checksums.
2. Co-located measured depths or water-level time series/high-water marks: projected coordinates, positional accuracy, ground reference, surveyed ground/elevation and vertical datum, measurement method/quality, units, uncertainty bounds, UTC times or explicitly bounded peak windows. Document event evidence, wave/run-up/debris effects and survey dates.
3. Independently observed instantaneous flood extents with acquisition timestamp/uncertainty, observed dry coverage, unknown/excluded/ambiguous areas, sensor resolution and urban/vegetation/shadow limitations; verified wet/dry interpretation and threshold, CRS, rasterization/resampling history and boundary uncertainty. A polygon alone does not prove all exterior pixels were observed dry.
4. Onset intervals from gauges/cameras/time-stamped reports with before/after evidence, clock/timezone accuracy, location, threshold/detection definition and missing-observation windows. If a record supplies only a peak, it cannot validate onset.
5. Actual hydraulic depth time grids spanning the event, model/version/run identity, reconciled terrain/ground/datum, complete forcing/boundary/drainage references, nodata masks and calibration lineage. Use sufficient output frequency for the intended depth/arrival comparison. A numerical/importer fixture alone cannot establish hydraulic accuracy.

Already evaluable: instantaneous or sampled-window peak depth error, aligned instantaneous wet/dry extent overlap with masks and coverage, and first-threshold onset timing with uncertainty/censoring. Not implemented: discharge/velocity validation, time-series hydrograph skill, maximum-event extent construction, source georeferencing/field survey, automatic parameter fitting, acceptance criteria, real Beirut empirical evaluation or safety certification. Evaluation tolerances/acceptance criteria must be predeclared for the intended decision using real observation quality.

## Primary technical references

- [USACE HEC-RAS: Calibration of Unsteady Flow Models](https://www.hec.usace.army.mil/confluence/rasdocs/rasum/6.4/performing-a-1d-unsteady-flow-analysis/calibration-of-unsteady-flow-models) describes parameter calibration against observations, datum errors and the variability/physical interpretation of high-water marks. This motivates explicit references and observation quality; no published generic accuracy is adopted as a Beirut uncertainty bound.
- [Copernicus Emergency Management Service: Global Flood Monitoring technical presentation](https://global-flood.emergency.copernicus.eu/media/events/3/2021_cems_gfm_final-min.pdf) distinguishes observed flood/water extents, reference-water/exclusion masks, uncertainty and advisory products. This motivates preserving acquisition coverage, uncertain and excluded areas rather than declaring unobserved pixels dry. It is not an imported Beirut observation product.

These references informed the workflow. Metrics/formulas and tests are transparent local implementations, not a claim of HEC-RAS/ANUGA numerical validation or compliance with a universal performance standard.

## Verification

Run 16 October 2026:

```text
.venv/bin/python -m pytest tests/test_flood_validation.py tests/test_flood_surface.py
59 passed in 0.29s
```

Validation owns **37 passing tests**; surface contributes 22 existing tests. Tests include known ±0.5 m depth errors (bias 0, MAE/RMSE 0.5 m), TP=2/FP=1/FN=1/TN=2 extent (CSI 0.5), known early/late onset errors, full-window sampled peaks, uncertainty/observation/model nodata exclusions, all-dry undefined CSI, no observations, invalid units/masks/times/depths, timezone alignment, no extrapolation, grid/datum mismatch, duplicate IDs, event/group split leakage, prior calibration lineage, JSON ingestion/report determinism and an actual `HydraulicOutput` adapter with rectangular-cell area. Fixtures are explicitly synthetic and do not calibrate any Beirut parameters.
