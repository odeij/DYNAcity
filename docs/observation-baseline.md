# Tari'ak temporal-holdout baseline

`src/dynacity/observation_baseline.py` and `scripts/evaluate_tariak_baseline.py` fit simple
climatology baselines on Tari'ak probe speeds from calibration years and score them on held-out
years. It exists to give any later traffic claim a historical reference to be compared with.

## What this is not

- **Not a simulation** and not an empirical SUMO validation. No simulated value is involved or fabricated.
- **Not a target already achieved** and not an irreducible noise floor. A better model could beat it.
- The calibration **p10-p90 band is a spread of individual probe readings**, not a confidence interval
  or measurement uncertainty.
- **The existing traffic model already used this data** (free-flow limit, initial density, `speed_check`).
  This split cannot make that model retroactively independent.
- The holdout is from the **same probe system**. Probe sampling bias (vehicle mix, areas, hours, app
  users) and spatial dependence between neighbouring ways are unquantified. Readings are pooled individual
  probe points: points from one trip are not independent, cell medians are not time-weighted, and nothing here
  is a survey measurement uncertainty.
- Describes probe speeds on mapped OSM ways in 2015-2019, not current conditions. No season, current-conditions
  or multimodal claim.
- **Calendar pooling.** Each cell pools every date of its years: months, seasons, public holidays, school
  terms and the weekday mix are not matched between calibration and holdout; day type is calendar-only.
- **File-wide scores are not Beirut targets.** The data package says the velocities resource is "confined to
  the country of Lebanon". The default run scores every way in the file; the file's geographic footprint is
  not verified here and the date range says nothing about geography. A simulator of one area must not be
  compared with file-wide errors.
- Tolerances, if supplied (`--tolerance-absolute/-relative`), are illustrative settings supplied in the run configuration,
  not accepted city accuracy targets.

## Fixed configuration (not a preregistration)

Set in `BaselineConfig` (defaults in the module) or on the command line, and hashed (`config_sha256`) before
the source is read in that process; nothing is tuned after scores are computed. This is **not an external
preregistration** and gives no evidence either way about whether anyone inspected the data earlier (the
existing traffic model already used it). The JSON records the actual years, hashes and any deviation from the
code defaults (`fixed_in_configuration_before_computation`, `preregistration`, `deviations_from_code_defaults`).

| Setting | Code default | Note |
|---|---|---|
| Calibration years | 2015, 2016, 2017 | configurable, must be disjoint from holdout |
| Holdout years | 2018, 2019 | must all be later than every calibration year; backwards or interleaved splits are refused |
| Minimum count | 20 | applies to held-out cells and to calibration groups; smaller ones are unscored and counted |
| Speed cap | 300 (file units) | larger readings are rejected and counted |

Scoring cell: (OSM way, day type, local hour); day type is calendar Mon-Fri vs Sat-Sun (holidays not
modelled). Baselines (all medians of calibration readings only): `global_hour`, `global_daytype_hour`,
`road_hour`, `road_daytype_hour`, `road_overall`. Day-type variants are included because the cell
definition already separates day types, so the comparison shows whether the split helps.

Metrics per baseline: MAE, median absolute error, signed bias, reading-weighted MAE, median
predicted/observed ratio (cells with observed 0 are excluded and counted), the optional p10-p90 band
check (held-out cell medians and held-out readings inside), and the same metrics on the cells scored by
*every* baseline so the denominators are comparable. A baseline with no calibration support for a cell
leaves it unscored (no fallback); `coverage_of_eligible_cells` and the below-minimum cell and reading
counts are always reported. A missing or empty calibration/holdout, or no eligible held-out cell, gives
`status: blocked` with a reason (`no_calibration_observations`, `no_held_out_observations`,
`no_eligible_held_out_cells`), exit code 2, and no scores.

## Source, period and unit

