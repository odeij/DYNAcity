import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from dynacity.access import AccessModel  # noqa: E402
from dynacity.access_gpu import BatchScorer, check  # noqa: E402
from dynacity.cli import main  # noqa: E402

SERVICES = ("education", "medical", "shopping", "work", "entertainment", "parks")


def random_model(seed=0, homes=300, sites=40):
    """Walks drawn at random around the 500 m threshold, with some sites limited to a few uses."""

    rng = np.random.default_rng(seed)
    base = rng.uniform(100, 1500, size=(len(SERVICES), homes))
    base[1, rng.random(homes) < 0.3] = np.inf  # out of reach of any mapped clinic
    site_dist = rng.uniform(0, 1500, size=(sites, homes))
    site_list = []
    for i in range(sites):
        allowed = list(SERVICES) if i % 3 else ["parks", "entertainment"]
        if i % 7 == 0:
            allowed = ["work"]  # a site whose only use is not scored
        site_list.append({"id": f"s{i}", "allowed": allowed})
    return AccessModel(services=SERVICES, home_ids=np.array([f"h{i}" for i in range(homes)]),
                       home_xy=np.zeros((homes, 2)), residents=rng.uniform(1, 50, size=homes), base=base,
                       site_ids=[s["id"] for s in site_list], site_dist=site_dist, sites=site_list,
                       destination_counts={}, meta={"score_services": ["education", "medical", "shopping",
                                                                       "entertainment"]})


def test_search_uses_are_the_scored_services_and_parks():
    scorer = BatchScorer(random_model(), "cpu")
    assert scorer.uses == ["education", "medical", "shopping", "entertainment", "parks"]
    assert not bool(scorer.mask[0].any()), "a work-only site has no search use"
    assert scorer.mask[3].tolist() == [False, False, False, True, True]


def test_batch_scores_match_the_model_exactly():
    m = random_model()
    scorer = BatchScorer(m, "cpu")
    generator = torch.Generator().manual_seed(1)
    plans = torch.cat([scorer.random_plans(200, n, generator) for n in (0, 1, 5, 20)])
    plans[0] = 0
    fast = scorer.objective(plans, alpha=2.0).numpy()
    slow = np.array([m.objective(m.distances(scorer.to_uses(p)), alpha=2.0) for p in plans.numpy()])
    assert np.abs(fast - slow).max() < 1e-12
    assert fast[0] == pytest.approx(m.objective(m.base, alpha=2.0))


def test_alpha_can_differ_per_plan():
    m = random_model(seed=2)
    scorer = BatchScorer(m, "cpu")
    plans = scorer.random_plans(50, 6, torch.Generator().manual_seed(0))
    alpha = torch.linspace(0.25, 4, 50, dtype=torch.float64)
    together = scorer.objective(plans, alpha)
    one_by_one = torch.stack([scorer.objective(p, float(a))[0] for p, a in zip(plans, alpha)])
    assert torch.allclose(together, one_by_one, rtol=0, atol=1e-12)


def test_random_plans_are_valid_and_use_the_budget():
    scorer = BatchScorer(random_model(), "cpu")
    plans = scorer.random_plans(500, 7, torch.Generator().manual_seed(3))
    assert bool(scorer.valid(plans).all())
    assert ((plans > 0).sum(dim=1) == 7).all()
    bad = plans.clone()
    bad[0, 0] = 1  # site 0 is work-only
    bad[1, 1] = 9  # past the last use
    assert scorer.valid(bad).tolist()[:3] == [False, False, True]


def test_plans_round_trip_to_the_model_s_site_uses():
    m = random_model()
    scorer = BatchScorer(m, "cpu")
    uses = {"s1": "medical", "s3": "parks", "s5": "education"}
    row = scorer.from_uses(uses)
    assert scorer.to_uses(row) == uses
    assert scorer.objective(torch.as_tensor(row))[0].item() == pytest.approx(m.objective(m.distances(uses)), abs=1e-12)


def test_small_chunks_give_the_same_scores():
    m = random_model(seed=4)
    plans = BatchScorer(m, "cpu").random_plans(300, 10, torch.Generator().manual_seed(0))
    whole = BatchScorer(m, "cpu").objective(plans)
    chunked = BatchScorer(m, "cpu", chunk=64).objective(plans)
    assert torch.equal(whole, chunked)


def test_a_different_threshold_moves_the_reach():
    m = random_model()
    plan = torch.zeros(1, len(m.site_ids), dtype=torch.int8)
    near = BatchScorer(m, "cpu", threshold=400).objective(plan).item()
    far = BatchScorer(m, "cpu", threshold=600).objective(plan).item()
    assert near < far


def test_check_and_bench_run_from_the_command_line(tmp_path):
    m = random_model()
    assert check(m, "cpu", n=300)["passed"]
    path = tmp_path / "access.npz"
    m.save(path)
    out = tmp_path / "bench.json"
    main(["bench-access-gpu", "--model", str(path), "--output", str(out), "--devices", "cpu",
          "--batches", "100", "500", "--check", "200"])
    result = json.loads(out.read_text())
    assert result["check"][0]["passed"] and [b["batch"] for b in result["bench"]] == [100, 500]
    assert len(result["record"]["model_sha256"]) == 64 and result["record"]["torch"]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="no GPU")
