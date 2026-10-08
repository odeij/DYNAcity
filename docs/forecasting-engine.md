# Forecasting Engine Design

For the decision-by-decision engineering rationale, including the canonical-state ontology and rejected alternatives, see [Implementation Rationale](implementation-rationale.md). For how this engine maps onto the funded Task 5.1/5.2 module breakdown, including which modules are not yet implemented, see [Task 5 AI Module Specification](task-5-module-specification.md).

## Supported question

Given a versioned Beirut building-state snapshot, what distribution of urban states and aggregate urban-form KPIs is expected after 2, 4, or 6 years?

The empirical model is observational. A business-as-usual result is not a causal prediction. A scenario containing interventions is explicitly labeled `assumption_based_scenario` and carries a warning.

## Data flow

`allowlisted BBED snapshot → canonical temporal panel → temporal benchmark → selected transition model → stochastic state rollouts → KPI distributions`

The panel also contains a direct 2018→2024 interval for descriptive status-transition reporting. It is kept out of the time-forward model benchmark.

The optional point-cloud path is:

`LAS header validation → explicit EPSG:32636 assignment → COPC conversion → BBED polygon join → morphology table → 2022→2024-only model ablation`

The LAS headers do not embed a CRS. EPSG:32636 was established externally through BBED alignment. The files were acquired after the 2020 Beirut port blast, so those features are forbidden in the 2018→2022 training rows.

The point clouds are photogrammetric reconstructions (Agisoft Metashape, exported to `.las` via PDAL), not laser-scanned LiDAR — confirmed by constant-zero intensity and single-return points in the source files. `.las`/`lidar_*` naming in the code and CLI is retained for continuity with the existing panel/API contract; it denotes "2020 point-cloud modality," not the sensing method. This does not change the leakage argument: the modality is still forbidden in pre-2020 training rows regardless of how it was captured.

## Canonical state taxonomy

| Canonical state | BBED examples |
|---|---|
| `stable_built` | Complete Residential, Complete Building, Non-Residential, Old-Bldg-Inhabited |
| `active_construction` | Under Construction, Construction Site |
| `stalled_or_cancelled` | Construction on-Hold, Cancelled Construction |
| `renovated` | Renovated |
| `vacant_or_evicted` | Evicted Building, Old threat of Eviction, Old-Bldg-Uninhabited |
| `empty_or_parking` | Empty Lot, Parking Lot |
| `demolished` | Demolished |
| `unknown` | Not Available or an unmapped label |

Raw status values remain in the panel for auditability. Building use remains a separate predictor; it is not erased by the collapsed transition taxonomy.

## Model gate

The primary evaluation is time-forward:

- Train: 2018→2022.
- Test: 2022→2024.
- Descriptive only: direct 2018→2024 observations.
- Baselines: no-change and smoothed empirical Markov transitions.
- Learned candidate: histogram gradient boosting.
- Research candidate: optional GraphSAGE implementation in `dynacity.graph`.

The learned candidate is selected only when it improves both macro-F1 and multiclass Brier score over both baselines. Otherwise, the strongest baseline is the production artifact and the negative result is retained.

After the time-forward selection decision, the chosen production model is refitted on both adjacent observed intervals. This gives the deployed model the exact two-year 2022→2024 transition evidence used by the forecast engine while keeping the reported holdout metrics honest.

Point-cloud value is measured separately through spatially grouped 2022→2024 ablations. It must not be mixed into the primary historical benchmark.

## KPI output

Every two-year rollout step reports p05, p50, and p95 for:

- Completed, active, stalled, vacant, demolished, and empty/parking quantities.
- Estimated gross floor area.
- Median and p90 building height.
- Built land fraction.

Longer horizons repeatedly apply an observed transition model, so uncertainty compounds. The service warns on 4- and 6-year forecasts.

## Scenario adjustment

An intervention adjusts target-state log odds for explicit objects and eligible starting states. Every adjustment requires an effect source and confidence grade. This mechanism exists for sensitivity analysis and future simulator integration; it is not a causal estimator.

### Compiling scenarios from planner prose