- Source: `C:/private/dynacity-data/traffic/tariak/velocities.csv` (Lebanon Traffic Dataset, Tari'ak),
  ODbL-1.0; attribution "Traffic speeds: Lebanon Traffic Dataset (Tari'ak), ODbL. Roads (c)
  OpenStreetMap contributors, ODbL." Source SHA-256, size, the sibling `datapackage.json` and `LICENSE.md`
  hashes are recorded in the JSON. No raw data is copied into git.
- **Period.** The data package describes the *whole dataset* as 2014-2019 (all Lebanon). *This file* has
  valid rows from 2015-03-20 to 2019-10-17 and **no 2014 rows**. 2019 is partial. This says nothing
  about geographic coverage; `traffic.py`'s "2015-2019 inside Beirut" is a statement about its own extract,
  not evidence about this file's footprint.
- **Row count.** The file has 6,006,401 data rows (the last line has no newline, so `wc -l` and the
  assessment's 6,006,400 undercount by one). All pass validation; 0 rejected.
- **Time.** Date/Time are Asia/Beirut local clock per the data package; the recorded local hour is used,
  with no UTC conversion or daylight-saving disambiguation.
- **Unit is unresolved.** The data package says Velocity is metres/second, not km/h; `traffic.py` defaults
  to km/h. Circumstantial evidence in the JSON: maximum value 119.999, 53% of readings at or above 40,
  which is implausible as m/s (432 km/h maximum) and ordinary as km/h. The evaluator makes no unit
  assumption: MAE is in the file's numeric units, ratios are unit-free. Resolving this needs the data
  owner or a documented source; traffic code that assumes km/h should not cite this file as proof.
- Zero speeds: none exist in this file (its maximum, 119.999, suggests upstream filtering at 120; not verified). Stationary readings would be kept (unlike `traffic.py`, which drops
  readings below its moving floor), so values are not comparable to its medians.

## Validation of rows

Every row is checked in this order and counted once under the first failure: `malformed_row` (field count,
csv error, blank), `missing_date`, `bad_date` (strict `YYYY-MM-DD`, real calendar date), `missing_time`,
`bad_time` (`HH:MM:SS`, hour < 24), `missing_way_id`, `bad_way_id` (`way/<=12 digits>`), `missing_speed`,
`invalid_speed` (non-numeric or NaN), `nonfinite_speed`, `negative_speed`, `speed_above_cap`. A bad header
aborts. Valid rows from years in neither split are counted and excluded.

## Output safety

Missing or unreadable inputs print a clear message, exit 1 and write nothing. A folder that already holds a
completed `observation-baseline.json` is rewritten only by a run with the same source hash and configuration
hash (including the way-set hash); a different source, configuration or way set is refused (exit 3) with a
request to use a new `--output` folder, and an unreadable existing result is never overwritten. A blocked
result may be replaced. Exit codes: 0 completed, 1 bad input, 2 blocked, 3 refused. The script writes only
`observation-baseline.json`, `results.md`, `run-metadata.json` and `manifest.json`; `handoff.md` and
`run-log.txt` in the evidence folder are hand-written owner notes.

## Reproducibility and cost

`observation-baseline.json` has no timestamps and is byte-identical across reruns and chunk sizes (tested).
Run times, command, peak memory and the SHA-256 of each run's JSON go to `run-metadata.json` (append-only
history). Memory: the CSV is streamed in 200,000-row chunks and hashed in 1 MiB blocks; compact valid
readings of the two split year-sets (about 6 M rows x 10 bytes) are retained, so memory grows with the
retained rows, not the raw file. Peak memory is the Windows `PeakWorkingSetSize` high-water mark read at
process exit (`resource.ru_maxrss` elsewhere; otherwise reported unavailable).

## Observation package: deliberately not exported

`validate_observations()` expects matched continuous intervals. The held-out values here are medians over
discontiguous weekday/hour slices across 2018-2019, so encoding them as time intervals would misstate what
was measured, and the baseline predictions are not simulation output. Wiring a simulator to this split
(milestone 2) must define honest non-overlapping intervals itself and label baseline predictions
`historical_baseline`. Missing holdouts are reported by this tool as blocked.

## Domains

Two runs are kept, in separate folders under `C:/private/dynacity-data/engine/goal-20261031/02-baseline/`:

| Folder | Domain | Selected valid rows | Notes |
|---|---|---|---|
| `lebanon-file-wide/` | every way in the source file (national dataset, footprint unverified) | 6,006,401 | corrected-label rerun of the first run |
| `beirut-network/` | the 5,569 unique `roads[*].way` IDs of `traffic/beirut-traffic.json` (10,817 segments) | 996,830 (5,009,571 valid rows on other ways excluded; 2,858 selected ways have rows, 2,711 have none) | `--traffic-model` |
| top level (first run) | whole file | 6,006,401 | superseded, kept unchanged for provenance; same numbers as `lebanon-file-wide/` |

`--traffic-model <model.json>` reads **only** `roads[*].way`, de-duplicates the IDs (segments share ways),
and rejects an unreadable, empty or malformed model (no roads list, non-integer, non-positive or
over-12-digit way ID). The model's speeds, readings and density are never read, so they cannot enter
calibration. The model's SHA-256, segment and unique-way counts and a canonical way-ID-set hash (SHA-256
of ascending IDs joined by newlines) are in the JSON `domain` block and in the config hash. Valid rows on
other ways are counted and dropped *before* the year split and before any fitting; held-out cell
denominators therefore refer to the selected ways, and there is no fallback to file-wide data. Counts: rows
rejected as malformed, valid rows outside the domain, and in-domain valid rows outside both split-year sets
are three separate numbers; `valid_rows`, by-year, by-hour and unit-evidence statistics stay file-wide and
are labelled so. The way-set run is a score over mapped traffic-model ways with enough readings, not a
statement about the whole city.

## Run

```powershell
.venv/Scripts/python.exe scripts/evaluate_tariak_baseline.py --output C:/private/dynacity-data/engine/goal-20261031/02-baseline/lebanon-file-wide
.venv/Scripts/python.exe scripts/evaluate_tariak_baseline.py --traffic-model C:/private/dynacity-data/traffic/beirut-traffic.json --output C:/private/dynacity-data/engine/goal-20261031/02-baseline/beirut-network
.venv/Scripts/python.exe -m pytest tests/test_observation_baseline.py -q
```

The script's built-in default output is the top-level `02-baseline` folder, which holds the preserved first
run (superseded, marked by `superseded.json`). A default-output run there is refused because its configuration
identity differs, so always pass an explicit `--output`.

**Evaluated network.** The way-set run uses the saved file `beirut-traffic.json` (4,185,100 bytes, SHA-256
`c66721e3adc0...` in the JSON), which is a different file from the current default traffic model
`beirut-traffic-20261024.json`. No date is claimed for it from file timestamps. Way counts and scores describe
that saved snapshot only; a run on the current default model would be a new run.

Tests use only synthetic CSVs in temporary folders; they never write results to the private evidence
folder and nothing they produce is a real result.
