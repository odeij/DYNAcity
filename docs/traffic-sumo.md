# Destination traffic and SUMO replay

The traffic adapter (`src/dynacity/traffic_sumo.py`) exports the existing road graph to SUMO, generates a fixed fleet of destination-based trips, and advances two independent SUMO runs: the scenario and an identical undisrupted baseline. The viewer draws scenario TraCI positions, headings and lanes, interpolating one-second samples (with 0.2-second simulation steps) at normal 1× playback. Kenney sedan/SUV assets and distance-dependent detail remain unchanged. Every active vehicle is rendered; insertion queues and completed arrivals are counted separately.

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

- `POST /v1/viewer/traffic/simulation`: create a session; body has `hour`, `seconds` (0.2–10), `closed`, `crashes`, and `hazards`.
- `POST /v1/viewer/traffic/simulation/{key}`: apply current disruptions and advance this same fleet.
- `DELETE /v1/viewer/traffic/simulation/{key}`: close both SUMO processes.

Responses contain `frames`, an assumptions statement, and the session key on creation. Each frame contains `time_s`, vehicle rows `[id, longitude, latitude, SUMO_angle, speed_mps, edge_id, lane_index]`, and metrics. Internal junction positions are returned too; cars do not jump between street endpoints. The viewer fetches two-second buffers, pauses when exhausted, and never extrapolates beyond SUMO data. Closure commands take effect at the next simulated buffer boundary (up to two seconds of visible buffered traffic). Changing the hour starts a new fleet; toggling detail does not.

Sessions are limited to four per server worker and expire after two idle minutes when the next session is created. Shutdown closes all sessions. Use a single API worker, since session storage is process-local. Closing Traffic releases the session. This is a local pilot API, not an authenticated multi-user simulation service.

Closures prohibit passenger entry in both directed edges of a road. Vehicles upstream reroute while preserving their destination. Those with no permitted detour stay queued; teleportation is disabled. Vehicles already within a newly closed street stop in place. Reopening restores original permissions and releases stopped vehicles. Junction-internal vehicles retry rerouting after leaving the intersection. Slowing hazards change desired edge speeds. SUMO's own car-following, safe insertion, lane changing, junction priority and signals determine actual movement.

## Calibration and limits

The available Beirut model has 10,817 split road segments / 14,900 directed edges, with 5,811 estimated speed profiles and 6,125 segments missing tagged lane counts. At 08:00, its density formula represents about 29,583 vehicles before stochastic rounding. OSM node identity, one-way direction, shape and per-direction lanes are preserved. Missing lanes borrow existing road-class defaults, and fractional lane estimates are rounded to integers. Signal locations come from OSM `highway=traffic_signals` nodes: one on a junction is used as is, one tagged on an approach is moved to the nearest junction of three or more roads within 25 m, and mid-block crossings are dropped (`traffic.SIGNAL_SNAP_M`). OSM turn restrictions through a via node become deleted SUMO connections (`only_*` deletes every other way out for that approach); via-way and conditional restrictions are left out. A road file built before this, without signal nodes, falls back to assumed lights at junctions with at least four incident segments, and `network.signal_source` says which applies. Signal timing is still SUMO's default program, and the model does not retain physical closure locations within a segment, bus fleet schedules, or verified lane connectors. SUMO generates lane connections geometrically. These are approximate junction controls, not a validated model of Beirut intersections. Overlapping roads connect only at shared graph nodes.

Each road's speed limit is its free-flow speed: the fastest of its Tari'ak 2014–2019 hourly medians. Using the chosen hour's median as the limit, as an earlier version did, counted congestion twice: the median already contains that hour's queues, and SUMO then queued the cars again on top. The hourly medians are now the check on the run instead. While nothing is closed or slowed, after a one-minute warm-up, each frame adds every car's speed to its road; `metrics.speed_check` compares the simulated mean with the observed median for that hour over roads with at least 20 vehicle-seconds (`roads`, `simulated_kmh`, `observed_kmh`, `ratio_p50`, `mae_kmh`). A ratio above 1 means the simulation runs faster than Beirut did, which points at too little demand or too generous junctions. CDR 2016–17 class counts and probe-hour shapes estimate initial density as directional flow × edge length / hourly speed. Uncounted classes use the existing assumed fraction of lane capacity. This is historical input calibration; no measured OD matrix or disruption validation exists. Destinations are sampled by legal connected walks, followed by shortest legal routing to that fixed destination. These **generated trajectories are not measured GPS trips**. Initial vehicles start at rest and accelerate; safe insertion queues excess demand rather than overlapping cars or discarding vehicles. The finite initial fleet drains as trips arrive. There is no replenishment, activity schedule, current demand forecast, or continuous origin inflow in this implementation.