`dynacity compile-scenario` (and `POST /v1/scenarios/compile`) turns a written scenario into a `ScenarioSpec` via `scenario_compiler.py`. A language model only drafts; deterministic code decides. The model provider is swappable (`providers.py`): Anthropic Claude (`llm` extra, `ANTHROPIC_API_KEY`, default `claude-opus-5`) or Google Gemini (`gemini` extra, `GEMINI_API_KEY`, default `gemini-3.8-flash`), chosen with `--provider`/`--llm-model`, `DYNACITY_LLM_PROVIDER`, or whichever key is set. Both return JSON validated against the same `ScenarioDraft` schema and go through the same checker:

- **Scope** — the model writes a selector (sectors, building uses, explicit ids) that code resolves against the snapshot. Unknown names are rejected with the valid choices listed.
- **Effect size** — the model picks `weak`/`moderate`/`strong`; the log-odds delta comes from `EFFECT_STRENGTH_LOG_ODDS` (0.5/1.0/2.0). A numeric delta is accepted only if the planner wrote that number.
- **Provenance** — `effect_source` must be quoted from the planner's text or be `unsourced planner assumption` (confidence then forced to `low`). A source the model introduced is rejected as an invented citation.

- **Scope limits** — a lever with no spatial filter is rejected unless the request explicitly asks for the whole city ("across Beirut", "toute la ville", "كل بيروت", …); a building-use filter that narrows the eligible set is flagged with the count it excludes.
- **Citations** — a registered source may be cited only if the request names its title or one of its `aliases`.

The scope and citation rules came from live testing on real BBED with Gemini: asked about a place not in the data (Karantina), a model dropped the place and applied the lever to all 3,144 eligible buildings; asked about grants in Medawar, it attached Law 194 although the planner never mentioned it. Both drafts passed the earlier checks and are now hard failures. An overloaded or unreachable model (Gemini returned 503 "high demand" often during testing) yields a `model.unavailable` issue after the SDK's retries and a fallback model chain, never a crash, and each attempt records the model that actually answered.

Hard issues trigger one repair round with the issues fed back; every attempt is kept in the output report. Requests the engine cannot express (traffic, rents, population) are listed as dropped rather than approximated. Only aggregate, allowlisted BBED attributes are sent to the model — no per-object features or point-cloud morphology. Compiled scenarios still forecast as `assumption_based_scenario`.

### Hazards

A scenario can also strike the city with a hazard (`ScenarioSpec.hazards`, `hazards.py`). The kinds follow God's Plan's disasters: `tornado`, `storm`, `rain`, `flood`, `fire`, `heat`, `earthquake`, `explosion` (gas leaks and bombings too), `plane_crash`, `orbital_strike` (fictional, a recovery stress test) and `riot`. God's Plan's road closures follow from these disasters on an optional street network (see *Traffic and road closures* below); its vehicle crashes are not included. Its population and development commands are ordinary levers here.

Each `HazardEvent` is located in exactly one way: `citywide`, a point (`center_x_m`/`center_y_m`, snapshot CRS), or a track (`path_m`, tornadoes). It carries a magnitude in the kind's own unit (EF scale, peak gust km/h, rainfall mm, flood depth m, fire class, °C, Mw, TNT-equivalent tonnes, aircraft mass t, beam radius m, unrest class) and strikes on one 2-year step (`occurs_step`). Unless `radius_m` is given, the radius follows from the magnitude. For example, a blast's damage distance scales with the cube root of the charge, so 1,000 t reaches about 2 km.

On its step the hazard acts before the transition model. Each building's intensity is `strength(magnitude) × exposure(distance) × vulnerability(storeys or use)`. Destruction has probability `destroy_ceiling × I²` and sends the building to `demolished`. Damage has probability `damage_ceiling × I − P(destroyed)` and sends it to `vacant_or_evicted` (a construction site goes to `stalled_or_cancelled`). Empty lots, demolished and unknown buildings have nothing to lose. Missed buildings take the model's normal transition. From the next step on, recovery is the model's own dynamics. Hazards on the same step act independently. `first_step_transitions` include the exact mixture. `ForecastResult.hazard_impacts` reports, per hazard, exposed buildings and p05/p50/p95 counts of buildings destroyed and damaged.

