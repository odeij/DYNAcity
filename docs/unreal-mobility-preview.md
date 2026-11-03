# Beirut mobility preview

The driving HUD minimap and the larger **City map** now support geographic
**Street / Satellite / Hybrid** backgrounds. Both register the car and exported
road overlay through the same authoritative EPSG:32636 origin; latitude/longitude
is projected rather than treated as a flat metre offset. Map coverage is not
proof of a modeled or drivable road. Destination selection retains the existing
road support/clearance checks.

While driving, **M** cycles map modes and **B** switches heading-up / north-up.
The City map controls and driving keys share `Saved/DynaCity/map-preferences.json`
(version 1: `mode`, `heading_up`). Changes are validated and atomically saved
before confirmation, apply to the native HUD immediately, and reload across
panel, Play and editor restarts. Failed writes preserve the previous selection
and display an error. The atlas itself remains north-up.

The native HUD smooths position, shortest-angle rotation and street-mode
speed-dependent zoom each rendered frame, snaps after teleports, clips raster tiles to the map
border, and fetches only visible tiles with at most four concurrent loads.
Street tiles use OpenStreetMap's public service with application identification,
HTTP caching/revalidation and no prefetch. Memory is bounded to 96 tile entries;
the disk cache is capped at 256 MiB and does not evict tiles younger than seven
days to make room. No API key is required. Attribution stays visible, and local
roads remain a fallback when tiles are unavailable.

The full atlas reuses vendored **Leaflet 1.9.4** and **Proj4js**, avoiding a WebGL
requirement in Unreal's embedded browser. It supports smooth pan/zoom, follow-car,
keyboard navigation, destination selection and an independently switchable teal
modeled-road overlay. EOX supplies the atlas's OSM-derived street cartography,
satellite background and hybrid labels; it does not call OSM's public tiles from
`file://`. See the UI's [map integration notes](../engine/unreal/Content/UI/MAP.md)
for dependency provenance, provider terms and the geographic contract.

Satellite imagery is the **EOX Sentinel-2 cloudless 2016** mosaic (modified
Copernicus data 2016 & 2017, CC BY 4.0), approximately **10 m/pixel** with native
zoom capped at 14. Satellite/Hybrid are regional overviews: the HUD enforces at
least 10 ground metres per screen pixel (2.2 km across for a 220 px map), and the
atlas does not permit zooming beyond level 14. This prevents the former muddy
street-level enlargement; it does not create high-resolution aerial photography.
Hybrid now uses geographic EOX roads and labels instead of the source-road
wireframe. Source road centrelines are fallback-only in the HUD, and the atlas's
modeled-road overlay starts off. Street remains the detailed driving view. The historical public endpoint is named
`s2cloudless_3857`, not `s2cloudless-2016_3857`. Online maps disclose the user's
IP address and viewed tile coordinates to their providers, require internet on
cache misses, and have no availability guarantee. There is no paid account or
secret embedded in the application.

**Find a place** searches downloaded OSM names as you type, with named shortcuts,
keyboard result selection, Arabic/Latin aliases and supported map links. It
does not require entering coordinates. Missing OSM names remain a data gap.

In Play, **C** cycles chase → hood → high → cockpit. **L** switches day/night,
including vehicle headlights and the reference-street lights. **F1** opens
controls, **F3** opens search. Opening the menu stops the exploration car.

## Free flight and vehicle recovery

In Play, **F** toggles free flight. The car stays parked at the departure point;
leaving flight returns to that car, **not** a landing at the current flight
location. Use **WASD** for view-relative movement, **E** up, **Q** down,
**Shift** to boost and the mouse to look around.

**U** recovers the car to its last recent safe ground checkpoint near its
current position, falling back to start if none is available. **R** resets to
the current start; **N** moves to the next area. The Controls page also offers
**Toggle free flight** and **Recover car** buttons.

The driving MCP tools expose `fly(enabled: bool)`,
`fly_input(forward, right, up, yaw, pitch, boost)` and `recover()`. All six
flight inputs are floats; `yaw` and `pitch` are **absolute degrees**, not mouse
deltas. `fly(False)` returns to the parked car. `release()` calls
`release_drive_inputs()`, clearing both external drive and flight inputs.
Action responses return native drive-status JSON, including `fly_mode` (bool),
`recovery_available` (bool) and `blocked_reason` (string).

