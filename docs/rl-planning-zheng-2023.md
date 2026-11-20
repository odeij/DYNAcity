# RL for spatial planning (Zheng et al. 2023) and how to build it here

Read on 20 November 2026 from the publisher's PDF. Numbers below are the paper's own.

**Zheng, Lin, Zhao, Wu, Jin & Li, "Spatial planning of urban communities via deep
reinforcement learning", *Nature Computational Science* 3, 748–762 (2023),
DOI 10.1038/s43588-023-00503-5.** Tsinghua University (FIB lab and the Department of
Urban Planning). Code: `tsinghua-fib-lab/DRL-urban-planning`, MIT licence, with the
community data.

## What the paper does

An RL agent lays out land use and roads for a community of a few km², aiming at the
15-minute city.

- **Two stages.** First the agent places facilities one by one (hospital, school,
  clinic, recreation, homes, park, office, business, then open space, in that fixed
  order). Then it turns plot boundaries into roads.
- **The map is a graph.** Plots are polygons, road segments and plot boundaries are
  lines, junctions are points. Two items are joined if they touch. The graph is built
  from vectors, not a raster, so irregular real blocks need no grid.
- **The agent only chooses where.** "What" is fixed by the order of land-use types and
  "how" by rules that cut the new plot's shape. A mask removes invalid choices. In the
  land-use stage the agent picks a plot–junction edge; in the road stage it picks a
  boundary and builds it. Without this, a 2 km × 2 km plan would have about 4,000
  choices at each of about 100 steps.
- **Networks.** One GNN encodes the graph. An edge-ranking MLP picks land-use
  locations, a node-ranking MLP picks roads, and a value head scores the whole graph.
  Trained with PPO.
- **Reward, only at the end of each stage.**
  - Service: the share of the five basic services (education, medical care, shopping,
    work, entertainment) within 500 m of each home, as straight-line distance.
  - Ecology: green-space coverage.
  - Traffic: road density and connectivity (one connected network, no dead ends) and
    penalties for road spacing over 600 m or under 100 m.
  - A weight α on service sets the style: a "life-circle" or a "green" community.
- **Cost.** About a million plans and 48 hours of training on one GPU per community.

### Results

- Synthetic 2.4 km grid and two real Beijing communities of about 4 km² (Huilongguan,
  Dahongmen), taken from OSM with OSMnx and geopandas.
- **Against baselines.** It beats centralised and decentralised rules, a genetic
  algorithm (by 16–83% depending on the measure) and the same RL with an MLP in place
  of the GNN (by 3–47%).
- **Against eight professional planners.** No planner beat it on both service and
  ecology. On Dahongmen it scored 13.6% higher on service and 15.4% higher on ecology
  than the best planner.
- **Transfer.** Pretraining on a small grid and fine-tuning on a real community scored
  about 4.7% better than training from scratch.
- **With people.** A planner sketches centres and axes and the agent fills in the rest:
  as good or better on the measures, in under 1 s against 50–100 minutes. In a blind
  test, 100 designers mostly showed no clear preference between AI and human plans.

### Limits the paper states

- Plans are scored by static map measures, with no traffic or pedestrian simulation.
  The authors name a city simulator or digital twin as the next step, so plans can be
  judged on simulated pedestrian and vehicle results.
- Distances are straight lines. Plot shapes come from rules, and the land-use order is
  fixed.
- Only communities of about 4 km² were tested. City scale is left open.

### Reading it critically

- Its genetic-algorithm baseline was run on only part of the problem: it could not
  divide plots. Costa et al. found RL only 0.6–3.1% ahead of Bayesian optimisation.
  The repo rule stands: RL must beat Bayesian optimisation and a genetic algorithm on
  the same evaluator.
- A million plans is affordable only because each reward costs microseconds. That is
  the main difference from our setting.

## Scope decided on 20 November 2026

