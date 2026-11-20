"""Score many land-use plans at once on the GPU, for search and RL (docs/rl-planning-gpu-plan.md, phase A0).

At a fixed threshold, "within reach" is a 0/1 matrix, so a batch of plans is one matrix product per
use: (plans x sites) one-hot times (sites x homes) reach. The result is exactly `AccessModel.objective`
for every plan in the batch, because min(base, site) <= t holds exactly when base <= t or site <= t.

A plan is an int8 row over the model's sites: 0 leaves a site unchanged, k gives it the k-th search use
(`BatchScorer.uses[k - 1]`). The search uses are the scored services plus parks; a use that is not in the
objective (work, by default) is left out, because giving a site that use can never change the score.

torch is imported inside the functions that need it, so the rest of the package runs without it.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .access import AccessModel

PARKS = "parks"


class BatchScorer:
    """`AccessModel.objective` for a batch of plans, on CPU or GPU."""

    def __init__(self, model: AccessModel, device: str = "cuda", threshold: float | None = None,
                 chunk: int = 32_768):
        import torch

        self.model = model
        self.device = torch.device(device)
        self.threshold = float(model.thresholds[0] if threshold is None else threshold)
        self.chunk = chunk
        scored = [model.services[k] for k in model._scored]
        self.scored = scored
        self.uses = scored + ([PARKS] if PARKS in model.services and PARKS not in scored else [])
        self.use_index = {u: i + 1 for i, u in enumerate(self.uses)}
        rows = [model._service[u] for u in self.uses]
        t = self.threshold

        # (uses, homes): reached already by a mapped place
        self.R_base = torch.as_tensor(model.base[rows] <= t, device=self.device)
        # (sites, homes): reached from each candidate site; bf16 holds the 0/1 values and counts up to 256 exactly
        self.R_site = torch.as_tensor(model.site_dist <= t, device=self.device).to(torch.bfloat16)
        # resident weights, summed in float64 so the result matches the numpy objective to ~1e-12
        self.weight = torch.as_tensor(model._weight, dtype=torch.float64, device=self.device)
        # (sites, uses): which search use each site may take
        mask = np.array([[u in model._allowed[s] for u in self.uses] for s in model.site_ids], dtype=bool)
        self.mask = torch.as_tensor(mask, device=self.device)
        # with an unchanged column first, so mask_full[s, plan[s]] is the validity of each entry
        self.mask_full = torch.cat([torch.ones(len(mask), 1, dtype=torch.bool, device=self.device), self.mask], 1)
        self.n_sites, self.n_homes = self.R_site.shape

    # -- plans <-> the model's {site: use} -----------------------------------

    def to_uses(self, plan) -> dict[str, str]:
        row = np.asarray(plan.cpu() if hasattr(plan, "cpu") else plan)
        return {self.model.site_ids[s]: self.uses[int(k) - 1] for s, k in enumerate(row) if k}

    def from_uses(self, uses: dict[str, str]) -> np.ndarray:
        row = np.zeros(self.n_sites, dtype=np.int8)
        for site, use in uses.items():
            row[self.model._site[site]] = self.use_index[use]
        return row

    def valid(self, plans):
        """(B,) bool: every entry is unchanged or an allowed use of its site."""

        import torch

        plans = plans.to(self.device).long()
        in_range = ((plans >= 0) & (plans <= len(self.uses))).all(dim=1)
        sites = torch.arange(self.n_sites, device=self.device).expand_as(plans)
        allowed = self.mask_full[sites, plans.clamp(0, len(self.uses))].all(dim=1)
        return in_range & allowed

    def random_plans(self, batch: int, budget: int, generator=None):
        """(batch, sites) int8: `budget` distinct sites, each with a uniformly drawn allowed use."""

        import torch

        eligible = self.mask.any(dim=1)
        budget = min(budget, int(eligible.sum()))
        keys = torch.rand(batch, self.n_sites, device=self.device, generator=generator)
        keys[:, ~eligible] = -1.0
        chosen = keys.topk(budget, dim=1).indices  # (batch, budget)
        options = self.mask[chosen].float()  # (batch, budget, uses)
        use = torch.multinomial(options.reshape(-1, len(self.uses)), 1, generator=generator).reshape(batch, budget)
        plans = torch.zeros(batch, self.n_sites, dtype=torch.int8, device=self.device)
        plans.scatter_(1, chosen, (use + 1).to(torch.int8))
        return plans

    # -- scoring ------------------------------------------------------------

    def reach(self, plans):
        """(B, uses, homes) bool reach for a batch that fits in memory."""

        import torch

        out = torch.empty(len(plans), len(self.uses), self.n_homes, dtype=torch.bool, device=self.device)
        for i in range(len(self.uses)):
            onehot = (plans == i + 1).to(torch.bfloat16)
            out[:, i] = self.R_base[i] | ((onehot @ self.R_site) > 0)
        return out

    def shares(self, plans):
        """(B, uses) float64: resident-weighted share within reach of each search use."""

        import torch

        plans = torch.as_tensor(plans, device=self.device)
        if plans.dim() == 1:
            plans = plans[None]
        out = torch.empty(len(plans), len(self.uses), dtype=torch.float64, device=self.device)
        for start in range(0, len(plans), self.chunk):
            block = plans[start:start + self.chunk]
            for i in range(len(self.uses)):
                onehot = (block == i + 1).to(torch.bfloat16)
                reached = self.R_base[i] | ((onehot @ self.R_site) > 0)
                out[start:start + len(block), i] = reached.to(torch.float64) @ self.weight
        return out

    def objective(self, plans, alpha=1.0):
        """(B,) float64: alpha x service score + parks share, as `AccessModel.objective`.

        `alpha` is a number or a (B,) tensor, so one batch can mix weightings (for RL)."""

        import torch

        shares = self.shares(plans)
        n = len(self.scored)
        service = shares[:, :n].mean(dim=1) if n else torch.zeros(len(shares), dtype=torch.float64, device=self.device)
        parks = shares[:, n] if len(self.uses) > n else torch.zeros_like(service)
        alpha = torch.as_tensor(alpha, dtype=torch.float64, device=self.device)
        return alpha * service + parks


# -- benchmark and correctness check (Gate A0) ---------------------------------


def _sync(device) -> None:
    import torch

    if torch.device(device).type == "cuda":
        torch.cuda.synchronize()


def check(model: AccessModel, device: str, n: int = 10_000, budget_max: int = 30, alpha: float = 1.0,
          seed: int = 0) -> dict:
    """Largest absolute difference between the batch scorer and `AccessModel.objective` on random plans."""

    import torch

    scorer = BatchScorer(model, device)
    generator = torch.Generator(device=scorer.device).manual_seed(seed)
    rng = np.random.default_rng(seed)
    budgets = rng.integers(0, budget_max + 1, size=n)
    plans = torch.zeros(n, scorer.n_sites, dtype=torch.int8, device=scorer.device)
    for budget in np.unique(budgets):
        rows = np.flatnonzero(budgets == budget)
        if budget:
            plans[rows] = scorer.random_plans(len(rows), int(budget), generator)
    assert bool(scorer.valid(plans).all())
    fast = scorer.objective(plans, alpha).cpu().numpy()
    slow = np.array([model.objective(model.distances(scorer.to_uses(p)), alpha) for p in plans.cpu().numpy()])
    diff = np.abs(fast - slow)
    return {"device": str(scorer.device), "plans": n, "budgets": f"0-{budget_max}", "alpha": alpha,
            "max_abs_diff": float(diff.max()), "mean_abs_diff": float(diff.mean()), "passed": bool(diff.max() < 1e-6)}


def bench(model: AccessModel, device: str, batches=(1_000, 16_000, 256_000), budget: int = 10,
          alpha: float = 1.0, repeats: int = 3, max_seconds: float = 60.0) -> list[dict]:
    """Plans scored per second for each batch size, best of `repeats` (plan sampling not timed)."""

    import torch

    scorer = BatchScorer(model, device)
    generator = torch.Generator(device=scorer.device).manual_seed(0)
    out = []
    for batch in batches:
        plans = scorer.random_plans(batch, budget, generator)
        scorer.objective(plans[:min(batch, 1_000)], alpha)  # warm-up
        _sync(device)
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            scorer.objective(plans, alpha)
            _sync(device)
            times.append(time.perf_counter() - start)
            if sum(times) > max_seconds:
                break
        best = min(times)
        out.append({"device": str(scorer.device), "batch": batch, "budget": budget, "seconds": round(best, 6),
                    "plans_per_second": round(batch / best), "repeats": len(times)})
    return out


def environment() -> dict[str, Any]:
    import platform

    import torch

    info = {"python": platform.python_version(), "torch": torch.__version__, "cuda": torch.version.cuda,
            "cuda_available": torch.cuda.is_available()}
    if torch.cuda.is_available():
        info["gpu"] = torch.cuda.get_device_name(0)
        info["capability"] = list(torch.cuda.get_device_capability(0))
    return info


def run_record(model_path: str, seconds: float, **extra: Any) -> dict[str, Any]:
    """What every run records (plan, Outputs and records): commit, model hash, versions, wall time."""

    import hashlib
    import subprocess
    from pathlib import Path

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                    cwd=Path(__file__).parent, capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    digest = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    return {"commit": commit, "uncommitted_changes": dirty, "model": str(model_path), "model_sha256": digest,
            **environment(), "wall_seconds": round(seconds, 2), **extra}