`tests/test_driving_tools.py` checks Python API forwarding and panel actions
using mocks only; it does not establish native flight, collision or recovery
behavior in Unreal.

## SUMO during driving

29 October runtime update: the working city now uses the prepared sedan, van,
taxi and bus as typed traffic groups. See `unreal-vehicle-candidates.md` for
the source scenario, omitted visual classes and live evidence. The shared
player Ferrari is no longer the default background traffic mesh.

Editor background CPU throttling was confirmed enabled. `play_performance.py`
temporarily disables `bThrottleCPUWhenNotForeground` and editor Slate
throttling while the driving adapter is attached; End Play restores the
original in-memory values without saving global preferences. This keeps the
editor-hosted simulation responsive while its separate control window has
focus. Unit checks cover repeated activation and restoration of both true and
false original preferences.
Live reflection checks also confirmed both settings false during Play and
restored to their original true values after End Play.

A short live run with 150 displayed vehicles had a sampled median world-tick
interval near 45 ms and 95th percentile near 63 ms during one window. A later
window at 74 visible vehicles measured roughly 14 ms median and 18 ms p95.
These are sampled simulation-update intervals, not GPU timings or a controlled
before/after FPS benchmark. Earlier background measurements were around
280–333 ms. Fleet size, warm-up and camera movement vary, so do not attribute
an exact speedup to the mesh replacement. The 150-vehicle display limit and
250 m visibility radius were retained. Private runtime reports and screenshots
are under `engine/traffic-fleet-fix-20261029/`.

Choose **Explore → Connect SUMO** after starting driving. The private project
configures `sumo_url` as `http://127.0.0.1:8014`; port 8012 is also supported.
The existing DynaCITY traffic server must be running. Connection failures appear
in the panel. The adapter owns one API session and releases it on disconnect
or when Play ends. It never deletes another client's simulation session.

The current finished-asset profile requests a 700 m square around the car's current location. The response
filter follows the car, including jumps between areas, while retaining the same
simulation session and clock. Requests run on one background worker; Unreal objects
are accessed only on the game thread. Two-second batches retain SUMO timestamps,
vehicle identity and source-road elevation. Unknown road heights are omitted.
Internal junction samples retain the vehicle's previous road where the bounded
distance check permits it; no unknown bridge elevation is invented.

The initial response supplies the session ID; subsequent step responses do not
need to repeat it. The client retains its owned ID and rejects an unexpected
replacement. A failed request clears displayed traffic and exposes the error,
so frozen cars cannot be mistaken for a connected simulation. Network starvation
while a request is pending is reported as waiting for the next batch.

The native renderer interpolates at 30 Hz. The current finished-asset profile draws
at most 150 vehicles within 250 m using an instanced mesh component; its hard
ceiling remains 750 vehicles / 750 m. It buffers up to roughly four seconds
ahead and freezes at the last sample when starved. The player's arcade car is
independent: traffic instances have no collision, and their signal appearance
does not control player movement. This is synthetic SUMO demand, not live GPS.
The adapter is editor-hosted; a packaged application still needs a runtime
network/data adapter.

## Reference street

A **240 m Charles Malek Avenue section**, anchored on source road 8416, provides
the finish preview. The current [finished-assets policy](finished-assets-policy.md)
replaces the handmade car/cockpit, seating, bins and trees with licensed library
models. The Ferrari source has an authored left-side wheel and an unmirrored
cabin. Furniture/tree/lamp PBR maps use 4K source textures.

The reference dressing uses nine library lamps, five seating placements and five
bin placements. Handmade pavement/curb cubes are removed. Seventy-four unsupported
procedural street-prop groups are hidden pending suitable finished replacements;
the original data records remain available. Lamp, bench and bin placements are
illustrative and grounded against loaded source collision. Hardware models do
not establish surveyed Beirut inventory or present-day appearance.

Private import/source manifests record original URLs, licences, hashes and bounds.
The new pipeline preserves authored geometry/UVs and uses source PBR factors/maps.
The retired procedural builders cannot regenerate those placeholder models.

