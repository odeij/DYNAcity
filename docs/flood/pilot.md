# Current real-data policy

Updated 16 October 2026: invented public demonstrations are disabled. The demo API returns 410, synthetic pilot imports and hypothetical rainfall are rejected, CLI pilot export requires an explicit input, and the drainage-demo command refuses execution. Flood selection opens documented playback; legacy illustrative flood drops are blocked. Mathematical test fixtures remain isolated from product data. See [solver.md](solver.md) for the new runnable ANUGA backend and its remaining real-input requirements. The historical integration notes below describe earlier fixture work, not current user-facing behavior.

# Neighborhood pilot and app integration

Implementation date: 16 October 2026. This integrates the six producer components after each owned handoff declared `Integration status: ready`. It does **not** deliver a calibrated Beirut flood model or a coupled surface/drainage solver.

## User-visible behavior

The main city viewer has a visible **Flood simulation** button opening controls for timestamped depth playback directly on the 3D city. **Compare exposure and budgets** opens the supplementary same-origin planning workspace; **Return to city** closes that workspace without reloading the map. `/flood` also remains directly addressable. Existing flood and rain tiles are labelled sketches, their tray states they are legacy illustrative scenarios, and viewer hazard/scenario API responses carry `flood_mode: legacy_illustrative_water_level`. Existing traffic lane alignment and adaptive replay changes are preserved.

The separate pilot starts **unavailable** unless `serve --flood-pilot /private/pilot.json` loads a validated bundle. A button explicitly loads a synthetic demonstration: 300 × 300 m, EPSG:32636, west/south outer edge `(732800, 3752000)`, nine 100 m schematic cells. Its location is a bounded Beirut demonstration extent, not a surveyed neighborhood catchment or a claim about actual flood-prone streets. The same hypothetical 30 mm/h, one-hour storm is associated with three hand-defined output fixtures: existing capacity, 50% capacity blockage, and 50% capacity increase. The arrays are **not** computed hydraulic responses; intervention differences are not causal predictions. No synthetic buildings, roads, facilities or population are placed on the Beirut map.

The page renders the imported grid north up, a timestamp slider, scenario selector, depths, optional velocity magnitude, provenance, source versus output resolution, missing cells, comparison table, per-asset exposure/closure durations and data gaps. A schematic grid is explicitly labelled; it is not a street basemap or an animated water-level substitute. Private JSON bundles are uploaded to the same local API for validation; this integration does not persist uploads. Keep real artifacts and browser downloads outside Git.

The budget panel imports **original planning run bundles**, allows portfolio selection, a caller-supplied budget and hypothesis, and displays feasibility, evidence, objective-specific changes and learning notes. Costs are assumptions, missing runs fail, and no weighted flood-reduction points or real-world achievement is awarded. Original input/plan fingerprints are checked by the planning component. A fixed-budget challenge journal accumulates iterations and can be downloaded; a changed budget starts a new journal. Planning bundles retain their own original inputs and are not automatically asserted to correspond to the map: inspect the full report's forcing, baseline, settings, coverage and uncertainty. No evaluations are reconstructed by assigning new fingerprints to unrelated model outputs.

## Public interfaces and units

New owned implementation: `src/dynacity/flood_pilot.py`; page: `src/dynacity/flood_pilot_template.html`; tests: `tests/test_flood_pilot.py`. Shared integration: `service.py`, `cli.py`, and a small flood-label/link edit in `viewer_template.html`. No new dependencies.

