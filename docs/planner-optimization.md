# Planner sandbox and plan optimisation (proposal)

Status: proposal, 10 November 2026. Nothing here is built yet beyond what
[Street planning](web-street-planning.md) and [SUMO traffic](traffic-sumo.md) already describe.

The goal: an expert urban planner sits at the viewer and edits the street network,
much as people do in SUMO demo videos (close a lane, add one, put in a roundabout,
change a junction). The platform must (1) **show** each change and what it does,
and (2) **optimise** the plan for cost, accessibility and traffic.

## 1. What already exists

| Capability | Where | State |
| --- | --- | --- |
| Close a road, cap its speed, give a lane to pavements | `traffic_planning.py`, `traffic_sumo.Simulation.apply` | Working in the viewer |
| Paired SUMO runs: unchanged baseline vs plan, same fleet and seed | `traffic_sumo.py` | Working, citywide or corridor |
| Fast static assignment: sampled trips, shortest paths, BPR curve | `traffic.traffic_impact` | Working; takes seconds, where SUMO takes minutes |
| Pareto front and backcast search over levers | `planning.py` | Working for building transitions, not for roads |
| Walking network and opportunities (amenities, jobs proxy, homes) | UNA inputs, `docs/pedestrian-flow-model.md` | Built, private data |
| Corridor scoping (1,426 of 10,818 roads at 250 m) | `corridor.py` | Working |

So the simulator, the comparison and a search pattern already exist. What is missing:
more types of edit, a scorecard that puts numbers on each plan, and an optimiser for road plans.

## 2. Edits the planner can make

| Edit | SUMO mechanism | New? |
| --- | --- | --- |
| Close a road / pedestrianise | lane permissions | exists |
| Speed cap | edge max speed | exists |
| Remove a lane (to pavement, cycle or bus) | lane permissions; bus-only via `allow="bus"` | exists; bus/cycle lane is new |
| **Add a lane** | rebuild the edge with one more lane (netconvert patch) | new |
| **Roundabout** | junction type → `priority` ring, via a netconvert `--roundabouts` patch | new |
| **Signal retiming / remove signal** | a custom `tlLogic` program, or junction type `priority` | new |
| **Turn ban** | delete the connection | new (OSM bans already handled) |
| **One-way conversion or reversal** | rebuild the edge direction | new |
| **New link** between two existing nodes | add an edge | later |

Lane, roundabout, one-way and new-link edits change the network itself, so they need a
rebuilt `.net.xml` rather than a TraCI call during the run. Each plan becomes a small
netconvert patch (`.edg/.nod/.con/.tll` overlays) applied to the base network and
cached by a hash of the plan. Runtime edits (closures, caps, permissions) keep using TraCI.

Adding a lane takes 3.2 m of street. BBED and Beirut 001 give building footprints, so
the scorecard can report **how many buildings the widening would hit**. That is often
the real limit on widening in Beirut, and the cost model needs it.

## 3. Showing the changes

- Every edit is drawn on the map in a consistent style: added lanes in a new-lane
  colour, roundabouts as rings, removed lanes as pavement, bans as junction arrows.
- A **plan list** panel shows every edit with its own cost, with buttons to toggle,
  delete or zoom to it.
- A **scorecard** shows baseline, plan and change for the three objectives below. Each
  number is labelled by how it was computed: *fast estimate* (static) or *simulated* (SUMO).
- A **difference map** colours each road by its change in flow or delay. The
  purple reroute overlay already does part of this.
- After optimising, a **Pareto chart** where each dot is a plan. Clicking a dot loads it
  onto the map so the planner can see it, change it and re-run it.

## 4. What "optimise" means: three objectives

All three use **fixed demand**: the same trips, origins and destinations in baseline and
plan. Section 6 covers what this leaves out (induced demand).

### 4.1 Cost: whole-life social cost, following Copenhagen

The Copenhagen work (Costa, Pereira et al., DTU and Copenhagen Municipality) defines
the reward as minus the sum of impacts per zone:

    R_t = − Σ_zones ( I + D + C + A + M )

Here I is infrastructure damage, D is travel delay costed at value of time, C is
cancelled trips at 80% of their no-disruption cost, A is implementation cost and M is
maintenance. They use PPO with a graph-convolution policy over 29 zones, choose
measures once a year from 2024 to 2100, and compare against Bayesian optimisation.

That maps onto our case without flooding. The disruption is the plan itself, plus the
works needed to build it:

    Cost(plan) = Capex + PV(Maintenance)
               + PV( VoT · ΔVehicleHours )             # D: delay, + or −
               + PV( 0.8 · VoT · t₀ · UnservedTrips )  # C: trips with no route
               + Construction-period delay             # works close lanes: the flood analogue
               [+ buildings affected × acquisition cost, if widening]