## How much Beirut is covered?

Measured on 26 October 2026 against the union of the **12 cached municipal
production areas**, approximately 20.04 km²:

| Layer | Coverage | Meaning |
| --- | --- | --- |
| Original source terrain inside the municipal reference | **19.60 / 20.04 km² (97.8%)** | Horizontal footprint of valid original terrain cells, preserving holes |
| Exported road length inside the municipal reference | **378.8 / 434.3 km (87.2%)** | Source road centreline length with an exported surface |
| Roads across the wider extract | **5,697 / 10,817 segments (52.7%)** | Includes roads beyond municipal Beirut; not a land-area percentage |
| Road length across the wider extract | **396.6 / 809.4 km (49.0%)** | Both carriageways and separate source segments count independently |
| Original model buildings | **17,569 records** | Source model inventory; not a count of surveyed present-day buildings |

The wider source road bounding box spans approximately **9.46 × 8.78 km**;
that rectangle is not the covered land area. Production areas are Achrafieh,
Ain el Mraisse, Bachoura, Mazraa, Medouar, Minet el Hosn, Moussaytbe, Port,
Ras Beyrouth, Remeil, Saife and Zoqaq el Blat. Boundary currency is unverified.

The terrain metric excludes later approximate DEM ground extensions. Road
geometry does not prove uninterrupted driving, complete collision coverage or
accurate present-day details. Source buildings date to January 2020; traffic
speed inputs are historical. A claim that all Greater Beirut is modeled would
be unsupported.

## Reproduce and verify

Main implementation: `DynaMobilityWorld` and `DynaDrivePawn` in the native
plugin; `mobility_runtime.py`, `traffic_client.py`, `mobility_core.py`,
`reference_street.py`, `control_panel.py`; bundled `city-map.js` and controls.

`scripts/report_beirut_coverage.py` intersects valid terrain cells and source
road centrelines with the projected source polygons in EPSG:32636. Save its
JSON as the private project's `Saved/DynaCity/mobility-coverage.json` to display
the measured values in the GUI. Rebuild the report after changing inputs.

`tests/test_mobility_runtime.py` checks map simplification, metric view extent,
bridge heights, junction identity, owned-session release and ground holes.
Together with existing location, drive-route and traffic conversion checks,
42 focused tests passed, including finished-asset provenance and LHD validation. They include follow-up responses without session IDs,
moving view bounds, session ownership and clearing cars on request failure.
`scripts/verify_mobility_panel.cjs` verifies typeahead,
keyboard selection, shortcuts, controls and layouts with a mock bridge.
`scripts/verify_unreal_mobility.py` separately exercises the installed native
map, cockpit, lighting state, advancing real SUMO clock, rendering cap and
disconnect cleanup. The sustained check now requires at least five received
batches, eight seconds of simulation advance, and fresh batches after an area
change without resetting the clock. Captures use the actual editor image during Play;
`CaptureViewport` alone shows the editor camera and cannot prove a cockpit.

The native minimap simplifies 87,853 source path points to 13,215 cartographic
points and indexes overlay segments in 200 m cells. Traffic instances avoid
spawning one actor per car. These are implemented workload bounds; no matched
before/after FPS improvement is asserted. Tile/network/GC and browser overhead
are additional to the earlier road-line-only renderer. Private build reports,
screenshots and runtime evidence remain outside Git.

For the geographic-map upgrade, Unreal 5.8 compilation and native automation
checks cover projection fixtures, clipping through a full rotation, frame-rate
independent smoothing, shortest-angle wrapping, CRS rejection and diagnostic
state. `tests/test_mobility_runtime.py` additionally verifies the shared payload's
UTM origin, round trips and invalid-reference rejection. Run
`node --test scripts/verify_city_map.cjs` for independent PROJ fixtures and pinned
vendor hashes; `scripts/verify_mobility_panel.cjs` tests atlas interaction with a
mock bridge, with optional `--live-tiles` for actual EOX imagery. Browser tests do
not establish embedded Unreal/CEF or city-scene runtime performance. The native
`status()` payload includes a `minimap` diagnostic object with tile loading,
errors, origin, center, mode and orientation for runtime verification.