| Interface | Contract |
| --- | --- |
| `PilotBundle.model_validate(payload)` / `.model_validate_json(text)` | Strict JSON-facing comparison input. Extra fields, nonfinite numbers, negative depth, mismatched schedules, invalid capacity factors, invalid/gapped rainfall, inconsistent grid sizes and out-of-domain asset cell IDs fail. |
| `synthetic_pilot() -> PilotBundle` | Explicitly invented ingestion/UI fixture with no spatial asset inventory or solver claim. |
| `summarize_pilot(bundle) -> dict` | Threshold exposure and closure duration from supplied depth cells, coverage, forcing hash, validation/facility unavailable states and warnings. No loss/fragility/population inference. |
| `rainfall_record(forcing) -> dict` / `validate_rainfall(record) -> RainfallForcing` | Connect the ready rainfall component. Explicit UTC interval starts/durations, intensity mm/h, complete provenance; missing rain is not dry. |
| `from_hydraulic_outputs(outputs=..., rainfall=..., rainfall_source=..., label=..., source_resolution_m=..., capacity_assumptions=..., assets=...) -> PilotBundle` | Connect the ready `HydraulicOutput` component. Keys exactly `existing`, `blocked`, `improved`. Requires identical terrain/grid/shape/datum/timestamps, boundary metadata and engine/version, plus the same supplied rainfall source reference and coverage. No solver execution. |
| `intersect_assets(grid, buildings=dict[id, Shapely geometry], roads=dict[id, Shapely geometry], crs=..., provenance=...) -> list[PilotAsset]` | Connect actual caller-supplied projected footprints and road lines. Includes only geometries fully covered by the pilot; excludes partially/outside coverage rather than calling unmodelled portions dry. IDs are prefixed `building:`/`road:`. No road/facility geometry invention. |
| `attach_terrain_evidence(bundle, terrain: TerrainGrid) -> PilotBundle` | Connect the ready terrain component. Exact CRS, cell spacing, bounds, vertical reference and terrain source SHA256 required; no implicit resampling/datum conversion. Retains quality warnings and effective source detail. |
| `drainage_rainfall(forcing) -> flood_drainage.Rainfall` | Depth-conserving 60 s rainfall steps for SWMM; requires whole-minute start and coverage. Gross rainfall only; no runoff duplication. |
| `PlanningRunBundle` / `evaluate_planning_bundle(bundle) -> dict` | Connect ready planning `PlanningChallenge`; retains original `Evaluation` hash echoes. Returns latest iteration, challenge journal, report hash and assumption notice. Optional prior challenge must retain identical experiment, objectives and budget. |
| `evaluate_observation_payload(payload) -> dict` | JSON adapter for ready validation types; same input shape as `load_validation_inputs`. Keeps missing/independent/calibration/holdout/uncertainty rules. Max 10,000 model cells and 240 timestamps. |

Grid: EPSG:32636 projected metres only, south/west **outer** origin, square cell spacing, south-first flattened cell index `row * cols + col`, maximum 2 km per side, 100 rows/columns. Vertical datum and terrain provenance/source resolution are explicit. Resampling adds no source detail. Depth is metres above local ground; zero is valid dry, JSON `null` is nodata. Optional velocity is scalar magnitude m/s; absent is unavailable. Surface adapters convert x/y velocity components to magnitude without inventing direction or treating missing components as zero. Source output metadata remains embedded in each scenario's provenance.

Times require aware UTC. Each frame has `timestamp`, `duration_s`, flattened `depth_m`, optional `velocity_m_s`. Positive-duration frames describe left-held values over the following interval; intervals are contiguous. An optional **terminal** zero-duration frame retains the final sample for peak exposure without extrapolation. Hydraulic snapshot imports preserve all timestamps, assigning durations from adjacent outputs and zero to the last. Threshold duration is therefore a sampled approximation, not exact flood onset/drain-down. Imported forcing must cover the comparison period; dry tails require explicit declared rainfall intervals.

Scenario capacities are supplied assumptions: baseline factor 1, blockage factor below 1, improvement above 1. Output receipts/metadata are caller declarations: equality checks cannot prove an external engine actually used the declared inputs, capacity intervention, numerical settings or immutable rainfall content. Preserve upstream run/settings manifests, hashes and scientific review. The adapter checks available declared identities rather than claiming independent hydraulic validation.

## Exposure and data coverage

Default **assumed screening** thresholds: building exposure depth ≥0.1 m; road closure depth ≥0.3 m. Both are editable bundle fields; these are not traffic safety limits. Each asset supplies intersecting grid cell IDs and provenance. Exposure uses the maximum over those cells; a known wet cell proves exposure even if another cell is missing. All cells must be known below threshold to declare a dry interval. Duration sums positive supplied intervals with known exceedance and separately reports unknown duration. A terminal wet sample can prove exposure while adding zero closure duration. No interpolation between snapshots, recession extrapolation or velocity-based safety classification.

Counts are counts of proven exposure within the supplied inventory, not citywide totals. Missing inventory is labelled unavailable by the page; missing cells remain visible in per-asset results. Geometry helper coverage deliberately excludes partially covered assets; callers must report the excluded inventory when describing a broader study. No centroid-to-whole-building assumption is made. Building-use labels do not establish a verified hospital/shelter inventory. Critical-facility accessibility is explicitly unavailable because no verified facility/road linkage and routing origins were supplied; no hospitals, shelters or population are invented.

## API

