# Two-way surface / pipe coupling

Implemented 19 October 2026. The ANUGA surface runner can now exchange water
dynamically with the actual EPA SWMM 5.2.4 dynamic-wave engine. This is a
numerically tested coupling implementation, not a calibrated Beirut flood model.
No Beirut inlets, pipes, capacities, blockage percentages or observed flood depths
were invented. The UN-Habitat street-condition/ponding records do not supply a
connected hydraulic network, and the displayed LAS cloud is not ground terrain.

## Physics and the exchange step

- ANUGA evolves depth and horizontal momentum on the supplied native triangular
  mesh, including bed slope, Manning resistance, wetting, rainfall, explicit
  water-limited infiltration and supplied boundary conditions.
- SWMM evolves the supplied one-dimensional pipe/storage network with dynamic
  wave routing, hydraulic heads, pipe conveyance and explicit outfall boundaries.
- At each exchange time, compare the surface stage in each mapped inlet cell
  with the sewer node's hydraulic head, in the **same declared vertical datum**.
  Flow may enter the sewer or reverse onto the street. No head difference, no
  head above the inlet rim, or a fully blocked opening gives no inlet exchange.
- The current quasi-steady inlet law takes the smaller of the supplied weir and
  orifice capacities: `Cw * width * upstream_head_above_rim**1.5` and
  `Cd * opening_area * sqrt(2*g*head_difference_above_downstream_or_rim)`.
  `g` comes from the surface engine. The open fraction multiplies both capacities.
  This uniform obstruction treatment is a modelling approximation, not a
  detailed grate/capture/sediment model. Coefficients require survey/calibration
  or documented derivation. None are given Beirut defaults.
- Aggregate requests jointly across shared cells and nodes. Surface withdrawals
  cannot exceed available cell water; reverse withdrawals cannot exceed current
  node storage. Multiple inlets cannot each claim the same available volume.
- Remove/add the surface volume once, preserving proportional momentum on
  withdrawal and adding no horizontal momentum for vertical re-entry. Supply
  the corresponding net node inflow to SWMM; evolve both systems to the same
  exchange time. Water remains stored in nodes and pipes until actually discharged.
- Return sewer flooding to each node's **explicitly mapped** surface cell.
  Integrate node overflow with the native internal routing steps and the same
  half-old/half-new quadrature as SWMM's global mass budget. The endpoint-based
  `NodeStats.volFlooded` is deliberately not used as that budget. No external
  report sampling or rounded report-table volumes are used for coupling.

The split is explicit, not a monolithic implicit solver. Millisecond-aligned
exchange steps, fixed SWMM routing steps and forcing endpoints must align.
Native SWMM substeps cannot exceed the exchange interval; an incompatible
`MINIMUM_STEP` fails. ANUGA retains its own CFL-limited substeps. Timestep
refinement and empirical validation remain necessary, particularly for rapid
head reversals, surcharging and shallow flow. A water-balance pass alone does
not establish accuracy or validate the inlet law.

## Documented inputs

The existing `RunManifest` optionally accepts `drainage: Coupling`. Its generated
JSON schema is in [solver-manifest.schema.json](solver-manifest.schema.json).
All fields except the entire optional coupling object are explicitly supplied:

- `engine: swmm-5.2.4`; local `network` INP path; CRS and vertical datum matching
  the surface model; exchange `step_s`; absolute and relative volume tolerances.
- `inlets`: unique ID, supplied node ID, surface cell index and measured/derived
  x/y location inside that triangle; rim elevation; opening area; effective weir
  width; weir/orifice coefficients; blockage fraction. Every quantity has units
  in its schema field name. Blockage 1 closes the opening in both directions.
- `overflow_cells`: exactly one mapped receiving triangle for every non-outfall
  sewer node, including manholes without surface capture inlets.
- The original required evidence files and scope remain required, including the
  drainage evidence. The actual network file is additionally hashed. A datum
  string must be explicitly matched; this is not an automatic vertical transform
  or certification of the producer's assertion.

The forcing NPZ retains its `drainage_exchange_m3_s` field for compatibility;
it must be explicitly zero **when dynamic coupling is supplied**, since the
network computes the exchange. Nonzero prescribed exchange plus dynamic coupling
is rejected as double counting. An absent network cannot be replaced by these
zeros for a claim that Beirut drains have been represented.

The initial supported INP is self-contained routing-only SI CMS, DYNWAVE,
`ALLOW_PONDING NO`, `IGNORE_RAINFALL YES`, `IGNORE_RDII YES`,
`IGNORE_GROUNDWATER YES`, `SKIP_STEADY_STATE NO`, `VARIABLE_STEP 0`.
Start/report time must match the surface UTC start, and the network end must
exceed the surface duration. Supported sections are TITLE, OPTIONS, JUNCTIONS,
STORAGE, OUTFALLS, CONDUITS, XSECTIONS, COORDINATES, VERTICES and inline TIMESERIES. External temp
paths, files, hot starts and duplicate sections/options are rejected. Outfalls
can be explicitly supplied FREE, NORMAL, FIXED or TIMESERIES. A time-varying
outlet level can restrict discharge, cause backwater and affect surface water
through the connected network. This uses SWMM's native stage-series boundary;
no sea level is automatically supplied. Pumps, repeating tidal curves,
control rules, a separate subcatchment runoff calculation and nested external
files are not supported by this coupling version. They are never silently
substituted. Conduits and native structure representations still need review.

