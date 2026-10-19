# Drainage/runoff adapter handoff

19 October update: this page describes the original **standalone** runoff adapter. A separate [native SWMM/ANUGA coupling](coupling.md) now supports supplied inlet exchange and surface re-entry inside `flood_solver.py`. The standalone `run_drainage()` API below remains uncoupled. Neither path supplies a surveyed Beirut network.

Integration status: ready

Ready means a tested optional EPA SWMM adapter and explicit synthetic fixtures. It does **not** mean a surveyed, calibrated or validated Beirut drainage model exists. No drains, capacities, imperviousness or soil measurements were inferred from roads/buildings. No existing API, dependencies, viewer or other producer modules were edited.

## Runtime and research

Local inspection on 16 October 2026 found no `runswmm`, `swmm5`, `swmm`, `pyswmm` or `swmm` Python package in the project environment. Python 3.13 and the C compiler were available. For numerical verification, the official USEPA v5.2.4 source archive was downloaded to `/tmp`, extracted and built with `cc`; the temporary executable is `/tmp/dynacity-runswmm-5.2.4`. This temporary file is not a durable project dependency. No requirements changes are necessary for this stdlib adapter. Integration needs a separately installed/built EPA SWMM 5.2 executable; configure `DYNACITY_SWMM_EXECUTABLE=/absolute/path/to/runswmm` or pass `executable=`. The runtime check returns `None` when absent; execution raises `DrainageError`, never fabricated results.

Primary references:

