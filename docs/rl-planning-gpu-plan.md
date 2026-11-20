# Corridor planning on the GPU rig: the plan

Written 20 November 2026 for rig5090 (RTX 5090, 32 GB), to be run there. It builds steps 3–5 of
[rl-planning-zheng-2023.md](rl-planning-zheng-2023.md#how-to-build-it) on top of `build-access`
and `score-access` (commit `9fa7a29`). Each phase ends with a gate: stop there, write up what came
out, and decide before starting the next one.

## What the GPU is for, and what it is not

**First, a fact about the problem.** At 500 m, with sites fixed, the paper's land-use objective
is a *maximum-coverage problem*:

- each home is either served or not for each service;
- a site given a use serves a fixed set of homes.

An integer program solves this exactly. With 135 sites × 5 uses there are at most 675 binary
choices, fewer after the size rules (which remove 125 site–use pairs) and the rail bed's two uses.
Work is not among them: it is not in the default score, so giving a site that use can never raise
the objective (see phase A1). The program also has one continuous variable per (service, home) pair
that a site could newly serve, possibly tens of thousands. scipy's HiGHS solver should still handle
it in seconds to minutes (measure it, and record the variable and constraint counts).

So on the static land-use problem, **no search and no RL can beat the exact optimum**. Running RL
there tests the pipeline, and the honest measure is its gap to the optimum. This is stricter than
the paper, which compared RL only with heuristics.

The GPU earns its keep in four places:

1. **Scoring plans in batches.** At a fixed threshold, "within reach" is a 0/1 matrix. A batch of
   plans is then one matrix product per service: plans × sites times sites × homes. That runs on
   tensor cores, at millions of plans per second (measured in A0).
2. **RL with thousands of parallel environments on the GPU.** No CPU sits in the training loop.
3. **Street measures change the walking network.** A new crossing, or the rail bed opened as a
   walkway, adds links, and walking distances change only through the new links' end points.
   A plan's new distances therefore come from small min-plus products over distances between end
   points, computed once. These products batch on the GPU and are exact. Joint plans (land uses
   plus links) are no longer a linear program, so search and RL can genuinely compete here.
4. **Robustness.** Each finalist plan is re-scored under thousands of perturbations of the
   proxies in one batch: residents, mapping gaps, the threshold.

SUMO stays on the CPU cores.

## Phase 0: setup on the rig

```sh
git fetch origin && git switch -c T5.1_RL-access-gpu origin/T5.1_RL-sumo-plans
python -m venv .venv && . .venv/bin/activate
# Blackwell (sm_120) needs a CUDA 12.8+ build of torch. Install it first, from the PyTorch index,
# so that no later install pulls a different torch build from PyPI.
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
python -m pip install -e '.[dev,access,sumo]'
python -m pip install pymoo botorch gpytorch
python -c "import torch; print(torch.__version__, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))"
nvidia-smi
```

On rig5090 (20 November 2026) the repo's `.venv` was made with uv and has no pip, so use
`uv pip install --python .venv/bin/python …` instead. The CUDA torch download ran at about 4 MB/s,
so the repo venv borrows torch 2.11.0+cu128 from the fleet's shared env (`~/env`) through one file,
`.venv/lib/python3.12/site-packages/zz-shared-env-torch.pth`, which puts `~/env`'s packages *after*
the venv's own: only what the venv lacks (torch, the nvidia libraries, triton) comes from there.
botorch and gpytorch were installed with `--no-deps` so that uv would not fetch a second torch.
Deleting the `.pth` file undoes it; a change to torch in `~/env` changes it here too.

- Add an `rl` extra to `pyproject.toml` (`torch`, `pymoo`, `botorch`, `gpytorch`). Note in a comment that the CUDA
  wheel comes from the PyTorch index and must be installed before the extra; installing
  `.[rl]` on its own may fetch a torch build without sm_120 support. After any install, re-run
  the `torch.cuda.get_device_capability` check above.
- Rebuild the access model at the current commit and keep its report next to it. Do two builds,
  with and without the rail bed, because the rail bed's width is not mapped and its parks count in
  full:

```sh
O=~/dynacity-data/traffic/osm-beirut-context-sources
A=~/private/dynacity-data/access/gpu-20261120
for RB in railbed no-railbed; do
  dynacity build-access --corridor datasets/corridor/masar-akhdar.geojson \
    --network $O/mobility.json --places $O/places.json --places $O/landscape.json \
    --facilities ~/private/dynacity-data/facilities/beirut-facilities.json \
    --beirut001 datasets/beirut001/beirut001-pack.npz --bbed datasets/buildings/bbed-2024.geojson \
    --add-parking $([ $RB = railbed ] && echo --add-railbed) \
    --sites datasets/corridor/committed-sites.geojson \
    --output $A/corridor-access-$RB.npz --report $A/corridor-access-$RB.report.json
done
```

- Check the data roots before the first build: the OSM extracts are read from `~/dynacity-data`
  and the facilities from `~/private/dynacity-data`. Adjust `O` if the extracts live under
  `~/private` on the rig. Both roots must stay outside the repository.

- Long runs go in `tmux`. Log `nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 30`
  next to each run. When the work is started from the controller rather than on rig5090 itself,
  dispatch and collect it with the `rig` CLI (the fleet setup), so the shared `~/env` and the
  results stay in sync.

**Gate 0.** torch sees the 5090. Both models build. Their baselines, read from the two report
files, match the results in `rl-planning-zheng-2023.md`: service score 0.52 at 500 m, and medical
care 14%. (The rail bed adds only sites, so the baselines must be the same in both builds.)

## Phase A0: the GPU scorer (`access_gpu.py`)

- **`BatchScorer(model, device, threshold=500)`** builds:
  - `R_base` (services × homes), 0/1 reach of the mapped places;
  - `R_site` (sites × homes), 0/1 reach of each candidate site;
  - resident weights;
  - the allowed-use mask (sites × uses).
- **A plan** is an `int8` row over the sites: 0 means unchanged, k means the k-th use. A batch is
  (B, sites). Only the scored services and parks are uses in the search; work stays out of the
  allowed-use mask while it is not scored.
- **Reach for service k:** `R_base[k] | (onehot_k(plans) @ R_site > 0)`.
  - The product runs in bf16 with fp32 accumulation. The counts are integers of at most N ≤ 30,
    well inside bf16's exact range (up to 256), so it stays exact.
  - The objective is then exactly `AccessModel.objective`: α × the mean over the scored services,
    plus the parks share, all resident-weighted. The resident-weighted sums over homes are taken
    in float64: in float32, summing thousands of weights can already err by about 1e-6 on an
    objective of 1–3.
- **The decay-based access index and the p80 walk** need distances, not reach. They are computed
  only for finalists, by gathering each chosen site's row, never inside the search loop.
- **`dynacity bench-access-gpu --model --output`** records plans per second for B = 1k, 16k and
  256k, on CPU and GPU.
- **Tests run without a GPU** (`device="cpu"`) and are skipped when torch is missing.

**Gate A0.**
- **Correctness:** the GPU scorer matches `AccessModel.objective` on 10,000 random valid plans,
  with a maximum absolute difference under 1e-6, on both CPU and GPU. This holds only with the
  float64 sums above. If they are dropped for speed, the tolerance becomes 1e-5 and the plan
  rankings must still agree.
- **Speed:** throughput is written down. If it is under 10⁶ plans a second, find out why before
  going on.

## Phase A1: the exact optimum and the baselines (`access_search.py`)

**The integer program (scipy `milp`, HiGHS).**
- **Variables:**
  - `x[s,u]`, binary, only for allowed (site, use) pairs whose use is scored or parks (work is
    left out: its coefficient in the objective is zero);
  - `y[k,h]` in [0, 1], only for homes not already served for k that some site could serve.
- **Maximise:** Σ_h w_h (α/|scored| Σ_{k∈scored} y[k,h] + y[parks,h]).
- **Subject to:**
  - y[k,h] ≤ Σ_s x[s,k] · R_site[s,h];
  - Σ_u x[s,u] ≤ 1 (one use per site);
  - Σ x ≤ N.
- **Runs:**
  - budgets N = 1…30;
  - α ∈ {0.5, 1, 2} (the paper's values) and 4;
  - both site sets.
- **The exact Pareto front** of service score against park score, for each N, by the ε-constraint
  method: maximise one score subject to a floor on the other.

**Baselines on the same scorer, all GPU-batched:**
- **random:** 10⁶ plans per (N, α); report the best and the distribution;
- **greedy:** the existing one, re-run on the GPU scorer;
- **NSGA-II** (`pymoo`): population 1,024–4,096, each generation scored in one batch.

**`dynacity search-access --model --method milp|greedy|random|nsga2 --budget 1 30 --alpha … --output`.**

**Gate A1.** A table for each N, α and method, giving:
- the objective and the gap to the optimum;
- residents within 500 m of each service;
- the uses chosen and the sites used.

Greedy should land close to the optimum, because coverage is submodular. The guarantee is weaker
than the familiar 63%, though: that bound (1 − 1/e) holds when the only limit is a count of N
choices. Here each site also takes at most one use, so the feasible plans form a matroid, not a
plain count limit, and greedy is guaranteed only half the optimum. In practice it is usually much
closer, and the integer program gives the actual gap. If greedy is already within about 1%
everywhere, say so plainly: the static problem then needs no RL. That is a result, not a
failure.

**Gate A1 result (rig5090, 20 November 2026).** The integer program proved the optimum in every
case (budgets 1–30, α 0.5–4, both site sets), each in under 1.6 s. Greedy is within 1% in 91% of
cases (median 0.56% with the rail bed, 0.37% without), but not everywhere: at α = 4 it drifts past
1%, from N = 27 with the rail bed and from N = 14 without (worst 1.9%). NSGA-II (population 1,024,
200 generations) is closer (median 0.24% and 0.07%, worst 1.13%); random search is 8–13% short. The static problem is
therefore solved exactly in seconds, and RL there can only be a check of the pipeline. Details:
[work log](beirut-simulation-work-log.md).

## Phase A2: RL on the static problem (`access_rl.py`)

The paper's agent, but now checked against a known optimum.

- **Environment:** E copies on the GPU, with E = 1,024–4,096.
  - **State:** the current plan, steps left, α and N.
  - **Action:** one (site, use) from the masked set of at most 675 (5 uses; work is left out).
  - **Episode:** N steps. The reward is the gain in objective at the end, as in the paper, with an
    option for the per-step gain.
  - **Pre-assigned sites:** an option to start with some sites fixed. This is the paper's
    human-sketch mode: a planner fixes some sites and the agent completes the plan.
- **Graph.** Nodes:
  - candidate sites: area, allowed uses, current use;
  - home groups: Beirut 001 homes grouped by block into about 200–400 nodes, with residents and,
    per service, served at 500 m and at 15 min;
  - mapped places: service type.

  Edges join nodes within 1,170 m of each other along the network, with the distance as a feature.
- **Policy:**
  - three or four message-passing layers, in plain torch (`index_add_`/scatter, no
    torch_geometric);
  - one logit per (site, use), plus a value head;
  - α and N go in as global inputs, so one policy covers every weighting, as in step 4 of the
    Zheng doc.
- **Training:** PPO with bf16 autocast. α is sampled log-uniform in [0.25, 4] and N uniform in
  5–20. Each run is capped at 4 hours; if it has not converged by then, report it as not
  converged.
- **Evaluation:**
  - five training seeds;
  - held-out α (0.7, 1.5, 3) and budgets;
  - the gap to the integer-program optimum, and residents within 500 m of each service;
  - inference time per plan, which the paper puts at under 1 s.
- **`dynacity train-access-rl --model --output --envs --seed …`** and
  **`dynacity eval-access --model --plans … --output`**.

**Gate A2.** Report the gap to the optimum. RL is not expected to beat it. The phase proves the
environment, the policy and the training before phase B, where no optimum is known.

## Phase B: street measures and the walking network (`walk_links.py`)

**Candidate links.** The candidates come from a reviewed file, never guessed:
`datasets/corridor/candidate-links.geojson`. Each link has its geometry, length, kind, source and
the person who reviewed it.
- **The rail bed as a walkway:** the 53 pieces joined along the line, tied to their park or play
  use.
- **Crossings:** a script proposes places on trunk roads and the river where mapped walkways on
  both sides are close in a straight line but far apart along the network (say a ratio above 3).
  A person keeps or drops each one.
- **Car-free streets:** these change nothing for walking unless they open a barrier, so they enter
  only when that barrier is mapped.

**Exact distances with links, batched.**
- Collect E, the end points of every candidate link (a few hundred).
- Compute these distance blocks once, on the CPU with scipy multi-source Dijkstra, then move them
  to the GPU:
  - D(home, end);
  - D(end, target), where targets are places and sites;
  - D(end, end);
  - D(home, target).
- **End points must be network nodes.** A crossing or rail-bed piece that meets a way partway
  along it splits that way at the meeting point first: two edges with the original lengths in
  proportion, plus the new node. The splits for all candidate links are applied once, before the
  distance blocks are computed, so that the base network, the blocks and the full-Dijkstra test
  all use the same split graph. Homes, places and sites stay snapped to the original nodes (split
  nodes only carry link end points); otherwise a split node could become a home's nearest node and
  change base distances.
- **For a plan with links L:**
  - shortest paths between the active end points, by Floyd–Warshall over at most 2·|L| nodes;
  - then d'(h, t) = min(d(h, t), min over a, b of D(h, a) + D'(a, b) + D(b, t)).

  This is exact, because walks can change only through the new links. It is a min-plus product
  that batches across plans. Its cost grows with the number of active end points, not with the
  14,759-node network.
- **Test:** compare with a full Dijkstra on the split network with the links added, on 1,000
  random link sets. Include links whose end points fall partway along a way, and sets in which
  walks use two or more new links.

**Status, 20 November 2026 (rig5090).** Built: `walk_links.py`, `dynacity propose-links` and
`dynacity build-links`.
- **Proposals** (`datasets/corridor/candidate-links.proposed.geojson`, unreviewed): 51 crossings
  (19 trunk, 6 trunk link, 16 motorway, 7 motorway link, 3 river), ranked by the walk they save,
  up to 2 km; and the rail bed as 79 walkway pieces (5.0 km), cut where the line crosses a
  walkable way (the access points) and at the rail-bed site boundaries, each tied to its site.
  Cut points on no way stay free, joined only through the walkway.
- **Exactness:** on the rail-bed model with all 130 proposals (186 end points, 30 split ways),
  1,000 random sets of up to 12 links (910 of them shorten some walk) match a full Dijkstra to
  0.24 mm (float32 storage); the batched GPU version matches to 0.43 mm. Two rules had to match
  `build_access` exactly: walks over 4 km (`LIMIT_M`) are cut on the network part, before the home
  and site snaps are added; and a repeated graph edge keeps its shorter length (see the work log
  for the bug this exposed).
- **Next:** a person reviews the proposals into `datasets/corridor/candidate-links.geojson`
  (`reviewed_by` on each kept link, `rejected: true` on the rest). Until then `build-links`
  refuses them, except with `--allow-unreviewed` for testing. The joint search follows.

**Joint search.** A plan is land uses plus links, under a budget on the count of each.
- greedy and NSGA-II;
- Bayesian optimisation on the same scorer, set up as in Costa et al. (arXiv 2603.06278, §4.2),
  the study behind the repo rule: botorch and gpytorch, a Gaussian process with a Matérn 5/2
  kernel and automatic relevance determination, batch log expected improvement (qLogEI), 1,000
  random feasible plans to start, and stopping when the best objective has not improved over 100
  iterations. That paper does not say how its discrete actions were encoded, so record ours: one
  binary input per allowed (site, use) pair and per candidate link, with the acquisition optimised
  over feasible plans only (one use per site, the budgets). A Gaussian process over several hundred
  binary inputs is hard; Costa et al. ran theirs only on reduced problems and called the comparison
  methodological, not a benchmark. So run it on the full problem and also on a reduced one (the
  top sites and links by single-step gain), and report the evaluations each method used next to
  its result;
- RL: the A2 policy plus link actions, the paper's second stage;
- a reference: the integer program over land uses with the links fixed, alternated with link
  search.

**Gate B.** Does RL beat Bayesian optimisation, NSGA-II and greedy on the joint problem, for
held-out weights, across five seeds? This is the first place where the repository's rule (RL must
beat Bayesian optimisation and a genetic algorithm on the same evaluator) is a real test. Losing to
either one is reported as such.