- **Capex and maintenance**: unit cost per edit type (per lane-km widened, per
  roundabout, per signal retimed, per km of bus lane). Lebanon has no published unit
  cost schedule in the repo. Start from the World Bank ROCKS road-cost database for the
  region, and treat each value as a parameter with a range.
- **Value of time**: no Lebanese figure is loaded. Use income-scaled transfer
  guidance as a parameter with a range, and always show the result's sensitivity to it.
- **Annualising**: one simulated hour does not give a year. Evaluate a few
  representative hours (08, 13, 17, 21), weight them by the Tari'ak / CDR hourly
  profile, multiply by working days, and discount over an appraisal period (e.g. 20 years).
- **Unserved trips** are `no_detour` and stuck `pending` trips, priced at the paper's 80% rule.

The result is one net-present-cost number per plan. This is an appraisal-style
cost–benefit figure. It is only as good as the unit costs and the value of time, so the
scorecard shows the range, not one point.

### 4.2 Accessibility: opportunities reachable, by mode, with an equity view

Use the standard gravity (Hansen) measure. It matches the decay UNA already uses:

    A_i^m = Σ_j O_j · exp(−β · t_ij^m)

- *i*: residential location (Beirut 001 homes, already built for UNA); *O_j*: amenities
  and jobs proxy; *m*: mode.
- **Car**: *t* from the plan's congested travel times (fast or SUMO).
- **Walking**: *t* over the walk network. Pedestrianised streets and new crossings
  shorten paths; motorways and trunk roads stay barriers.
- **Bus** (later): OSM bus routes exist, but there are no timetables.

Report the **population-weighted mean change** per mode and the **change for the
worst-served 20% of residents** (equity). Do not merge modes into one number until we
have a mode share we trust. If car accessibility were the only measure, the optimiser
would always build more road; walking accessibility and cost are the counterweight.

### 4.3 Traffic: total delay, not average speed

- **Primary**: total vehicle-hours of delay against free flow, for the fixed demand,
  plus unserved trips.
- **Secondary**: travel-time reliability (95th-percentile trip delay) and VKT-weighted
  mean speed.

Average speed alone is easy to game. Closing slow streets, or letting trips fail,
raises average speed while making travel worse. That is why it is reported but not optimised.

### 4.4 More objectives worth adding

- **Road safety**: conflict points (roundabouts and turn bans reduce them),
  speed × pedestrian-flow exposure, using the UNA flows.
- **Emissions**: per-vehicle CO₂/NOx from SUMO's emission model (HBEFA classes are built in).
- **Disruption during construction**: kept as its own number as well as a cost line;
  planners care about it.
- **Buildings and frontage affected** by widening.
- **Pedestrian space and comfort**: the existing pavement-area indicator.

## 5. How the optimiser works

### Setting up the problem

The planner picks a **study area** (the green corridor by default), the **edit types
allowed**, a **budget**, and a mode:

1. **Monetised**: minimise the one cost number from 4.1, with accessibility and traffic
   converted to money. This is the Copenhagen approach.
2. **Pareto**: return the plans no other plan beats on every objective at once. This
   is the default for a planner, and `planning.py` already does it.
3. **Backcast**: the cheapest plan that is no worse than today on delay and accessibility
   (or meets targets the planner sets). `planning.py` already has this idea.

### Two speeds of evaluation

SUMO takes about 19 s to start and roughly real time to step, citywide, which is far
too slow to score thousands of candidate plans. So:

- **Fast evaluator** (`traffic_impact`, static BPR assignment over sampled trips, a few
  hours): scores every candidate during search.
- **Faithful evaluator** (paired SUMO, several seeds, common random numbers): verifies
  the top 5–10 plans before they are shown as results.
- **Rank check**: before trusting the fast evaluator, check that it puts a set of plans
  in the same order as SUMO does (Spearman rank). If the two disagree, the optimiser
  cannot rely on the fast model, and we fix that first.

### Search, in the order to build it

1. **Candidates**: the planner's own edits, plus automatic suggestions at bottlenecks
   (roads with V/C > 0.9, signalised junctions with large SUMO delay, roads with heavy
   pedestrian flow next to fast traffic).
2. **Greedy / beam search** over candidates with the fast evaluator. It is simple, easy
   to explain to a planner ("this edit came first because …") and a strong baseline.
3. **Bayesian optimisation or NSGA-II** for combinations, giving the Pareto front.
4. **Reinforcement learning**, once the question becomes a **sequence over years**:
   a yearly budget, demand growth, and construction works that themselves close lanes.
   This is the same structure as Copenhagen's yearly decisions, and RL is suited to it:
   PPO with a graph policy over road or zone nodes, as in the paper.

