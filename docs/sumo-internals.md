# How SUMO works here, and where learning can plug in

Step 0 of [simulator-learning-layer.md](simulator-learning-layer.md): understand the
simulator before deciding what to learn. This covers SUMO as `traffic_sumo.py` uses it.
For the API, playback and calibration status, see [traffic-sumo.md](traffic-sumo.md).

The `sumo` extra pins Eclipse SUMO, `sumolib` and `traci` to 1.22.0. `sumolib` was unpinned
until 8 November 2026, and pip had installed 1.28.0 beside SUMO 1.22.

## What SUMO is

A microscopic traffic simulator. Every car is an individual with a route, a position on a
lane and a speed, advanced in fixed time steps (one second here, `STEP_S`). There is no
equation for traffic as a whole: queues, spillback and detours emerge from each car
following the one ahead, changing lanes and giving way at junctions.

## The pieces of a run

| Piece | In SUMO | In this repo |
|---|---|---|
| Network | Edges (one direction of a road) made of lanes, joined at junctions by explicit lane-to-lane connections. `netconvert` builds the compiled `.net.xml`. | `export_network` writes nodes, edges and connections from the OSM road model (lanes, one-ways, turn bans, signal nodes) and runs `netconvert --no-turnarounds`. Lane width 3.2 m is assumed. |
| Demand | Vehicles with a departure time and a route (a list of edges), or trips (origin and destination) routed on departure. | The initial fleet is written as explicit routes (`demand`, `trips.rou.xml`). Continuous entries and interior trips are added during the run over TraCI (`BoundaryInflow`). Routes come from Python Dijkstra over SUMO's edge graph (`EdgeRouter`). |
| Vehicle types | Length, acceleration, deceleration, a desired-speed factor and driver imperfection per type. | SUMO's default passenger car; an optional mixed fleet adds SUMO default types per class (`mobility_inputs.py`). No Beirut-specific values. |
| Car following | Each step a car picks the fastest speed that lets it stop safely behind its leader. The default is SUMO's Krauss model: `accel` 2.6 m/s², `decel` 4.5 m/s², `tau` 1 s reaction, `sigma` 0.5 random dawdling, `minGap` 2.5 m. | Defaults. |
| Desired speed | Each car drives at the edge's speed limit × its own `speedFactor`, drawn around 1.0 (spread `speedDev` 0.1 by default). | Each road's limit is the fastest of its Tari'ak hourly medians, its free-flow speed. |
| Lane changing | LC2013 by default: change for the route, for speed, to keep right, or to cooperate. | Defaults. |
| Junctions | Right of way from road priority and junction type; cars on minor roads wait for gaps. Cars cross on internal junction lanes. | Edge priority follows the OSM road class, with SUMO's own OSM import values (`ROAD_PRIORITY`; before 8 November 2026 every edge had the same priority and netconvert guessed the main road). `--ignore-junction-blocker 60`: a car may enter past one standing in the junction 60 s (knots, see [traffic-sumo.md](traffic-sumo.md#gridlock)). |
| Signals | `netconvert` makes a fixed-time program per signalised junction (90 s cycle by default). Actuated programs are possible. | Locations from OSM; timing is SUMO's default. Survey programs can replace it over TraCI. |
| Routing during the run | A car keeps its route unless told otherwise; a rerouting device or TraCI can reroute it on current travel times. | A closure removes passenger permission on the edges; cars upstream are rerouted to the same destination over TraCI (`apply`). Routing algorithm CH. |
| Control | TraCI: a socket protocol to read and change anything each step (vehicles, edges, lanes, signals). State can be saved and loaded. | One SUMO process per run. The baseline is a saved-state copy of the scenario at the first disruption (`fork`). Subscriptions read the fleet in bulk. |
| Outputs | Files such as per-edge aggregates (`edgeData`), per-trip records (`tripinfo`) and positions (FCD). | Read live over TraCI instead: frames, metrics, `speed_check` against Tari'ak. |

## One step