## Phase C: plans staged over the years (later; it needs inputs)

- **Site availability by year** comes from the forecast engine: stalled and vacant buildings
  becoming free, with probabilities.
- **The budget** is a count of sites per year until unit costs exist.
- **Uncertainty:** the plan is made without knowing which sites will free up, which suits RL.
- **Baselines:**
  - re-solving the integer program each year with the sites then free;
  - a fixed schedule.
- **Do not start before** ownership and costs exist. It is listed here so that the A and B code
  does not block it: plan state, masks, and a budget per step.

## Phase D: robustness of the finalists (GPU Monte Carlo)

**A change it needs.** `build-access` must also save per-place distances (places × homes, about a
million values), as schema `dynacity-access/2`. Today it keeps only the nearest place for each
service. The new schema must keep the site × home distances too: varying the threshold between 400
and 600 m means recomputing reach from distances, for places and sites alike, not reusing the 0/1
matrices built at 500 m. `AccessModel` must still read `dynacity-access/1` files.

**For each finalist from A1, A2 and B, 10,000 draws in one batch:**
- residents per building × a log-normal factor (σ = 0.3);
- each mapped place kept with probability 0.9 (mapping gaps);
- the threshold between 400 and 600 m.

**Report:**
- how often each finalist ranks first;
- the median and 5–95% range of its objective;
- the median and 5–95% range of medical-care access.