`scripts/verify_unreal_minimap.py --output <private-evidence-directory>` verifies
live geographic tiles through the project's Unreal MCP server. It refuses to
interrupt an existing Play session unless `--use-current` explicitly identifies
an agent-owned session. `--leave-playing` leaves a successful run stationary with
the user's original saved map settings; otherwise it ends its own Play session.
The verifier also checks imagery overzoom prevention, the real panel settings
action, saving outside Play, and restoring preferences into a new Play session. On 27 October 2026, the
installed private city project passed all **18 native minimap checks**: all three
modes rendered their visible tiles without provider errors, north/heading-up
settled correctly, the stationary center matched the shared UTM projection,
movement changed the map center and zoom, and reset followed the car back to
Charles Malek Avenue. Captures use Unreal MCP's `CaptureEditorImage`. This
establishes installed runtime behavior, not surveyed accuracy, current imagery,
packaged-game support or an FPS improvement. The online test contacts OSM and
EOX for on-demand visible tiles.

`scripts/profile_unreal_mobility.py` samples stationary cockpit conditions in day
and night, with and without traffic. It records world tick intervals, local MCP
latency, visible fleet and buffer depth. World tick intervals are not GPU times.

The optional `traffic_fleet_meshes` field in `Saved/DynaCity/library-assets-active.json`
accepts up to 15 imported `/Game/` static mesh asset paths. The existing driving
vehicle remains the default fleet mesh; an absent or empty field preserves the
single-mesh fleet. Additional vehicles need suitable licensed, correctly scaled
models and a rebuilt DynaCityDrive plugin. The configuration is not a visual or
performance validation of those models.
Mixed traffic is off by default. Experimental repository code can map SUMO
type IDs to mesh groups using `traffic_type_groups` (0 is the default car and
1–15 index the additional meshes) and can request mixed traffic with
`traffic_mixed: true`. Unmapped types are omitted when a mapping is active.
This needs a rebuilt plugin, a SUMO fleet with declared type IDs, compatible
mesh dimensions, origins and axes, and native runtime verification. No bus or
van assets or representative Beirut fleet are configured yet. Until those
checks pass, use passenger-car-sized meshes only.

On 27 October 2026, a backend-only check loaded the private Mar Mikhael road
model and historical mobility package with mixed traffic enabled, seed 42 and
continuous demand. Startup took 1.77 seconds and a three-second advance took
0.06 seconds; the final frame contained all eight declared type IDs. This is a
small pilot-network check, not a current-asset Unreal frame-rate measurement or
an observation of present-day Beirut traffic.
Do not connect that pilot road model directly to the city Unreal adapter:
its road indices were renumbered. An in-process API-to-adapter check using
the city location index omitted all 1,015 vehicle samples across its returned
frames. For example, pilot road 0 is Port Interchange, whereas city-index road
0 and whole-city model road 0 are Bachir Gemayyel. Use the matching whole-city
road model, or implement and validate a road identity mapping before using the
pilot model. The historical mix remains limited in geographic and temporal
representativeness even if attached to a whole-city model.
An offline comparison uniquely matched all 104 pilot roads to whole-city roads
using the OSM way ID and exact ordered geometry. The private
`engine/recovery-audit/pilot-road-match-20261027.json` records the mapping and
both source hashes. In a separate in-process experiment that translated road
IDs in API responses, the city adapter placed 341 vehicles in the final pilot
frame and omitted no samples. The repository now supports an optional
`traffic_road_mapping` path in the private active-assets configuration. Generate
the mapping with `scripts/prepare_unreal_traffic_mapping.py`; it binds the
ordered source-road identity and city-index identity, which the adapter checks
against the server response and current index before mapping. The matching
server and editor-side source changes must be deployed first. No mapping is
enabled in the live city project by this change.
An in-process check of that implementation placed all 1,015 vehicle samples
in the returned pilot response, with 341 in the last frame. This does not test
Unreal visuals or establish present-day traffic proportions.
In a separate in-process API-to-adapter check using `beirut-traffic.json` with
the historical package and the pilot bounds, the final frame contained 336
placed vehicles spanning all eight type IDs; the adapter omitted no vehicle
samples in that response. This verifies that response against the saved city
index, not the appearance of vehicles or every road in Unreal.
An initial longer profile exposed the first-batch freeze that the earlier brief
smoke check missed; that frozen-traffic run is not a valid live performance
baseline. The profiler now rejects disconnected or non-advancing traffic.