- [EPA SWMM official downloads and engine/API guide](https://www.epa.gov/water-research/storm-water-management-model-swmm). EPA distributes both the engine source and a callable API guide; this component uses the process interface to avoid ABI/Python package coupling.
- [EPA SWMM 5.2 user manual, Appendix D](https://www.epa.gov/system/files/documents/2022-04/swmm-users-manual-version-5.2.pdf). The documented console interface takes input, report and optional binary output paths. This adapter omits the binary path to obtain selected time series in the text report. It emits SI CMS input, Green-Ampt infiltration, dynamic-wave routing, circular conduits, constant-area storage and free outfalls. Metric runoff/routing continuity volumes are reported in hectare-metres.
- [Official USEPA v5.2.4 report implementation](https://github.com/USEPA/Stormwater-Management-Model/blob/v5.2.4/src/solver/report.c) verified the text table labels, node output fields and rounding used by the parser. [Official command-line entry point](https://github.com/USEPA/Stormwater-Management-Model/blob/v5.2.4/src/run/main.c) also confirms invocation. Both process status and report errors are checked because command wrappers may return zero despite model errors.

Temporary verification build (not a required app startup command):

```sh
cc -O2 -I/tmp/Stormwater-Management-Model-5.2.4/src/solver \
  -I/tmp/Stormwater-Management-Model-5.2.4/src/solver/include \
  /tmp/Stormwater-Management-Model-5.2.4/src/solver/*.c \
  /tmp/Stormwater-Management-Model-5.2.4/src/run/main.c \
  -lm -o /tmp/dynacity-runswmm-5.2.4
```

## Public API and units

Module: `dynacity.flood_drainage`. Frozen dataclasses are plain Python objects; adapt upstream rainfall into `Rainfall` explicitly. No import dependency on the other producers.

| Interface | Contract |
| --- | --- |
| `Catchment(id, outlet, area_m2, impervious_fraction, width_m, slope_fraction, suction_mm, conductivity_mm_hr, moisture_deficit, ...)` | Explicit homogeneous basin and outlet. Area m²; width m; fractions 0–1; suction/depression storage mm; saturated conductivity mm/hr; moisture deficit dimensionless. Optional Manning n and depression storage parameters have modelling defaults, which remain assumptions unless supplied. Slope strictly positive. |
| `Node(id, invert_m, max_depth_m, storage_area_m2=0, outfall=False)` | Invert in declared vertical datum, depth m, constant storage area m². Initially empty. Non-storage nodes are junctions. Free outfalls require supplied boundary suitability. |
| `Link(id, upstream, downstream, length_m, diameter_m, roughness, max_flow_m3_s)` | Circular conduit length/diameter m, Manning n, explicit positive flow cap m³/s. Links connect supplied node IDs. |
| `DrainageNetwork(catchments, nodes, links, evidence_kind, provenance, vertical_datum, crs)` | Tuples of objects; evidence is exactly `supplied_survey` or `synthetic_fixture`. Labeling a survey does not certify its accuracy. Provenance must identify survey/source and assumptions. No geometry is stored; CRS/datum are metadata, not transformations. Unknown datum must be explicitly called unknown and prevents defensible cross-model elevation matching. |
| `Rainfall(start, interval_s, intensity_mm_hr, provenance)` | Aware whole-minute timestamp, uniform interval of whole minutes from 60 to 86340 seconds; nonnegative finite intensity tuple. Each value covers `[start+i*interval, start+(i+1)*interval)`. Start is converted to UTC for SWMM. An explicit zero record ends rainfall. No return period inferred. |
| `Intervention(name, capacity_factors=(), added_storage_m2=())` | Tuples `(link_id, positive_multiplier)` and `(non_outfall_node_id, added_area_m2)`. Capacity factors multiply the original cap; added storage is constant area, not volume. Baseline objects remain unchanged. |
| `validate(network, rain)` | Validate evidence, numbers, IDs, references and directed connectivity to outfalls. Raises `ValueError`. |
| `build_swmm_input(network, rain, intervention=Intervention('baseline'), *, drain_down_s=3600, report_step_s=60, routing_step_s=5)` | Returns audited INP string. All three time options are positive integer seconds below one day. Routing step cannot exceed rainfall/report step. |
| `detect_swmm(executable=None)` | Explicit path/name, then environment, then PATH discovery. Returns executable path or `None`; explicit missing path never silently selects another solver. Discovery is not engine verification. |
| `run_drainage(network, rain, work_dir, intervention=..., *, executable=None, continuity_tolerance_pct=2.0, timeout_s=120, **time_options)` | Creates a **new** directory; never overwrites one. Writes input, manifest, report, solver log and result JSON. Returns `DrainageResult`, or raises `DrainageError` for missing/failed solver, missing/invalid continuity, or continuity outside tolerance. Invalid inputs raise `ValueError`. Existing output folder raises `FileExistsError`. Use an absolute private path outside Git for every run. |
| `compare_drainage(network, rain, work_dir, cases, **run_options)` | Runs automatic baseline plus uniquely named interventions in `case_0`, `case_1`, etc. Same forcing, network and numerical options throughout. Returns `{name: DrainageResult}`. Fails if any run fails; diagnostic artifacts remain. |
| `parse_continuity(report_text)` | Returns runoff/routing volume dictionaries in m³ and their signed internal continuity error percentages. Rejects incomplete/nonmetric tables. |
| `read_node_timeseries(report_path)` | Returns `{node_id: tuple[NodeSample, ...]}`. Samples contain UTC timestamp, inflow/overflow m³/s, depth above invert m and hydraulic head in declared datum m. These are instantaneous report samples, rounded to 0.001 SI. They are drainage node results; no surface grid or velocity is implied. |
| `synthetic_fixture()` | Returns `(network, rainfall)` with fully invented verification parameters. |

`DrainageResult` includes scenario, common `comparison_key`, exact INP SHA256, reported engine version, evidence/provenance, report path, runoff/routing m³ dictionaries, signed continuity errors and warnings. The comparison key hashes network, rain and supplied numerical options; actual INP hashes distinguish interventions. It does not certify cross-version comparability. Compare through a single `compare_drainage` invocation and retain engine version and manifests. Supplying an explicit default option versus omitting it may yield different comparison keys conservatively.

## Integration example

```python
from pathlib import Path
from dynacity.flood_drainage import (
    Intervention, compare_drainage, read_node_timeseries, synthetic_fixture,
)
network, rain = synthetic_fixture()  # replace with documented supplied survey/input
results = compare_drainage(
    network, rain, Path('/tmp/dynacity-drainage-example-unique'),
    cases=(
        Intervention('blocked_capacity', capacity_factors=(('P1', 0.1),)),
        Intervention('improvement', capacity_factors=(('P1', 4.0),),
                     added_storage_m2=(('S1', 500.0),)),
    ),
    executable='/tmp/dynacity-runswmm-5.2.4',
)
base_overflow_m3 = results['baseline'].routing_m3['Flooding Loss']
node_samples = read_node_timeseries(results['baseline'].report_path)
```

For real work use a durable private data directory outside the checkout, not `/tmp`. No private inputs, runtime binary or generated simulation artifacts are added to Git by this workstream.

## Numerical checks and coverage

Executed:

```sh
DYNACITY_SWMM_EXECUTABLE=/tmp/dynacity-runswmm-5.2.4 \
  .venv/bin/python -m pytest tests/test_flood_drainage.py
# 13 passed
.venv/bin/python -m pytest tests/test_flood_drainage.py
# 10 passed, 3 skipped (optional solver absent from PATH/environment)
```

Checks cover unit conversion, rainfall interval termination, UTC conversion, evidence/parameter/connectivity rejection, immutable interventions, unavailable runtime, zero-status solver errors, solver timeout, incomplete reports, zero rain, pervious infiltration response, actual SWMM scenario comparisons, time series, volume conservation including retained storage, continuity rejection and routing timestep sensitivity (5 versus 2 seconds). A 2% continuity tolerance is a configurable adapter gate, not an EPA safety criterion or validation claim.

Synthetic basin: 100,000 m², 70% impervious, 60 mm/hr for 30 minutes, one storage node and pipe/free outfall; one-hour drain-down. All are arbitrary fixture assumptions.

| Case | Rain m³ | Runoff m³ | Routing outflow m³ | Overflow loss m³ | Final routing storage m³ | Runoff / routing error % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 3000 | 2100 | 1080 | 1010 | 10 | -0.057 / -0.159 |
| cap ×0.1 | 3000 | 2100 | 160 | 1800 | 140 | -0.057 / -0.077 |
| cap ×4, storage +500 m² | 3000 | 2100 | 2040 | 0 | 60 | -0.057 / -0.063 |

Volumes are read from SWMM's three-decimal hectare-metre tables, hence quantized to 10 m³. Budgets allow rounding tolerance and use internal continuity percentages for acceptance; do not interpret sub-10 m³ differences as resolved. All three runoff budgets match under identical forcing; routing interventions affect drainage. Residual water is retained storage, not assumed drained away at simulation end. Node report sampling can miss peaks; do not integrate it as an exact overflow volume.

Blockage is explicitly a **flow-cap sensitivity** at supplied links; it is not a physical sediment/inlet obstruction model. Exact zero cap is rejected because SWMM interprets zero maximum flow as unlimited; no tiny-value substitution is made. A higher cap cannot exceed hydraulic conveyance allowed by geometry, heads and roughness. Improvements here modify cap/storage only; altered diameters, invert levels or soil assumptions require an explicitly revised input network and are not automatically comparable through the existing intervention API.

No inlet capture, sea-level/tidal boundary, pumps, control rules, LID, groundwater, sewage baseline, spatial rainfall variation, surface re-entry or 1D/2D coupling is implemented. Evaporation is zero; initial stores are empty. Overflow leaves the drainage calculation, so it is **not** mapped flood depth. No spatial validity/depth/velocity raster can be returned by this component. Integration must establish surveyed inlet catchment routing and matched datum/geometry and use a separately supported coupling method before passing overflow to a surface solver; otherwise expose drainage summaries only. There is no point-cloud detail or independent observed-data validation.