def test_the_gpu_gives_the_cpu_s_scores():
    m = random_model()
    plans = BatchScorer(m, "cpu").random_plans(1000, 12, torch.Generator().manual_seed(5))
    cpu = BatchScorer(m, "cpu").objective(plans)
    gpu = BatchScorer(m, "cuda").objective(plans).cpu()
    assert (cpu - gpu).abs().max().item() < 1e-12


# -- phase A1: the exact optimum and the baselines --------------------------------

from itertools import combinations, product  # noqa: E402

from dynacity.access_search import (  # noqa: E402
    CoverageProgram, front_gap, greedy_path, nsga2, pareto_front, random_search, use_weights,
)


def brute_force(scorer, budget, alpha):
    """Best plan by enumeration, for small models."""

    best = -np.inf
    options = [np.flatnonzero(m) + 1 for m in scorer.mask.numpy()]
    eligible = [s for s, o in enumerate(options) if len(o)]
    for size in range(budget + 1):
        for sites in combinations(eligible, size):
            for uses in product(*[options[s] for s in sites]):
                plan = np.zeros(scorer.n_sites, dtype=np.int8)
                plan[list(sites)] = uses
                best = max(best, scorer.objective(torch.as_tensor(plan)[None], alpha).item())
    return best


@pytest.mark.parametrize("alpha", [0.5, 2.0])
def test_the_integer_program_finds_the_enumerated_optimum(alpha):
    m = random_model(seed=6, homes=80, sites=8)
    scorer = BatchScorer(m, "cpu")
    program = CoverageProgram.build(scorer)
    base = scorer.objective(torch.zeros(1, 8, dtype=torch.int8), alpha).item()
    for budget in (1, 2, 3):
        r = program.solve(budget, use_weights(scorer, alpha))
        assert r["optimal"] and (r["plan"] > 0).sum() <= budget and bool(scorer.valid(torch.as_tensor(r["plan"])[None]))
        exact = scorer.objective(torch.as_tensor(r["plan"])[None], alpha).item()
        assert exact == pytest.approx(base + r["gain"], abs=1e-9)
        assert exact == pytest.approx(brute_force(scorer, budget, alpha), abs=1e-9)


def test_greedy_random_and_nsga2_stay_valid_and_below_the_optimum():
    m = random_model(seed=7, homes=120, sites=12)
    scorer = BatchScorer(m, "cpu")
    opt = (scorer.objective(torch.zeros(1, 12, dtype=torch.int8)).item()
           + CoverageProgram.build(scorer).solve(3, use_weights(scorer, 1.0))["gain"])
    path = greedy_path(scorer, 3, 1.0)
    assert [int((p > 0).sum()) for p in path] == [1, 2, 3]
    assert scorer.objective(torch.as_tensor(path[-1])[None]).item() <= opt + 1e-9
    r = random_search(scorer, 3, 1.0, samples=2000, batch=512)
    assert r["objective"] <= opt + 1e-9 and r["distribution"]["p05"] <= r["distribution"]["p95"]
    front = nsga2(scorer, 3, population=40, generations=15)
    assert bool(scorer.valid(torch.as_tensor(front["plans"])).all()) and ((front["plans"] > 0).sum(1) <= 3).all()
    assert (front["service"] + front["parks"]).max() <= opt + 1e-9


def test_the_exact_front_runs_from_best_service_to_best_parks():
    m = random_model(seed=8, homes=100, sites=10)
    program = CoverageProgram.build(BatchScorer(m, "cpu"))
    front = pareto_front(program, 2, points=5)
    services = [p["service"] for p in front]
    assert services == sorted(services, reverse=True), "more parks costs service along the front"
    assert front_gap(front, front) == 0
    worse = [{"service": p["service"] * 0.9, "parks": p["parks"]} for p in front]
    assert front_gap(front, worse) == pytest.approx(10)
    assert front_gap(front, []) == 100


def test_search_runs_from_the_command_line(tmp_path):
    m = random_model(seed=9, homes=80, sites=8)
    path = tmp_path / "access.npz"
    m.save(path)
    out, md = tmp_path / "search.json", tmp_path / "summary.md"
    main(["search-access", "--model", str(path), "--output", str(out), "--summary", str(md), "--device", "cpu",
          "--budget", "1", "2", "--alpha", "1", "--samples", "500", "--population", "20", "--generations", "5",
          "--pareto-points", "3"])
    result = json.loads(out.read_text())
    gaps = {r["method"]: r["gap_pct"] for r in result["results"] if r["budget"] == 2}
    assert gaps["milp"] == 0 and min(gaps.values()) >= -1e-9
    assert "| 2 |" in md.read_text()
