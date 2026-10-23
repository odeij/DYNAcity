# Destination traffic and SUMO replay

The traffic adapter (`src/dynacity/traffic_sumo.py`) exports the existing road graph to SUMO, generates an initial fleet of destination-based trips, optionally adds continuous boundary entries, and advances two independent SUMO runs: the scenario and an identical undisrupted baseline. The viewer enables continuous entries. It draws scenario TraCI positions, headings and lanes, interpolating one-second samples (with 0.2-second simulation steps) at adaptive playback up to 1×, slowing when delivery falls behind. All active vehicles remain in replay; only offscreen drawing is culled. Insertion queues and completed arrivals are counted separately.

This integration follows the inspected God's Plan reference at commit `23997b0482f8ad2cd94386b622f029ca5842c8c1`: its `frontend/src/babylon/traffic.ts` draws TraCI tracks and `backend/cityshift/live/network.py` updates lane permissions, reroutes to the existing destination and retains vehicles without detours. No reference source was copied.

Road geometry uses a shared 3.2 m assumed lane width and the same rounded lane counts in SUMO and the viewer. One-way carriageways are centred on their OSM way; two-way ways put each direction to the right of the shared centreline. The viewer receives the full carriageway width, replacing fixed road-class widths that could leave simulated cars outside the coloured road. These widths are simulation assumptions, not surveyed kerb boundaries.

## Runtime

Install the optional runtime in the environment that runs the DynaCITY server:

```sh
python -m pip install -e ".[sumo]"
```

SUMO and netconvert must be discoverable on PATH, or under `SUMO_HOME/bin`. This is optional: normal forecasting and static road-impact endpoints still work without SUMO; the traffic viewer reports an explicit unavailable message instead of generating local random motion. Temporary XML and compiled networks are kept outside Git. Private road data stays in the configured private data root.

Run the existing `dynacity serve` command with its real `--traffic`, `--snapshot`, and `--bbed` inputs. The integration is independent of the scenario AI provider. A fake preview provider is not evidence of real provider behavior.

## API and playback

### Street appearance

The Traffic panel switches between **Street view** and **Traffic colours**.
Street view uses opaque asphalt, a narrow road-edge casing and close-up lane
markings. Traffic colours retains the historical hourly speed overlay. Switching
appearance does not restart the session, reseed demand or alter any vehicle route.
Closures and detour overlays remain visible in both appearances.

Lane markings use the same assumed 3.2 m lane width and rounded lane counts as
the SUMO export. They are illustrative, not surveyed road paint or kerbs. Markings
are generated near the camera at zoom 16.5 or higher, leave junction-end margins,
and are omitted on short segments. No turn restrictions, crossings or signal
locations are inferred from these visual details.

The existing Kenney sedan/SUV geometry now supports stable body-paint variations,
contact shadows and brake lamps. Glass, tyres and original light lenses retain
their source colours. Lamps indicate sampled deceleration/stops, not a separate
driver-intent prediction. Sampled positions and headings are still authoritative;
interpolation does not create intermediate lane paths or remove one-second
sampling limitations at tight turns.

Replay caches per-frame identity matching and mutates existing pose arrays during
screen refreshes. Close-up drawing culls cars outside the viewport with a 100 m
margin, while full-fleet positions and metrics are retained. This is a presentation
optimization, not a smaller simulated demand. Diagnostics distinguish total active
cars from drawn cars. Real-time citywide throughput is still a measurement question.

- `POST /v1/viewer/traffic/simulation`: create a session; body has `hour`, `seconds` (0.2–10), `continuous` (default false for existing API clients), `closed`, `crashes`, and `hazards`. The viewer sends `continuous: true`.
- `POST /v1/viewer/traffic/simulation/{key}`: apply current disruptions and advance this same fleet.
- `DELETE /v1/viewer/traffic/simulation/{key}`: close both SUMO processes.

Responses contain `frames`, an assumptions statement, and the session key on creation. Each frame contains `time_s`, vehicle rows `[id, longitude, latitude, SUMO_angle, speed_mps, edge_id, lane_index]`, and metrics. Internal junction positions are returned too; cars do not jump between street endpoints. The viewer fetches two-second buffers, pauses when exhausted, and never extrapolates beyond SUMO data. Closure commands take effect at the next simulated buffer boundary (up to two seconds of visible buffered traffic). Changing the hour starts a new fleet; toggling detail does not.

