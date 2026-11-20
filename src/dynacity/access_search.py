"""The exact optimum and the search baselines for land-use plans (docs/rl-planning-gpu-plan.md, phase A1).

At a fixed threshold, choosing site uses to maximise `AccessModel.objective` is a maximum-coverage
problem, so an integer program solves it exactly. The baselines (random, greedy, NSGA-II) run on the
same objective through `access_gpu.BatchScorer`, and every result is re-scored there, so all methods are
compared on one evaluator. Each result's gap is to the integer program's optimum for the same budget,
weighting and site set.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .access import AccessModel
from .access_gpu import BatchScorer


# -- the integer program ---------------------------------------------------------


@dataclass
class CoverageProgram:
    """Variables and fixed constraints of the coverage program for one model and threshold.

    x[s, u] (binary): site s takes search use u, for allowed pairs only.
    y[k, h] in [0, 1]: home h newly reaches use k; only homes not already reached that some allowed
    site could reach. y[k, h] <= sum_s x[s, k] R_site[s, h], one use per site, at most N uses.
    """

    pairs: np.ndarray  # (P, 2) site, use index (0-based into scorer.uses)
    cover: np.ndarray  # (Y, 2) use index, home
    rows: Any  # sparse (Y + sites + 1 [+ extra], P + Y) constraint matrix without the budget value
    weight: np.ndarray  # (homes,) resident weights
    base_shares: np.ndarray  # (uses,) share already reached
    scorer: BatchScorer

    @classmethod
    def build(cls, scorer: BatchScorer) -> CoverageProgram:
        from scipy.sparse import coo_matrix

        R_site = scorer.R_site.float().cpu().numpy() > 0
        R_base = scorer.R_base.cpu().numpy()
        mask = scorer.mask.cpu().numpy()
        weight = scorer.weight.cpu().numpy()
        pairs = np.argwhere(mask)
        cover = []
        for k in range(len(scorer.uses)):
            sites_k = pairs[pairs[:, 1] == k, 0]
            reachable = R_site[sites_k].any(axis=0) & ~R_base[k] if len(sites_k) else np.zeros(scorer.n_homes, bool)
            cover.extend((k, h) for h in np.flatnonzero(reachable))
        cover = np.array(cover, dtype=int).reshape(-1, 2)
        P, Y = len(pairs), len(cover)
        r, c, v = [], [], []
        # coverage: y[k,h] - sum_s x[s,k] R[s,h] <= 0
        pair_of = {(int(s), int(k)): i for i, (s, k) in enumerate(pairs)}
        for j, (k, h) in enumerate(cover):
            r.append(j), c.append(P + j), v.append(1.0)
            for s in np.flatnonzero(R_site[:, h]):
                i = pair_of.get((int(s), int(k)))
                if i is not None:
                    r.append(j), c.append(i), v.append(-1.0)
        # one use per site
        for i, (s, _) in enumerate(pairs):
            r.append(Y + s), c.append(i), v.append(1.0)
        # the budget
        for i in range(P):
            r.append(Y + scorer.n_sites), c.append(i), v.append(1.0)
        rows = coo_matrix((v, (r, c)), shape=(Y + scorer.n_sites + 1, P + Y)).tocsr()
        base_shares = (R_base.astype(float) @ weight)
        return cls(pairs=pairs, cover=cover, rows=rows, weight=weight, base_shares=base_shares, scorer=scorer)

    @property
    def size(self) -> dict:
        return {"binary": len(self.pairs), "continuous": len(self.cover), "constraints": self.rows.shape[0],
                "nonzeros": int(self.rows.nnz)}

    def _use_coefficients(self, use_weights: np.ndarray) -> np.ndarray:
        """Objective over (x, y) given a weight per use (maximised, so negated for scipy)."""

        c = np.zeros(len(self.pairs) + len(self.cover))
        c[len(self.pairs):] = use_weights[self.cover[:, 0]] * self.weight[self.cover[:, 1]]
        return -c

    def _share_row(self, k: int) -> np.ndarray:
        row = np.zeros(len(self.pairs) + len(self.cover))
        on = self.cover[:, 0] == k
        row[len(self.pairs):][on] = self.weight[self.cover[on, 1]]
        return row

    def solve(self, budget: int, use_weights: np.ndarray, floors: dict[int, float] | None = None,
              time_limit: float = 600.0) -> dict:
        """Maximise sum_k use_weights[k] x share_k with at most `budget` uses and optional share floors."""

        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import vstack

        P, Y = len(self.pairs), len(self.cover)
        n_fixed = self.rows.shape[0]
        upper = np.concatenate([np.zeros(Y), np.ones(self.scorer.n_sites), [budget]])
        lower = np.full(n_fixed, -np.inf)
        A = self.rows
        if floors:
            extra = [self._share_row(k) for k in floors]
            A = vstack([A] + [np.atleast_2d(e) for e in extra]).tocsr()
            lower = np.concatenate([lower, [floors[k] - self.base_shares[k] for k in floors]])
            upper = np.concatenate([upper, np.full(len(floors), np.inf)])
        integrality = np.concatenate([np.ones(P), np.zeros(Y)])
        started = time.perf_counter()
        result = milp(self._use_coefficients(use_weights), integrality=integrality, bounds=Bounds(0, 1),
                      constraints=LinearConstraint(A, lower, upper),
                      options={"time_limit": time_limit, "mip_rel_gap": 1e-9, "disp": False})
        seconds = time.perf_counter() - started
        if result.x is None:
            return {"status": result.message, "feasible": False, "seconds": seconds}
        chosen = self.pairs[result.x[:P] > 0.5]
        plan = np.zeros(self.scorer.n_sites, dtype=np.int8)
        plan[chosen[:, 0]] = chosen[:, 1] + 1
        bound = getattr(result, "mip_dual_bound", None)
        gain = -float(result.fun)
        return {"status": result.message, "feasible": True, "optimal": bool(result.status == 0),
                "plan": plan, "gain": gain, "bound_gain": None if bound is None else -float(bound),
                "seconds": seconds}


def use_weights(scorer: BatchScorer, alpha: float) -> np.ndarray:
    """alpha / |scored| for each scored service and 1 for parks: the objective as a sum over use shares."""

    n = len(scorer.scored)
    return np.array([alpha / n if u in scorer.scored else 1.0 for u in scorer.uses])


# -- baselines on the batch scorer -----------------------------------------------


def random_search(scorer: BatchScorer, budget: int, alpha: float, samples: int = 1_000_000,
                  batch: int = 262_144, seed: int = 0) -> dict:
    """Best of `samples` random plans with exactly `budget` sites, and the distribution of their scores."""

    import torch

    generator = torch.Generator(device=scorer.device).manual_seed(seed)
    best_value, best_plan, values = -np.inf, None, []
    for start in range(0, samples, batch):
        plans = scorer.random_plans(min(batch, samples - start), budget, generator)
        v = scorer.objective(plans, alpha)
        i = int(v.argmax())
        if float(v[i]) > best_value:
            best_value, best_plan = float(v[i]), plans[i].cpu().numpy()
        values.append(v.cpu().numpy())
    values = np.concatenate(values)
    q = np.quantile(values, [0.05, 0.5, 0.95])
    return {"plan": best_plan, "objective": best_value, "samples": samples, "seed": seed,
            "distribution": {"p05": float(q[0]), "median": float(q[1]), "p95": float(q[2]), "mean": float(values.mean())}}


def greedy_path(scorer: BatchScorer, max_budget: int, alpha: float) -> list[np.ndarray]:
    """Greedy plans for budgets 1..max_budget: each step adds the best single (site, use), every candidate
    scored in one batch. It stops early when nothing gains, as `AccessModel.greedy`; later budgets then
    keep the last plan."""

    import torch

    pairs = torch.nonzero(scorer.mask)  # (P, 2)
    plan = torch.zeros(scorer.n_sites, dtype=torch.int8, device=scorer.device)
    current = float(scorer.objective(plan[None], alpha)[0])
    path = []
    for _ in range(max_budget):
        free = plan[pairs[:, 0]] == 0
        cand = pairs[free]
        if len(cand):
            batch = plan.repeat(len(cand), 1)
            batch[torch.arange(len(cand)), cand[:, 0]] = (cand[:, 1] + 1).to(torch.int8)
            values = scorer.objective(batch, alpha)
            i = int(values.argmax())
            if float(values[i]) > current + 1e-12:
                plan = batch[i].clone()
                current = float(values[i])
        path.append(plan.cpu().numpy().copy())
    return path


def nsga2(scorer: BatchScorer, budget: int, population: int = 2048, generations: int = 200,
          seed: int = 0) -> dict:
    """NSGA-II (pymoo) on (service score, parks share), each generation scored in one batch.

    A plan is the integer row; sampling draws `budget` random allowed (site, use) pairs; crossover is
    uniform; mutation changes, adds or drops one site's use; a repair keeps every entry allowed and at most
    `budget` sites in use. Returns the final non-dominated plans, from which each alpha takes its best."""

    import torch
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.crossover import Crossover
    from pymoo.core.mutation import Mutation
    from pymoo.core.problem import Problem
    from pymoo.core.repair import Repair
    from pymoo.core.sampling import Sampling
    from pymoo.optimize import minimize

    rng = np.random.default_rng(seed)
    mask = scorer.mask.cpu().numpy()
    allowed = [np.flatnonzero(m) + 1 for m in mask]
    eligible = np.flatnonzero(mask.any(axis=1))
    n_sites, n_scored = scorer.n_sites, len(scorer.scored)

    def repair_rows(X):
        X = X.astype(np.int64)
        X = np.clip(X, 0, len(scorer.uses))
        ok = np.concatenate([np.ones((n_sites, 1), bool), mask], axis=1)[np.arange(n_sites), X]
        X[~ok] = 0
        for row in X:
            on = np.flatnonzero(row)
            if len(on) > budget:
                row[rng.choice(on, len(on) - budget, replace=False)] = 0
        return X

    class Plans(Problem):
        def __init__(self):
            super().__init__(n_var=n_sites, n_obj=2, xl=0, xu=len(scorer.uses), vtype=int)

        def _evaluate(self, X, out, *args, **kwargs):
            shares = scorer.shares(torch.as_tensor(X.astype(np.int8), device=scorer.device)).cpu().numpy()
            service = shares[:, :n_scored].mean(axis=1)
            parks = shares[:, n_scored]
            out["F"] = -np.column_stack([service, parks])

    class Sample(Sampling):
        def _do(self, problem, n_samples, **kwargs):
            generator = torch.Generator(device=scorer.device).manual_seed(int(rng.integers(1 << 31)))
            sizes = rng.integers(1, budget + 1, size=n_samples)
            X = np.zeros((n_samples, n_sites), dtype=np.int64)
            for size in np.unique(sizes):
                rows = np.flatnonzero(sizes == size)
                X[rows] = scorer.random_plans(len(rows), int(size), generator).cpu().numpy()
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
            X = X.copy()
            for row in X:
                on = np.flatnonzero(row)
                move = rng.integers(3) if len(on) else 1
                if move == 0:  # change a use
                    s = rng.choice(on)
                    row[s] = rng.choice(allowed[s])
                elif move == 1:  # add a site (or swap one out when at budget)
                    if len(on) >= budget:
                        row[rng.choice(on)] = 0
                    free = np.setdiff1d(eligible, np.flatnonzero(row))
                    if len(free):
                        s = rng.choice(free)
                        row[s] = rng.choice(allowed[s])
                else:  # drop a site
                    row[rng.choice(on)] = 0
            return X

    class Keep(Repair):
        def _do(self, problem, X, **kwargs):
            return repair_rows(X)

    started = time.perf_counter()
    algorithm = NSGA2(pop_size=population, sampling=Sample(), crossover=Uniform(), mutation=Change(),
                      repair=Keep(), eliminate_duplicates=True)
    result = minimize(Plans(), algorithm, ("n_gen", generations), seed=seed, verbose=False)
    X = np.atleast_2d(result.X).astype(np.int8)
    F = -np.atleast_2d(result.F)
    return {"plans": X, "service": F[:, 0], "parks": F[:, 1], "evaluations": population * generations,
            "seconds": time.perf_counter() - started, "population": population, "generations": generations,
            "seed": seed}


# -- describing a plan -------------------------------------------------------------


def describe(scorer: BatchScorer, plan: np.ndarray, alpha: float) -> dict:
    """Objective, residents within the threshold of each service, and the uses and sites chosen."""

    import torch

    row = torch.as_tensor(np.asarray(plan, dtype=np.int8), device=scorer.device)[None]
    shares = scorer.shares(row)[0].cpu().numpy()
    uses = scorer.to_uses(plan)
    counts: dict[str, int] = {}
    for use in uses.values():
        counts[use] = counts.get(use, 0) + 1
    return {"objective": float(scorer.objective(row, alpha)[0]), "sites_used": len(uses),
            "residents_within": {u: round(float(s), 4) for u, s in zip(scorer.uses, shares)},
            "uses_chosen": counts, "uses": uses}


# -- the phase A1 runs -------------------------------------------------------------


def pareto_front(program: CoverageProgram, budget: int, points: int = 11) -> list[dict]:
    """Exact (service score, parks share) front for one budget by the epsilon-constraint method: maximise
    the service score subject to a floor on the parks share, the floors spread from the parks share at
    the best service plan to the best parks share. A small weight on parks keeps each point efficient."""

    scorer = program.scorer
    n = len(scorer.scored)
    k_parks = len(scorer.uses) - 1
    service_w = np.array([1.0 / n] * n + [0.0])

    def point(result):
        plan = result["plan"]
        shares = scorer.shares(plan[None])[0].cpu().numpy()
        return {"service": float(shares[:n].mean()), "parks": float(shares[n]), "optimal": result["optimal"],
                "plan": plan}

    best_service = point(program.solve(budget, service_w + np.array([0.0] * n + [1e-6])))
    best_parks = point(program.solve(budget, np.array([1e-6 / n] * n + [1.0])))
    front = [best_service]
    for floor in np.linspace(best_service["parks"], best_parks["parks"], points)[1:-1]:
        front.append(point(program.solve(budget, service_w + np.array([0.0] * n + [1e-6]), floors={k_parks: floor})))
    front.append(best_parks)
    unique = {}
    for p in front:
        unique.setdefault((round(p["service"], 9), round(p["parks"], 9)), p)
    return sorted(unique.values(), key=lambda p: p["parks"])


def front_gap(exact: list[dict], found: list[dict]) -> float:
    """Relative epsilon indicator, in %: the smallest shortfall such that every exact point has a found
    point within it on both the service score and the parks share. 0 means the found front reaches every
    exact point. The exact front is sampled at a few points, so this is a lower bound on the indicator
    against the whole exact front."""

    worst = 0.0
    for e in exact:
        nearest = min(max(0.0, (e["service"] - f["service"]) / e["service"], (e["parks"] - f["parks"]) / e["parks"])
                      for f in found) if found else 1.0
        worst = max(worst, 100 * nearest)
    return worst


def run_search(model: AccessModel, methods: list[str], budgets: list[int], alphas: list[float],
               device: str = "cuda", samples: int = 1_000_000, population: int = 1024, generations: int = 200,
               pareto_points: int = 11, seed: int = 0, log=print) -> dict:
    scorer = BatchScorer(model, device)
    empty = np.zeros((1, scorer.n_sites), dtype=np.int8)
    baseline = {a: float(scorer.objective(empty, a)[0]) for a in alphas}
    program = CoverageProgram.build(scorer)
    out: dict[str, Any] = {"uses": scorer.uses, "sites": scorer.n_sites, "threshold_m": scorer.threshold,
                           "program_size": program.size, "baseline": {str(a): baseline[a] for a in alphas},
                           "baseline_residents_within": describe(scorer, empty[0], 1.0)["residents_within"],
                           "results": [], "pareto": {}}

    def add(method, N, alpha, plan, **extra):
        out["results"].append({"method": method, "budget": N, "alpha": alpha,
                               **describe(scorer, plan, alpha), **extra})

    if "milp" in methods:
        for N in budgets:
            for a in alphas:
                r = program.solve(N, use_weights(scorer, a))
                add("milp", N, a, r["plan"], optimal=r["optimal"], seconds=round(r["seconds"], 3),
                    bound=None if r["bound_gain"] is None else baseline[a] + r["bound_gain"])
            front = pareto_front(program, N, pareto_points)
            out["pareto"][str(N)] = {"milp": [{"service": p["service"], "parks": p["parks"], "optimal": p["optimal"],
                                               "uses": scorer.to_uses(p["plan"])} for p in front]}
            log(f"milp N={N}: {', '.join(f'{r['objective']:.4f}' for r in out['results'][-len(alphas):])}; "
                f"front {len(front)} points")
    if "greedy" in methods:
        for a in alphas:
            path = greedy_path(scorer, max(budgets), a)
            for N in budgets:
                add("greedy", N, a, path[N - 1])
        log("greedy done")
    if "random" in methods:
        for N in budgets:
            for a in alphas:
                r = random_search(scorer, N, a, samples=samples, seed=seed)
                add("random", N, a, r["plan"], samples=samples, seed=seed, distribution=r["distribution"])
            log(f"random N={N} done")
    if "nsga2" in methods:
        for N in budgets:
            r = nsga2(scorer, N, population=population, generations=generations, seed=seed)
            for a in alphas:
                i = int(np.argmax(a * r["service"] + r["parks"]))
                add("nsga2", N, a, r["plans"][i], evaluations=r["evaluations"], population=population,
                    generations=generations, seed=seed, seconds=round(r["seconds"], 1))
            out["pareto"].setdefault(str(N), {})["nsga2"] = [
                {"service": float(s), "parks": float(p)} for s, p in sorted(zip(r["service"], r["parks"]), key=lambda t: t[1])]
            log(f"nsga2 N={N}: {r['seconds']:.0f}s, front {len(r['plans'])}")
    add_gaps(out)
    return out


def add_gaps(out: dict, optimum: dict | None = None) -> None:
    """Gap of every result to the integer program's objective for the same budget and alpha, in %."""

    source = optimum or out
    best = {(r["budget"], r["alpha"]): r["objective"] for r in source["results"] if r["method"] == "milp"}
    for r in out["results"]:
        opt = best.get((r["budget"], r["alpha"]))
        if opt is not None:
            r["gap_pct"] = round(100 * (opt - r["objective"]) / opt, 4)
    for N, fronts in out["pareto"].items():
        exact = (source.get("pareto", {}).get(N) or {}).get("milp")
        if exact and "nsga2" in fronts:
            fronts["nsga2_front_gap_pct"] = round(front_gap(exact, fronts["nsga2"]), 4)
            fronts.pop("nsga2_hypervolume_share", None)