The earlier procedural-asset run on 26 October 2026 sampled thirty stationary-cockpit observations
per condition. Median world tick intervals were 8.42 ms by day / 8.33 ms by
night without traffic, and 11.92 ms by day / 11.05 ms by night with approximately
504–505 visible moving vehicles. Those are engine tick intervals, not measured
GPU times or proof of an FPS improvement. The initial connection took 28.9 s.
Traffic buffer medians were only 0.35–0.53 s, so simulation delivery remains a
separate pacing constraint even though rendering continues at a shorter cadence.
The run is saved privately as `profile-continued-fixed.json`. These timings
predate the finished Ferrari and 4K library assets; they are not performance
measurements of the current asset profile.

On 27 October, a separate Python cProfile run used the local Beirut traffic
model, continuous demand, an approximately central Beirut view and a 1,000-car
response cap. Session initialization took 23.8 s and the first two simulated
seconds took another 6.64 s under profiling. Initialization included two SUMO
process starts (7.41 s cumulative), network export (4.25 s) and demand
generation (3.79 s). TraCI context subscriptions also contributed to the
elapsed time. These timings overlap within the call tree and should not be
added together. This was one instrumented backend-only run under memory
pressure, not a current-asset Unreal rendering benchmark or a comparison of
optimizations. It identifies startup and subscription work as candidates for
measurement; preserve baseline/scenario comparison semantics when optimizing.
The baseline context subscription is now deferred until the first disruption,
when its state is copied from the scenario. A single comparable instrumented
run after this change measured 22.89 s initialization and 6.48 s for the first
batch. These two individual runs do not establish a reliable speedup; system
load varied and neither measures the Unreal scene. Comparison tests cover the
transition from shared metrics to a subscribed baseline after a disruption.
An experimental narrower first scenario subscription, followed by an in-view
subscription, reduced Python call count in one profiled run but measured
23.21 s initialization and 6.90 s for the first batch. That single run did
not demonstrate an elapsed-time improvement, so the experiment was removed.
The second SUMO process is now started at the first disruption instead of
session creation. One comparable profiled backend run measured 17.98 s startup
and 6.68 s for the first batch, versus 22.89 s and 6.48 s in the preceding
single run. This shifts process startup to the first comparison; these are not
repeated controlled measurements or Unreal FPS results. Tests exercise the
copied baseline, random state continuity and observed signal programs.
An unprofiled single run on the full Beirut model then initialized in 12.58 s,
generated its first two-second batch in 3.41 s, and handled the first road
closure in 12.33 s. After the closure both processes reported simulation time
5.0 s. This checks one scenario under that machine load; it does not establish
typical latency or real-time throughput across all disruptions.

Three consecutive unprofiled backend-only sessions with the same central view
and continuous demand had median initialization 11.866 s, first two-second
batch 3.228 s, and next two-second batch 3.084 s. Each subsequent batch thus
took more wall time than its simulated duration in these short runs, indicating
that the backend alone could deplete a real-time playback buffer under these
conditions. These samples do not include Unreal, driving, an area change, or a
long-duration steady state; the private `backend-repeat-20261027.json` stores
the individual results. No before/after speedup follows from this measurement.

The backend now keeps a context subscription while the requested view remains
within its subscribed circle; SUMO refreshes it on each simulation step. A
50 m margin allows small movements without a new subscription. It refreshes
when the requested view no longer fits and restores full-fleet fields when the
view is cleared. Returned cars are still filtered to the exact view. A
subsequent three-run measurement under the same backend-only workload had a
median first batch of 2.685 s and next batch of 2.023 s, versus 3.228 s and
3.084 s before, respectively. These sequential samples are short and not
isolated from machine load; the 2.023 s median is still slightly slower than
two seconds of simulated traffic and does not prove sustained real-time
playback in Unreal. Individual results are in the private
`backend-repeat-watch-20261027.json`. Tests cover movement across steps, view
changes, removal of the view and comparison scenarios.
An independent three-batch test on the Beirut model compared the complete
frames from the reuse path with a forced re-subscription on every step; they
matched exactly for the tested view shifts and seed. A smaller deterministic
network test also compares the paths through a road-closure scenario. Neither
comparison proves equality for every possible network or view.

