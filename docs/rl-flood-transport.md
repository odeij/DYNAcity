# Reinforcement learning for flood adaptation and transport: reading and fit

Requested 3 November 2026: read the Copenhagen paper on reinforcement learning for
flooding, see how it relates to what the traffic and mobility work here already
shows, and apply it to traffic for a contribution. Mobility is more than cars, but
cars come first.

## The papers

- Costa, Petersen, Vandervoort, Drews, Morrissey, Pereira (DTU), *Climate Adaptation
  with Reinforcement Learning: Experiments with Flooding and Transportation in
  Copenhagen*, NeurIPS 2024 Tackling Climate Change with ML workshop,
  [arXiv:2409.18574](https://arxiv.org/abs/2409.18574). Code:
  [MLSM-at-DTU/floods_transport_rl](https://github.com/MLSM-at-DTU/floods_transport_rl) (MIT).
- The journal follow-up, *Artificial Intelligence for Climate Adaptation: Reinforcement
  Learning for Climate Change-Resilient Transport*,
  [arXiv:2603.06278](https://arxiv.org/abs/2603.06278) (March 2026).

Both build an integrated assessment model with four parts: rainfall projection, flood
model, transport model and impacts. An RL agent then chooses which adaptation measure
to deploy in which zone, year by year, to keep the total cost low.

| | Workshop (2024) | Follow-up (2026) |
|---|---|---|
| Area | 29 traffic zones, inner Copenhagen | same |
| Step, horizon | one year, 2023–2100 | one year, 2024–2100 |
| Rain | one daily-total event sampled per year from DMI Klimaatlas return-period amounts, stationary per 30-year slice, RCP8.5 | same, for RCP2.6/4.5/8.5; trained under one scenario, tested under another |
| Flood | SCALGO Live: uniform rain fills depressions on a 0.4 m DEM, no drainage network | same |
| Transport | zone-centroid graph at a constant 50 km/h, gravity demand, Dijkstra; cars only, no capacity or congestion | 84,000 survey trips on OSM drive, cycle and walk networks (2.7% car, 19.3% bicycle, 78% foot), shortest path per trip, no congestion |
| Depth to speed | cars: Pregnolato et al. 2017, closed above 300 mm | adds walking (closed at 1.5 m) and cycling (closed at 20 cm) |
| Actions | one: raise a zone's roads by 1 m | eight per zone (soakaway, bioretention planter, storage tank, four permeable surfaces, nothing), with lifetimes, decaying effect and masking |
| Reward | −(road damage + delay cost + action cost), all weight 1, undiscounted | adds trip cancellations (80% of trip cost) and maintenance; DKK |
| Agent | MaskablePPO, MLP (Stable-Baselines3) | PPO with a graph convolutional policy over zones |
| Baselines | random policy | no control, random, Bayesian optimisation on reduced problems (5–10 years, 10–29 zones) |
| Result | learned policy beats random by about half the impact cost | beats no control by 22% in cumulative reward and BO by 0.6–3.1% |

Road damage uses van Ginkel et al. (2021) depth-damage curves by road class; delays are
priced with the Danish value of time. Both papers say the results depend on simulation
assumptions and that compute limits the zones, measures and scenarios they can run.

The papers have some internal inconsistencies: the workshop's "47% reduction" is the
remaining fraction (the table gives about 53%), the follow-up's Table 2 total looks off
by a factor of ten, and it gives 10 seeds in one place and 3 in another. They don't
change the method.

## What this repository already has

| Paper component | Here | Gap |
|---|---|---|
| Rain projection | `flood_rainfall.py` holds a supplied hyetograph with provenance | No Beirut return-period or climate-projection amounts. `docs/flood/workstreams.md` forbids inventing them. |
| Flood model | `hazards.flood_level` fills low ground over the GLO-30 terrain; `flood_surface.py` + `flood_coupling.py` run ANUGA with two-way SWMM drainage | The physical solver needs a Beirut drainage network that does not exist yet. The quick flood is a fill level, which is roughly what SCALGO does. |
| Depth to car speed | `traffic.road_disruption` closes roads above 0.3 m and, as of this change, slows them below that on the Pregnolato curve (`flood_speed_kmh`) | none for cars |
| Transport | OSM road graph, Tari'ak hourly observed speeds, CDR peak counts, BPR congestion with successive-averages rerouting (`traffic_impact`), SUMO microsimulation (`traffic_sumo.py`) | none for cars; the flood side is richer than the paper's |
| Other modes | OSM bus and van routes, so a closure can name the lines it cuts | No walking or cycling network, no pedestrian or transit demand (`docs/mobility-data-readiness.md`) |
| Impacts | trips cut off, delay p50/p90, diverted vehicles per hour, spillover | No money: no road damage curves, no value of time, no intervention costs |
| Actions | `flood_planning.InterventionKind`: drain maintenance, retention, permeable surface, access protection, with caller-supplied costs and budgets | Effects must come from a real evaluator; none are assumed |
| Planner | `flood_planning.compare_plans` (budgeted portfolios, Pareto set) and `planning.plan` (enumerated backcast) | No sequential, learned policy. CLAUDE.md and the README put RL out of scope on this branch. |

## Where the contribution is

The paper's own limitations are where this work is already stronger, or could be:

1. **Congestion.** Neither paper loads detours onto road capacity. The workshop lists
   "increased congestion levels" as future work. Here detours fill roads by the BPR
   curve and can jam, so a flood's delay includes the queues it pushes onto side
   streets. Running the paper's policies with and without congestion would show how
   much a congestion-blind reward misprices each measure.
2. **Observed, hourly speeds.** The paper drives at posted limits. Tari'ak gives
   observed speeds per road and hour, so the same flood costs differently at 08:00 and
   03:00. That gives the reward a time-of-day weighting.
3. **Drainage as an action.** SCALGO has no drains. In Beirut, blocked drains are a
   main cause of street flooding. `drain_maintenance` is already an action kind, and
   ANUGA with SWMM can represent it once a network exists. That is a measure the
   Copenhagen setup cannot evaluate.
4. **A city that changes.** The Copenhagen city is static for 77 years. Here, building
   states change over time (`engine.ForecastEngine`), so exposure can change too. That
   coupling is new, though the forecast horizon is 2–6 years, not decades.
5. **A data-poor city.** Copenhagen has a national travel survey and a 0.4 m DEM.
   Beirut has crowd-sourced speeds, 2016–17 counts and 30 m terrain. Showing what the
   method can and cannot say under that scarcity, with the uncertainty kept visible, is
   a contribution in itself.
6. **Transit next.** The 2026 paper adds walking and cycling but no public transport,
   and lists transit accessibility as future work. Beirut's buses and vans run on the
   same flooded roads; the OSM routes are already joined to the road graph.

## Applying it, starting with cars

Done in this change: the Pregnolato depth-speed curve for cars
(`traffic.FLOOD_SPEED_CURVE`, `traffic.flood_speed_kmh`). A road's speed is capped at
the safe speed for the deepest water along it, below the existing 0.3 m closure.
Before this change, water shallower than 0.3 m had no effect at all.

Next steps, in order:

1. **A cost layer for the existing impact report.** Add road depth-damage by class
   (van Ginkel curves, the same ones the paper's code uses), delay cost at a stated
   value of time, and cut-off trips at a stated share of trip cost. Each number gets a
   source and an `assumed` status, like the costs in `flood_planning`. This alone gives
   a monetised flood-to-traffic impact for Beirut.
2. **Zones.** Choose the decision unit. Copenhagen uses its national transport zones,
   and Beirut has no published equivalent. Options are cadastral sectors, a regular
   grid, or the Al Masar al Akhdar corridor's segments (`corridor.py`).
3. **An environment.** A Gymnasium environment with an annual step. Each year it
   samples a rain event, makes a flood, runs `road_disruption` and `traffic_impact`,
   and returns the negative cost. The actions are the `flood_planning` kinds per zone,
   with masking for measures already in place. PPO needs on the order of 10⁵–10⁶
   steps, too many to route trips afresh each time. So the environment should read a table
   of impacts precomputed over discrete rain amounts and zone protection states, which
   is the surrogate route the follow-up paper itself recommends.
4. **Baselines first.** No control, random, and the existing non-learned planners
   (`flood_planning.compare_plans`, `planning.plan`). The spec in
   `docs/task-5-module-specification.md` already names `planning.plan` as the baseline
   a learned policy must beat. Only then add PPO.
5. **Other modes.** Walking needs an OSM footway network and the walking depth curve
   (closed at 1.5 m). Buses and vans need the cut-route count turned into a cost.

## Missing inputs (data requests)

- Beirut daily or sub-daily rainfall return-period amounts, now and under climate
  projections. Possible sources are the Lebanese Meteorological Department or
  published regional IDF curves.
- A drainage network: inlets, pipes, outfalls.
- Lebanese value of time and road construction costs per class, or an explicit
  decision to transfer European values with a stated adjustment.
- Unit costs and lifetimes for the intervention kinds in Beirut.

## Decision needed

RL is explicitly out of scope on this branch (CLAUDE.md, README,
`docs/implementation-rationale.md`). Steps 1 and 2 above stay within that scope. Steps 3
and 4 bring RL in and need either a scope change here or a separate branch.