Metrics use exactly the displayed fleet, without extrapolation to citywide vehicles/hour:

- `total = active + pending + arrived`; `pending` is awaiting safe insertion.
- `rerouted` counts distinct vehicles whose SUMO route changed, not commands issued.
- `stopped_on_closed` counts vehicles held within a newly closed edge.
- `no_detour` counts active vehicles whose remaining route still contains a prohibited road; pending trips remain in `pending`.
- `paired_arrivals` counts trips completed in both runs; `mean_completed_delay_s` is their scenario arrival time minus baseline arrival time, observed at one-second sample boundaries. It can be negative and is not an estimate for unfinished trips.
- `overdue` counts trips that arrived in baseline but remain unfinished in the scenario.

Completed delay has survivor bias while blocked trips are unfinished, so overdue and no-detour counts must be read alongside it. The older `/v1/viewer/traffic` static assignment remains for closure/slowdown road overlays and offline scenario summaries; it is separate sampled-trip sensitivity analysis. While Traffic is enabled, the viewer presents SUMO fleet metrics instead of those static trip counts. Purple overlays while Traffic is enabled count rerouted vehicles on their actual current road, rather than sampled static detour assignment. Road colors still show historical hourly medians, not instantaneous SUMO lane speeds.

## Verification

Run the real-runtime integration scenarios and replay adapter checks:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python -m pytest tests/test_traffic_sumo.py tests/test_traffic.py tests/test_viewer.py
node tests/viewer_traffic.test.cjs
```

The integration tests skip explicitly if SUMO is unavailable. They exercise blocked destinations, no vehicle disappearance, reopening, actual detour edge positions, fixed destinations, queue spacing, stopping within newly closed roads, and an identical baseline producing zero delay. Browser checks must use this isolated checkout's server; the shared preview on port 8767 does not contain the integration until it is applied.

## Measured pilot performance and network exclusions

A headless SUMO 1.22 run on the available Beirut network generated 29,591 trips at 08:00. Network conversion took about 1.6 seconds, demand generation about 5.7 seconds, and initialization of both runs about 18 seconds. After replacing per-vehicle initialization with route XML and sampling fleet context only at replay boundaries, a two-second advance still took about 4.5 seconds on this machine (including three coordinate frames, before HTTP serialization). Those three frames occupied about 7.8–8.2 MB of JSON. Subsequent API responses omit the duplicate first frame, but **continuous real-time throughput is not established**. The viewer retains normal 1× interpolation and pauses when its buffer runs out. Further work should measure profiling under representative hardware and consider a binary/delta replay protocol or an independent simulation worker; reducing the represented fleet or speeding up playback would change the requested semantics.

The converted network contains 14,898 directed edges and 1,269 assumed signal controls (from the road file built before OSM signals were kept). SUMO drops the two directed arcs of road index 10714, a 20.4-metre self-loop. The API reports this in `network.excluded_roads`; it is excluded from generated demand rather than silently inventing connectivity. Supporting this small ring requires explicitly splitting its geometry and validating lane connections. All trips generated on the converted network remain accounted for, including trips without detours. These counts are a synthetic fleet estimate, not observed traffic volume.

A full-network closure/reopening run retained total accounting throughout: a closure on road 0 changed 11 vehicle routes and left 10 active vehicles without a permitted detour; reopening cleared the no-detour count. The finite run's baseline arrival comparison stayed coherent (zero delay before disruptions). This short run verifies mechanics and accounting, not calibrated incident impact or steady-state throughput.
