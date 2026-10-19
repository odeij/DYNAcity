# Verified survey map context

The Flood panel can display acquired UN-Habitat ponding points and street-condition survey lines. This uses actual source geometry and categories, independently of ANUGA and all traffic, exposure and intervention calculations. It never creates a pipe graph, individual drain locations, water depths, event dates or capacities from survey labels.

The current verified package contains 224 ponding points and 679 street records: 385 labelled `No drains`, 112 `Blocked drains`, 171 `Functional drains` and 11 blank conditions retained as unknown. Street records carry survey-year fields from 2016–2019. Ponding points have no supplied event or survey year; no year is borrowed from nearby streets. Coverage is partial, including suburbs outside municipal Beirut. These are historical observations, not present-day drain condition.

## Load locally

Add this option to the existing `dynacity serve` command, using the acquired package outside Git:

```sh
--flood-context /Users/johnkofdarelli/dynacity-private/data/flood-inputs-20261016/real-input-handoff.json
```

The preview started for this implementation is at `http://127.0.0.1:8767/`. It uses the existing BBED/snapshot/forecast files with the normal CARTO basemap, terrain, traffic and evidence configuration. The stripped verification-only view was replaced with the complete city view; the survey overlay remains optional. No pre-existing server was stopped. The normal city launcher can use the same option with its existing terrain, traffic and basemap configuration.

Open **Disasters → Flood → Historical survey records**, then enable **Show survey records on map**. Filter by neighborhood and street condition; ponding points have their own toggle because they are independent observations, not evidence of a connection to a selected street. **Find selected records** enables the layer and frames the filtered geometry. Hover a line or point for source neighborhood, original condition, source road ID where supplied, survey year or explicit unavailability, and interpretation limits. Clicking a survey record never selects a building or edits a road closure. Both ordinary and photorealistic map modes support survey overlays; any draping is only a visual reference, not an elevation measurement.

Source links, retrieval dates, reuse terms and missing hydraulic-input status remain available in the panel. Attribution identifies the UN-Habitat survey separately from BBED's ODbL attribution. The data remains outside Git. UN-Habitat reuse terms are unverified, so the CLI requires a loopback host and the API refuses remote clients for this package. No public deployment or redistribution is part of this integration.

## Data boundary

`load_flood_context()` reads the acquisition manifest from the completed data handoff. It requires exactly the known two context layers, stored beside that manifest, actual matching file and provenance hashes, matching feature counts, EPSG:32636, the correct geometry type, present unique record IDs, complete geometries and no truncated ArcGIS response. Invalid input fails startup; absent configuration produces an explicit unavailable API response.

Only necessary survey attributes and coordinates enter `/v1/flood/context`. Private paths and unrelated downloaded content are omitted. UTM coordinates are converted to longitude/latitude; the source's unverified Z values are discarded. Blank conditions stay unknown. Multi-part streets remain single observations, even though each path is rendered separately. All records carry `hydraulic_asset=false`, and `simulation_ready` remains false regardless of `planning_ready` in the source handoff. An acquired non-null model-input entry would still require review; acquisition is not solver acceptance.

The API is a read-only local context endpoint. The UI never feeds these geometries/categories into `flood_solver`, inferred drainage capacity, flood damage, water level or traffic closure. Missing inputs are listed explicitly. Numerical flood demos remain disabled.

## Verification

```sh
DYNACITY_TEST_FLOOD_HANDOFF=/absolute/private/path/real-input-handoff.json \
  .venv/bin/python -m pytest tests/test_flood_context.py tests/test_flood_pilot.py tests/test_viewer.py
```

Integration tests copy genuine source bytes into temporary storage rather than adding acquired assets or manufactured observations to Git. They verify counts/unknowns, two-dimensional geometry, no path leakage, checksum/CRS/count/partial-extract/duplicate-ID/geometry refusal, local-only access, and simulation staying unavailable. Set `DYNACITY_TEST_FLOOD_CONTEXT` to a temporary JSON response from `load_flood_context()` to run the actual-data filter and layer assertions in `tests/viewer_flood.test.cjs`.

Browser verification followed the complete local flow and confirmed Karm El-Zeytoun's 146 street observations and 9 independent ponding points on the map. Source acquisition and interpretation are documented in [data-inventory.md](data-inventory.md).