A plan that wins only in the central case is reported as fragile.

## Phase E: traffic check of the finalists (CPU)

New land uses add no car trips yet (the gap stated in the Zheng doc), so only finalists with links
that touch roads go to SUMO, for example signalised crossings and closures. Each runs through
`dynacity run-plan` with a paired baseline over seeds, with one worker per core.

## Outputs and records

- All outputs are private, under `~/private/dynacity-data/access/gpu-<date>/`:
  - `scorer-bench.json`
  - `milp-*.json`
  - `baselines-*.json`
  - `rl/<run>/` (config, checkpoints, curves)
  - `eval-*.json`
  - `robustness-*.json`
  - `summary.md`
- Each run records the commit, the model file's hash, the seeds, the torch and CUDA versions, and
  the wall time.
- Each phase adds an entry to [beirut-simulation-work-log.md](beirut-simulation-work-log.md) and
  updates *Progress* in [rl-planning-zheng-2023.md](rl-planning-zheng-2023.md#progress).
- New code follows the repo's habits: CPU-only tests in `tests/`, `torch` imported inside the
  functions that need it, and RL work only on `T5.1_RL…` branches.

## What not to claim

- Results compare plans with each other on proxies: residents from floor area, mapped places
  whose operation is not verified. They are not measured outcomes.
- Every use is allowed on every site until ownership and allowed uses arrive.
- Parks on the rail bed are optimistic. Always show the no-rail-bed run beside them.
- New uses add no car trips, and SUMO is not calibrated.

## Inputs still missing

Each is needed from the phase shown:

| Input | Needed from |
|---|---|
| Review of the candidate links | B |
| Width of each rail-bed piece | B |
| Ownership and allowed uses per site | C, and for any plan presented as feasible |
| Unit costs | C |
| Trip rates per new use | E |
