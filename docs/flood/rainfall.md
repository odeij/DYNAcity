# Rainfall forcing handoff

Integration status: ready

Implemented in `src/dynacity/flood_rainfall.py`; no shared API, CLI, contracts or dependency files changed. Uses only the Python standard library. This component produces precipitation forcing, not runoff, ponding depths, drainage capacity or hydraulic results.

## Public interface

Import directly from `dynacity.flood_rainfall`:

| API | Contract |
| --- | --- |
| `RainfallProvenance(kind, source, station_id=None, notes="")` | Frozen source metadata. `kind`: `"hypothetical"`, `"observed"` or `"estimated"`. Nonempty source required. Observed data require a nonempty station ID. Caller declares provenance; this does not independently verify it. |
| `RainfallInterval(start, end, intensity_mm_h)` | Frozen interval, with aware Python datetimes and a finite nonnegative interval-average rate in mm/hour. Normalized to UTC. Properties: `duration_seconds`, `depth_mm`, `intensity_m_s`. |
| `RainfallForcing(intervals, provenance)` | Frozen, nonempty ordered sequence of contiguous intervals. Copies the sequence to a tuple. Gaps, overlaps and duplicates fail. Explicit zero-rate intervals represent known dry weather. |
| `hyetograph(*, start, values, timestep_seconds, unit, provenance)` | Regular start-of-interval input; returns `RainfallForcing`. Duration is number of values times timestep. No implicit terminal interval or silent padding. |
| `import_rainfall_csv(path, *, unit, provenance)` | Read supplied UTF-8 CSV into `RainfallForcing`; schema below. No downloads or writes. |
| `forcing.start`, `.end`, `.duration_seconds` | UTC coverage endpoints and elapsed duration. |
| `forcing.timestep_seconds` | Common native interval length, or `None` for irregular intervals. |
| `forcing.total_depth_mm` | Full event precipitation depth. |
| `forcing.depth_between(start, end)` | Overlap-integrated depth in mm. Both aware timestamps must fall within coverage; zero-length query returns zero. |
| `forcing.cumulative_depth_mm(at)` | Event precipitation since coverage start in mm, evaluated at an aware timestamp within coverage. |
| `forcing.steps(timestep_seconds)` | Tuple of `RainfallStep` records, each with `start`, `end`, `duration_seconds`, `intensity_mm_h`, `depth_mm`, `cumulative_depth_mm` (at step end), plus properties `depth_m` and `intensity_m_s`. Final step may be shorter. |
| `forcing.provenance`, `.warnings` | Original immutable metadata and explicit evidence/spatial/IDF limitations. Carry these into downstream results. |

Public type aliases: `RainfallKind` and `RainfallUnit`. Supported units: `mm/h`, `m/s`, `in/h` (rates), `mm`, `m`, `in` (depth **over each supplied interval**, not a cumulative gauge register). One inch is 25.4 mm. Downstream water-volume calculation is `depth_m * area_m2`; no losses are applied here.

Time convention: half-open `[start, end)` intervals; all calculations use elapsed UTC time. Input must carry a timezone/offset, including CSV rows. Naive wall times are rejected. When constructing local times near daylight-saving transitions, the caller must resolve ambiguous or nonexistent wall times before ingestion. Store original local zone and measurement/QC details in provenance notes. Timestep must be positive, finite and representable in whole datetime microseconds. No CRS or vertical datum applies to these nonspatial precipitation depths. They are not water-surface elevations.

## Integration example

```python
from datetime import datetime, timezone
from dynacity.flood_rainfall import RainfallProvenance, hyetograph

rain = hyetograph(
    start=datetime(2026, 1, 1, tzinfo=timezone.utc),
    values=[12.0, 24.0, 0.0],
    timestep_seconds=600,
    unit="mm/h",
    provenance=RainfallProvenance(
        kind="hypothetical",
        source="user-specified demonstration storm",
        notes="Uniform pilot forcing; no return period or Beirut calibration",
    ),
)
assert rain.total_depth_mm == 6.0
steps = rain.steps(timestep_seconds=900)
assert [step.depth_mm for step in steps] == [4.0, 2.0]
# An actual runoff/surface adapter consumes each step's depth_m and duration_seconds.
# rain.provenance and rain.warnings must accompany its results.
# Example supplied-area precipitation volume, before infiltration/drainage:
volume_m3 = sum(step.depth_m * 2000.0 for step in steps)
assert volume_m3 == 12.0
```