- **The green corridor only**, not the city. Two boundaries exist:
  - the 250 m buffer around the rail line that `--corridor` uses: 5.0 km of line, 2.13 km²;
  - the VR corridor boundary: that buffer plus the south-west capture block, 2.66 km²
    (`vr_corridor/boundary/`). The buffer lies wholly inside it; the extra 0.54 km² is
    the block chosen for the 3D captures.
- **Every kind of measure, not only mobility.** Land use, services, green and public
  space, walking and cycling, streets and car traffic. Water stays an optional what-if.
- **Key buildings are the paper's 15-minute services**: the buildings residents must
  reach within 15 minutes. These are education, medical care, shopping, work and
  entertainment, plus parks for the green measure. The key-buildings layer
  (`build-facilities`, 20 November 2026) gives education, medical care and government
  offices from OSM and the education ministry's 2017 school list. Shopping,
  entertainment, offices and parks come from the saved OSM extracts.

### Boundary: decided on 20 November 2026

- **Plan inside the 250 m buffer.** Every existing tool already uses it: `--corridor`,
  the SUMO corridor network and the counts below. It follows the route itself.
- **Measure access over a wider ring.** Count homes inside the buffer, but count services
  out to a 15-minute walk beyond it. Inside the buffer there are 4 education facilities;
  within 500 m of it there are 34, and within 1.2 km, 76. If only services inside the
  boundary counted, homes at the edge would look unserved. The agent would then place
  schools beside ones residents already walk to.
- **Keep the VR boundary for the 3D model.** If the south-west block should be planned
  too, it can be added later: the VR boundary contains the buffer, so only the inventory
  grows.

### The 15-minute threshold

The paper counts a service as reachable within 500 m in a straight line. A real 15-minute
walk is about 1.2 km (at 1.3 m/s, the speed the walkers here use). Use walking-network
distance, keep the threshold as a setting, and report both 500 m (to compare with the
paper) and 15 minutes. Check that the measure does not saturate. If nearly every home
already reaches a school within 1.2 km, plans cannot differ on it. A score that falls
off smoothly with distance, as UNA uses, avoids that.

Widening the scope makes the paper fit better. Both of its stages now apply: what goes on
each site, and what happens to each street. Most of the measures that score a plan are as
cheap as the paper's. Only car traffic needs SUMO's minutes.

## What the corridor holds today

Counted inside the 250 m buffer on 20 November 2026 from the private data on rig5090.