def summary_table(out: dict, label: str) -> str:
    """Gate A1's table: objective and gap per budget, alpha and method, plus the residents reached."""

    methods = [m for m in ("milp", "greedy", "nsga2", "random") if any(r["method"] == m for r in out["results"])]
    alphas = sorted({r["alpha"] for r in out["results"]})
    budgets = sorted({r["budget"] for r in out["results"]})
    by = {(r["method"], r["budget"], r["alpha"]): r for r in out["results"]}
    lines = [f"### {label}", "", f"{out['sites']} sites; search uses {', '.join(out['uses'])}; integer program "
             f"{out['program_size']['binary']} binary and {out['program_size']['continuous']} continuous variables.", ""]
    for a in alphas:
        lines += [f"**α = {a}** (baseline {out['baseline'][str(a)]:.4f}). Objective, and gap to the optimum in %:", "",
                  "| N | " + " | ".join(methods) + " |", "|---|" + "---|" * len(methods)]
        for N in budgets:
            cells = []
            for m in methods:
                r = by.get((m, N, a))
                if r is None:
                    cells.append("")
                elif m == "milp":
                    cells.append(f"{r['objective']:.4f}" + ("" if r.get("optimal", True) else " (not proven)"))
                else:
                    cells.append(f"{r['objective']:.4f} ({r.get('gap_pct', float('nan')):.2f})")
            lines.append(f"| {N} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["**Residents within the threshold of each use, optimal plans** (baseline first):", "",
              "| N | α | " + " | ".join(out["uses"]) + " | uses chosen |", "|---|---|" + "---|" * (len(out["uses"]) + 1),
              "| 0 | | " + " | ".join(f"{out['baseline_residents_within'][u]:.0%}" for u in out["uses"]) + " | |"]
    for N in budgets:
        for a in alphas:
            r = by.get(("milp", N, a))
            if r and N in (1, 5, 10, 20, 30):
                chosen = ", ".join(f"{u} {c}" for u, c in sorted(r["uses_chosen"].items()))
                lines.append(f"| {N} | {a} | " + " | ".join(f"{r['residents_within'][u]:.0%}" for u in out["uses"])
                             + f" | {chosen} |")
    fg = {N: f["nsga2_front_gap_pct"] for N, f in out["pareto"].items() if f.get("nsga2_front_gap_pct") is not None}
    if fg:
        lines += ["", "NSGA-II's front against the exact front (relative epsilon indicator, in %: the shortfall "
                  "within which NSGA-II comes to every computed exact point, on both scores), by budget: "
                  + ", ".join(f"N={N} {v:.2f}" for N, v in sorted(fg.items(), key=lambda t: int(t[0])))]
    return "\n".join(lines) + "\n"