In one later backend-only run with an approximately 700 m wide view, four
two-second batches took 1.47, 1.30, 1.37 and 1.47 s. Small shifts reused the
subscription, while a roughly 185 m shift refreshed it. This is a short
synthetic movement test, not a live driving or sustained-throughput result;
different traffic and view sizes have different costs.
Another backend-only run advanced twelve consecutive two-second batches while
shifting the view approximately five metres east per batch. The median batch
took 1.393 s, and the 24 simulated seconds took 16.46 s of batch-processing
time, excluding initialization. This demonstrates delivery faster than the
simulated duration for this short synthetic movement case; it does not measure
the Unreal renderer, startup, longer sessions, or other traffic conditions.
Per-batch results are in private `backend-sustained-moving-20261027.json`.
An in-process HTTP API check on the full Beirut model also created a continuous
session (HTTP 200, 13.13 s), advanced a shifted view (HTTP 200, 1.22 s, time
3.0 to 5.0 s), and deleted the session successfully. This verifies the session
API boundary in one run, not a network deployment or an Unreal client.

The earlier corrected native check passed thirteen checks. It received five batches
and reached 10.232 simulation seconds before an area jump, then received two
more batches and reached 13.699 seconds with a different view filter. This
verifies sustained delivery beyond the initial batch and continuity across
the jump; it does not establish unlimited citywide throughput.

On 27 October 2026 the installed finished-asset profile passed all fifteen
native checks, including the authored left-hand steering/eye alignment,
positive cabin scale, day/night switching, sustained SUMO batches, the
150-instance cap, area-change continuity and disconnect cleanup. The local
backend was restarted before this run. The sustained observation allows sixty
wall-clock seconds while still requiring five batches and eight simulation
seconds; a thirty-second window was insufficient under the observed editor
cadence. Evidence is saved privately under `engine/asset-quality-pass/native-check`.

### Reopened-editor traffic check (27 October 2026)

The reopened city editor connected to a locally started server using the whole-city
`beirut-traffic.json` model and the current installed project assets. A 10-sample
stationary profile reported 20.56 s to first visible traffic, 150 visible vehicles,
12 then 21 batches, and advancing simulation in both traffic conditions. The
record is `engine/traffic-profile-20261027-city.json` under the private data root.
The reported 333 ms median tick interval even without traffic indicates a
throttled or otherwise constrained editor; this is not a valid foreground FPS
baseline or a comparable improvement measurement. The test used the installed
plugin, not the separately built optional mixed-fleet plugin. SUMO positions are
simulated and the default fleet is not evidence of Beirut vehicle proportions.

During editor recovery, all three backed-up material autosaves were selected,
but Unreal reported it could not check them out from revision control. Their
restored state remains unverified; the original and autosave backups remain in
`engine/recovery-audit/20261027-173527/` under the private data root.

### Reusing SUMO network conversion

The backend now holds a bounded 32 MiB process-local cache of converted SUMO
networks keyed by the complete generated node, edge and connection XML and the
converter executable identity. Changed inputs trigger conversion again; private
network data is not written to a persistent cache. A single local full-Beirut
check measured 3.292 s for cold export and 0.267 s on reuse, with identical
output bytes. This only accelerates later sessions in the same backend process;
it is not a first-start or Unreal performance measurement. The currently
running server predates the change and must be restarted to use it. Traffic and
mobility test suites passed after the change.

A follow-up native SUMO check created two separate deterministic sessions from
the same input: the converter ran once and all returned frames matched. This
checks cache reuse through session creation on the test network; it does not
replace a longer city driving run.

A full-Beirut in-process HTTP API check then created and deleted two sequential
continuous sessions on the same app with an identical central view, hour and
seed. The first response took 13.621 s and the next 10.569 s; both returned
three frames and 758 vehicles in the final frame. The cache was cold for the
first session and warm for the second. This single sequential pair includes
network conversion, SUMO startup and the first batch; it is not a controlled
before/after benchmark or a live Unreal measurement. The running port 8014
server still predates the cache change.