Sessions are limited to four per server worker and expire after two idle minutes when the next session is created. Shutdown closes all sessions. Use a single API worker, since session storage is process-local. Closing Traffic releases the session. This is a local pilot API, not an authenticated multi-user simulation service.

Closures prohibit passenger entry in both directed edges of a road. Vehicles upstream reroute while preserving their destination. Those with no permitted detour stay queued; SUMO teleportation is disabled, and the gridlock breaker below never moves them. Vehicles already within a newly closed street stop in place. Reopening restores original permissions and releases stopped vehicles. Junction-internal vehicles retry rerouting after leaving the intersection. New entry requests at a closed origin wait in an explicit entry queue, counted as pending, and enter SUMO after reopening. Slowing hazards change desired edge speeds. SUMO's own car-following, safe insertion, lane changing, junction priority and signals determine actual movement.

### Continuous boundary demand

`BoundaryInflow` identifies inward-facing directed roads in a fringe of the network
bounding box (3% of the smaller dimension, at least 25 m). Their existing estimated
directional vehicles/hour set seeded Poisson departure rates. Up to four reachable
routes per origin are sampled toward outward-facing fringe roads; disconnected
origins fall back to a legal local walk. These are explicit synthetic demand rules,
not measured entry counts, an OD matrix or a calibrated trip-distribution model.

Both runs receive identical IDs, destinations and scheduled departure times,
quantized to SUMO's 0.2 s step. Demand never depends on scenario completion rates.
The scheduler is independent of API request chunk size. Future departures are not
counted as represented trips until eligible in SUMO; blocked-origin entry requests
remain accounted for outside SUMO until their origin reopens. Once admitted, safe
insertion controls spacing. The initial fleet can still redistribute or shrink;
continuous entries do not impose a constant vehicle count or establish equilibrium.
There is no calibrated warm-up period or automatic change to the next hourly profile.

`metrics.demand` reports `mode`, `initial_trips`, `added_trips`, `entry_roads` and
`expected_entry_vph`. `total` grows with eligible trips. If no fringe origins are
available, mode remains `finite`, making the limitation visible. Changing demand
mode or hour requires a fresh session. Legacy clients keep finite-fleet behavior.

## Calibration and limits

### Mobility comparison

In the Traffic panel, choose **Make car-free**, then click individual road segments.
Click a selected segment again to reopen it. This uses the existing passenger-car
closure mechanics; pedestrian activity and benefits are not simulated.

The **Normal traffic vs your changes** table compares the undisrupted baseline and
scenario at the displayed simulation time, using identical initial trips, destinations
and random seed, including the same continuous entry schedule. It shows completed trips, cars on the road, cars awaiting entry, and
cars stopped below 0.1 m/s. Stopped cars include signals and initial acceleration;
these instantaneous counts are not measured queue lengths or sustained congestion.
The viewer adds continuous boundary entries; no calibrated warm-up period is applied.

New entries reroute to their fixed destinations before insertion when a legal detour
exists. If their origin/destination is closed or no legal route exists, they stay in
the explicit entry queue; reopening retries these requests without losing them.

The panel also reports delay for trips completed in both runs, overdue trips and active
trips without detours. Roads with diverted or stopped cars can be clicked to frame
them on the map; the list shows up to five segments ranked by diverted count, then
stopped count. Counts describe cars currently on those segments, not hourly flow.
Historical road colours retain their existing meaning.

**Restart comparison** retains current closures, crashes and hazards and resets both
runs. Reopening a road during a run does not erase its previous effects. The panel
flags active crashes or hazards so a mixed scenario is not presented as a pure
pedestrianisation trial. Old API responses without baseline metrics show no invented
comparison results.

### Gridlock

Without intervention the Beirut network locks within the first half minute: cars stall inside junction boxes or wait on full exit roads in cycles that never clear, and at 08:00 about 6,000 cars were still standing after ten simulated minutes. Three measures keep it moving. SUMO lets cross traffic ignore a car that has blocked a junction for 20 s (`--ignore-junction-blocker`) and lets a car waiting 30 s accept smaller gaps (`--time-to-impatience`). The adapter's gridlock breaker (`Simulation.unstick`) then takes any car that has stood 120 s while first on its lane and places it at the start of the nearest of its next five roads with 7.5 m free, as SUMO's teleport would. SUMO's own teleport stays off because it would also move cars a closure holds, and a closed lane accepts no stop that could exempt them. The breaker skips cars held or blocked by a closure and never searches past a closed road. In ten simulated minutes it moved 456 cars at 08:00 and 869 at 17:00, about 1.5–2% of trips; one and three cars respectively had stood longer than five minutes at the end, and a five-road closure kept all 734 held cars with none entering.

