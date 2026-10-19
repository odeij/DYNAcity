# ANUGA surface-water runner

Implemented 16 October 2026 in `src/dynacity/flood_solver.py`. This executes the real ANUGA 3.3.7 finite-volume shallow-water engine. It does not supply a Beirut drainage network, calibrated urban terrain, observed event, or validated forecast. No missing physical input is filled with a numerical default. See `data-inventory.md` for the separate real-data acquisition work.

19 October update: optional [two-way EPA SWMM coupling](coupling.md) now replaces prescribed exchange when a documented network and inlet mapping are supplied. It is native-engine tested on mathematical fixtures; no real Beirut coupled run has been supplied or validated.

## Execution

The city can now validate portable input packages, execute ANUGA in a background process and play back native mesh results. See [run-workflow.md](run-workflow.md). Missing Beirut inputs remain missing; the acquired survey inventory is not a runnable package.

Install the optional project runtime with `.venv/bin/python -m pip install -e '.[flood]'`. ANUGA 3.3.7 was installed and actually exercised on the local Python 3.13 interpreter. This is a sequential runner; do not launch it through MPI.

Use an existing documented input manifest and a new output directory outside the checkout:

```sh
.venv/bin/python -m dynacity.cli flood-run \
  --manifest /absolute/private/path/run.json \
  --output-dir /absolute/private/path/new-run
```

`python -m dynacity.flood_solver` accepts the same arguments. The command never constructs a demonstration. It validates all files before engine launch, refuses missing arrays and nodata, and never overwrites an existing run directory. A caller must prepare the mesh from documented source geometry; there is no automatic inference of drains, building walls, culverts, or underpass connections.

## Manifest contract

The machine-readable contract is `solver-manifest.schema.json`, generated from `RunManifest`. Required fields:

- `schema_version`, `run_id`, `purpose` (`real_data` or `numerical_benchmark`), timezone-aware `start`, projected metre `crs`, and documented `vertical_datum`.
- `mesh` and `forcing`: NPZ paths, relative to the manifest or absolute. Files load without Python pickle.
- `boundary_edges`: `[triangle_index, local_edge_index, tag]` for **every** external mesh edge, including holes. Local edge `e` is opposite local triangle vertex `e`. Internal edges, duplicates, missing edges, unused tags and unconfigured tags are rejected.
- `boundaries`: tag to `kind` (`reflective`, `transmissive`, or `stage_momentum`). The last requires `values`: one triple `[stage_m, xmomentum_m2_s, ymomentum_m2_s]` per forcing time, interpolated linearly with no extrapolation. Stage must share the bed's vertical datum. Wall/open choices must be justified by source evidence; boundaries are never guessed from the extent.
- `sources`: exactly `terrain`, `mesh`, `roughness`, `initial_conditions`, `rainfall`, `infiltration`, `drainage_exchange`, `boundaries`, `structures`. Each requires an existing `path`, matching SHA-256, `description`, and evidence classification (`measured`, `derived`, or test-only `analytical_benchmark`). A real-data run rejects benchmark evidence. A source file can document justified derivations or the documented treatment/exclusion of a structure; it cannot replace missing values with assumptions. Checksums identify files, not the truth of producer claims. Downstream survey review is still required.
- `scope`: explicit coverage, structure representation, exclusions and remaining limitations.
- `numerics`: selected `DE0`/`DE1`/`DE2` algorithm, CFL, maximum timestep, minimum numerical depth, absolute and relative water-balance tolerances. These are numerical controls, not assumed physical inputs. Timestep geometry always uses the true triangle inscribed radius to handle elongated cells. Other engine algorithm settings follow the pinned engine version.

## Numerical arrays

Let P be vertex count, N triangle count, and K forcing interval count.

| Mesh NPZ array | Shape | Meaning |
| --- | --- | --- |
| `points_m` | P × 2 | Absolute projected x/y coordinates |
| `triangles` | N × 3 integers | Counterclockwise, nondegenerate vertex indices |
| `elevation_m` | N × 3 | Bed elevations at each triangle's three vertices; discontinuous beds allowed |
| `friction` | N | Nonnegative Manning roughness per triangle, explicitly supplied |
| `initial_stage_m` | N | Water-surface elevation at triangle centroids, at or above mean bed |
| `initial_momentum_m2_s` | N × 2 | Initial x/y depth-integrated momentum; dry cells require zero momentum |

