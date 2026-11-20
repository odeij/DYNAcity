"""Joint plans: land uses plus walking links (docs/rl-planning-gpu-plan.md, phase B).

A joint plan gives candidate sites new uses (at most N) and opens reviewed walking links (at most K
link groups: each crossing is a group, the rail-bed walkway is one group). Links change who is within
reach of what, so a plan's score depends on both.

At a fixed threshold a link set matters only through the reach matrices it produces (homes within reach
of each mapped service, and of each candidate site). Many link sets produce the same matrices, so
`JointScorer` enumerates every link set once, keeps the distinct reach patterns, and scores a batch of
joint plans on the GPU by pattern. The exact optimum then needs one coverage integer program per
distinct pattern (`exact_joint`); greedy, NSGA-II and Bayesian optimisation run on the same scorer.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import numpy as np

from .access import AccessModel
from .access_gpu import BatchScorer
from .access_search import CoverageProgram, use_weights
from .walk_links import LinkModel

MAX_GROUPS = 14  # 2**14 link sets are enumerated once


def link_groups(links: LinkModel) -> tuple[list[list[int]], list[str]]:
    """Each crossing alone, and every rail-bed walkway piece together (reviewed as one decision)."""

    groups, names, walkway = [], [], []
    for i, (link_id, kind) in enumerate(zip(links.link_ids, links.link_kind)):
        if kind == "railbed_walkway":
            walkway.append(i)
        else:
            groups.append([i])
            names.append(link_id)
    if walkway:
        groups.append(walkway)
        names.append("railbed-walkway")
    return groups, names


class JointScorer:
    """`AccessModel.objective` for joint plans: (B, sites) int8 land uses and (B,) link-set ids, where bit
    g of a link-set id opens link group g."""

    def __init__(self, model: AccessModel, links: LinkModel, device: str = "cuda", threshold: float | None = None,
                 groups: list[list[int]] | None = None, names: list[str] | None = None, chunk: int = 32_768):
        import torch

        from .walk_links import BatchLinks

        self.model = model
        self.base = BatchScorer(model, device, threshold)
        b = self.base
        self.device, self.threshold, self.uses, self.scored = b.device, b.threshold, b.uses, b.scored
        self.mask, self.weight, self.n_sites, self.n_homes, self.chunk = b.mask, b.weight, b.n_sites, b.n_homes, chunk
        if groups is None:
            groups, names = link_groups(links)
        if len(groups) > MAX_GROUPS:
            raise ValueError(f"{len(groups)} link groups: at most {MAX_GROUPS} can be enumerated")
        self.groups, self.names, self.G = groups, list(names), len(groups)
        sets = np.arange(2 ** self.G)
        self.bits = ((sets[:, None] >> np.arange(self.G)) & 1).astype(bool)
        active = np.zeros((len(sets), len(links.link_ids)), dtype=bool)
        for g, members in enumerate(groups):
            active[np.ix_(self.bits[:, g], members)] = True
        batch = BatchLinks(links, model, device)
        rows = torch.as_tensor([model._service[u] for u in self.uses], device=self.device)
        S = len(model.services)
        keys: dict[str, int] = {}
        cfg = np.empty(len(sets), dtype=np.int64)
        R_base, R_site = [], []
        for start in range(0, len(sets), 64):
            reach = batch.distances(active[start:start + 64]) <= self.threshold  # (b, T, H)
            packed = np.packbits(reach.cpu().numpy(), axis=-1)
            for r in range(len(reach)):
                key = hashlib.sha1(packed[r].tobytes()).hexdigest()
                if key not in keys:
                    keys[key] = len(keys)
                    R_base.append(reach[r, rows])
                    R_site.append(reach[r, S:])
                cfg[start + r] = keys[key]
        self.R_base = torch.stack(R_base)  # (C, uses, homes) bool
        self.R_site = torch.stack(R_site).to(torch.bfloat16)  # (C, sites, homes)
        self.set_pattern = torch.as_tensor(cfg, device=self.device)
        self.pattern_of_set = cfg
        popcount = self.bits.sum(axis=1)
        self.n_patterns = len(keys)
        # the fewest links that reach each pattern, and one such link set
        self.pattern_links = np.full(self.n_patterns, 99)
        self.pattern_set = np.zeros(self.n_patterns, dtype=np.int64)
        for s in np.argsort(popcount, kind="stable"):
            c = cfg[s]
            if popcount[s] < self.pattern_links[c]:
                self.pattern_links[c], self.pattern_set[c] = popcount[s], s
        # groups that never change a reach pattern, whatever else is open
        self.inert = [g for g in range(self.G)
                      if all(cfg[s] == cfg[s | (1 << g)] for s in range(len(sets)) if not s >> g & 1)]

    def set_names(self, s: int) -> list[str]:
        return [self.names[g] for g in range(self.G) if s >> g & 1]

    def shares(self, plans, sets):
        """(B, uses) float64: resident-weighted share within the threshold of each search use."""

        import torch

        plans = torch.as_tensor(plans, device=self.device)
        sets = torch.as_tensor(sets, device=self.device, dtype=torch.long)
        if plans.dim() == 1:
            plans, sets = plans[None], sets.reshape(1)
        pattern = self.set_pattern[sets]
        out = torch.empty(len(plans), len(self.uses), dtype=torch.float64, device=self.device)
        for c in torch.unique(pattern).tolist():
            rows = torch.nonzero(pattern == c).squeeze(1)
            for start in range(0, len(rows), self.chunk):
                r = rows[start:start + self.chunk]
                block = plans[r]
                for i in range(len(self.uses)):
                    onehot = (block == i + 1).to(torch.bfloat16)
                    reached = self.R_base[c, i] | ((onehot @ self.R_site[c]) > 0)
                    out[r, i] = reached.to(torch.float64) @ self.weight
        return out

    def objective(self, plans, sets, alpha=1.0):
        import torch

        shares = self.shares(plans, sets)
        n = len(self.scored)
        alpha = torch.as_tensor(alpha, dtype=torch.float64, device=self.device)
        return alpha * shares[:, :n].mean(dim=1) + shares[:, n]

    def program(self, c: int) -> CoverageProgram:
        """The coverage integer program for land uses with reach pattern `c`."""

        view = SimpleNamespace(R_site=self.R_site[c].cpu(), R_base=self.R_base[c].cpu(), mask=self.mask.cpu(),
                               weight=self.weight.cpu(), uses=self.uses, n_sites=self.n_sites, n_homes=self.n_homes)
        return CoverageProgram.build(view)

    def describe(self, plan: np.ndarray, s: int, alpha: float) -> dict:
        import torch

        row = torch.as_tensor(np.asarray(plan, dtype=np.int8), device=self.device)[None]
        sets = torch.tensor([int(s)], device=self.device)
        shares = self.shares(row, sets)[0].cpu().numpy()
        uses = self.base.to_uses(plan)
        counts: dict[str, int] = {}
        for use in uses.values():
            counts[use] = counts.get(use, 0) + 1
        return {"objective": float(self.objective(row, sets, alpha)[0]), "sites_used": len(uses),
                "links": self.set_names(int(s)), "links_used": int(bin(int(s)).count("1")),
                "residents_within": {u: round(float(v), 4) for u, v in zip(self.uses, shares)},
                "uses_chosen": counts, "uses": uses}


# -- the exact optimum ----------------------------------------------------------------


def exact_joint(js: JointScorer, budgets: list[int], link_budgets: list[int], alphas: list[float],
                time_limit: float = 600.0, log=print) -> dict:
    """Best joint plan for every budget, link budget and alpha: the coverage integer program for each
    distinct reach pattern reachable within the link budget, then the best of them. Exact, because a
    link set acts only through its pattern."""

    needed = [c for c in range(js.n_patterns) if js.pattern_links[c] <= max(link_budgets)]
    programs = {c: js.program(c) for c in needed}
    out: dict[tuple, dict] = {}
    solved: dict[tuple, dict] = {}
    started = time.perf_counter()
    for N in budgets:
        for a in alphas:
            for c in needed:
                r = programs[c].solve(N, use_weights(js, a), time_limit=time_limit)
                value = float(js.objective(r["plan"][None], [int(js.pattern_set[c])], a)[0])
                solved[(N, a, c)] = {"plan": r["plan"], "objective": value, "optimal": r["optimal"],
                                     "seconds": r["seconds"]}
            for K in link_budgets:
                options = [c for c in needed if js.pattern_links[c] <= K]
                best = max(options, key=lambda c: solved[(N, a, c)]["objective"])
                out[(N, K, a)] = {"pattern": int(best), "set": int(js.pattern_set[best]), **solved[(N, a, best)],
                                  "all_optimal": all(solved[(N, a, c)]["optimal"] for c in options),
                                  "patterns_compared": len(options)}
        log(f"exact N={N}: {len(needed) * len(alphas)} integer programs, {time.perf_counter() - started:.0f} s so far")
    return out


# -- greedy ---------------------------------------------------------------------------


def greedy_joint(js: JointScorer, N: int, K: int, alpha: float) -> tuple[np.ndarray, int, int]:
    """Add the best single land use or link group each step, all candidates scored in one batch, until
    both budgets are spent or nothing gains. Returns the plan, the link set and the steps taken."""

    import torch

    pairs = torch.nonzero(js.mask)
    plan = torch.zeros(js.n_sites, dtype=torch.int8, device=js.device)
    s = 0
    current = float(js.objective(plan[None], [s], alpha)[0])
    steps = 0
    while True:
        cand_plans, cand_sets = [], []
        if int((plan > 0).sum()) < N:
            free = pairs[plan[pairs[:, 0]] == 0]
            if len(free):
                batch = plan.repeat(len(free), 1)
                batch[torch.arange(len(free)), free[:, 0]] = (free[:, 1] + 1).to(torch.int8)
                cand_plans.append(batch)
                cand_sets.append(torch.full((len(free),), s, device=js.device))
        if bin(s).count("1") < K:
            off = [g for g in range(js.G) if not s >> g & 1]
            if off:
                cand_plans.append(plan.repeat(len(off), 1))
                cand_sets.append(torch.tensor([s | (1 << g) for g in off], device=js.device))
        if not cand_plans:
            break
        plans, sets = torch.cat(cand_plans), torch.cat(cand_sets)
        values = js.objective(plans, sets, alpha)
        i = int(values.argmax())
        if float(values[i]) <= current + 1e-12:
            break
        plan, s, current = plans[i].clone(), int(sets[i]), float(values[i])
        steps += 1
    return plan.cpu().numpy(), s, steps


# -- NSGA-II ----------------------------------------------------------------------------


def nsga2_joint(js: JointScorer, N: int, K: int, population: int = 1024, generations: int = 200,
                seed: int = 0) -> dict:
    """NSGA-II (pymoo) on (service score, parks share) over land uses and link groups together.

    The genome is the site row followed by one 0/1 entry per link group. Sampling draws up to N sites
    and up to K groups; crossover is uniform; mutation changes, adds or drops a site's use, or opens,
    closes or swaps a link group; a repair keeps every use allowed and both budgets."""

    import torch
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.crossover import Crossover
    from pymoo.core.mutation import Mutation
    from pymoo.core.problem import Problem
    from pymoo.core.repair import Repair
    from pymoo.core.sampling import Sampling
    from pymoo.optimize import minimize

    rng = np.random.default_rng(seed)
    mask = js.mask.cpu().numpy()
    allowed = [np.flatnonzero(m) + 1 for m in mask]
    eligible = np.flatnonzero(mask.any(axis=1))
    S, G, n_scored = js.n_sites, js.G, len(js.scored)
    ok_full = np.concatenate([np.ones((S, 1), bool), mask], axis=1)
    powers = 1 << np.arange(G)

    def split(X):
        X = np.asarray(X, dtype=np.int64)
        return X[:, :S], X[:, S:]

    def repair_rows(X):
        X = np.asarray(X, dtype=np.int64).copy()
        sites, links = X[:, :S], X[:, S:]
        np.clip(sites, 0, len(js.uses), out=sites)
        np.clip(links, 0, 1, out=links)
        sites[~ok_full[np.arange(S), sites]] = 0
        for row_s, row_l in zip(sites, links):
            on = np.flatnonzero(row_s)
            if len(on) > N:
                row_s[rng.choice(on, len(on) - N, replace=False)] = 0
            on = np.flatnonzero(row_l)
            if len(on) > K:
                row_l[rng.choice(on, len(on) - K, replace=False)] = 0
        return X

    class Plans(Problem):
        def __init__(self):
            super().__init__(n_var=S + G, n_obj=2, xl=0, xu=np.r_[np.full(S, len(js.uses)), np.ones(G)], vtype=int)

        def _evaluate(self, X, out, *args, **kwargs):
            sites, links = split(X)
            sets = links @ powers
            shares = js.shares(torch.as_tensor(sites.astype(np.int8), device=js.device),
                               torch.as_tensor(sets, device=js.device)).cpu().numpy()
            out["F"] = -np.column_stack([shares[:, :n_scored].mean(axis=1), shares[:, n_scored]])

    class Sample(Sampling):
        def _do(self, problem, n_samples, **kwargs):
            generator = torch.Generator(device=js.device).manual_seed(int(rng.integers(1 << 31)))
            sizes = rng.integers(1, N + 1, size=n_samples)
            X = np.zeros((n_samples, S + G), dtype=np.int64)
            for size in np.unique(sizes):
                rows = np.flatnonzero(sizes == size)
                X[rows, :S] = js.base.random_plans(len(rows), int(size), generator).cpu().numpy()
            for row in X:
                k = rng.integers(0, min(K, G) + 1)
                row[S + rng.choice(G, k, replace=False)] = 1
            return X

    class Uniform(Crossover):
        def __init__(self):
            super().__init__(2, 2)

        def _do(self, problem, X, **kwargs):
            _, n_matings, n_var = X.shape
            swap = rng.random((n_matings, n_var)) < 0.5
            a, b = X[0].copy(), X[1].copy()
            a[swap], b[swap] = X[1][swap], X[0][swap]
            return np.stack([a, b])

    class Change(Mutation):
        def _do(self, problem, X, **kwargs):
            X = np.asarray(X, dtype=np.int64).copy()
            for row in X:
                sites, links = row[:S], row[S:]
                if K > 0 and rng.random() < 0.3:
                    on, off = np.flatnonzero(links), np.flatnonzero(links == 0)
                    if len(on) and (len(on) >= K or rng.random() < 0.5):
                        links[rng.choice(on)] = 0
                    if len(off) and np.count_nonzero(links) < K:
                        links[rng.choice(off)] = 1
                    continue
                on = np.flatnonzero(sites)
                move = rng.integers(3) if len(on) else 1
                if move == 0:
                    s = rng.choice(on)
                    sites[s] = rng.choice(allowed[s])
                elif move == 1:
                    if len(on) >= N:
                        sites[rng.choice(on)] = 0
                    free = np.setdiff1d(eligible, np.flatnonzero(sites))
                    if len(free):
                        s = rng.choice(free)
                        sites[s] = rng.choice(allowed[s])
                else:
                    sites[rng.choice(on)] = 0
            return X

    class Keep(Repair):
        def _do(self, problem, X, **kwargs):
            return repair_rows(X)

    started = time.perf_counter()
    algorithm = NSGA2(pop_size=population, sampling=Sample(), crossover=Uniform(), mutation=Change(),
                      repair=Keep(), eliminate_duplicates=True)
    result = minimize(Plans(), algorithm, ("n_gen", generations), seed=seed, verbose=False)
    X = np.atleast_2d(result.X).astype(np.int64)
    F = -np.atleast_2d(result.F)
    sites, links = split(X)
    return {"plans": sites.astype(np.int8), "sets": links @ powers, "service": F[:, 0], "parks": F[:, 1],
            "evaluations": population * generations, "seconds": time.perf_counter() - started}


# -- Bayesian optimisation ----------------------------------------------------------------


@dataclass
class Encoding:
    """Binary inputs for the Gaussian process: one per allowed (site, use) pair, one per link group."""

    pairs: np.ndarray  # (P, 2) site, use index (0-based)
    groups: np.ndarray  # (g,) link-group indices

    @property
    def dim(self) -> int:
        return len(self.pairs) + len(self.groups)

    def encode(self, plan: np.ndarray, s: int) -> np.ndarray:
        x = np.zeros(self.dim)
        chosen = plan[self.pairs[:, 0]] == self.pairs[:, 1] + 1
        x[:len(self.pairs)] = chosen
        x[len(self.pairs):] = [(s >> int(g)) & 1 for g in self.groups]
        return x


def bo_joint(js: JointScorer, N: int, K: int, alpha: float, *, reduced: int | None = None, initial: int = 1000,
             q: int = 10, window: int = 100, max_iterations: int = 300, refit_every: int = 10, pool: int = 4000,
             screen: int = 200, seed: int = 0, log=None) -> dict:
    """Bayesian optimisation as in Costa et al. (arXiv 2603.06278, sec. 4.2): botorch and gpytorch, a
    Gaussian process with a Matern 5/2 kernel and automatic relevance determination, batch log expected
    improvement (qLogEI), `initial` random feasible plans, and stopping when the best objective has not
    improved for `window` iterations (or after `max_iterations`).

    Their discrete encoding is not stated; here every allowed (site, use) pair and every link group is
    one binary input, and the acquisition is maximised over a pool of feasible plans (random ones and
    neighbours of the best found: a use changed, a site moved, a link group swapped), screened first by
    single-point log expected improvement to the best `screen`. Lengthscales take
    botorch's dimension-scaled log-normal prior, needed with hundreds of inputs. Hyperparameters are
    refitted every `refit_every` iterations, with a noise floor of 1e-3 (the objective has no noise, but
    the default floor leaves the kernel matrix unfactorable on 0/1 inputs). With `reduced`, only the
    `reduced` best (site, use) pairs by
    single-step gain and the link groups that gain alone are searched."""

    import torch
    from botorch.acquisition.analytic import LogExpectedImprovement
    from botorch.acquisition.logei import qLogExpectedImprovement
    from botorch.exceptions.errors import ModelFittingError
    from botorch.fit import fit_gpytorch_mll
    from botorch.models import SingleTaskGP
    from botorch.models.transforms.outcome import Standardize
    from botorch.optim import optimize_acqf_discrete
    from gpytorch.constraints import GreaterThan
    from gpytorch.kernels import MaternKernel
    from gpytorch.likelihoods import GaussianLikelihood
    from gpytorch.mlls import ExactMarginalLogLikelihood
    from gpytorch.priors import LogNormalPrior

    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    started = time.perf_counter()
    pairs = torch.nonzero(js.mask).cpu().numpy()
    groups = np.arange(js.G)
    if reduced:
        plans = np.zeros((len(pairs), js.n_sites), dtype=np.int8)
        plans[np.arange(len(pairs)), pairs[:, 0]] = pairs[:, 1] + 1
        gains = js.objective(plans, np.zeros(len(pairs), dtype=np.int64), alpha).cpu().numpy()
        pairs = pairs[np.argsort(-gains, kind="stable")[:reduced]]
        empty = np.zeros((js.G, js.n_sites), dtype=np.int8)
        link_gain = js.objective(empty, 1 << groups, alpha).cpu().numpy() - float(
            js.objective(empty[:1], [0], alpha)[0])
        groups = groups[link_gain > 1e-12]
    enc = Encoding(pairs, groups)
    by_site: dict[int, list[int]] = {}
    for i, (s, u) in enumerate(pairs):
        by_site.setdefault(int(s), []).append(int(u) + 1)
    sites = np.array(sorted(by_site))
    n_sites, n_links = min(N, len(sites)), min(K, len(groups))

    def random_plan():
        plan = np.zeros(js.n_sites, dtype=np.int8)
        for s in rng.choice(sites, n_sites, replace=False):
            plan[s] = rng.choice(by_site[int(s)])
        s = int(sum(1 << int(g) for g in rng.choice(groups, n_links, replace=False))) if n_links else 0
        return plan, s

    def neighbour(plan, s):
        plan = plan.copy()
        move = rng.integers(3) if n_links and len(groups) > n_links else rng.integers(2)
        on = np.flatnonzero(plan)
        if move == 0 and len(on):  # change a use
            site = int(rng.choice(on))
            plan[site] = rng.choice(by_site[site])
        elif move == 1 and len(on):  # move a site
            free = np.setdiff1d(sites, np.flatnonzero(plan))
            if len(free):
                plan[int(rng.choice(on))] = 0
                site = int(rng.choice(free))
                plan[site] = rng.choice(by_site[site])
        else:  # swap a link group
            on_g = [g for g in groups if s >> int(g) & 1]
            off_g = [g for g in groups if not s >> int(g) & 1]
            if on_g and off_g:
                s = s & ~(1 << int(rng.choice(on_g))) | (1 << int(rng.choice(off_g)))
        return plan, s

    def evaluate(cands):
        plans = np.stack([c[0] for c in cands])
        sets = np.array([c[1] for c in cands])
        return js.objective(plans, sets, alpha).cpu().numpy()

    seen: set[bytes] = set()
    cands, X = [], []
    for _ in range(20 * initial):  # small problems may have fewer distinct plans than `initial`
        if len(cands) >= initial:
            break
        c = random_plan()
        key = enc.encode(*c).astype(np.uint8).tobytes()
        if key not in seen:
            seen.add(key)
            cands.append(c)
            X.append(enc.encode(*c))
    Y = list(evaluate(cands))
    dkw = {"dtype": torch.float64, "device": js.device}
    d = enc.dim
    best, best_iteration, iterations, state, fit_failures = max(Y), 0, 0, None, 0
    for it in range(1, max_iterations + 1):
        iterations = it
        train_x = torch.as_tensor(np.array(X), **dkw)
        train_y = torch.as_tensor(np.array(Y)[:, None], **dkw)
        # the prior's parameters must start on the model's device (gpytorch does not move a prior's distribution)
        prior = LogNormalPrior(torch.tensor(math.sqrt(2) + 0.5 * math.log(d), **dkw), torch.tensor(math.sqrt(3), **dkw))
        kernel = MaternKernel(nu=2.5, ard_num_dims=d, lengthscale_prior=prior).to(**dkw)
        # the objective has no noise, but a nugget of 1e-3 (standardised units) keeps the kernel matrix
        # factorable: with the default floor of 1e-4 the fit fails on these 0/1 inputs
        likelihood = GaussianLikelihood(noise_constraint=GreaterThan(1e-3)).to(**dkw)
        model = SingleTaskGP(train_x, train_y, covar_module=kernel, likelihood=likelihood,
                             outcome_transform=Standardize(m=1)).to(train_x)
        refit = state is None or (it - 1) % refit_every == 0
        if refit:
            try:
                fit_gpytorch_mll(ExactMarginalLogLikelihood(model.likelihood, model))
                state = {k: v.detach().clone() for k, v in model.state_dict().items()
                         if not k.startswith("outcome_transform")}
            except ModelFittingError:
                fit_failures += 1  # keep the last good hyperparameters
                if state is None:
                    raise
                refit = False
        if not refit:
            model.load_state_dict(state, strict=False)
        model.eval()
        top = np.argsort(Y)[::-1][:20]
        pool_c = [random_plan() for _ in range(pool // 2)]
        pool_c += [neighbour(*cands[int(rng.choice(top))]) for _ in range(pool - len(pool_c))]
        fresh, fresh_x = [], []
        for c in pool_c:
            x = enc.encode(*c)
            key = x.astype(np.uint8).tobytes()
            if key not in seen:
                seen.add(key)
                fresh.append(c)
                fresh_x.append(x)
        if not fresh:
            break
        # screen the pool with single-point log EI (closed form), then pick the batch of q by qLogEI among
        # the best `screen` (sequential greedy, as botorch does for discrete choices)
        pool_x = torch.as_tensor(np.array(fresh_x), **dkw)
        with torch.no_grad():
            single = LogExpectedImprovement(model, best_f=train_y.max())
            scores = torch.cat([single(block[:, None, :]) for block in pool_x.split(512)])
        keep = scores.topk(min(screen, len(fresh))).indices
        acq = qLogExpectedImprovement(model, best_f=train_y.max())
        chosen, _ = optimize_acqf_discrete(acq, q=min(q, len(keep)), choices=pool_x[keep], max_batch_size=128,
                                           unique=True)
        index = {x.astype(np.uint8).tobytes(): i for i, x in enumerate(fresh_x)}
        picked = [fresh[index[row.round().cpu().numpy().astype(np.uint8).tobytes()]] for row in chosen]
        values = evaluate(picked)
        for c, v in zip(picked, values):
            cands.append(c)
            X.append(enc.encode(*c))
            Y.append(float(v))
        if max(values) > best + 1e-12:
            best, best_iteration = float(max(values)), it
        if log and it % 25 == 0:
            log(f"  bo N={N} K={K} a={alpha}{' reduced' if reduced else ''}: it {it}, best {best:.4f}")
        if it - best_iteration >= window:
            break
    i = int(np.argmax(Y))
    return {"plan": cands[i][0], "set": int(cands[i][1]), "objective": float(Y[i]), "evaluations": len(Y),
            "iterations": iterations, "stopped": "no improvement" if iterations - best_iteration >= window else
            ("iteration cap" if iterations >= max_iterations else "pool exhausted"),
            "inputs": d, "reduced": reduced, "fit_failures": fit_failures, "seconds": time.perf_counter() - started}


# -- the phase B runs ---------------------------------------------------------------------


def run_joint(model: AccessModel, links: LinkModel, methods: list[str], budgets: list[int], link_budgets: list[int],
              alphas: list[float], *, bo_budgets: list[int] | None = None, bo_link_budgets: list[int] | None = None,
              bo_reduced: int = 40, bo_max_iterations: int = 300, population: int = 1024, generations: int = 200,
              device: str = "cuda", seed: int = 0, log=print) -> dict:
    started = time.perf_counter()
    js = JointScorer(model, links, device)
    log(f"{js.G} link groups, {js.n_patterns} distinct reach patterns; groups that never change reach: "
        f"{[js.names[g] for g in js.inert]} ({time.perf_counter() - started:.1f} s)")
    out: dict[str, Any] = {"uses": js.uses, "sites": js.n_sites, "threshold_m": js.threshold, "groups": js.names,
                           "patterns": js.n_patterns, "inert_groups": [js.names[g] for g in js.inert],
                           "pattern_links": js.pattern_links.tolist(), "results": []}
    empty = np.zeros((1, js.n_sites), dtype=np.int8)
    out["baseline"] = {str(a): float(js.objective(empty, [0], a)[0]) for a in alphas}
    out["baseline_residents_within"] = js.describe(empty[0], 0, 1.0)["residents_within"]

    def add(method, N, K, a, plan, s, **extra):
        out["results"].append({"method": method, "budget": N, "link_budget": K, "alpha": a,
                               **js.describe(plan, s, a), **extra})

    if "exact" in methods:
        for (N, K, a), r in exact_joint(js, budgets, link_budgets, alphas, log=log).items():
            add("exact", N, K, a, r["plan"], r["set"], optimal=r["all_optimal"], patterns_compared=r["patterns_compared"])
        # land uses only (no links), for the value of the links
        for (N, _, a), r in exact_joint(js, budgets, [0], alphas, log=lambda *_: None).items():
            add("exact_no_links", N, 0, a, r["plan"], r["set"], optimal=r["all_optimal"])
    if "greedy" in methods:
        for N in budgets:
            for K in link_budgets:
                for a in alphas:
                    plan, s, steps = greedy_joint(js, N, K, a)
                    add("greedy", N, K, a, plan, s, steps=steps)
        log("greedy done")
    if "nsga2" in methods:
        for N in budgets:
            for K in link_budgets:
                r = nsga2_joint(js, N, K, population=population, generations=generations, seed=seed)
                for a in alphas:
                    i = int(np.argmax(a * r["service"] + r["parks"]))
                    add("nsga2", N, K, a, r["plans"][i], int(r["sets"][i]), evaluations=r["evaluations"],
                        population=population, generations=generations, seed=seed, seconds=round(r["seconds"], 1))
                log(f"nsga2 N={N} K={K}: {r['seconds']:.0f} s")
    if "bo" in methods:
        for N in bo_budgets or budgets:
            for K in bo_link_budgets or link_budgets:
                for a in alphas:
                    for reduced in (None, bo_reduced):
                        r = bo_joint(js, N, K, a, reduced=reduced, max_iterations=bo_max_iterations, seed=seed)
                        add("bo_reduced" if reduced else "bo", N, K, a, r["plan"], r["set"],
                            **{k: r[k] for k in ("evaluations", "iterations", "stopped", "inputs", "reduced",
                                                 "fit_failures")},
                            seconds=round(r["seconds"], 1), seed=seed)
                        log(f"bo{' reduced' if reduced else ''} N={N} K={K} a={a}: {r['objective']:.4f} after "
                            f"{r['evaluations']} evaluations ({r['stopped']}, {r['seconds']:.0f} s)")
    best = {(r["budget"], r["link_budget"], r["alpha"]): r["objective"] for r in out["results"] if r["method"] == "exact"}
    for r in out["results"]:
        opt = best.get((r["budget"], r["link_budget"], r["alpha"]))
        if opt is not None and r["method"] != "exact_no_links":
            r["gap_pct"] = round(100 * (opt - r["objective"]) / opt, 4)
    out["wall_seconds"] = round(time.perf_counter() - started, 1)
    return out


def joint_table(out: dict, label: str) -> str:
    methods = [m for m in ("exact", "greedy", "nsga2", "bo", "bo_reduced") if any(r["method"] == m for r in out["results"])]
    by = {(r["method"], r["budget"], r["link_budget"], r["alpha"]): r for r in out["results"]}
    alphas = sorted({r["alpha"] for r in out["results"]})
    budgets = sorted({r["budget"] for r in out["results"] if r["method"] == "exact"})
    link_budgets = sorted({r["link_budget"] for r in out["results"] if r["method"] == "exact"})
    lines = [f"### {label}", "",
             f"{out['sites']} sites; link groups {', '.join(out['groups'])}. {out['patterns']} distinct reach "
             f"patterns; groups that never change reach at {out['threshold_m']:.0f} m: "
             f"{', '.join(out['inert_groups']) or 'none'}.", ""]
    for a in alphas:
        lines += [f"**α = {a}**. Objective, and gap to the exact optimum in %; last column: the optimum with no links:",
                  "", "| N | K | " + " | ".join(methods) + " | no links |", "|---|---|" + "---|" * (len(methods) + 1)]
        for N in budgets:
            for K in link_budgets:
                cells = []
                for m in methods:
                    r = by.get((m, N, K, a))
                    if r is None:
                        cells.append("")
                    elif m == "exact":
                        cells.append(f"{r['objective']:.4f}" + ("" if r.get("optimal", True) else " (not proven)"))
                    else:
                        cells.append(f"{r['objective']:.4f} ({r.get('gap_pct', float('nan')):.2f})")
                base = by.get(("exact_no_links", N, 0, a))
                cells.append(f"{base['objective']:.4f}" if base else "")
                lines.append(f"| {N} | {K} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["**Links chosen by the exact optimum:**", "", "| N | K | α | links | uses |", "|---|---|---|---|---|"]
    for N in budgets:
        for K in link_budgets:
            for a in alphas:
                r = by.get(("exact", N, K, a))
                if r:
                    uses = ", ".join(f"{u} {c}" for u, c in sorted(r["uses_chosen"].items()))
                    lines.append(f"| {N} | {K} | {a} | {', '.join(r['links']) or 'none'} | {uses} |")
    return "\n".join(lines) + "\n"