- `GET /flood`: pilot UI, independent of legacy illustrative water.
- `GET /v1/flood/pilot`: loaded bundle/summary, or explicit unavailable state.
- `GET /v1/flood/pilot/demo`: labelled synthetic fixture.
- `POST /v1/flood/pilot/compare`: validated `PilotBundle`, returns `{bundle, summary}`; malformed inputs return 422.
- `GET /v1/flood/runtime`: surface runner status, SWMM discovery and explicit uncoupled status. Package discovery alone does not establish a working engine.
- `POST /v1/flood/planning/compare`: `PlanningRunBundle`; over-budget plans are not evaluated; missing/mismatched original evaluations return 422.
- `POST /v1/flood/validation`: JSON depth model and independent observation records; returns validation report, or 422 on invalid input. No file paths are accepted from HTTP.

Forecast/traffic endpoints remain available. These endpoints do not buy data, launch paid services or run a surface solver. The standard server/API controls remain the deployment owner's responsibility.

## Runnable integration examples

Use a private path outside Git. A synthetic bundle can be inspected or served with an existing forecast model:

```sh
.venv/bin/python -m dynacity.cli flood-pilot --output /private/tmp/pilot.json
.venv/bin/python -m dynacity.cli serve --model /private/model.joblib --flood-pilot /private/tmp/pilot.json
# Open http://127.0.0.1:8000/flood
```

The bundle is directly accepted by the JSON importer or `--flood-pilot`. `flood-pilot --input /private/pilot.json --output /private/checked.json` validates an existing bundle. The main city viewer still uses its usual snapshot/BBED/traffic arguments.

Actual precomputed hydraulic outputs:

```python
from dynacity.flood_surface import load_hydraulic_output
from dynacity.flood_pilot import from_hydraulic_outputs, intersect_assets

# These references must come from the external engine's run manifests.
# rain is a validated RainfallForcing, not a visual water depth.
def assemble(outputs_by_case, rain, rainfall_source, assumptions, source_resolution_m):
    return from_hydraulic_outputs(
        outputs={case: load_hydraulic_output(path) for case, path in outputs_by_case.items()},
        rainfall=rain, rainfall_source=rainfall_source,
        label="Supplied bounded Beirut neighborhood outputs",
        source_resolution_m=source_resolution_m,
        capacity_assumptions=assumptions,
    )
# assumptions[case] = {label, capacity_factor, assumption}, for all three cases.
# Attach supplied footprints/roads using intersect_assets, then revalidate a
# copy of the bundle with those assets. Never manufacture an inventory.
```

CLI `flood-import --manifest /private/manifest.json --output /private/pilot.json` performs the same adaptation. Manifest keys: `outputs` (case→hydraulic NPZ path, relative to the manifest or absolute), `rainfall` (the `rainfall_record()` schema), `rainfall_source` (same immutable reference as all output metadata), `label`, `source_resolution_m`, `capacity_assumptions`, optional `assets` (serialized PilotAsset records), optional `terrain` (ready `TerrainGrid` NPZ path with exact output alignment and matching source SHA256).

`flood-validate --input /private/event-and-observations.json --output /private/validation.json` connects the ready event validator. See `docs/flood/validation.md` for schema, split discipline and uncertainty requirements. Empty observation sets do not mean zero error or validation success.

Planning JSON follows `docs/flood/gamification.md`'s precomputed adapter bundle with `inputs`, `plans`, `budget`, `evaluator_id`, `evaluations` keyed by **original** plan fingerprint, plus `hypothesis` and optional `objectives`/prior `challenge`. Never stamp new fingerprints on unrelated outcomes merely to make an import pass.

Independent SWMM drainage demonstration:

```sh
.venv/bin/python -m dynacity.cli flood-drainage-demo \
  --swmm /private/tmp/dynacity-runswmm-5.2.4 \
  --output-dir /private/tmp/new-unique-drainage-run
```

This runs the ready drainage adapter on its invented network, using the same 30 mm/h one-hour rainfall record as the pilot and flow-cap factors 1, 0.5, 1.5 at synthetic link P1. It writes audited per-case inputs/manifests/reports/results to a new private directory. Missing SWMM fails explicitly. It does not couple overflow to a surface model. Temporary executable used for verification is not a durable dependency; configure a separately maintained executable for ongoing use.

Observed integration-run results (synthetic network only): precipitation 3,000 m³ in every case; runoff 2,030 m³ in every case; overflow 580 / 1,110 / 170 m³ for baseline / blocked / improved. Runoff continuity error −0.034%; routing errors −0.063 / −0.094 / −0.080%. Report volume precision is 10 m³; reported engine EPA SWMM 5.2.4. These are adapter/numerical checks, not local drain measurements or depth predictions.

