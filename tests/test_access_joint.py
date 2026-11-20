import json
from itertools import product

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from dynacity.access import Homes, Place, WalkGraph, build_access  # noqa: E402
from dynacity.access_gpu import BatchScorer  # noqa: E402
from dynacity.access_joint import (  # noqa: E402
    JointScorer, bo_joint, exact_joint, greedy_joint, link_groups, nsga2_joint,
)
from dynacity.cli import main  # noqa: E402
from dynacity.walk_links import Link, build_links  # noqa: E402

X0, Y0 = 733000.0, 3752000.0


def two_banks():
    """Two streets 40 m apart (a river between), joined only by a bridge at the far east end."""

    xy = [(X0 + 100 * i, Y0) for i in range(11)] + [(X0 + 100 * i, Y0 + 40) for i in range(11)]
    edges = [(i, i + 1) for i in range(10)] + [(11 + i, 12 + i) for i in range(10)] + [(10, 21)]
    source, target, length = [], [], []
    for a, b in edges:
        d = float(np.hypot(*(np.subtract(xy[a], xy[b]))))
        source += [a, b]
        target += [b, a]
        length += [d, d]
    return WalkGraph(np.asarray(xy, float), np.array(source), np.array(target), np.array(length))


def link(id_, a, b, kind="crossing"):
    xy = np.array([a, b], float)
    return Link(id_, kind, xy, float(np.hypot(*(xy[1] - xy[0]))), "reviewer", {})


@pytest.fixture(scope="module")
def setup():
    g = two_banks()
    hx = [X0 + 100 * i for i in range(0, 11, 2)]
    homes = Homes(np.array([f"h{i}" for i in range(12)]),
                  np.array([(x, Y0 - 10) for x in hx] + [(x, Y0 + 50) for x in hx]),
                  np.linspace(5, 30, 12))
    places = [Place("p0", "medical", X0 + 950, Y0 + 45, "test", ""), Place("p1", "parks", X0 + 20, Y0 - 3, "test", "")]
    sites = [{"id": f"s{i}", "x": X0 + 150 + 150 * i, "y": Y0 + (42 if i % 2 else -2),
              "allowed": ["medical", "parks"] if i != 2 else ["parks"]} for i in range(6)]
    model = build_access(g, homes, places, sites, services=("medical", "parks"), score_services=("medical",))
    links = [link("bridge-west", (X0 + 100, Y0), (X0 + 100, Y0 + 40)),
             link("bridge-mid", (X0 + 500, Y0), (X0 + 500, Y0 + 40)),
             link("duplicate", (X0 + 200, Y0), (X0 + 300, Y0))]  # repeats a street: changes nothing
    blocks, _ = build_links(g, homes, places, sites, model.services, links)
    return model, blocks


def reference(model, blocks, plan_uses, active, alpha):
    """Objective by plain arrays: link distances, then the plan's sites as new places."""

    d = blocks.distances(model, active)
    S = len(model.services)
    D = d[:S].copy()
    for site, use in plan_uses.items():
        k = model._service[use]
        D[k] = np.minimum(D[k], d[S + model._site[site]])
    return model.objective(D, alpha)


def test_link_groups_keep_the_walkway_together(setup):
    model, blocks = setup
    blocks2 = type(blocks)(blocks.link_ids + ["railbed-walk/0", "railbed-walk/1"],
                           blocks.link_kind + ["railbed_walkway"] * 2, blocks.links, blocks.length, blocks.end_xy,
                           blocks.D_he, blocks.D_et, blocks.D_ee, blocks.targets)
    groups, names = link_groups(blocks2)
    assert names == ["bridge-west", "bridge-mid", "duplicate", "railbed-walkway"] and groups[-1] == [3, 4]


def test_no_links_scores_as_the_land_use_scorer(setup):
    model, blocks = setup
    js = JointScorer(model, blocks, "cpu")
    base = BatchScorer(model, "cpu")
    p0 = js.pattern_of_set[0]
    assert torch.equal(js.R_base[p0], base.R_base) and torch.equal(js.R_site[p0], base.R_site)
    plans = base.random_plans(50, 3, torch.Generator().manual_seed(0))
    assert torch.allclose(js.objective(plans, torch.zeros(50, dtype=torch.long), 2.0), base.objective(plans, 2.0),
                          rtol=0, atol=1e-12)


