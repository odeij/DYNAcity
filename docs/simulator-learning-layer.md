# Plans and traffic on the corridor: the simulator, a learning layer and RL

## The instructions

From the voice notes of 3 and 8 November 2026:

- Read the Copenhagen paper on RL for flooding (Costa et al.; see
  [rl-flood-transport.md](rl-flood-transport.md)). Relate it to our insights on mobility,
  meaning all mobility, not only cars. Start with cars, and apply it to traffic for a
  contribution.
- The supervisors ask why use RL rather than just the simulator. What stakeholders need is a
  what-if: **change the plan, see what happens to traffic.** The supervisors do not want RL
  that tries actions directly in traffic, such as rerouting cars to make traffic better.
- Explore a learning mechanism that makes the traffic simulator better. First understand
  how SUMO works ([sumo-internals.md](sumo-internals.md)), then build on that.

From the paper, this work takes the method: an agent chooses plans over time, scored by a
simulation. It is applied to traffic plans. Floods, the paper's subject, are kept only as
an optional what-if (see [Floods](#floods-an-optional-what-if)).

## Short answer

Each part does a different job, and RL needs the other two.

| Part | Job | Question it answers |
|---|---|---|
| SUMO (`traffic_sumo.py`) | Runs one plan, car by car | If we carry out this plan, what happens to traffic? |
| Learning layer | (a) fits SUMO's uncertain inputs to Beirut's observed speeds and counts; (b) learns from SUMO runs to predict a plan's effect in milliseconds, retraining where it is unsure | Can we trust the answer? Can a stakeholder compare hundreds of plans, not three? |
| RL, or another optimiser | Searches over plans, including plans staged over years | Which plan should we choose, and in what order? |

The simulator alone is the right tool for one well-posed what-if, and it stays the
reference. It cannot answer "which plan" by itself, it is too slow to compare many plans,
and it is not yet calibrated.

## Where this sits in the proposal

The Task 5 module tables ([task-5-module-specification.md](task-5-module-specification.md))
already ask for these parts:

| Proposal module | Training input → output | Inference | Here |
|---|---|---|---|
| Scenario Forecasting (5.2) | existing simulation datasets → scenario model | urban scenario → future city evolution | the learning layer (b): a model trained on SUMO runs |
| Multi-objective Optimization (5.2) | simulation results → optimization model | KPIs + constraints → Pareto-optimal solutions | the search over plans, scored by (b) |
| Reinforcement Learning (5.1) | state, action, reward trajectories → learned policy | current city state → optimal intervention | RL chooses interventions (plans), not signal phases or routes |
| KPI Evaluation (5.2) | KPI definition → evaluation rules | simulation outputs → KPI scores | delay, vehicle-hours, trips completed and stranded, speeds per road; priced with `impact_costs.Prices` where useful |

## Why not the simulator alone

- **One plan per run, slowly.** On the corridor, an hour of traffic with its paired
  baseline costs about 13 minutes of machine time, or about 6 with three runs in parallel.
  That is roughly 250 plan-hours a day
  ([sumo-internals.md](sumo-internals.md#measured-on-the-corridor-8-november-2026)): enough
  to calibrate and to train a learned model on, but too slow for a search to call SUMO for
  every candidate.
- **Not yet calibrated.** On the corridor at 08:00, cars waiting to enter grow by about 900
  a minute, and the speed ratio slides from 1.1 to 0.89 over 20 minutes. The baseline
  never settles, and a stakeholder what-if needs a stable, checked baseline first.
- **It does not choose.** It answers "what if", not "what should we do".

## What the learning layer would be

1. **Calibration: making the simulator better.** Fit the inputs nobody measured until
   simulated speeds match Tari'ak road by road and hour by hour, and simulated flows match
   the available counts ([corridor-data-sources.md](corridor-data-sources.md#traffic-counts-and-trips)).
   Hold some roads and hours out to check. The inputs are entry rates at the corridor's
   edge, interior trip rates, trip lengths and driver parameters. This is learning
   *around* SUMO, with no change to its code. Bayesian optimisation is the standard
   baseline; a learned method counts as a contribution only if it beats it.
2. **A model learned from SUMO runs: the simulator's "brain".** Train a graph network on the
   road graph to predict, from a plan, each road's change in speed and flow and the total
   delay. Train it on SUMO runs of many generated plans. Where it is unsure, run SUMO on
   that plan and retrain (active learning), so it improves as the simulator runs. On
   held-out plans it must beat the existing static assignment (`traffic.traffic_impact`,
   with BPR congestion), or it does not ship.
3. **Not recommended now: learned behaviour inside SUMO.** That means changing and rebuilding
   SUMO's C++ code, and there are no Beirut vehicle trajectories to learn driver behaviour
   from. See [sumo-internals.md](sumo-internals.md#where-learning-can-plug-in).

## What the literature says

The papers below were found in a search on 8 November 2026. Details not marked as
checked come from the papers' abstracts and the search results.

- **Calibrating from speeds alone works.** Vishnoi, Shetty, Tsogsuren, Arora & Osorio
  (ACM SIGSPATIAL 2023, arXiv 2412.14089) calibrate SUMO demand for Salt Lake City from
  road speeds only. They use a fast physics-based stand-in for the simulator and beat SPSA
  by 84% on speeds. Guist et al. (SUMO Conference 2026) calibrate SUMO from probe speeds
  without an OD matrix. Beirut has the same kind of data: Tari'ak speeds and few counts.
- **Models learned from simulations predict closures well.**
  - Natterer et al. (TR-C 180, 2025, arXiv 2501.11057): a graph network trained on about
    10,000 MATSim runs of Paris with capacity cuts, R² 0.91.
  - Agriesti, Cantelmo, Pereira et al. (DTU, arXiv 2603.06763, 2026): a graph network that
    adapts to unseen closures, R² about 0.85.
  - Saneii et al. (CACAIE 38, 2023): pairs such a model with active learning, as proposed
    here.
- **RL against simpler search over plans.**
  - In Costa et al. (arXiv 2603.06278), RL beat Bayesian optimisation by only 0.6–3.1%, and
    the margin grew with more zones and more years.
  - A genetic algorithm searching over a graph-network stand-in came within 1.5% of the
    exact solution (Madadi & Correia, ESWA 2024).
  - Tree search beat earlier RL methods at adding links to a network (Darvariu et al.,
    Proc. R. Soc. A 2023).
  - RL did beat a genetic algorithm and human planners at laying out urban communities
    (Zheng et al., Nature Computational Science 2023).

**For the corridor:** for a one-off plan, search the learned model with Bayesian
optimisation or a genetic algorithm. It is simpler and about as good. For staged plans,
compare RL with those same methods, and keep RL only if it wins.

## Plans staged over years

A staged plan says which measure goes where, and in which year. A one-off plan is the case
where everything is built in year 1.

**What changes from year to year:**

| What | Source here | Limit |
|---|---|---|
| Measures in place | the plan; build time and cost per measure if given | no Lebanese unit costs found; costs are inputs, never defaults |
| Buildings, and so trips | `engine.ForecastEngine` building states at +2, +4 and +6 years, turned into trips per zone with the population grids | the forecast stops at 6 years, in 2-year steps; demand is not yet linked to buildings |
| Budget left | the plan | caller-supplied |

**One year, evaluated.** A typical weekday's peak traffic, with that year's measures and
that year's demand, is compared against no plan. The result is delay, vehicle-hours,
trips completed and stranded, and speeds per road, scaled to a year.

**The run is cumulative.** Each year starts from the previous year's state, with what is
built, the budget and the buildings carried over.

**Uncertainty.** The building forecast gives a range (its Monte Carlo draws), and SUMO
runs vary by seed. A plan is evaluated over several forecast draws and seeds, so its
result is a distribution (p05/p50/p95), like the forecast's KPI bands.

**Cheaper than it looks.** A year's result depends only on what is built and on that
year's demand. Identical combinations are computed once and reused. With a few measures,
that is enough for SUMO alone; with many measures and places, the combinations outgrow any
cache, and the learned model scores them instead.

**Two ways to search, judged on the same evaluator:**
- **A fixed schedule** (which measure, where, in which year), all decided at the start.
  Bayesian optimisation or a genetic algorithm searches over schedules.
- **An adaptive policy** that decides each year from the current state: what is built,
  the budget left, how the city has developed, and how traffic is doing. This is what RL
  learns. Without floods, the main thing to adapt to is how the city develops, which is
  slower and smoother than a flood. RL's edge over a fixed schedule may therefore be
  small. The comparison will show it.

Both are compared against doing nothing, random plans and the best one-off plan.

## What a plan can contain (to confirm)

| Plan measure | In the SUMO model today |
|---|---|
| Close a road, make it car-free | yes (closures; the viewer's **Make car-free**) |
| Add lanes to chosen roads | yes, not yet committed (`Simulation(lanes=…)`, `widen`) |
| A spare lane opened on demand | yes, not yet committed (`Simulation(spare=…)`) |
| One-way change, new link | no |
| Signal timing | survey programs can be loaded (`mobility_inputs.py`); no lever to change them |
| New development or demolition (changes demand) | no: demand is not linked to buildings or the forecast |
| Bus or van route change | no |
| Walking and cycling | no: SUMO can simulate pedestrians, but there is no walking network or demand ([mobility-data-readiness.md](mobility-data-readiness.md)) |

## Floods: an optional what-if

Floods are not part of the core. They stay available as one what-if: "what happens to
traffic if Karantina floods as it did on 22–23 December 2023?" The viewer already closes
and slows roads under a flood. The rainfall and flood-event data found on the web are kept
for reference ([corridor-data-sources.md](corridor-data-sources.md)). Nothing in the
traffic-plan work depends on them.

## Set aside

- [rl-mobility-review.md](rl-mobility-review.md) recommends RL signal control that reacts
  to traffic as it happens. That is RL acting directly in traffic, which the instructions
  rule out.
- The flood-adaptation setup of [rl-flood-transport.md](rl-flood-transport.md), with rain
  sampled each year and flood measures as the actions, now survives only as the optional
  what-if above.

## Decided

- **Scale: the Al Masar al Akhdar corridor** ([green-corridor.md](green-corridor.md); 1,426
  of 10,818 roads at the 250 m buffer), not citywide.
- **Traffic plans are the core.** Floods are an optional what-if.
- **Plans staged over several years must be evaluable**, not only one-off plans.
- **RL is the intended method, not a requirement.** If another search over plans does
  better on the corridor, use it: Bayesian optimisation, evolutionary search, or
  `planning.plan`'s enumeration. RL has to beat these to be kept, as the forecasting model
  has to beat its baselines.

## Questions for the supervisors

1. **What is a plan?** Which measures come first: street changes, new development, or
   transit?
2. **Who uses it, and what do they need to see?** For example a municipality, a ministry or a
   donor. A map of affected roads? Vehicle-hours lost? Trips stranded? A time-of-day
   profile?
3. **How accurate must it be?** Is "calibrated to Tari'ak speeds and the available counts,
   uncertainty shown" enough, or is validation against an observed change expected?
4. **Time scale.** For a typical day: one peak hour, both peaks, or a whole day? For staged
   plans, how many years? The building forecast covers 6.
5. **Data access.** The public data is gathered in
   [corridor-data-sources.md](corridor-data-sources.md): 2016–17 peak counts on Charles
   Helou and Pierre Gemayel, 2023 counts on Armenia Street, a 1998 district trip table and
   fine-grained population. Could the project obtain what is not public?
   - the AUB travel surveys (restricted);
   - Traffic Management Center counts and signal timings;
   - the 2018 TMS Consult or Khatib & Alami traffic models;
   - the corridor project's own drawings.

## Phases

1. **A batch runner.** Plan in, paired baseline and scenario runs over several seeds and
   hours, KPI bands out, with no viewer. **Done: `dynacity run-plan` (`plan_runs.py`).**
   - **What it does.** The plan's closures and added lanes apply from the start. Cars that
     would start on a closed road are left out of both runs. New trips whose origin the
     plan closes are counted apart from cars waiting at an open origin. The output has
     totals for both runs and their change (low/median/high across seeds), paired-trip
     metrics, and every road's cars and speed in both runs (the learned model's training
     target).
   - **First result (8 November 2026).** Armenia Street car-free on the corridor, 08:00,
     seeds 1 and 2, 10 minutes warm-up and 20 measured:
     - **Delay:** +24.6 vehicle-hours (+1.3%), 23.7–25.6 across seeds.
     - **Where it lands:** the detour loads a segment of Corniche Pierre Gemayel, whose
       speed falls from 58 to 6 km/h.
     - **Cars left out:** 65–76 that would have started on Armenia Street.
     - **Not trustworthy yet:** the runner warns that the baseline has not settled
       (about 1,040 more cars waiting to enter each minute).
     - **Cost:** 15 minutes of machine time for both seeds.
2. **Calibration.** Entry rates from the counts, then a search until the corridor settles
   and matches Tari'ak.
   - **First pass (8 November 2026): demand alone does not fit.** A 57-minute Bayesian
     search (153 runs) tuned the entry rates, the interior trip rate and the junction
     blocker time. Details are in
     [sumo-internals.md](sumo-internals.md#first-calibration-search-8-november-2026).
     - **Best setting:** entries ×0.2, interior ×0.25.
     - **Speeds:** a median ratio of 1.1 against Tari'ak, but still about 14 km/h out per road.
     - **Not settled:** the corridor keeps filling, by about 150 cars a minute.
     - **Charles Helou** carries about 2,000 cars an hour each way at any demand, against
       7,024 counted westbound.
     - **So** the limit is the network's junctions and signals, not demand.
   - **Second pass (8 November 2026): network fixes and driver settings, still no fit.**
     - **Where the queues form:** behind unsignalised junctions. The corridor has only 13
       signals, holding 2% of the halted cars.
     - **Two errors fixed:** road priority now follows the road class, and each entry
       draws 32 routes instead of 6.
     - **A 66-minute search** of demand and driver behaviour, on two seeds a setting, gets
       the corridor nearly settled only by admitting 10–20% of the boundary traffic. Speeds
       are then 30% too fast and the counted roads carry well under their counts.
     - **Main-road entries at count level** queue on the motorways themselves, even with side
       streets nearly empty.
   - **Third step (8 November 2026): network rebuilt the way SUMO recommends for OSM.**
     Junction clusters are joined and ramps built (`OSM_IMPORT_OPTIONS`). It changed little.
     - **Where the motorways jam:** in the right lane only, queued for one-lane off-ramps.
     - **Why:** the synthetic exit choice sends those ramps more cars than they carry.
   - **Next:** sample routes so road flows match the counts and the main roads' class flows
     (SUMO's `routeSampler.py` or Cadyts), instead of one nearness rule for exits. Details in
     [sumo-internals.md](sumo-internals.md#joined-junctions-and-ramps-8-november-2026).
3. **The learned model.** Trained on runner output, with active learning.
4. **Search over plans.** One-off plans first, then staged ones; RL against Bayesian
   optimisation and a genetic algorithm.
5. **Walking and cycling,** once a network and demand exist.
