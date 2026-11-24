# Paper: an exact optimum for RL-style community planning

Written 24 November 2026. An outline of a paper on the corridor planning work and the
experiments it still needs. Results so far: [rl-planning-gpu-plan.md](rl-planning-gpu-plan.md),
[rl-planning-zheng-2023.md](rl-planning-zheng-2023.md) and the
[work log](beirut-simulation-work-log.md) (20 November 2026 entries). Slides: "Corridor planning:
RL versus the exact optimum" (21 November 2026).

## The claim

Zheng et al. (2023) placed land uses for the 15-minute city with deep RL and compared it only with
heuristics and planners. Grounded in a real city, with fixed candidate sites and walks along the
network, the land-use problem is a maximal covering location problem (Church & ReVelle 1974), and
it is solved to a proven optimum in seconds. Walking links make it non-linear, but only a few link
groups change who reaches what, so the joint problem splits into a small number of integer
programs and is still solved exactly. RL is then measured by its gap to a known optimum, and it is
useful only where exact solving stops being practical.

Do not claim the integer program is new. The contribution is the benchmark, the exact treatment
of links, and the evidence on when RL is worth using.

**Working title:** "Do we need reinforcement learning for 15-minute city planning? Exact optima
on a Beirut corridor."

## Contributions

1. An exact benchmark for a problem recently solved with deep RL: integer-program optima for
   budgets 1–30 and four weightings, against which greedy, NSGA-II, random search and RL are
   measured.
2. Exact joint planning of land uses and walking links: link sets are grouped by the reach pattern
   they give at the threshold, and one integer program per pattern gives the joint optimum.
3. Walking distances along the network, with exact distances under added links (checked against
   a full Dijkstra to 0.24 mm), instead of straight lines.
4. A real infill case: 135 candidate sites from survey, OSM and rail land; 11 hand-reviewed
   crossings and the rail-bed walkway.
5. A rule for when RL earns its place, from how the number of reach patterns grows with the
   number of effective link groups.

## Outline

1. **Introduction.** The 15-minute city; RL for spatial planning (Zheng et al. 2023; Costa et al.
   2026 for street networks); the gap: no comparison with an exact optimum.
2. **Related work.** Facility location and maximal covering (Church & ReVelle 1974; location
   allocation); network design; RL and black-box search in planning (Zheng et al., Costa et al.,
   NSGA-II, Bayesian optimisation); accessibility measures.
3. **Study area and data.** Al Masar al Akhdar, the 250 m buffer around the old railway, 2.13 km²:
   1,925 Beirut 001 homes, about 26,300 residents from floor area; services from OSM and the MEHE
   school list, counted out to a 15-minute walk; 135 sites (40 BBED parcels, 41 parking lots, 53
   rail-bed pieces, the station land); size rules from Beirut's mapped places. Baseline at 500 m:
   medical care 14%, parks 36%, schools, shops and leisure 62–67%.
4. **Method.**
   - The objective, as in Zheng et al.: services weighted by α, plus parks, at 500 m.
   - The coverage integer program (HiGHS).
   - Exact distances with links: min-plus products over link end points.
   - Reach patterns: 12 link groups, 32 distinct patterns; one integer program per pattern.
   - The GPU scorer: 6 million plans a second, exact to 10⁻¹⁵.
   - Baselines on the same scorer: greedy, random, NSGA-II, Bayesian optimisation (as in Costa et
     al.), RL (E1).
5. **Results.**
   - Land uses alone: every case proven optimal in under 1.6 s; greedy median gap 0.56% (worst
     1.9% at α = 4 without the rail bed); NSGA-II median 0.23%; random 8–13%.
   - With links: the exact joint optimum in under 2 minutes; ten sites (7 parks, 3 clinics) and
     crossings #9 and #24: medical care 14% → 59%, parks 36% → 92%; 7 of 11 crossings change no
     walk within 500 m.
   - RL's gap to the optimum and its cost (E1).
   - Zheng et al.'s own communities (E2).
   - Scaling (E3) and robustness (E4).
6. **Discussion.** When RL is worth it: areas too large to solve exactly, many effective link
   groups, plans staged over years. Using the exact solver as a check on any learned policy.
   Planning reading: free plots sit along the line, not where the gaps are.
7. **Limitations.** Proxies (residents from floor area, mapped services, every use allowed until
   ownership is known, rail-bed width unmapped, so every result is also run without the rail bed);
   one corridor until E3; static scores, no demand response.
8. **Conclusion.**

## Experiments still to run

**E1. RL on the corridor (phase A2 of the GPU plan).** The paper's style of agent: graph network,
PPO, masked (site, use) actions, α and N as inputs. Report its gap to the optimum on held-out α
and budgets, over five seeds, with training time and inference time. Then the joint problem, where
its gap to the exact joint optimum is the result.

**E2. Zheng et al.'s own data.** Their code and community data are public
(`tsinghua-fib-lab/DRL-urban-planning`, MIT licence). Fix the plots (the partition their trained
agent produces, and the initial one), keep their uses, counts and straight-line 500 m reward, and
solve the assignment exactly. Score their RL plans with the same evaluator and report the gap.
This answers "your problem is easier than theirs". If their full problem (plot cutting and roads)
cannot be posed exactly, say so, and report the exact result for the part that can.

**E3. More areas and scaling.** At least two more Beirut areas, or the Beijing communities from
E2. Report sites, homes, effective link groups, reach patterns and solve time, and find where
exact solving becomes impractical.

**E4. Robustness of the finalists (phase D).** Perturb residents, mapping gaps and the threshold
in GPU batches; report how often each finalist stays optimal or within 1%.

**E5. Log the joint baselines.** `search-joint` (greedy, NSGA-II, Bayesian optimisation against
the exact joint optimum) ran with phase B, but its gaps are not yet in the work log. Rerun at a
fixed commit and record them.

## Venues

- *Environment and Planning B: Urban Analytics and City Science* or *Computers, Environment and
  Urban Systems*: the best fit.
- *Transactions in GIS* or *Geographical Analysis*: if the reach-pattern method leads.
- A Matters Arising in *Nature Computational Science*: only if E2 shows their own problem can be
  solved exactly.
- A workshop first (Tackling Climate Change with ML at NeurIPS or ICLR) to get the idea out early.

## Before submitting

- **Data.** Beirut 001 is private and BBED is under ODbL. Agree with the DynaCITY partners what can
  be published and what can be shared for reproducibility; aggregate results and code can go,
  per-building data cannot.
- **Authorship** within the AUB DynaCITY project.
- **Reproducibility.** Every run records its commit, model hash, versions and wall time
  (`~/private/dynacity-data/access/`); keep that for the final runs.
