# Run a documented flood simulation from the city

The city now connects input validation, background ANUGA execution, real progress, cancellation and native-mesh result playback. This is a runnable software path when a complete documented input package is supplied. **No runnable Beirut package is currently available.** The acquired historical survey/airport data inventory is not sufficient and is explicitly refused as a simulation package. No example storm, made-up drain network or default physical array is generated.

Optional [two-way SWMM coupling](coupling.md) accepts an additional documented INP network and inlet/overflow mappings in the same package. Configure the pinned native library through `DYNACITY_SWMM_LIBRARY`; the input-status API reports its verified availability. Native velocity vectors are exported alongside speed/depth, with dry velocities still missing. Coupled execution changes the computed water depths; it does not require a separate invented flood animation.

## Local session

Keep the normal city launch configuration and add:

```sh
--flood-runs /absolute/private/path/flood-runs \
--flood-context /absolute/private/path/real-input-handoff.json
```

Both options are local-only. Run storage must be outside the repository. The current local preview on port 8767 has execution enabled with `/tmp/dynacity-flood-runs`, retaining its normal basemap, buildings, terrain, traffic and evidence configuration. Legacy viewer terrain is not silently used as solver terrain.

Open **Disasters → Flood → Load simulation inputs**, choose a documented ZIP and wait for validation. The **Run flood** button becomes available only when all required files and inputs pass. Selecting it launches ANUGA in a separate process. The browser reports actual simulation time, saved-frame progress and terminal failure/completion. **Cancel run** terminates the worker and refuses partial output. Jobs have a 15-minute time limit, enforced even if the browser closes; one solver process runs at a time. Shutdown terminates an active worker. Invalid packages never create an accepted run.

After completion, the map loads the actual native triangles and stored depths at their original timestamps. It initially shows the output with greatest computed stored water, then allows timeline selection and **Play results**. No intermediate hydraulic state is invented. Velocity remains undefined for numerically dry/shallow cells. Colors represent depth; the overlay does not claim an absolute water-surface elevation aligned to the basemap or photorealistic mesh. Native conservation residual, engine version, run identity and producer scope are visible. Completing a numerical run does not establish independent calibration.

Historical survey observations are collapsed into a separate optional section. They do not supply mesh heights, rainfall, pipe capacity, closures or intervention effects. Missing acquired model inputs are listed in **Required inputs & current gaps**.

## Prepare an input package

Use the existing [solver contract](solver.md) and [JSON schema](solver-manifest.schema.json). When genuine inputs have been assembled, this helper validates the original files and makes a portable ZIP without altering physical arrays:

```sh
.venv/bin/python -m dynacity.cli flood-package \
  --manifest /absolute/private/path/verified-run.json \
  --output /absolute/private/path/new-inputs.zip
```

The ZIP contains `manifest.json` at its root. Mesh, forcing and every referenced source file are included with package-relative paths. Existing packages are never overwritten. Benchmark-purpose inputs cannot be packaged for the city. The helper is not a data generator and cannot turn the acquisition inventory into numerical input.

Alternatively build that self-contained ZIP directly. Absolute paths, traversal, symlinks, duplicate archive paths, encrypted entries, missing files and truncated geometry are rejected. Limits are 50 MB uploaded, 200 MB expanded package/combined numerical arrays, 20,000 triangles and 240 output times. The session accepts at most 20 checked packages. The loader checks physical-array validity, complete boundary assignment, time coverage, source hashes and real-data purpose before enabling execution; checksums identify bytes, not the scientific truth of a producer's claims.

## API and artifacts

| Endpoint | Operation |
| --- | --- |
| `GET /v1/flood/solver/inputs` | Run configuration, input schema and currently missing acquired inputs |
| `POST /v1/flood/solver/packages` | Upload ZIP bytes; validate and create a ready job, or return explicit 422/413 refusal |
| `POST /v1/flood/solver/jobs/{id}/start` | Start a checked package once |
| `GET /v1/flood/solver/jobs/{id}` | Actual process/progress/terminal state |
| `POST /v1/flood/solver/jobs/{id}/cancel` | Terminate an active process; accept no partial results |
| `GET /v1/flood/solver/jobs/{id}/geometry` | Checksum-verified native result geometry and metadata, only after completion |
| `GET /v1/flood/solver/jobs/{id}/frames/{index}` | Computed depth and speed at one exact stored output time |

Jobs retain `inputs/`, `solver.log` and `output/` under their generated private run directory. The engine writes atomic progress snapshots and a terminal audit; successful results retain their output SHA-256. Benchmark outputs, failed conservation, interrupted runs and altered output files cannot be served as city results. The in-memory job registry belongs to one server session. Page reloads can resume a known job in that session; server restarts do not reconstruct the registry, though files remain on disk. Temporary `/tmp` storage is not a permanent archive.

## Verification and remaining limit

`tests/test_flood_jobs.py` verifies package refusal, path confinement, incomplete-input reporting, local-only access, cancellation/timeouts and output-purpose enforcement. The real worker executes a mathematical ANUGA benchmark in temporary test storage; the city boundary then correctly refuses to publish it as city flooding. Native export unit tests inspect genuinely computed mathematical output without bypassing the production purpose gate in the running app. Browser-side tests cover native dry/missing/wet cells, stale-frame refusal and pause behavior during an in-flight request. Existing solver benchmarks separately test physical numerics, wetting/drying and conservation.

The original local API verification reported execution enabled and 11 unavailable acquired model-input categories. Uploading the actual acquired inventory returns 422: it has no hydraulic mesh or matched event forcing. Browser inspection confirmed the controls in the normal city view, with Run flood disabled until a complete package passes. The system file picker could not be automated through the protected host app; refusal was verified through the local API. The later two-way coupling has been exercised with the actual native engines on mathematical fixtures. No successful real-data Beirut run or observation calibration has been demonstrated.

The remaining acquisition requirements are recorded in [data-inventory.md](data-inventory.md); a concrete unsent request is in [data-request.md](data-request.md).