Roughly, each second SUMO first carries out the TraCI commands it has received
(closures, new trips, reroutes). Every car then works out its safe speed from its leader,
the signal ahead and the junction's right of way, decides whether to change lane, and
moves. Cars waiting to depart are inserted where there is safe room; otherwise they wait
(`pending`). Teleporting stuck cars is switched off here (`--time-to-teleport -1`).
`unstick` moves the front car of a queue that has stood 300 s instead, without passing a
closure.

## What the repo adds on top of SUMO

Most of what decides whether the run matches Beirut sits in the repo, not in SUMO.
These are the candidates for calibration:

| Setting | Value now | What it controls |
|---|---|---|
| `INTERIOR_DEMAND_SCALE` | 1.0 | share of the replacement trip rate started inside the area |
| `INTERIOR_MIN_TRIP_M` | 1,000 m | shortest straight-line interior trip (trips come out about 4 km) |
| `EXIT_DETERRENCE_S` | 60 s | how strongly entering cars prefer near exits |
| `MIN_ENTRY_M` | 60 m | shortest entry road a boundary car may start on |
| `JUNCTION_BLOCKER_S` | 60 s | when a car may enter past a junction blocker |
| `UNSTICK_AFTER_S` | 300 s | when a stuck queue's front car is moved ahead |
| Boundary entry rates | from CDR class counts and road density | vehicles per hour entering at the edge |
| Vehicle parameters | SUMO defaults | `speedFactor` spread, `sigma`, `tau` (not exposed yet) |

## Faster ways to run SUMO

| Mode | What changes | Status here |
|---|---|---|
| Microscopic (now) | every car on every lane | about real time citywide |
| Mesoscopic (`--mesosim`) | each edge is a few queues; no lanes or car following inside an edge; junctions simplified | SUMO's documentation puts it up to 100× faster. Untested here. Closures would work; lane-level effects, such as an added lane, would be coarser. Worth timing for generating training runs, checked against microscopic runs. |
| Macroscopic (`marouter`) | static assignment, no time steps | the repo's own `traffic.traffic_impact` (BPR curve, successive averages) plays this role |
| `libsumo` instead of TraCI | the same API inside the Python process, no socket | not installed. Faster when there are many calls per step. One simulation per process, so the paired baseline needs a second process. |

## Calibration tools that ship with SUMO

All present under `.venv/Lib/site-packages/sumo/tools`.

- `routeSampler.py`: picks routes so edge counts match measured counts. It needs per-road
  counts. Beirut has CDR counts per road class and peak, not per road, so it fits only
  partly.
- `assign/cadytsIterate.py`: Cadyts, which adjusts demand to match counts. Same data
  limit.
- `assign/duaIterate.py`: iterates route choice until drivers settle on an equilibrium.
  The demand here routes each trip once, on free-flow times.
- Calibrators (`<calibrator>`, `traci.calibrator`): force a flow and speed on an edge
  during the run. This is useful at the boundary, but it does not explain why the inside
  differs.

The observation with wide coverage is speed: Tari'ak hourly medians, which give 5,811 of
the 10,818 roads a speed profile.
So calibration here means choosing demand and driver settings so simulated speeds match
those, which none of the tools above does directly. That gap is where a learned
calibration method has room.

## Where learning can plug in

| Where | What would be learned | Changes SUMO's code? | Data it needs | Verdict |
|---|---|---|---|---|
| Inputs, before the run | demand rates, per-zone multipliers, trip lengths, driver parameters (calibration) | no | Tari'ak speeds, CDR counts (have) | first |
| Outputs, after many runs | a model that predicts a plan's effect on each road (learned from simulation) | no | SUMO runs of generated plans (we make them) | second |
| Above SUMO | a policy that chooses plans, scored by the learned model with SUMO checks (RL) | no | the two above | third |
| During the run, over TraCI | learned route choice or speed choice steering each car | no, but a TraCI call per car per step is slow | observed Beirut trajectories or route choices (do not have) | not now |
| Inside SUMO's C++ | a new car-following or lane-change model | yes: a SUMO fork rebuilt per change | as above | no |