Every number in `hazards.PROFILES` is an illustrative default, not a calibrated fragility curve. BBED records no labelled hazard losses to fit them to. Results carrying hazards are therefore `assumption_based_scenario`, warn that they are a stress test rather than a loss estimate, and add each kind's caveat (approximate flood terrain, distance-only shaking, no blast shielding, …). Buildings need a centroid to be reached by a local hazard. `snapshot_from_bbed` now records one, and `snapshot.attach_centroids` backfills older snapshot files from BBED. Unlocated buildings are counted and warned about.

A flood follows terrain when buildings carry `ground_elevation_m`. `dynacity fetch-terrain` reads the Copernicus GLO-30 surface model over the snapshot extent (range reads of the public tile), approximates bare earth with a 10th-percentile filter over ~150 m (roofs are filtered out, so streets and open lots set the level), masks the sea, and writes a 10 m grid in the snapshot CRS; `serve --terrain` attaches each building's ground level. The water then fills to the flood's depth above the low ground in reach (the 5th percentile of reached buildings' ground), each building's own depth is that level minus its ground, and buildings above it stay dry. The viewer draws the flooded ground cells at their depth. Without elevations the depth is uniform inside the footprint. Flow, drainage, defences and sea level are not modelled, and a 30 m surface model cannot resolve street-scale depressions.

The compiler drafts hazards from prose under the same rules as levers. The model names the kind, a sector and a severity (`minor`/`moderate`/`severe`/`extreme` → magnitude from the profile). Code builds the footprint from that sector's building centroids: a point at their centre; for floods, storms, rain, heat and riots, a radius grown to cover the sector; for tornadoes, a track along the sector's long axis. A numeric magnitude or radius counts only if the planner wrote it, and the model never writes coordinates. A citywide footprint is allowed without explicit wording only for storms, rain, heat and earthquakes. `PlanningRequest.hazards` puts every candidate in a plan under the same disaster, so the search asks which levers best absorb it.

On the real snapshot, a 1,000 t blast at the port destroys p50 78 (p05–p95 67–92) and damages 175 (156–193) of 993 exposed buildings. Recovery is as fast as the fitted model says. The shipped Markov model has seen almost no demolished buildings and returns 45% of them to `stable_built` within one step, so recovery curves after a hazard should be read as optimistic.

### Traffic and road closures

`traffic.py` adds a street network beside the buildings. It sits outside the transition model, which never sees it, and it never changes a forecast's numbers. `dynacity build-traffic` joins open sources over the snapshot extent (building centroids plus 800 m) and writes a JSON file under `DYNACITY_DATA_ROOT`:

- **Roads:** drivable OpenStreetMap ways (motorway down to living street; service roads and footways left out), fetched from Overpass or read from a saved Overpass JSON (`--roads`). They are split at every node shared with another way into a routable graph, with one-way rules honoured. Each road keeps its name and its lanes in each direction (OSM `lanes`, halved on two-way roads). About 90% of major roads are tagged; untagged ones get a class default (`DEFAULT_LANES`).
- **Bus and van routes:** OSM `route=bus|minibus|share_taxi` relations touching the extent (30 on the 2024 snapshot, including LCC lines 2, 5, 6, 12, 15 and 24 and the airport–Dora vans). Only member ids are fetched, which keeps the query light enough for the public servers; geometry comes from the road ways already loaded, so members outside the extent drop out. Fetched with the roads, or read from `--bus-routes`. Overpass calls retry on a mirror when the main server answers 429 or 504.
- **Speeds:** the [Lebanon Traffic Dataset](https://github.com/ramikay/lebanon-traffic-dataset) (Tari'ak app, ODbL). It holds 6.0 million crowd-sourced vehicle speeds from 17,274 phones, 2014–2019 (the readings inside Beirut start in 2015), each map-matched to an OSM way. Each way gets a median speed per hour of day (Beirut time). Readings under 3 km/h are dropped: they are phones standing still, and at 4 a.m. they would pull an empty road's median to zero. A way needs 10 readings in an hour to use its own median. Otherwise it borrows its road class's hourly profile, scaled to its own all-day median when it has one, and it is flagged `estimated`. The dataset's metadata says the Velocity column is in m/s, but its values only make sense as km/h (motorway median 57, hard cap 120), so km/h is the default (`--velocity-unit`).

- **Volumes:** peak-hour vehicle counts from the CDR's [environmental and social impact assessment for the Tabarja–Beirut Bus Rapid Transit system](https://documents1.worldbank.org/curated/en/648821508851766209/pdf/SFG3708-EA-P160224-Box405306B-PUBLIC-Disclosed-10-24-2017.pdf) (ELARD, 2017, Table 4-4). Its traffic survey counted vehicles per hour in each direction on named roads, AM peak 09:00–10:00 and PM peak 15:30–16:30. The 12 stations inside Beirut are in `traffic.COUNT_STATIONS`. Each is matched to OSM ways by name, keeping the road's main class. The survey gives road names, not exact count points, so a station stands for its whole road. Sagesse Road has no matching OSM name and is reported as not found.

The counts track Tari'ak reading density only weakly (r = 0.49 over 8 roads), so they are not used to estimate each road's volume. They are summarised per road class instead (`class_peak_volumes`, median AM vehicles per hour per direction): motorway 5,364 (1 station), trunk 2,194 (6), secondary 1,515 (4). Primary was not counted; it gets the geometric mean of trunk and secondary (1,823), flagged `interpolated`. `estimated_flow` spreads each class's peak over the day by that class's share of probe readings per hour. That treats the probe app's users as following drivers' daily rhythm, which is an assumption. The curve is then bent to pass through the PM count too: a multiplier of 1 up to 09:00 rises linearly to the PM factor at 16:00 and holds there. The night trough comes out at about 7% of the peak. A road's own figure is its class's curve × its lanes over the median lanes of that class's counted roads, kept within 0.5–2 (`lane_factor`).

For congestion every road needs a volume and a capacity. Capacity is lanes × an assumed per-lane figure (`CAPACITY_PER_LANE`: 1,900 motorway down to 300 living street; links 80% of their class). Uncounted classes are assumed to run at 0.6 of capacity at 09:00, shaped over the day by their own readings (`background_flow`); the counted classes come out at 0.69 (trunk) and 0.76 (secondary).

On the 2024 snapshot this gives 10,817 road segments (809 km) and about 1.0 million readings. 5,006 segments have their own speeds; the rest borrow their class's. Typical speeds drop from about 26 km/h at night to about 19 km/h from 07:00 to 19:00.

When the server holds a traffic model (`serve --traffic`), every dropped or compiled disaster also returns `traffic`: which roads it closes or slows, and what that does to trips at the chosen hour:

| Effect | Rule (an illustrative assumption) |
| --- | --- |
| Rubble | A building reaches the streets within half its height plus half its footprint width when it collapses. A road is closed when P(blocked) = 1 − Π(1 − P(destroyed)) over buildings in reach is ≥ 0.5, and slowed in proportion between 0.15 and 0.5. |
| Water | A flood closes a road where it stands deeper than 0.3 m: the same water level as the buildings, against terrain when it is loaded, otherwise flat. |
| Cordon | Fire, riot, blast, plane crash and orbital strike close the roads where the hazard's exposure is at least 0.5. |
| Weather | Storm and rain multiply speeds by 0.7 and 0.85 at full exposure, easing back to 1 at the footprint edge. |
| Crash | Blocks its segment and slows every road within 250 m, to half speed at the crash, easing back to normal at 250 m. |

Roads can also be closed by hand, God's Plan style (`road_events`). With a road tool on in the Traffic panel, clicking a road closes it (reason `manual`) or puts a vehicle crash on it (reason `crash`); the crash rule in the table stands in for the queue behind it.

They can also be typed. When the server holds a traffic model, the scenario compiler accepts `road_events` in the draft: kind `closure` or `crash` and the street name. The model only copies the street name as the planner wrote it; code checks that the name appears in the request and matches it against OSM road names, ignoring accents, case and words like street, avenue or rue (`scenario_compiler.match_street`). A closure shuts every segment of the street; a crash sits on its busiest segment (most probe readings), because prose names a street, not a spot on it. An unknown street is a hard issue, sent back for repair or moved to `unsupported_requests`. Without a traffic model, road events stay unsupported as before. They never enter `ScenarioSpec`, so the engine contract is unchanged. Typed and hand-placed events with any disasters on the map (`combine_disruptions`). `POST /v1/viewer/traffic` measures the road impact of disasters, closures and crashes at an hour without re-running the building forecast. The page calls it when you close a road, place a crash, or move the hour slider, so the impact always matches the hour shown.

`traffic_impact` samples about 1,000 trips between road junctions. Ends are drawn in proportion to probe readings × road length, because Tari'ak records speed but not volume, so readings stand in for how busy a road is. Each trip is routed by travel time before and after the disruption.

The vehicles that used the closed roads then load the detours:

1. Every sampled trip stands for the same number of vehicles per hour (`vph_per_trip`), chosen so the sample reproduces the volume on the counted roads: vehicle-km per hour ÷ sampled trip-km. That is about 97 at 09:00 and 6 at 03:00. An earlier version gave each trip its closed road's flow ÷ the trips sampled there; a rarely sampled segment then handed one trip its whole flow, and the diversion came out three times too large.
2. Trips over a closed road are rerouted in four batches (`ASSIGNMENT_STEPS`), each on the network the earlier batches have already loaded (incremental assignment). All at once, every detouring trip took the same shortest detour and loaded it to 12× capacity; in batches it spreads over the alternatives (about 3× at worst).
3. Loaded roads slow down by the BPR volume-delay curve, time × (1 + 0.15 (V/C)^4), relative to their usual volume. Relief on the emptied roads is not credited.
4. Every trip is timed once more on that congested network.

This is one pass of incremental assignment, not a traffic equilibrium.

The result reports:
- trips whose usual route crosses a closed or slowed road (`affected`), how many of those are cut off, and the p50/p90 extra minutes for the rest (`delay_min`), with the uncongested figure beside it (`delay_min_uncongested`);
- other trips the detour traffic holds up (`spillover`);
- vehicles per hour diverted (`diverted_vph`), road slowed by congestion (`jammed_km`, `jammed`) and the worst volume-to-capacity ratio (`max_vc`);
- the estimated flow on the busiest closed road of a counted class (`busiest_closed_vph`), the bus and van lines a closure cuts (`bus_routes_hit`), and the roads that pick up rerouted trips (`detour`).

Delays are over affected trips only, so a local closure is not averaged away across the city. When no sampled trip uses the closed streets, the result says so.

The trip sample is random, and one sample of about 1,000 trips is noisy. Across six seeds, closing Charles Helou at 09:00 moved the median delay by ±1.5 min around 9.4 (±16%) and the 90th percentile by ±8.8 min around 25 (±35%). The server therefore draws three independent samples (`samples=3`, about 1.2 s). It reports the one with the middle median delay, plus `ranges`: the lowest and highest value of each headline figure across the three. The page shows those ranges in brackets.

On the 2024 snapshot, closing all of Charles Helou at 09:00 diverts about 17,000 vehicles an hour and slows 108 km of road. Trips that used it take a median +9 minutes (90% under +23) instead of +2.4 without congestion; 587 other trips lose about 3 minutes; LCC lines 2 and 15 and the airport–Dora vans are cut. At 03:00 the same closure costs under a minute and jams nothing. Each request takes about 0.4 s.

In the viewer, the Traffic button colours each road by its historical median speed at the chosen hour against its fastest hour. When the optional SUMO runtime is available, animated cars replay actual SUMO/TraCI positions at normal 1× time, with destinations, lane queues and closure rerouting. The synthetic fleet is estimated from historical density; vehicles awaiting safe insertion and completed arrivals are counted separately. It is finite and drains as trips finish. Positions are simulated, not measured GPS tracks. Network controls, destinations and demand remain assumptions. If simulation cannot produce frames as fast as playback consumes them, playback pauses while buffering; it never invents movement to fill the gap. Without SUMO, the viewer reports that simulation is unavailable rather than generating random trajectories.

Nearby cars at zoom 16.5 or closer use imported Kenney CC0 sedan/SUV geometry (about 2,000–2,500 triangles), merged and instanced per type with baked vertex colours. Cars beyond 450 m from the view centre, or at wider zoom, use a shared procedural mesh. Detail changes preserve vehicle identity and position; they do not reduce the represented fleet. The model geometry is embedded in the HTML. Traffic statistics while playback is enabled describe the same simulated fleet and paired baseline run; the static sampled-trip road-impact analysis remains separate. Road colours still show historical medians rather than instantaneous simulated lane speeds. Bus routes and closed/slowed-road overlays remain available. The page receives aggregate road measurements and generated simulation positions, never individual phone probe readings. See [the SUMO integration notes](traffic-sumo.md) for calibration, buffering and network exclusions.

Limits:
- **Old data:** the speeds are typical pre-2020 conditions, before the 2019 crisis and the 2020 port blast.
- **Volumes:** they come from one 2016–17 survey, per road class scaled by lanes, and are assumed (0.6 of capacity) on uncounted classes.
- **Capacities** are typical values, not Beirut measurements.
- **No traffic behaviour beyond rerouting:** there are no signals, turn restrictions or parking. There is no demand response: nobody cancels a trip or leaves earlier.
- **Not an equilibrium:** congestion is one pass of incremental assignment.
- **Uncalibrated rules:** closure rules are not calibrated against any observed disruption. Read the output as a picture of the consequence, not a traffic model.

Other sources considered: the same study models peak volumes in PCU per corridor section with and without the BRT (Tables 7-15 to 7-20), which could anchor a future demand model. [Lebanese-Bus-Routes](https://github.com/LebaneseDevelopers/Lebanese-Bus-Routes) (MIT, last updated 2019) has KML bus routes; the OSM relations were used instead because they are more current and share the roads' licence. Open Data Lebanon has monthly national crash totals only, with no locations. The Beirut Urban Observatory reports 2023 trip data that is not openly downloadable.

### Evidence bundles

`evidence.py` holds a registry of citable sources (laws, decrees, reports) curated by people, frozen per snapshot date into an `EvidenceBundle` whose id is a hash of its content (`dynacity freeze-evidence`). A compiled lever may cite a source by id; the checker rejects unknown, superseded, withdrawn, draft, not-yet-effective, and expired sources, naming the replacement when one exists. Unverified sources are allowed but cap the lever at `low` confidence. The resulting `effect_source` is `evidence:<source_id>@<bundle_id>`, and `ScenarioSpec`/`ForecastResult` carry `evidence_bundle_id`, so a forecast identifies the exact text it was conditioned on. `examples/evidence_registry.example.json` is a template: its entry is unverified until someone fills it from the primary document.

### Backcasting and trade-offs

`dynacity plan` (and `POST /v1/plan`) runs the engine over combinations of candidate levers — each tried off and at each allowed log-odds strength and activation step — and reports:

- **backcast** — the least-intensive combinations (Σ |Δ| × buildings in scope) meeting every KPI target, or the closest ones with a warning when none does;
- **Pareto front** — combinations not beaten on every objective at once (KPI objectives at p05/p50/p95, plus intensity by default).

Search is exhaustive up to `max_evaluations` (default 128), otherwise a seeded sample that always includes business-as-usual and every single-lever setting. Every candidate reuses the request seed, so differences come from levers, not Monte Carlo noise. Answers inherit the levers' assumed effect sizes and are labelled `assumption_based_scenario`.

### 3D viewer

`dynacity export-viewer --bbed --snapshot [--forecast] --output` writes one self-contained HTML page (deck.gl from jsDelivr) that extrudes BBED footprints to recorded height (else floors × 3.2 m, else 6 m, flagged as estimated) with four lenses: observed state, most likely state in two years, chance of leaving the current state, and — after a scenario — the change versus baseline. A KPI strip shows p50 and the p05–p95 band per horizon. `dynacity serve --model --snapshot --bbed [--evidence]` serves the same page at `/` with the prompt bar live: prose goes to `/v1/viewer/scenario`, which compiles it against the server-side snapshot, runs the forecast, and returns the audit trail and result. A disaster tray beside the tool rail lets a planner drag any hazard kind onto the city (a tornado is then steered by dragging its track, a plane aimed by dragging out the direction it comes from, and a flood given a reach); the drop posts longitude/latitude points and a severity to `/v1/viewer/hazard`, which converts them to snapshot metres, runs the forecast with no language model, and returns per-hazard impacts plus the buildings each hazard hits hardest, so the camera flies there. Dropped disasters stack (up to eight, all on the first step), and the change-versus-baseline lens shows a disaster as the rise in the chance of losing the current state. Each strike plays out in 3D: a plane dives in trailing smoke and ends in a fireball, a funnel crosses its track, flood water rises over the terrain, rain and storms fall from a cloud deck (storms with wind-slanted rain and lightning), a fire front spreads and sets roofs alight, heat casts a warm haze, a quake shakes the view and raises dust, and a riot fills the streets with crowds, burning cars, tear gas and police lights. The median number of destroyed buildings (from the top of that hazard's own hit ranking) topple or pancake into rubble and dust; damaged ones shake and are left with a broken top. Weather and quakes reach kilometres, so the camera watches them from street level at the drop point rather than framing every building they touch. Which buildings fall is a picture of the median, not a per-building prediction; the panel says so. The page embeds footprints, heights, states, sector, use, and model outputs only — never per-object `features` or point-cloud morphology — but it is still a data artifact: write it under `DYNACITY_ARTIFACT_ROOT`. The state colours are a categorical set checked for colour-vision deficiency in legend order.

Footprints are converted from UTM to longitude/latitude (`viewer.utm_to_lonlat`, sub-millimetre against pyproj) and drawn over a CARTO/OpenStreetMap basemap (streets, sea, parks, sky and haze), with OpenStreetMap buildings extruded in pale white as context wherever BBED has no footprint. The camera opens on the densest cluster of surveyed buildings and flies to a scenario's affected buildings when one runs. The basemap requests tiles from CARTO, which reveals the viewed area to that server; `--basemap none` draws the buildings on a plain ground slab with no tile requests. In the Δ lens a building's value is the change in the probability of reaching the target state of the lever covering it, on a symmetric scale fitted to the scenario's largest change.

## Known limitations

- BBED contains few observation waves and substantial class imbalance.
- The 2020 port blast and Lebanon's crises introduce structural breaks.
- Point-cloud coverage is partial and its classifications are unassigned.
- BBED footprints, not watershed instances, remain authoritative at party walls.
- Heritage layers are not used as a v1 KPI because their vintages disagree.
- The present out-of-distribution score covers missing core morphology only; a learned density score is future work.
- Hazard fragility curves are uncalibrated defaults, and post-hazard recovery rests on very few observed demolitions.


### Evidence in the viewer

Building details distinguish the matched BBED status wave (including 2022
fallbacks), recorded heights, floor-count height estimates, and illustrative
fallback heights. The snapshot reference date is not a per-field measurement
date. Map extrusion estimates never enter the KPI calculations. Source-wave
counts cover drawn buildings; KPI input coverage covers all snapshot objects.

Forecast results now retain `baseline_as_of`, `training_intervals`, and
`monte_carlo_draws`. These are optional for older saved results; missing metadata
is displayed as unavailable rather than inferred. KPI p05–p95 ranges describe
sampled transitions under fixed inputs, model and effect sizes, not total
predictive uncertainty. Recorded heights alone enter height KPIs; missing floor
counts contribute zero to the estimated footprint × floors gross floor area.

Hazard counts, collapse animations and traffic impacts remain illustrative and
uncalibrated. Confidence grades are declared by the scenario author, not a
calibrated probability that the effect size is correct. The displayed coverage
heuristic is not a calibrated confidence score.