| Forcing NPZ array | Shape | Meaning |
| --- | --- | --- |
| `times_s` | K + 1 | Seconds from start; begins at zero, strictly increasing |
| `rainfall_m_s` | K | Nonnegative, spatially uniform gross precipitation rate |
| `infiltration_capacity_m_s` | K × N | Nonnegative prescribed infiltration capacity |
| `drainage_exchange_m3_s` | K × N | Prescribed signed exchange per triangle: positive to surface, negative withdrawal |

All values must be finite. Explicit measured/documented zeros are valid; missing is not zero. Mesh connectivity, duplicates, orientation, nonmanifold edges and triangulation topology are checked. Coordinates are translated to a local origin internally and preserved absolutely in output. No terrain smoothing or elevation/datum conversion occurs.

All forcing arrays represent interval averages over `[times_s[i], times_s[i+1])`. The solver stops at every change before the next rate is applied, avoiding time interpolation across a rainfall discontinuity. Output contains the initial state and all interval endpoints. Choose documented forcing resolution and an output schedule appropriate to observed storm dynamics; the runner does not create sub-hourly observations from daily totals.

Operators apply rain, then water-limited infiltration, then prescribed drainage each internal step. Infiltration is a capacity-limited sink, not a Green-Ampt soil implementation. Gross rainfall must not already have infiltration subtracted. A prescribed withdrawal that exceeds water in **any individual cell** fails the run; it cannot silently become an invented smaller drain flow.

## Outputs and acceptance

`surface.npz` preserves native triangles, vertices, areas, bed elevations, times, centroid stage/depth, x/y momentum, velocity, storage, cumulative rain/infiltration/drainage volumes, signed boundary/operator volume and water-balance residual. Velocity is momentum/depth; dry and numerically shallow cell velocities are undefined (NaN), never supplied as measured zero. `manifest.json` preserves the producer declaration; `audit.json` records actual input/output hashes, engine version, numerical choices, status and limitations.

At every saved time, the acceptance gate is:

`residual = current_storage - initial_storage - integrated_boundary_flux - integrated_operator_flux`

The permitted residual is the explicitly supplied absolute tolerance plus the relative tolerance times the larger storage/flux scale. Nonfinite states, materially negative depths, impossible withdrawals and failed conservation stop execution. Engine failures leave a failed audit and no accepted result bundle. A numerical pass is not observational calibration.

Results are native mesh output, **not** a regular `HydraulicOutput` grid or the viewer's three-scenario comparison bundle. No nearest-cell or invented grid conversion is performed. The native result API now preserves triangles, exact output timestamps, nodata and numerical provenance for playback without raster interpolation. The three-scenario comparison importer remains separate. The viewer has no Beirut results until suitable documented inputs are supplied and a run passes.

## Verification and limits

Tests in `tests/test_flood_solver.py` execute the installed engine on mathematical benchmarks: a lake at rest over sloping bed, dry-bed rainfall with discontinuous intensity, water-limited infiltration, dam release and wetting, open-boundary volume accounting, and a time-varying stage boundary. A refined dry-bed dam release is compared with the analytical Ritter solution using the engine's gravity constant; area-weighted mean absolute depth error must be below the test's declared numerical threshold. Elongated triangles exercise the corrected timestep radius. Tests also reject missing evidence, missing arrays, unknown datum, geographic coordinates, incomplete boundaries, altered hashes and benchmark-to-real promotion. Impossible drainage must leave a failed audit without results.

These are mathematical numerical tests, kept in temporary test storage. They are never Beirut inputs or public demonstrations. The app's synthetic demo route returns 410; synthetic pilot imports and hypothetical rainfall are rejected by the product boundary. CLI pilot export requires an explicit input, and the drainage demo is disabled. Flood selection opens documented playback; legacy illustrative flood drops/scenario calculations are rejected in the viewer API.

Not supplied/validated for Beirut: surveyed inlet capture parameters and connected pipe network, mesh generation from verified ground/building surveys, spatial rainfall fields, evolving soil infiltration, calibrated boundary model, observational calibration, or mesh-to-viewer raster export. Missing drains must not be represented by a zero-exchange array. A documented run needs supplied dynamic network/inlet inputs, genuine prescribed exchange, or an explicitly evidence-backed scope establishing its exclusion; no such Beirut package has been supplied. See [coupling.md](coupling.md) for the supported dynamic path and its numerical limitations.

Primary engine reference: [ANUGA background and governing equations](https://anuga.readthedocs.io/en/stable/background.html). Runtime API behavior was also inspected in the installed 3.3.7 engine source, including the rate operators, true inscribed radius and native boundary/operator volume accounting.