Clipping the network at the map edge leaves stub entry roads, some only a few metres long, where a car cannot be inserted. Boundary inflow trips start on the first road of their route at least 60 m long (`MIN_ENTRY_M`). The boundary queue still grows: the busy entry trunks are congested back to the map edge, which stands for queues outside the modelled area.

The available Beirut model has 10,817 split road segments / 14,900 directed edges, with 5,811 estimated speed profiles and 6,125 segments missing tagged lane counts. At 08:00, its density formula represents about 29,583 vehicles before stochastic rounding. OSM node identity, one-way direction, shape and per-direction lanes are preserved. Missing lanes borrow existing road-class defaults, and fractional lane estimates are rounded to integers. Signal locations come from OSM `highway=traffic_signals` nodes: one on a junction is used as is, one tagged on an approach is moved to the nearest junction of three or more roads within 25 m, and mid-block crossings are dropped (`traffic.SIGNAL_SNAP_M`). OSM turn restrictions through a via node become deleted SUMO connections (`only_*` deletes every other way out for that approach); via-way and conditional restrictions are left out. A road file built before this, without signal nodes, falls back to assumed lights at junctions with at least four incident segments, and `network.signal_source` says which applies. Signal timing is still SUMO's default program, and the model does not retain physical closure locations within a segment, bus fleet schedules, or verified lane connectors. SUMO generates lane connections geometrically. These are approximate junction controls, not a validated model of Beirut intersections. Overlapping roads connect only at shared graph nodes.

Each road's speed limit is its free-flow speed: the fastest of its Tari'ak 2014–2019 hourly medians. Using the chosen hour's median as the limit, as an earlier version did, counted congestion twice: the median already contains that hour's queues, and SUMO then queued the cars again on top. The hourly medians are now the check on the run instead. While nothing is closed or slowed, after a one-minute warm-up, each frame adds every car's speed to its road; `metrics.speed_check` compares the simulated mean with the observed median for that hour over roads with at least 20 vehicle-seconds (`roads`, `simulated_kmh`, `observed_kmh`, `ratio_p50`, `mae_kmh`). A ratio above 1 means the simulation runs faster than Beirut did, which points at too little demand or too generous junctions. CDR 2016–17 class counts and probe-hour shapes estimate initial density as directional flow × edge length / hourly speed. Uncounted classes use the existing assumed fraction of lane capacity. This is historical input calibration; no measured OD matrix or disruption validation exists. Destinations are sampled by legal connected walks, followed by shortest legal routing to that fixed destination. These **generated trajectories are not measured GPS trips**. Initial vehicles start at rest and accelerate; safe insertion queues excess demand rather than overlapping cars or discarding vehicles. Legacy finite mode drains as trips arrive. The viewer's continuous mode adds estimated boundary entries as described above; it does not provide activity schedules or a current demand forecast.

Metrics use exactly the displayed fleet, without extrapolation to citywide vehicles/hour:

- `total = active + pending + arrived`; `pending` is awaiting safe insertion.
- `unstuck` counts cars the gridlock breaker moved further along their route.
- `rerouted` counts distinct vehicles whose SUMO route changed, not commands issued.
- `stopped_on_closed` counts vehicles held within a newly closed edge.
- `no_detour` counts active vehicles whose remaining route still contains a prohibited road; pending trips remain in `pending`.
- `paired_arrivals` counts trips completed in both runs; `mean_completed_delay_s` is their scenario arrival time minus baseline arrival time, observed at one-second sample boundaries. It can be negative and is not an estimate for unfinished trips.
- `overdue` counts trips that arrived in baseline but remain unfinished in the scenario.

Completed delay has survivor bias while blocked trips are unfinished, so overdue and no-detour counts must be read alongside it. The older `/v1/viewer/traffic` static assignment remains for closure/slowdown road overlays and offline scenario summaries; it is separate sampled-trip sensitivity analysis. While Traffic is enabled, the viewer presents SUMO fleet metrics instead of those static trip counts. Purple overlays while Traffic is enabled count rerouted vehicles on their actual current road, rather than sampled static detour assignment. Road colors still show historical hourly medians, not instantaneous SUMO lane speeds.

## Verification

### Optional mobility evidence package