## Evidence limits and next real inputs

All producer handoffs were inspected as ready: terrain, rainfall, drainage, surface outputs, validation and budget planning. Their ready status describes callable tested components. ANUGA remains unavailable and no surface runner is implemented; drainage/surface dynamic coupling remains unimplemented. The current terrain is coarse filtered Copernicus DSM, source history/coverage limitations remain visible, and the loaded snapshot has no point-cloud features. Existing separate experimental cloud arrays do not establish verified bare earth or a vertical datum. No surveyed drains/inverts/capture geometry, verified fine DTM/checkpoints, local rainfall observations/IDF, independent flood-event observations, critical-service linkage, population or local cost quotations are supplied. No Beirut calibration, citywide fidelity, hydraulic safety or causal intervention benefit is claimed.

## Verification

- `DYNACITY_SWMM_EXECUTABLE=/private/tmp/dynacity-runswmm-5.2.4 .venv/bin/python -m pytest -q -o addopts=''`: **388 passed**, 22.78 s. Existing SUMO tests need local sockets and pass outside the restricted process sandbox. The first restricted run's SUMO port failures were environment restrictions; traffic code was not changed to bypass them. Existing FastAPI lifespan deprecation warnings remain.
- `.venv/bin/python -m pytest tests/test_flood_pilot.py -q -o addopts=''`: **21 passed**, including imported NPZ→servable bundle roundtrip, rainfall conservation, frame/nodata/CRS/source checks, terminal sample exposure, terrain quality, observation unavailable states, planning fingerprint rejection, private destination guard and challenge journals.
- `node --test tests/viewer_traffic.test.cjs tests/viewer_usability.test.cjs tests/viewer_evidence.test.cjs`: **18 passed**, preserving lane/replay, saved-input and illustrative-evidence behavior.
- `git diff --check`: passed.
- Real optional SWMM 5.2.4 integration run completed as described above; all three scenarios have the same comparison key/forcing and runoff, with accepted continuity.

Browser flow: `http://127.0.0.1:8767/flood` → unavailable state → explicit synthetic demo → timestamp 00:20 UTC → blocked case depth 0.50 m / missing velocity → private JSON import → assumed plan over budget at 10 USD → within budget at 20 USD → second iteration retained. Page identity/nonblank content, no framework overlay, no page/console errors, nine rendered grid cells, proportional metadata fonts and screenshots verified. Desktop 1440 × 1000 and mobile 390 × 844 pass; mobile horizontal overflow found initially and fixed. Original browser binary default was absent; the already-cached headless executable was used. No browser dependencies installed. Main 3D rendering was covered by existing viewer regression tests, not a new citywide browser/hydraulic validation.

Evidence screenshots: `/private/tmp/dynacity-flood-pilot-desktop.png`, `/private/tmp/dynacity-flood-pilot-mobile.png`. Test scripts/fixture imports are temporary and not committed. The local review server is a lightweight flood-page test server, not a replacement for the running forecast/traffic service.

Integration status: ready

 Browser plugin is not available in this session, so Playwright uses the existing cached Chromium headless executable. macOS requires the test server/browser to run outside the restricted process sandbox; no browser package was downloaded. Screenshots, temporary scripts, imported fixtures and SWMM artifacts remain in `/private/tmp`, outside Git.

## Main-app integration follow-up

The running main app at `http://127.0.0.1:8000/` was restarted with its existing model, snapshot, terrain, traffic, BBED, forecast and evidence inputs. The pilot is now an embedded workspace in this app, replacing reliance on the separate test preview. Browser-verified flow: main city → Flood planning → synthetic demo → blocked case / 00:20 UTC → Return to city → reopen with scenario and time retained. Main URL stays unchanged; no page errors. Desktop 1440×1000 and mobile 390×844 screenshots saved outside Git as `/private/tmp/dynacity-main-flood-workspace.png` and `/private/tmp/dynacity-main-flood-mobile.png`. Targeted Python tests: 35 passed; viewer JavaScript regressions: 18 passed. The earlier full-suite result remains 388 passed.

## On-map flood playback (latest main-app behavior)