Note the paper's own numbers: RL beat Bayesian optimisation by only about 0.6–3% on the
reduced problems. For a one-off plan, search is cheaper and easier to explain. RL earns
its place on phasing over years, so it comes last.

## 6. Known weaknesses the optimiser will find and exploit

An optimiser tends to find the model's mistakes before it finds good plans. Before
anything is presented as a recommendation:

- **Signals run SUMO's default programs.** Any "retime signals" or "replace the signal
  with a roundabout" edit will look better than it would in reality. Retiming needs
  surveyed or optimised baseline timings, or both sides must use SUMO's own timing
  optimiser (`tlsCycleAdaptation`).
- **No OD matrix and no steady state.** After 10 minutes the speed ratio is still
  0.87–1.14 and the fleet is still growing. Plans should be compared at the same time
  point and over several seeds.
- **Fixed demand.** Adding capacity does not attract new traffic in this model, so
  road-building plans look better than they are. Phase 3 should add a simple elasticity
  check (demand × (1 + ε·Δgeneralised cost)).
- **Lane counts are guessed on 6,125 segments**, and widths are 3.2 m assumptions.
- **Braess effects**: the optimiser may find that closing a road helps. That can
  genuinely happen, but check it in SUMO before claiming it.

Each result carries the label `assumption_based_scenario`, as the forecasting side already does.

### Calibrate the simulator from the data, rather than building a separate data model

Today Tari'ak and CDR are SUMO inputs: the fastest hourly median sets each road's
free-flow speed, and CDR class counts × probe-hour shape × lanes set the starting cars
and boundary entries. Hourly medians are only checked after the run. Where trips go is
invented (random legal walks, interior trips of at least 1 km) and is not fitted to anything.

A model learned directly from Tari'ak cannot answer "what if": it has only seen the
2015–2019 network. Its points carry no device ID, so trips and destinations cannot be
rebuilt from it. The data should set SUMO's settings and judge its output instead:

1. **Steady state**: warm up until the active fleet stops growing, then measure.
2. **Fit demand**: adjust zone trip production and attraction until simulated speed per
   road and hour matches the Tari'ak medians and class totals match CDR. Use SUMO
   `routeSampler` for counts and an SPSA-style search for speeds. Keep some roads and
   hours aside for testing.
3. **Signals**: give baseline and plan the same sensible timing (`tlsCycleAdaptation`),
   so retiming does not win by default.
4. **Test the response to change, not only the baseline**. The Tari'ak data end on
   17 October 2019, so the protest road blockades fall outside it. Closures known to
   fall inside 2015–2019 can serve as natural experiments: replay the closure and
   compare predicted against observed speed changes nearby. Candidates are the Beirut
   Marathon (Novembers 2015–2018) and the 22 November parade route; their dates, routes
   and probe density still need checking.

The fitted model describes 2015–2019 Beirut, before the 2019 crisis and the 2020 blast.
Today's volumes may differ.

## 7. Build plan

| Phase | Delivers | Demo value |
| --- | --- | --- |
| **0. Calibration** (alongside 1; required before 3) | Steady state, demand fitted to Tari'ak / CDR with held-out roads, shared signal timing, natural-experiment check | Numbers that track the data, and one test of how the model responds to change |
| **1. Sandbox** | New edits (add lane, roundabout, signal off/retime, turn ban, one-way, bus lane) through netconvert patches; plan list; difference map; scorecard using the fast evaluator | The planner can play, SUMO-video style |
| **2. Scorecard in full** | `plan_kpis.py`: cost (NPV, ranges), accessibility (car + walk, equity), traffic (delay, unserved, reliability); annualising; SUMO check of a chosen plan | Each plan gets defensible numbers |
| **3. Optimiser** | Study area, budget and mode settings; candidate generation; greedy then BO/NSGA-II; top plans verified in SUMO; Pareto chart that loads plans onto the map | "Optimise this corridor" button |
| **4. Phasing with RL** | Multi-year environment (budget, demand growth, works disruption); PPO + GNN policy (Gymnasium / Stable-Baselines3, like the paper's code `MLSM-at-DTU/floods_transport_rl`) | A phased programme, not just a final plan |

## Sources

- Costa et al., *Climate Adaptation with Reinforcement Learning: Experiments with Flooding and Transportation in Copenhagen*, NeurIPS 2024 CCAI workshop, [arXiv:2409.18574](https://arxiv.org/abs/2409.18574)
- Long-horizon IAM version (2024–2100, 29 zones, PPO + GCN, BO comparison), [arXiv:2603.06278](https://arxiv.org/abs/2603.06278)
- Economic vs quality-of-life pathways, [arXiv:2511.03243](https://arxiv.org/abs/2511.03243)
- DTU MA'AT project page, [mlsm.man.dtu.dk/research-projects/maat](https://mlsm.man.dtu.dk/research-projects/maat/)