"A learning layer inside the simulator" is therefore best read as learning *on SUMO's
inputs and outputs*, wrapped around an unchanged SUMO. That keeps SUMO as the reference
every learned answer can be checked against. It also keeps the work independent of SUMO
releases.

## Measured on the corridor (8 November 2026)

Corridor at the 250 m buffer (1,426 roads), 08:00, continuous demand, no view (`limit=0`),
60-second chunks, on this Windows PC. The test warms up 10 simulated minutes, then closes
Armenia Street (28 road pieces) for 10 minutes.

| Measure | Result |
|---|---|
| Model load and SUMO start | 0.3 s and 1.8 s |
| One simulated minute, no closure | 4–7 s, slower as the fleet grows |
| One simulated minute, closure and paired baseline | 12–16 s; the first, with the fork, 15 s |
| 20 simulated minutes in all | 197 s |
| Three runs in parallel processes | each 94 s for 10 simulated minutes, against about 70 s alone: about 2.2× the throughput |
| `--mesosim` through the adapter | fails at start ("SUMO replay is missing an active vehicle"): the adapter's accounting expects every car in the context subscription, which the mesoscopic model does not provide the same way |
| `--mesosim`, SUMO alone, initial fleet only, 15 min | 0.5 s against 2.9 s micro (about 6×); per-road speeds differ by a median 5 km/h (correlation 0.75; meso faster, 28.8 against 24.0 km/h), median trip 47 s against 70 s |

**The corridor run does not reach a steady state.** Cars waiting to enter grow by about
900 a minute (444 after one minute, 7,062 after ten, 16,934 after twenty); stopped cars
and `unstuck` moves keep rising; the speed ratio falls from 1.1 to 0.89 and keeps falling.
Boundary demand exceeds what the corridor's entries admit. A what-if measured against a
baseline that is still degrading mixes the plan's effect with the drift. Calibration
therefore comes first, and it has to include the boundary demand.