Initial demand generation now caches sorted outgoing edges during a seeded
route walk. A full-Beirut check generated 29,621 trips with identical serialized
IDs, routes, lanes and positions before and after (same SHA-256). Two sequential
runs measured 2.029/1.377 s before and 1.732/1.261 s after; these short,
non-isolated runs suggest less work but do not establish a typical speedup.
Traffic and mobility tests passed. The existing backend process must be
restarted to load this change; the first connection remains unmeasured in Unreal.

### Experimental traffic mesh

A private evaluation copy of the installed Ferrari FBX was decimated in Blender
without changing the installed hero or traffic assets. The candidate has 71,757
triangles versus 358,788 in the source (about 80% fewer). Blender reimport found
one mesh, one UV layer and 17 material slots; its three overall dimensions
changed by at most about 5 mm in the reimported coordinate frame. A single
untextured Blender workbench view retains the general silhouette but shows some
small surface differences. This does not check texture fidelity, all viewing
angles, Unreal material assignment, Nanite or actual frame rate. The candidate,
source/output hashes, scripts and two preview images are private under
`engine/asset-quality-pass/traffic-lod-evaluation-20261027`. It is not installed;
retain the full-detail car until native visual and performance checks support a
switch. This is a performance candidate, not a representative Beirut fleet.

For a later native evaluation, the editor adapter now accepts an optional
`traffic_default_mesh` path under `/Game/` in the active library presentation.
When configured, this mesh is used for the default SUMO traffic group while the
driven vehicle keeps its existing mesh. With no setting, behavior is unchanged;
invalid or missing assets fail clearly. Fleet type group zero refers to this
configured default mesh. This source change passed mocked editor setup tests,
but the candidate has not been imported or enabled in the running project.

The experimental traffic mesh now also has a private `prepared.json` and
`source.json` suitable for the existing library import validator. They preserve
the original CC BY 4.0 attribution and source hash, record the simplified FBX
hash and triangle count, and identify the adaptation. The original source and
candidate hashes validated, and all 17 original material names resolved to
source definitions. This is preparation only: FBX orientation, material
assignment, visual fidelity and performance still require native Unreal checks.

A separate minimal Unreal 5.8 project then imported both the original and
candidate through `library_assets.import_prepared` with a null renderer. Both
passed the importer's source and licence checks and resolved 17 material slots.
Unreal measured the original at 453.36 × 225.71 × 123.59 cm and the candidate
at 453.79 × 225.69 × 123.77 cm. The candidate metadata records 71,757 source
triangles; these values do not establish runtime GPU cost. The original import
logged degenerate tangent-basis warnings under memory pressure. Detailed results
and the isolated assets are under `engine/asset-quality-pass/traffic-lod-native-host-20261027`.
No rendered material comparison or city/PIE performance measurement was made;
the candidate remains uninstalled in the city project.

The candidate import also logged degenerate tangent-basis and nearly-zero
bi-normal warnings, so visual shading needs particular scrutiny in Unreal.

The backend `serve` command now supports `--prewarm-traffic` together with
`--traffic`. This deliberately converts the default (non-mixed) network before
the server starts accepting requests, retaining it only in the bounded in-memory
cache. It moves the work to backend startup; it does not remove the work or
prewarm a different mixed network. Without the flag behavior is unchanged.
If the network exceeds the cache budget, the server reports that it could not
retain it; an explicit prewarm failure reports an error instead of silently
claiming success.

One full-Beirut in-process HTTP check took 3.436 s to prewarm and 10.641 s for
the first traffic response, which contained three frames and 758 final-frame
vehicles. These times are one observation under machine load, not a controlled
comparison or an Unreal FPS result. The existing port 8014 process predates the
option; it has not been restarted or configured to use prewarming. The SUMO and
mobility tests passed, including first-session reuse of a prewarmed network.

The actual `dynacity serve --prewarm-traffic` path was also exercised with the
full private Beirut model while substituting the server runner to avoid opening
another listener. It invoked the runner once and retained a 23,508,662-byte
network in one cache entry. Omitting `--traffic` returned the expected explicit
error. This checks CLI integration, not a live editor connection.