| Layer | In the corridor | Notes |
|---|---|---|
| Roads (SUMO) | 1,426 of 10,818 | whole roads touching the buffer's box |
| Beirut 001 buildings | 3,773 | in the buffer's box; heights and outlines |
| BBED buildings | 176 on 136 parcels | BBED surveys only part of the corridor |
| ... stable | 126 | |
| ... empty or parking | 28 (about 5,900 m²) | candidate sites |
| ... stalled or cancelled | 6 (about 3,300 m²) | candidate sites |
| ... vacant or evicted | 6 | candidate sites |
| ... under construction / renovated | 6 / 4 | |
| Mapped facilities | 20 | 4 schools, 1 hospital, 1 clinic, 6 pharmacies, 3 government offices, 3 community centres, 1 courthouse, 1 fire station (`facilities/beirut-facilities.json`: OSM, and education-ministry schools from 2017) |
| Trees measured from the drone data | 3,650 | `vr_corridor/variantD/trees_measured.json` |
| Walking network and modelled pedestrian flows | yes | UNA ([pedestrian-flow-model.md](pedestrian-flow-model.md)) |
| Projects on the line | Badawi linear park (about 670 m), Mar Mikhael station green space (10,000 of 62,000 m²) and restoration, Beirut River 2.0 proposals | [corridor-data-sources.md](corridor-data-sources.md#the-corridor-project) |

BBED covers 176 of the corridor's 3,773 buildings, so most buildings have no recorded state.
The government information has to fill that gap: who owns a site, what it may be used for,
and whether it can change.

The facilities layer covers two of the paper's five services: education and medical
care. Shopping, work and entertainment need shops, offices and leisure places. The UNA
inputs have these from OSM, but on the Windows PC, and jobs there are a guessed proxy.
The key-building files should cover all five, plus parks.

## How the paper maps onto the corridor

| Paper | Corridor |
|---|---|
| Stage 1: place facilities on vacant land | What goes on each candidate site: park, plaza, school, clinic, market, community centre, housing, or parking kept or removed |
| Cuts new plots with fixed rules | Not needed: sites are real parcels and lots (BBED parcels, mapped parking, the rail bed in short pieces, the station land) |
| Stage 2: turn boundaries into roads | Street measures: car-free, cycle lane, lane given to pavement, new crossing, street trees, permeable paving |
| Contiguity graph of plots, lines and junctions | The same graph built from corridor sites, street pieces and junctions; the rail bed is a chain of linear sites |
| Planning needs (how many schools, how much park) | Targets from government standards or the team |
| Action mask from planning rules | Mask from ownership, allowed uses and heritage listing |
| α picks a style | Weights over services, green space, walking, traffic and cost, given to the policy as input |
| Human sketches centres and axes | The planner marks centres and axes in the viewer; the agent completes the plan |
| Five services within 15 minutes (education, medical care, shopping, work, entertainment) | The key buildings: the same five, plus parks |
| Score: static map measures | Static measures for most things, along the real walking network; SUMO for car traffic |

The action space is small next to the paper's: on the order of a hundred or a few hundred
sites, each with fewer than ten uses, plus street measures on 1,426 roads.

## Where key buildings and government information go

| Input | What it does in the plan |
|---|---|
| **Key buildings** (the paper's 15-minute services: education, medical care, shopping, work, entertainment; and parks) | The supply in the services and green measures. Existing ones are fixed in every plan. New ones are what the agent places on candidate sites. |
| **Ownership** (railway authority, municipal, private) | Decides which sites are candidates at all, and what they cost: public land has no purchase cost. |
| **Allowed uses** (zoning, building rules, heritage listing) | The action mask: which uses each site may take. |
| **Approved or funded projects** | Fixed in every plan, including the do-nothing plan: Badawi park, Mar Mikhael station. |
| **Budgets, programmes, unit costs** | The budget limit and the cost measure. |
| **Service standards** (such as school catchments) | Targets, like the paper's planning needs. |

Every item keeps its source, date and licence, as the facilities layer already does.
Nothing government-supplied is mixed with OSM without saying which is which.

## How each plan is scored, and how fast

| Domain | Measure | Built from | Time per plan |
|---|---|---|---|
| Services (15-minute) | share of homes within a short walk of each service type, along the walking network (the paper uses straight lines); the worst-served 20% shown apart | facilities, key buildings, Beirut 001 homes, UNA network | milliseconds, once walking distances are computed |
| Green and public space | green area per resident; homes within 300 m of a park; tree canopy | OSM landscape, measured trees, plan | milliseconds |
| Walking and cycling | continuity of the green route; pedestrian flow on changed streets | UNA network and flows | milliseconds for continuity; minutes to rerun UNA |
| Car traffic | vehicle-hours of delay, trips left stranded | `traffic_impact` (seconds), SUMO (minutes) | seconds to minutes |
| Water (optional) | permeable area; runoff on roads that flooded before | flood events, plan | milliseconds |
| Cost and feasibility | capital cost, purchase cost, buildings affected | government information, unit costs | milliseconds |

The first five measures are fast enough for the paper's approach as it stands: train
directly on them, with millions of trial plans on the GPU. Car traffic is the exception.
During search it uses the static estimate, and the shortlisted plans go through SUMO.

**A gap to state plainly:** a new school or market brings trips, but SUMO demand is not
linked to buildings yet. In the first version, changing a site's use does not change car
demand. Trip rates per use come later, with a stated source.

## How to build it

1. **The corridor site inventory.** One GeoJSON listing every candidate site and every
   fixed building. Each entry has: outline, current use, BBED state, owner, key-building
   flag, allowed uses, cost and source. This is where the key buildings and government
   information go. Define the fields now and fill them as information arrives.
2. **Fast scoring.** Walking distances from every home to every site, computed once on
   the walking network. Then services, green space, route continuity, cost and the
   constraint checks. Each must score a plan in milliseconds. **Services and parks are
   built: see [Progress](#progress).**
3. **Reference plans and simple search.**
   - The team's own ideas, and the UN-Habitat and Beirut River 2.0 proposals, scored the
     same way. This mirrors the paper's comparison with human planners.
   - Then greedy search and NSGA-II (`pymoo`) for the Pareto front.
4. **RL, following the paper.**
   - A GNN over the corridor graph, with one head for site uses and one for street
     measures, a value head, and masks from the inventory.
   - The objective weights go in as input, so one policy covers the whole Pareto front.
   - PPO, trained on the fast scores.
   - It is kept only if it beats step 3.
5. **Car traffic for the shortlisted plans.**
   - Every finalist runs through `traffic_impact`, then paired SUMO runs over several
     seeds. Only SUMO-checked traffic numbers are reported.
   - Making SUMO cheaper helps here: cache the baseline per hour and seed (if runs are
     deterministic), use libsumo, run several workers.
   - Check whether `traffic_impact` ranks plans the way SUMO does (about 50 plans). If it
     does not, train a learned stand-in on SUMO runs and use that during search.
   - Calibration still comes first for any traffic claim.
6. **Plans staged over years.** The building forecast says which sites are likely to free
   up (stalled and vacant buildings). Budgets come by year. This is where RL is most likely
   to beat a fixed schedule.
7. **In the viewer.** The planner marks centres and axes, the agent completes the
   plan, and the scorecard shows every domain. Plans are labelled "estimate" or "simulated".

New dependencies go in an `rl` extra: `torch`, `pymoo`, and `libsumo` for the traffic step.

## Progress

### GPU scorer, phases 0 and A0 of the GPU plan (20 November 2026)

`access_gpu.py` scores batches of plans as in [rl-planning-gpu-plan.md](rl-planning-gpu-plan.md#phase-a0-the-gpu-scorer-access_gpupy).
On rig5090 it matches `AccessModel.objective` to within 9e-16 on 10,000 random plans and scores
4.5–6.4 million plans a second on the GPU (about 50,000–73,000 on the CPU). Search uses are the four
scored services and parks; work is left out while it is not scored.

```sh
dynacity bench-access-gpu --model ~/private/dynacity-data/access/gpu-20261120/corridor-access-railbed.npz \
  --output ~/private/dynacity-data/access/gpu-20261120/scorer-bench-railbed.json
```

### `dynacity build-access`: services and parks (20 November 2026)

`access.py` builds steps 1 and 2 for services and parks.
- **Planning area:** homes and candidate sites inside the 250 m buffer.
- **Reach:** services count out to a 15-minute walk (1,170 m) beyond it.
- **Distances:** along OSM walkable ways.
- **Output:** a model file (`.npz`) and a report. The report has the baseline, every
  candidate site's gain for each use, and a greedy plan.
- **Scoring a plan** (`score-access`, or `AccessModel.score` in Python) takes about 12
  microseconds, fast enough for RL.

```sh
O=~/dynacity-data/traffic/osm-beirut-context-sources
dynacity build-access --corridor datasets/corridor/masar-akhdar.geojson \
  --network $O/mobility.json --places $O/places.json --places $O/landscape.json \
  --facilities ~/private/dynacity-data/facilities/beirut-facilities.json \
  --beirut001 datasets/beirut001/beirut001-pack.npz --bbed datasets/buildings/bbed-2024.geojson \
  --output ~/private/dynacity-data/access/corridor-access.npz
dynacity score-access --model ~/private/dynacity-data/access/corridor-access.npz \
  --plan plan.json --output score.json   # plan.json: {"uses": {"bbed/2802": "medical"}}
```

**First results, rig5090, 20 November 2026.**
- **Homes:** 1,925 model buildings, about 26,300 residents (proxy).
- **Candidate sites:** 40 from BBED.
- **Walking network:** 14,759 nodes. Build time is 2 s.

| Service | Places in reach | Residents within 500 m | Within 15 min | 80% of residents walk at most |
|---|---|---|---|---|
| Education | 59 | 62% | 98% | 706 m |
| Medical care | 28 | **14%** | 78% | **1,203 m** |
| Daily shopping | 239 | 66% | 97% | 659 m |
| Entertainment | 66 | 64% | 98% | 602 m |
| Work (not scored) | 25 | 35% | 92% | 794 m |
| Parks | 63 | 36% | 96% | 945 m |

- **Service score** (the paper's measure, four services): 0.52 at 500 m, 0.93 at 15
  minutes. At 15 minutes nearly every home reaches everything, so plans are compared at
  the paper's 500 m.
- **Medical care is the gap.** Hospitals and clinics are the least reachable service on
  the corridor. Part of this may be mapping: OSM records doctors' practices unevenly.
- **Greedy plan, α = 1:** parks on 7 of the first 10 sites and clinics on 3. Parks win
  because the paper weighs green space as much as all services together. With α = 2 the
  order changes little.
- **An example plan:** two clinics (`bbed/2802`, `bbed/1936`) and one park (`bbed/1304`).
  Residents within 500 m of medical care rise from 14% to 40%, and of a park from 36% to 49%.

**Choices made here:**
- Trunk roads count as walkable, unlike in the UNA inputs. Without them, 145 corridor
  homes sat on a piece of network cut off by Charles Helou and the river; with them, 20.
- Pharmacies are not medical care, and universities are not education, following the
  paper's hospitals, clinics and schools.
- Only daily shops count as shopping.

**Limits, all printed as warnings in the report:**
- **Sites are small.** 28 of 40 are under 200 m², and no minimum size per use is set yet
  (`--min-site-m2 USE=M2`). A school on 150 m² is not a real option. Each suggestion
  shows the site's size.
- **Allowed uses are unknown,** so every use is allowed until the government information
  gives ownership and rules.
- **Coverage.** The key-buildings layer stops short of the reach area to the east and
  north, so schools and clinics there are missing, not absent.
- **Work is not scored.** OSM maps few offices here.
- **Parks of any size count,** and so does a new park on a small lot.

### More sites, and site sizes from published sources (20 November 2026)

**What a web search found (20 November 2026):**

- **No Lebanese minimum plot size for schools or clinics is published online.**
  - The closest Lebanese figures are about classrooms. The emergency education standards
    written with the Ministry of Education ask for 1.25 m² per pupil in elementary and
    intermediate classes and 1.5 m² in secondary
    ([INEE, Lebanon, 2014](https://inee.org/sites/default/files/resources/INEE_MS_Contextualized_Lebanon_English_2014.pdf)).
    The research centre (CRDP) proposes classrooms of about 40 m².
  - A regional example, Qatar's 2017 rules for private schools, asks for 1,250 m² for a
    kindergarten, 2,750 m² for a primary school and 3,500 m² for a secondary school. It
    does not apply to Lebanon.
- **The Ministry of Public Health's primary care standards** ([Accreditation Canada, May
  2015](https://www.moph.gov.lb/en/Pages/6/755/accreditation-primary-health-care-centers))
  set no floor area. They only require that "the clinic's physical space meets applicable
  legislation".
- **Parks.** WHO Europe's "Urban green spaces: a brief for action" (2017) is widely cited
  for green space of 0.5–1 ha within 300 m of home. The PDF could not be read, so the exact
  wording is unchecked. Pocket parks have no official minimum; published ranges start
  from a few tens of m².
- **Ownership.**
  - The rail bed is railway land, which the Railway and Public Transport Authority
    counts as public property. Its director describes encroachment on more than 10
    million m² of it nationally, including parking lots
    ([The New Arab, Nov 2025](https://www.newarab.com/features/mar-mikhael-train-station-revival-sees-hope-and-doubt-collide)).
    The registered owner of each piece is not published.
  - The corridor strip is used today as "a makeshift parking lot, a storage space, and a
    neglected walkway" (Arcadis, on the Badawi linear park, 2025).
  - Most of Beirut's parking lots are private empty parcels, and some belong to
    institutions such as hospitals and universities
    ([Salam wa Kalam, 2022](https://www.salamwakalam.com/articles/611/parking-lots-in-beirut-a-pattern-of-singular-space/en)).
    No list by owner is published.
  - Mar Mikhael station: 10,000 m² of the 62,000 m² complex is being restored as a
    public space with green areas, due in early 2027. The Railways Authority maintains
    it, and the Directorate General of Antiquities runs its programme.

**Size rules from Beirut's own places (`--size-rules local`, the default).** Since no
standard was found, a new place must have at least the land that most places of its kind
in Beirut already have. The figures come from OSM outlines in the saved extracts:

| Use | Smallest site | Read from |
|---|---|---|
| Education | 868 m² | a quarter of Beirut's 70 mapped school grounds are smaller |
| Parks | 383 m² | a tenth of Beirut's 84 mapped parks are smaller |
| Entertainment | 253 m² | half of Beirut's 6 mapped playgrounds are smaller (few mapped) |
| Medical care, shopping, work | none | they take a floor of an ordinary building; clinics are mapped as points |

`--min-site-m2 USE=M2` replaces any of these once an official figure is known.

**New candidate sites:**
- `--add-parking`: the 41 mapped surface lots in the buffer, median about 1,000 m².
- `--add-railbed`: the corridor line in 53 pieces of about 100 m. These are limited to
  parks and play, as in the Badawi linear park design, and have no size because the
  bed's width is not mapped.
- `datasets/corridor/committed-sites.geojson`: the funded Mar Mikhael station public
  space, with its sources.

There are now 135 sites in all, and the size rules remove 125 site–use pairs.

**Results:**
- **The rail bed leads the greedy plan:** parks on four of its pieces first, then a park
  and clinics on parking lots and BBED lots.
- **Over 20 steps (α = 1):** 9 parks, 6 clinics, 3 play spaces, 1 school, 1 shop.
- **A ten-step plan** raises residents within 500 m of medical care from 14% to 54%, and
  of a park from 36% to 92%. The paper's service score rises from 0.52 to 0.62. The parks
  figure is optimistic: each rail-bed piece counts as a full park whatever its width.
- **The station park alone barely moves the score.** The corridor homes near it already
  have a park within 500 m. Homes outside the buffer are not counted.

**Still open:** the width and ownership of each rail-bed piece; per-lot ownership of the
parking lots (land registry); official size standards; and whether each piece of the
line is still open ground.

## What would be new

- **A planning agent for a real corridor** that scores services, green space, walking,
  traffic and cost together. Traffic is checked in a simulator, which is the paper's
  stated next step.
- **Real constraints**: ownership, allowed uses, heritage listing and funded projects. The
  paper mostly set planning rules aside.
- **One policy for every set of weights**, giving a Pareto front without retraining.
- **The first planning RL work on Beirut.**

## Risks

- **Inventory gaps.** BBED covers 176 of 3,773 corridor buildings. Plans can only be as
  good as the site inventory; unknown sites are left out, not guessed.
- **Proxies.** The homes, jobs and facility layers are proxies, and facility operation and
  capacity are not verified. Results compare plans with each other; they are not measured
  outcomes.
- **Fixed car demand.** New uses add no trips yet, and added lanes attract none.
- **Uncalibrated traffic.** SUMO numbers are reported as comparisons with the same demand,
  not as Beirut traffic.
- **SUMO's default signal programs** make signal measures look better than they are. Leave
  them out until real timings exist.