The latest main entry supersedes the embedded-only entry above: **Flood simulation** opens a control panel while keeping the 3D city visible. Model-output cells are transformed from EPSG:32636 to longitude/latitude and rendered as a depth layer at their geographic locations. Scenario changes and the timestamp slider replace the source frame discretely; Play advances one output every 900 ms and stops at the final timestamp without interpolation or looping. This display interval is not a hydraulic timestep. Dry cells are clear, missing cells grey, and optional velocity remains unavailable when absent. Depth geometry uses metres without exaggeration above the viewer's schematic zero-ground baseline, not an invented absolute stage. Rendering on Google's absolute terrain mesh is explicitly unavailable without matched ground elevations. Legacy illustrative flood water is suppressed while output playback is visible to avoid mixing layers.

`pilot_map_geometry(bundle)` supplies cell polygons/centre and explicit render-reference limitations; `POST /v1/flood/pilot/map` validates a PilotBundle and returns bundle, summary and map geometry. The main controls support private JSON import and an explicitly synthetic demo; no hydraulic runner was added or claimed. The extra planning workspace remains available through the controls.

Latest checks: 36 targeted Python tests passed; 19 viewer JavaScript tests passed, including exact depth/nodata rendering without exaggeration. Actual main-app browser flow verified at `http://127.0.0.1:8000/`: Flood simulation → Load synthetic demo → 00:20 UTC / blocked case → seven wet cells and 0.50 m maximum → Play outputs → final frame 5 and automatic stop → disable layer. No page errors. Desktop/mobile screenshots: `/private/tmp/dynacity-flood-on-map.png` and `/private/tmp/dynacity-flood-on-map-mobile.png`. The running main app was restarted with its existing private inputs. This is on-map playback of supplied depth outputs, not a new rainfall-driven fluid solver.

## Restored original entry location

At the user's request, the separate main-map Flood simulation launcher was removed. The original **Disasters → Flood** tile now opens the on-map output playback controls. The tile opens controls rather than dragging imported outputs to an arbitrary location; other disaster interactions remain unchanged. Main app restarted with the same private inputs. Verification: 14 viewer Python tests and 19 viewer JavaScript tests passed; browser flow uses the original Disasters rail button and Flood tile.

Live browser verification passed through Disasters → Flood → synthetic demo → blocked case / 00:20 UTC: seven rendered wet cells, 0.50 m peak, no page errors, and no separate launcher. Initial external-library load timed out; retry succeeded.

## Flood-control usability update

The original Disasters → Flood entry is retained. The initial panel now has one primary **Play flood demo** action, which loads and starts the synthetic example in one click (reduced-motion users get a manual Play control). Starting data, file import and technical metadata no longer compete at the top. The loaded panel has Existing / Blocked / Improved buttons, current depth in cm, elapsed event minutes, a scrubber, Play / Pause / Replay, Find water and Show water. The prominent synthetic/validation notice remains visible. Advanced imports and UTC/velocity/nodata/reference details are under **Your data & model details**; planning remains a secondary link. Load failures use a separate alert while preserving the loaded source label. Closing the flood controls pauses playback and returns to Disasters; opening them hides the tray to prevent overlap.

A higher-contrast proportional-font panel replaces low-contrast technical paragraphs. Mobile uses a bounded bottom panel. The previous two-step load/play, raw ISO timestamp as the primary time label and overlapping trays were replaced; no physical depth, timing, forcing, fragility or velocity data were changed.

Verification: 14 viewer Python tests passed; 20 JavaScript tests passed across flood playback and existing traffic/evidence/usability checks (including a new pause/replay/end-of-series check). JavaScript syntax and `git diff --check` passed. Actual main-app browser flow: Disasters → Flood → Play flood demo → automatic playback → pause → 20 min → Blocked → 50 cm → play to end → Replay → Back to disasters. No page errors or mobile horizontal overflow. Desktop 1440×1000 and mobile 390×844 verified. Screenshots outside Git: `/private/tmp/dynacity-flood-ux-start.png`, `/private/tmp/dynacity-flood-ux-desktop.png`, `/private/tmp/dynacity-flood-ux-mobile.png`. Browser plugin absent; existing Playwright/cached Chromium used, no dependencies installed. Main app started with the same private inputs on port 8000.

## Original disaster interaction restored (latest behavior)

The user requested Flood behave exactly like the other disaster tiles. Flood now uses the shared drag handler and click-to-drop path again, with the common severity slider and flood reach. Clicking Flood no longer opens model-output/demo controls. A flood drop pauses/hides any separately loaded output playback so it cannot mask the dropped illustrative water. The tray labels disaster effects illustrative. Model-output import, planning and validation components remain available via their separate API/page; this UI restoration does not turn the dropped water-level illustration into a hydraulic solver. Targeted checks: 50 Python hazard/viewer tests and 20 JavaScript checks passed.