For irregular hyetographs construct explicit `RainfallInterval` records and pass their tuple plus provenance to `RainfallForcing`. A model with adaptive time steps may call `depth_between(start, end)` instead of materializing regular steps. Solver times outside rainfall coverage raise `ValueError`; extend coverage with explicit observed or assumed dry intervals only when justified, and label any assumption in provenance. If measurements and estimates are mixed, label the resulting series `estimated` and describe the measured/estimated intervals upstream; this API deliberately does not silently impute gaps.

Rebinning integrates exact source-interval overlaps. It assumes constant intensity within each source interval, preserves depth to floating-point precision and does not manufacture temporal resolution. Coarsening can reduce peaks. `steps()` is linear in source intervals plus output steps, and materializes a tuple; callers should choose a sensible finite simulation horizon/timestep. The rainfall timestep is not a hydraulic stability criterion.

## Measurement ingestion

No local rainfall measurements have been supplied or loaded. Keep actual station exports, locations and derived private files outside Git. Normalize a station's data dictionary explicitly to:

```csv
start,end,value
2026-01-01T00:00:00+02:00,2026-01-01T00:05:00+02:00,1.0
2026-01-01T00:05:00+02:00,2026-01-01T00:20:00+02:00,6.0
```

These numbers are **fixtures**, not observations from Beirut. With `unit="mm"` they express interval depths of 1 and 6 mm; the intervals have different lengths. Import real supplied measurements as follows, using their real attribution:

```python
from dynacity.flood_rainfall import RainfallProvenance, import_rainfall_csv

def load_supplied_station(private_csv_path, source, station_id, qc_notes):
    return import_rainfall_csv(
        private_csv_path,
        unit="mm",  # confirm against station's measurement definition
        provenance=RainfallProvenance("observed", source, station_id, qc_notes),
    )
```

Required CSV columns are exactly `start,end,value`; order is arbitrary. Every row supplies its bounds, so end-of-interval gauges can be normalized without guessing timestamp semantics. Rates or interval depths are selected explicitly by `unit`, never inferred from a filename. Unknown sentinel values (such as -9999), blanks, nonfinite or negative values, missing times, gaps, overlaps and unordered records fail. CSV parse failures include the record line number where available. No interpolation, sorting, cumulative-register reset detection or missing-as-zero policy exists. Convert cumulative registers only after station-specific reset, counter-wrap and QC handling; do not pass cumulative readings as interval depths.

## Provenance and limits

Rainfall semantics follow the primary [US EPA SWMM 5.2 User's Manual, rain gage and rainfall time-series sections](https://www.epa.gov/system/files/documents/2022-04/swmm-users-manual-version-5.2.pdf): distinguish average rate from interval precipitation depth and record the measurement interval. The implementation here is a standalone forcing adapter; it does not claim to implement the SWMM file format or invoke SWMM.

No local IDF evidence was provided. The public interface has no return-period field and does not infer a recurrence interval from intensity or depth. All examples are hypothetical. Observed provenance is caller-declared, with no empirical validation performed. This is spatially uniform forcing; a station observation is not proof of an areal field. No spatial interpolation, gauge bias correction, calibration or safety/design claim is supplied. A verified local IDF source and documented station quality/representativeness would be separate inputs to a future design-event workflow.

## Verification

Command: `.venv/bin/python -m pytest tests/test_flood_rainfall.py`

Result: **49 passed** (16 October 2026). Covers six unit conversions, precipitation-depth/volume conservation under multiple rebinning timesteps, weighted overlapping intervals, partial final steps, cumulative depth, explicit irregular inputs, offset/DST handling, immutable inputs, CSV fixtures and validation failures (missing/overlapping times, blank/invalid measurements, negative/nonfinite rates, invalid timestep/units and overflow). Fixture checks do not establish local calibration. Shared application tests and full pilot integration remain the integration chat's responsibility.