For TIMESERIES outfalls, include the series ID and an explicit YES/NO flap gate.
Every inline series row must contain `ID MM/DD/YYYY HH:MM:SS level_m` in the
surface run's UTC clock and shared vertical datum. Dates may not be omitted.
Only referenced outfall series are allowed; each needs at least two finite
levels with strictly increasing times spanning the complete surface simulation.
Native SWMM interpolates within these supplied records. Missing coverage,
duplicate timestamps, nonfinite stages and external FILE series are rejected
before execution. No boundary endpoint is extrapolated to fill a missing record.
The original level observations/derivation still require drainage/boundary
evidence; a correctly formatted series is not proof of a measured coastal level.

`flood-package` includes the INP in the ZIP. The normal real-data-only city gate,
safe ZIP paths, bounded native mesh, subprocess timeout and cancellation apply.
Analytical benchmarks cannot be packaged or played as Beirut results. Coupling
history has a two-million-scalar-record budget including diagnostic overhead.

## Runtime and output

Build a durable local runtime from an **existing official EPA v5.2.4 source tree**:

```sh
.venv/bin/python scripts/build_swmm_library.py \
  --source /path/to/Stormwater-Management-Model-5.2.4 \
  --output /private/runtime/libswmm-5.2.4.dylib
export DYNACITY_SWMM_LIBRARY=/private/runtime/libswmm-5.2.4.dylib
```

Use `.so` on Linux. The script compiles the original engine and the small bridge
that reads native unrounded volume totals. It records every source/header hash,
bridge hash and compiled-library hash, and refuses to overwrite an output.
It does not alter engine equations. A library is never accepted from an uploaded
input package. The local runtime used here is under the private artifacts root,
not Git. Runtime version and bridge API are verified before execution. SWMM has
global native state: one live instance per process; job isolation is mandatory.

The regular `surface.npz` includes actual depth, momentum, velocity and signed
surface exchange. `drainage.json` includes output-frame native heads/storage/
integrated outflow and each exchange-step inlet transfer and volume diagnostics.
Positive inlet transfer is into the sewer. Capture/backflow budget totals are
netted per node because that is the rate SWMM receives; individual inlet volumes
remain recorded even when two inlets at one node have opposing flows. Native
SWMM negative external inflow is booked as outflow; that internal transfer is
subtracted when reporting actual outfall discharge, so it is not counted twice.

Acceptance checks run every exchange step: native sewer storage/flux balance,
requested-versus-native API inflow delivery, returned-versus-native overflow,
and combined surface/sewer storage against rain, infiltration, surface boundary,
outfall and native sewer losses. The sewer's tolerance scale is separate, so a
large surface lake cannot hide a bad small sewer budget. The surface native
conservation gate remains independent. SWMM's time integration can yield an
exchange-delivery residual; it is checked and recorded, never silently corrected
by inventing water. All actual maxima and runtime hashes are audited. Failures
leave a failed audit and no accepted surface artifact. Tolerances are explicit
numerical choices, not empirical flood validation or an EPA safety criterion.

## Verification

`tests/test_flood_coupling.py` executes both installed native engines on private
mathematical fixtures. It covers free capture, full inlet blockage, pressure
backflow, pipe conveyance changes, mapped overflow re-entry, shared availability,
datum/mapping rejection, double-counting refusal, unsafe INP refusal, failed
native-budget rejection and exchange-step refinement. The fixture gate is
explicitly 0.003 m³ absolute plus 0.5% relative; this is a small numerical test
choice, not a Beirut accuracy claim. Native surface-only tests retain their
separate tighter tolerances. One abrupt initially dry large-conduit test fails
the native volume gate and is deliberately rejected. No accepted output from
that case is published. Results do not validate arbitrary network configurations.
Time-series boundary checks also verify that a constant inline stage matches
the equivalent fixed boundary, and that a rising downstream stage changes the
coupled surface volume in a pressurised mathematical network. These stages are
test inputs, not Beirut sea-level records or a storm scenario.

Primary references: [EPA SWMM and API downloads](https://www.epa.gov/water-research/storm-water-management-model-swmm),
[pinned official 5.2.4 source](https://github.com/USEPA/Stormwater-Management-Model/tree/v5.2.4),
[ANUGA governing equations](https://anuga.readthedocs.io/en/stable/background.html).
The local pinned API, routing, mass-balance and statistics sources were inspected
to check stepping, SI conversions and the difference between overflow quadratures.