**Entry rates alone are not the cause.** At 08:00 the boundary sends about 103,500 cars
an hour into the corridor through 116 cut nodes. Every road of a class carries the class's
average count (`traffic.background_flow`). Against the counts in
[corridor-data-sources.md](corridor-data-sources.md#traffic-counts-and-trips):
- **Charles Helou:** 7,152 cars/h each way in the model, against 3,705 eastbound and 7,024
  westbound counted (09:00–10:00).
- **Armenia Street:** 444–480 cars/h each way, against 277–307 counted in 2023.
- **Corniche Pierre Gemayel:** 1,900–2,700 cars/h, close to the counted 1,744–3,033.

Scaling the entries down does not settle the run, though. The interior trip rate rises to
keep the represented fleet, and the inside jams instead:

| After 20 min at 08:00 | entries ×1 | ×0.5 | ×0.35 |
|---|---|---|---|
| Active cars | 5,718 | 7,560 | 7,980 |
| Stopped | 4,545 | 6,776 | 7,217 |
| Waiting to enter | 16,934 | 7,477 | 5,615 |
| Speed ratio (simulated/observed) | 0.89 | 0.34 | 0.25 |

The total demand, set by the represented fleet (`flow × length / speed`), exceeds what the
corridor's junctions clear. Calibration must therefore search the total demand, the split
between entries and interior trips, and the junction behaviour together. It is scored on
three things:
- whether the run settles;
- simulated speeds against Tari'ak;
- simulated flows against the counts.

**Mesoscopic mode is not a drop-in.** It is several times faster but gives different
speeds, so it would need its own calibration, and the adapter would need changes.

**Throughput.** At about 13 s per paired simulated minute and about 2.2× from three
parallel processes, one paired hour of corridor traffic costs about 6 minutes of machine
time. That is roughly 250 paired plan-hours a day on this PC: enough for calibration and a
first learned model, not for an RL agent that calls SUMO at every step.

The timing at 60-second chunks exposed an adapter bug: a car left to retry after a closure
could arrive before the next `apply`, which then asked SUMO about a vehicle it no longer
knew. `apply` now retries only cars still present.

## First calibration search (8 November 2026)

**The search.** Three demand-side settings were searched jointly:
- the boundary entry rates, × 0.2 to 1.2;
- `INTERIOR_DEMAND_SCALE`, 0.05 to 1.5 (searched on a log scale);
- `JUNCTION_BLOCKER_S`, 10 to 180 s.

**Each run.** Corridor at 08:00, seed 1, 5 minutes of warm-up, then 10 measured minutes
read every 10 s through `plan_runs._RoadSampler`.

**The loss, lower is better.** It is the sum of three terms:
- **Speed:** |ln| of the median simulated/Tari'ak speed ratio, over roads with real readings
  and at least 60 vehicle-seconds.
- **Counts:** the mean |ln(simulated/counted flow)| for Charles Helou eastbound, Charles Helou
  westbound and Armenia Street.
- **Drift:** the growth of cars waiting plus cars driving, per minute, divided by 300.

**The method.** A Latin hypercube of 8 points plus today's defaults, then Bayesian
optimisation: a Gaussian process with expected improvement, in batches of 3 parallel runs.
The best three were then re-run with seed 2.

The run took 57 minutes: 153 runs, none failed. The script is in the session scratchpad,
not the repo, and **no default was changed.**

| | Defaults (entries ×1, interior ×1, blocker 60 s) | Best (entries ×0.20, interior ×0.25, blocker 140 s) | Counted / observed |
|---|---|---|---|
| Loss | 6.29 | 1.56 (seed 2: 1.86) | 0 |
| Speed ratio, median | 0.37 | 1.12 | 1 |
| Speed error per road, mean | 19.7 km/h | 14.4 km/h | — |
| Cars waiting + driving, growth per minute | +1,110 | +156 | 0 |
| Waiting to enter at the end | 12,155 | 430 | — |
| Charles Helou E / W, cars per hour | 2,167 / 2,342 | 2,262 / 1,768 | 3,705 / 7,024 |
| Armenia Street, both ways | 25 | 243 | 584 |

What it shows:

- **No demand setting fits.** Every good setting sits on the search's lower edge, with
  entries at ×0.2. Even there, the corridor keeps filling at about 130–200 cars a minute.
  Each road's speed is still off by about 14 km/h on average, even where the median ratio is
  near 1. Lowering the demand trades jams for empty roads: Armenia Street then carries too
  little.
- **Charles Helou is capped near 2,000 cars an hour each way, whatever the demand.** It is
  4 lanes each way in the model, and Tari'ak has it at 48–65 km/h at 08:00. With entries at
  ×1, which send it 7,152 cars an hour, it carries about 2,200. The westbound count is 7,024.
  The limit therefore sits in the network the cars reach it through, not in the demand.
- **Not the insertion lane.** Boundary cars are inserted on one lane (`departLane='best'`).
  Re-running three settings with `departLane='free'` changed little: at entries ×1, still
  12,485 cars waiting and Charles Helou at 2,336 / 2,955.
- **The junction blocker time hardly matters.** Settings from 10 to 180 s sit side by side
  among the best.
- **Seed noise is as large as the differences.** With seed 2, the best three scored 1.84–2.01,
  against 1.56–1.61 with seed 1. Most of the 40 best settings lie within that noise of each
  other, so a single seed cannot rank them.

## Where the queues form (8 November 2026)

**The method.** Each road's halted cars (speed under 0.1 m/s) were averaged over minutes
10–15 of a run. A *queue head* is a jammed road (at least half full and at least 3 halted
cars) whose ways out are mostly free (under 40% full): the junction at its end holds the
queue, rather than a queue spilling back from further on.

| Entries, interior trips | Halted cars | Behind priority junctions | Behind signals | Queue heads (halted cars they hold) |
|---|---|---|---|---|
| ×1, ×1 | 3,457 | 3,380 | 78 | 32 (319) |
| ×0.2, ×0.25 | 1,493 | 1,416 | 77 | 12 (121) |

- **Signal timing is not the lever.** The corridor has 13 signalised junctions out of
  1,079, all on SUMO's default 90 s program (42 s green per direction). They hold about 2% of
  the halted cars.
- **Most of the queue is spillback, not a few bad junctions.** At entries ×1 the queue
  heads hold only 9% of the halted cars. The rest stand behind other queues. No single
  junction fix clears it.
- **Main roads gave way to minor ones.** The exporter set no edge priority, so netconvert
  guessed the main road from the angles. At priority junctions, 19 of 104 motorway turns and
  26 of 160 trunk turns had to yield. One of the worst queue heads is Corniche Pierre
  Gemayel (road 477) giving way to its own slip road. **Fixed:** edge priority now follows
  the road class, with SUMO's OSM import values.
- **Entries sampled too few routes.** Each entry drew at most six exits and split its cars
  evenly among them. A draw with a few percent probability thus took a sixth of the entry:
  for example, 750 cars an hour from the Joseph Chader Bridge entry (4,486 an hour) into
  residential Street 87. On Charles Helou eastbound, the queue is cars for a one-lane,
  30 m off-ramp (road 40) to Corniche Pierre Gemayel, which two of the six draws sent a third
  of the entry. **Fixed:** 32 draws per entry (`ENTRY_ROUTE_DRAWS`).
- **What the fixes changed, at seed 1:**
  - **Entries ×1:** Charles Helou westbound rose from 2,342 to 4,636 cars an hour (7,024
    counted). Eastbound fell from 2,167 to 1,453.
  - **Settling:** the corridor still did not settle, at +1,056 cars a minute against +1,110
    before.
  - **Entries ×0.2, interior ×0.25:** loss 2.02. Before the fixes, the same demand with a
    140 s blocker scored 1.56 with seed 1 and 1.86 with seed 2. The blocker time hardly
    mattered in the first search.
- **The conclusion.** Both were real modelling errors, but neither is why the corridor
  jams. The total demand exceeds what this many small priority junctions clear under SUMO's
  default driving. What is left to calibrate is the demand and the drivers: car following
  (`tau`, `sigma`, `minGap`) and gap acceptance at minor roads (`jmTimegapMinor`).

## Second calibration search (8 November 2026)

**The search.** Seven settings were searched together, on the fixed network:
- **Demand:**
  - entries ×0.1 to 1;
  - interior trips ×0.05 to 1;
  - `EXIT_DETERRENCE_S` 20 to 240 s.
- **Drivers:** `tau` 0.4 to 1.2 s, `sigma` 0 to 0.6, `minGap` 1 to 2.5 m and `jmTimegapMinor`
  0.25 to 1.5 s. They were given to SUMO as a redefined `DEFAULT_VEHTYPE` in an additional file.

**Scoring.** Each setting got seeds 1 and 2, with 7 minutes of warm-up, and the loss was as
in the first search. The best three were re-run with seeds 3 and 4.

**The cost.** 66 minutes: 42 settings, 90 runs, none failed. **No default was changed.**

| | Best (seeds 1–2) | Same, seeds 3–4 | First search's best (seeds 1–2) | Defaults |
|---|---|---|---|---|
| Loss | 1.76 | 1.73 | 1.56 / 1.86 | 6.00 |
| Speed ratio, median | 1.33 | | 1.12 | 0.40 |
| Speed error per road | 13.0 km/h | | 14.4 km/h | 19.5 km/h |
| Growth, cars a minute | +61 | | +156 | +1,075 |
| Charles Helou E / W | 1,990 / 1,288 | | 2,262 / 1,768 | 1,087 / 4,517 |
| Armenia Street | 135 | | 243 | 39 |

The best setting was entries ×0.16, interior ×0.18, exit deterrence 30 s, `tau` 0.65 s,
`sigma` 0.27, `minGap` 2.3 m and `jmTimegapMinor` 0.65 s.

- **Settling, or speeds and counts, but not both.** No setting both settled (under 50 a
  minute) and had a median speed within 15% of Tari'ak. The settings that nearly settle
  admit 10–20% of the boundary traffic. Their roads are then 30% too fast, and the counted
  roads carry a fifth to a half of their counts.
- **Driver settings do not add enough capacity.** With entries above ×0.4, every setting
  kept growing by at least 515 cars a minute, whatever `tau`, `minGap` or gap acceptance
  it had.
- **Entries scaled by road class.** As a check, entries on motorway, trunk and primary cut
  nodes were kept at ×1 or ×0.7, and side-street entries were cut to ×0.05–0.1:
  - **Flows:** Charles Helou rises to about 2,900 / 4,700–5,400 cars an hour, the closest
    to the counts in any run (3,705 / 7,024).
  - **Inside the corridor:** only about 3,000 cars on its roads, at speeds 26–31% above
    Tari'ak.
  - **At the entries:** 3,500–6,800 cars are waiting to enter after 10 minutes. They queue
    on the motorway entries themselves: Elias Al Hrawi, Émile Lahoud, Joseph Chader Bridge
    and Charles Helou westbound, at 5–25 km/h.
  - **Not the insertion lane:** inserting on any free lane did not change this.

**What this points to.** The motorway interchanges inside the corridor clear far less
traffic in SUMO than they carry in Beirut. The network is built straight from OSM nodes,
without the steps SUMO recommends for OSM imports:
- **`--junctions.join`:** an interchange is many close OSM nodes, each a junction with its
  own right of way.
- **`--ramps.guess`:** on-ramp merges and acceleration lanes.

That is the next thing to test. Some short edges would disappear, so the road-identity
accounting has to allow for them. Demand cannot be calibrated until the main roads can
carry counted flows.

## Joined junctions and ramps (8 November 2026)

`export_network` now runs netconvert with `--junctions.join` and `--ramps.guess`
(`OSM_IMPORT_OPTIONS`). Cut nodes and surveyed signals are kept out of joined clusters,
because they are looked up by their own id.

**What changes on the corridor:**
- **Junctions:** 59 clusters are joined, and 1,079 nodes become 1,006.
- **Roads:** 81 short roads, 948 m in all and mostly residential and tertiary, disappear
  inside clusters (`excluded_roads`). A plan that closes one of them changes nothing.
- **Signals:** 13 signal programs become 6.
- **Ramps:** 9 ramp pieces are added (`<road>:<direction>-Added…RampEdge`). They count
  towards their road, since every road lookup reads the number before the colon.

**The effect was small.** Seed 1, after the same 7-minute warm-up:

| Setting | Before | Joined + ramps |
|---|---|---|
| Defaults: growth a minute, waiting to enter | +1,075, 12,155 (seeds 1–2) | +1,036, 12,001 |
| Main-road entries ×1, side ×0.05, best drivers: waiting to enter | 3,526–6,431 | 4,925 |
| Same: Charles Helou E / W | 2,850–3,030 / 4,710–5,390 | 2,791 / 5,210 |

**Where the motorways actually jam: one lane.** With main-road entries ×1, cars were
counted per lane on the busiest motorway pieces:
- **Only the right lane is queued.** On Charles Helou eastbound (road 1038), 91 cars stand
  in lane 0 and 49 in lane 1, while lanes 2 and 3 hold about 12 cars each and move.
  Elias Al Hrawi (road 1421) has 41.5 cars in lane 0 and none in lane 2.
- **The queue is for an off-ramp.** On Charles Helou it is cars for the one-lane ramp
  (road 40) into the trunk links (road 624) to Corniche Pierre Gemayel. Road 624 was itself a
  queue head (above).
- **It traps through traffic.** The queue backs up the right lane for over 700 m. Through
  cars in lane 1 stall too: they are blocked by cars waiting to merge right.
- **Why the ramp is overloaded.** An entering car's exit is drawn by the exit's flow and how
  near it is (`EXIT_DETERRENCE_S`). That rule sends a one-lane ramp more cars than it carries.
  The second search preferred short trips (deterrence 20–30 s), which makes this worse.

**What this points to.** The limit is now the demand's split over exits, not the junction
geometry. The fix is to choose routes so each road's flow matches its expected flow:
- **Targets:** the counts where we have them, and the class flows on the main roads where
  we do not.
- **Tool:** SUMO's `routeSampler.py` does this from edge counts. Cadyts does it during the
  run.
- **Contrast:** today, a single nearness rule sets every split.
