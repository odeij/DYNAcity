# Reinforcement learning for flood adaptation and transport: reading and fit

> Scope narrowed on 8 November 2026: per the instructions, the work applies this paper's
> method to traffic plans on the corridor; floods remain an optional what-if. See
> [simulator-learning-layer.md](simulator-learning-layer.md).

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
| Impacts | trips cut off, delay p50/p90, diverted vehicles per hour, spillover; priced in US$ by `impact_costs.py` (step 1 below) | No intervention costs |
| Actions | `flood_planning.InterventionKind`: drain maintenance, retention, permeable surface, access protection, with caller-supplied costs and budgets | Effects must come from a real evaluator; none are assumed |
| Planner | `flood_planning.compare_plans` (budgeted portfolios, Pareto set) and `planning.plan` (enumerated backcast) | No sequential, learned policy yet (steps 3–4 below) |

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

### Step 1, done: what a flood costs (`impact_costs.py`)

`event_cost(model, disruption, Prices(), start_hour=8, duration_h=3)` returns road
damage, delay, spillover delay and cut-off trips in US dollars, hour by hour, with
every unit value and its source. `road_disruption` now records each road's flooded
share and mean depth, and `traffic_impact` reports `vehicle_hours` lost per hour of
disruption.

| Unit value (`Prices`) | Default | Source |
|---|---|---|
| Road construction cost | van Ginkel Table 1 range per type: motorways and trunks at the 25th percentile (no OSM `lit` tag, so "simple"), other types at the 50th; ±25% per lane from the type's default | van Ginkel et al. 2021, Tables 1 and 2 and Sect. 2.5 (read from the paper) |
| Damage share | Table 2 curves C3 (motorway, trunk) and C5 (other), low flow, over the flooded length at its mean depth | same; Costa et al. also use the low-flow curves |
| EU-28 2015 € → Lebanon US$ | ×0.262: Lebanon / EU-28 GDP per capita in 2015 (7,714 / 32,624 US$), then the 2015 euro rate (1.1095 US$ per €) | van Ginkel's own GDP-per-capita scaling; World Bank API data |
| Value of time | US$2.62 per person-hour | World Bank ICR3868 (2016), Annex 3, from the Beirut Peripherique Highway Feasibility Study (2011). Price year not stated. |
| Occupancy | 1.3 persons per vehicle | World Bank PAD2574 (2018), para. 7 |
| Cut-off trip | 80% of its usual trip cost | Costa et al. 2026, Sect. 3.4 |

Other published values: the 1996 World Bank roads appraisal used US$1.72 per car
passenger-hour. Anas et al. (2017) value time at the wage, 4,363 LBP/h in municipal
Beirut (US$2.91 at 1,500 LBP). Car occupancy is 1.7 in the 2002 PAD and the CDR BRT
studies. Any of these can be passed in `Prices`.

On the real road model (10,818 roads), the whole network's replacement value comes out
at about US$221 M. A 500 m-radius flood at the city centre, lasting 3 hours from 08:00,
costs about US$5k at 0.15 m (75 roads slowed, none closed) and about US$25k at 0.6 m
(123 roads closed), mostly road damage. Each hour's routing takes 0.4–0.7 s. These are
scales of magnitude, not loss estimates: the unit values come from other places and
years, and nothing is checked against observed Beirut flood losses.

### Step 2, done: zones (`zones.py`, `dynacity build-zones`)

Zones come from OpenStreetMap administrative boundaries, finest level first. First are
Beirut's sectors (admin_level 8, 60 inside the municipality). Then come the coarser
boundaries (admin_level 7) only where no sector lies: the neighbouring municipalities,
Burj Hammoud, Sinn El Fil, Haret Hreik and others. Finally a 500 m grid covers the
rest of the road model. Every road gets one zone by its midpoint, every building by its
centroid, and `adjacency` lists the zones that touch, for a graph policy.

On the current road model this gives 110 zones: 60 sectors, 9 municipalities and 41 grid
cells, with 325 touching pairs and a median of 6 neighbours. All 10,818 roads are placed,
and 3,345 of the 3,349 buildings (the other 4 lie beyond the roads). Sectors are small
(median 5.4 km of road), but the municipalities are not: Burj Hammoud holds 63 km. With
`--admin-level 8` alone, the suburbs fall to the grid instead, giving 147 zones of more
even size. Sectors are administrative, not traffic or drainage units; a flood and a
detour cross them freely.

The raw boundaries and the zone map live in the private data root
(`zones/osm-admin.json`, `zones/beirut-zones.json`), like the road model.

### Next

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
- Local road construction or repair costs per class, to replace the GDP-scaled
  European values, and a current Beirut value of time with a price year. The World
  Bank figure dates from a 2011 study and predates the 2019 crisis.
- Unit costs and lifetimes for the intervention kinds in Beirut.

## Branch

The RL work lives on `T5.1_RL`, branched from `T5.1_Tech`, where RL stays out of scope.