`dynacity serve --traffic <pilot-model.json> --mobility-inputs <mobility.json>`
adds the source-labelled mobility controls to the existing viewer. The bundle
uses schema `dynacity-mobility/1`, validated by `mobility_inputs.py`. Sources,
bounds, features, vehicle types and missing observations travel with the input.
Opening the viewer never downloads mobility inputs.

Prepare a package from an already available OSM XML extract, optionally producing
a separate whole-road pilot network:

```powershell
$env:PYTHONPATH='src'
python scripts/prepare_mobility_pilot.py --osm <local-extract.xml> --bounds 35.522 33.895 35.530 33.900 --retrieved-on 2026-10-22 --output <mobility.json> --traffic-model <beirut-traffic.json> --output-traffic <pilot-model.json>
```

The command is offline. Its historical fleet option transfers the 2015 report's
aggregate shares; it is not a measured local mix. The UI starts with that option
off. Enabling it starts a new paired simulation with stable class assignment in
both runs, including continuous incoming vehicles. Class dimensions and driving
parameters use SUMO defaults. The supplied profile excludes motorcycles because
their share was unmeasured. See [the source assessment](mobility-data-readiness.md).

Optional signal programs and vehicle stop events require survey sources. Signals
reference existing graph node IDs and must match SUMO's controlled-link count.
Stops reference an initial generated vehicle ID, a route edge, lane, position and
duration; invalid or off-route records fail rather than being silently relocated.
`parking: false` stops in the lane; `parking: true` uses SUMO's off-road parking
behaviour. These imports are mechanics, not a substitute for local observations;
the current pilot loads neither. Imported programs do not certify that supplied
movement phases are conflict-free.

Replay vehicle rows append a type ID at index 7; `network.vehicle_types` supplies
its actual SUMO dimensions and class. Positions are front-bumper coordinates;
the renderer offsets mesh centres without changing the authoritative position.
Signal rows contain `[node, linkIndex, lon, lat, state, surveyed]`. Signal colours
use the displayed sample, without interpolating discrete states or using future
buffered samples. Mapped parking areas and bus stops represent infrastructure,
not occupied spaces or scheduled transit demand.

### Runtime checks

Run the real-runtime integration scenarios and replay adapter checks:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python -m pytest tests/test_mobility_inputs.py tests/test_traffic_sumo.py tests/test_traffic.py tests/test_viewer.py
node tests/viewer_traffic.test.cjs
```

The integration tests skip explicitly if SUMO is unavailable. They exercise blocked destinations, no vehicle disappearance, reopening, actual detour edge positions, fixed destinations, queue spacing, stopping within newly closed roads, and an identical baseline producing zero delay. Browser checks must use this isolated checkout's server; the shared preview on port 8767 does not contain the integration until it is applied.

## Measured pilot performance and network exclusions

A headless SUMO 1.22 run on the available Beirut network generated 29,591 trips at 08:00. Network conversion took about 1.6 seconds, demand generation about 5.7 seconds, and initialization of both runs about 18 seconds. After replacing per-vehicle initialization with route XML and sampling fleet context only at replay boundaries, a two-second advance still took about 4.5 seconds on this machine (including three coordinate frames, before HTTP serialization). Those three frames occupied about 7.8–8.2 MB of JSON. Subsequent API responses omit the duplicate first frame, but **continuous real-time throughput is not established**. The viewer retains normal 1× interpolation and pauses when its buffer runs out. Further work should measure profiling under representative hardware and consider a binary/delta replay protocol or an independent simulation worker; reducing the represented fleet or speeding up playback would change the requested semantics.

The converted network contains 14,898 directed edges and 1,269 assumed signal controls (from the road file built before OSM signals were kept). SUMO drops the two directed arcs of road index 10714, a 20.4-metre self-loop. The API reports this in `network.excluded_roads`; it is excluded from generated demand rather than silently inventing connectivity. Supporting this small ring requires explicitly splitting its geometry and validating lane connections. All trips generated on the converted network remain accounted for, including trips without detours. These counts are a synthetic fleet estimate, not observed traffic volume.

A full-network closure/reopening run retained total accounting throughout: a closure on road 0 changed 11 vehicle routes and left 10 active vehicles without a permitted detour; reopening cleared the no-detour count. The finite run's baseline arrival comparison stayed coherent (zero delay before disruptions). This short run verifies mechanics and accounting, not calibrated incident impact or steady-state throughput.