def test_joint_scores_match_plain_distances(setup):
    model, blocks = setup
    js = JointScorer(model, blocks, "cpu")
    rng = np.random.default_rng(1)
    plans = js.base.random_plans(40, 3, torch.Generator().manual_seed(1))
    sets = torch.as_tensor(rng.integers(0, 2 ** js.G, size=40))
    fast = js.objective(plans, sets, 1.5).numpy()
    for i in range(40):
        active = [j for j in range(js.G) if int(sets[i]) >> j & 1]
        assert fast[i] == pytest.approx(reference(model, blocks, js.base.to_uses(plans[i]), active, 1.5), abs=1e-9)
    assert js.inert == [2], "the duplicate link never changes reach"
    assert js.n_patterns == 4


def test_exact_joint_matches_enumeration(setup):
    model, blocks = setup
    js = JointScorer(model, blocks, "cpu")
    options = [[0] + list(np.flatnonzero(m) + 1) for m in js.mask.numpy()]
    plans = np.array(list(product(*options)), dtype=np.int8)
    for N, K, a in [(1, 1, 1.0), (2, 1, 0.5), (2, 2, 4.0), (3, 0, 1.0)]:
        result = exact_joint(js, [N], [K], [a], log=lambda *_: None)[(N, K, a)]
        best = -np.inf
        for s in range(2 ** js.G):
            if bin(s).count("1") > K:
                continue
            ok = (plans > 0).sum(1) <= N
            v = js.objective(plans[ok], torch.full((int(ok.sum()),), s), a).numpy().max()
            best = max(best, v)
        assert result["objective"] == pytest.approx(best, abs=1e-9) and result["all_optimal"]
        assert bin(result["set"]).count("1") <= K and (result["plan"] > 0).sum() <= N


def test_greedy_nsga2_and_bo_stay_feasible_and_below_the_optimum(setup):
    model, blocks = setup
    js = JointScorer(model, blocks, "cpu")
    N, K, a = 2, 1, 1.0
    opt = exact_joint(js, [N], [K], [a], log=lambda *_: None)[(N, K, a)]["objective"]
    plan, s, steps = greedy_joint(js, N, K, a)
    assert (plan > 0).sum() <= N and bin(s).count("1") <= K and steps >= 1
    assert float(js.objective(plan[None], [s], a)[0]) <= opt + 1e-9
    r = nsga2_joint(js, N, K, population=24, generations=6)
    assert ((r["plans"] > 0).sum(1) <= N).all() and all(bin(int(t)).count("1") <= K for t in r["sets"])
    assert bool(js.base.valid(torch.as_tensor(r["plans"])).all())
    assert (a * r["service"] + r["parks"]).max() <= opt + 1e-9
    for reduced in (None, 3):
        b = bo_joint(js, N, K, a, reduced=reduced, initial=12, q=2, max_iterations=3, pool=40)
        assert (b["plan"] > 0).sum() <= N and bin(b["set"]).count("1") <= K
        assert b["objective"] <= opt + 1e-9
        assert b["evaluations"] >= (12 if reduced is None else 1), "the reduced space has only a few distinct plans"


def test_search_joint_runs_from_the_command_line(setup, tmp_path):
    model, blocks = setup
    model.save(tmp_path / "access.npz")
    blocks.save(tmp_path / "links.npz")
    out, md = tmp_path / "joint.json", tmp_path / "joint.md"
    main(["search-joint", "--model", str(tmp_path / "access.npz"), "--links", str(tmp_path / "links.npz"),
          "--output", str(out), "--summary", str(md), "--device", "cpu", "--method", "exact", "greedy", "nsga2",
          "--budgets", "2", "--link-budgets", "1", "--alpha", "1", "--population", "16", "--generations", "4"])
    result = json.loads(out.read_text())
    gaps = {r["method"]: r["gap_pct"] for r in result["results"] if "gap_pct" in r}
    assert gaps["exact"] == 0 and min(gaps.values()) >= -1e-9 and result["inert_groups"] == ["duplicate"]
    assert "no links" in md.read_text()
