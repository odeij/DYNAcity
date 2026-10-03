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

`traffic.py` adds a street network beside the buildings. It sits outside the transition model, which never sees it, and it never changes a forecast's numbers. `dynacity build-traffic` joins two open sources over the snapshot extent (building centroids plus 800 m) and writes a JSON file under `DYNACITY_DATA_ROOT`:

- **Roads:** drivable OpenStreetMap ways (motorway down to living street; service roads and footways left out), fetched from Overpass or read from a saved Overpass JSON (`--roads`). They are split at every node shared with another way into a routable graph, with one-way rules honoured.
- **Speeds:** the [Lebanon Traffic Dataset](https://github.com/ramikay/lebanon-traffic-dataset) (Tari'ak app, ODbL). It holds 6.0 million crowd-sourced vehicle speeds from 17,274 phones, 2015–2019, each map-matched to an OSM way. Each way gets a median speed per hour of day (Beirut time). Readings under 3 km/h are dropped: they are phones standing still, and at 4 a.m. they would pull an empty road's median to zero. A way needs 10 readings in an hour to use its own median. Otherwise it borrows its road class's hourly profile, scaled to its own all-day median when it has one, and it is flagged `estimated`. The dataset's metadata says the Velocity column is in m/s, but its values only make sense as km/h (motorway median 57, hard cap 120), so km/h is the default (`--velocity-unit`).

On the 2024 snapshot this gives 10,817 road segments (809 km) and about 1.0 million readings. 5,006 segments have their own speeds; the rest borrow their class's. Typical speeds drop from about 26 km/h at night to about 19 km/h from 07:00 to 19:00.

When the server holds a traffic model (`serve --traffic`), every dropped or compiled disaster also returns `traffic`: which roads it closes or slows, and what that does to trips at the chosen hour:

| Effect | Rule (an illustrative assumption) |
| --- | --- |
| Rubble | A building reaches the streets within half its height plus half its footprint width when it collapses. A road is closed when P(blocked) = 1 − Π(1 − P(destroyed)) over buildings in reach is ≥ 0.5, and slowed in proportion between 0.15 and 0.5. |
| Water | A flood closes a road where it stands deeper than 0.3 m: the same water level as the buildings, against terrain when it is loaded, otherwise flat. |
| Cordon | Fire, riot, blast, plane crash and orbital strike close the roads where the hazard's exposure is at least 0.5. |
| Weather | Storm and rain multiply speeds by 0.7 and 0.85 at full exposure, easing back to 1 at the footprint edge. |

`traffic_impact` samples about 1,000 trips between road junctions. Ends are drawn in proportion to probe readings × road length, because Tari'ak records speed but not volume, so readings stand in for how busy a road is. Each trip is routed by travel time before and after the disruption. It reports trips whose usual route crosses a closed or slowed road (`affected`), how many of those are cut off, and the p50/p90 extra minutes for the rest. It also reports the roads that pick up the rerouted trips (`detour`). Delays are over affected trips only, so a local closure is not averaged away across the city. When no sampled trip uses the closed streets, the result says so.

In the viewer, the Traffic button colours each road by its median speed at the chosen hour against its fastest hour, and moves cars along the roads at 4× real time. The number of cars is a picture of how busy a road is, not a count. After a disaster, closed roads turn dark red and empty, slowed roads crawl, and purple marks the detours. The page receives only per-road hourly medians and reading counts, never individual probe readings.

Limits: the speeds are typical pre-2020 conditions, before the 2019 crisis and the 2020 port blast. There is no traffic volume, signal timing, or demand response, so rerouted trips do not slow the roads they move onto. Closure rules are not calibrated against any observed disruption. Read the output as a picture of the consequence, not a traffic model.

Other sources considered: the CDR's environmental and social impact assessment for the Tabarja–Beirut Bus Rapid Transit project (World Bank SFG3708) has corridor traffic counts, which could calibrate volume later. [Lebanese-Bus-Routes](https://github.com/LebaneseDevelopers/Lebanese-Bus-Routes) (MIT, last updated 2019) has KML bus routes. Open Data Lebanon has monthly national crash totals only, with no locations. The Beirut Urban Observatory reports 2023 trip data that is not openly downloadable.

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

