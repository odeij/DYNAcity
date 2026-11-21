import gzip
import json

import numpy as np
import pytest

pytest.importorskip("torch")

from shapely.geometry import box  # noqa: E402

from dynacity.access import Homes, Place, WalkGraph, _shortest, build_access  # noqa: E402
from dynacity.access_joint import run_joint  # noqa: E402
from dynacity.access_view import (  # noqa: E402
    SCHEMA, build_access_view, forecast_section, load_view, save_view,
)
from dynacity.cli import main  # noqa: E402
from dynacity.walk_links import Link, attach, augmented_graph, build_links  # noqa: E402

X0, Y0 = 733000.0, 3752000.0
CRS = "EPSG:32636"


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


def link(id_, a, b, kind="crossing", **props):
    xy = np.array([a, b], float)
    return Link(id_, kind, xy, float(np.hypot(*(xy[1] - xy[0]))), "reviewer", props)


@pytest.fixture(scope="module")
def scene():
    g = two_banks()
    hx = [X0 + 100 * i for i in range(0, 11, 2)]
    homes = Homes(np.array([f"h{i}" for i in range(12)]),
                  np.array([(x, Y0 - 10) for x in hx] + [(x, Y0 + 50) for x in hx]), np.linspace(5, 30, 12))
    places = [Place("p0", "medical", X0 + 950, Y0 + 45, "test", "East clinic"),
              Place("p1", "parks", X0 + 20, Y0 - 3, "test", "")]
    sites = [{"id": f"bbed/{i}", "x": X0 + 150 + 150 * i, "y": Y0 + (42 if i % 2 else -2), "area_m2": 500.0 + i,
              "state": "stalled_or_cancelled", "allowed": ["medical", "parks"] if i != 2 else ["parks"]} for i in range(6)]
    model = build_access(g, homes, places, sites, services=("medical", "parks"), score_services=("medical",))
    links = [link("crossing/0", (X0 + 100, Y0), (X0 + 100, Y0 + 40), barrier_name="River", saved_at_least_m=700.0),
             link("crossing/1", (X0 + 500, Y0), (X0 + 500, Y0 + 40)),
             link("crossing/2", (X0 + 200, Y0), (X0 + 300, Y0))]  # repeats a street: changes nothing
    blocks, _ = build_links(g, homes, places, sites, model.services, links)
    ends = attach(g, links)
    joint = run_joint(model, blocks, ["exact", "greedy"], [1, 2], [1], [1.0, 2.0], device="cpu", log=lambda *_: None)
    joint = json.loads(json.dumps(joint, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
    view = build_access_view(model=model, link_model=blocks, links=links, ends=ends, original=g, places=places,
                             joint=joint, crs=CRS, label="Test set", log=lambda *_: None)
    return {"g": g, "homes": homes, "places": places, "model": model, "links": links, "blocks": blocks, "ends": ends,
            "joint": joint, "view": view}


def edges_of(view, key, use):
    """Provider and via per home: the baseline with the scenario's differences applied, as the page does."""

    provider = np.array(view["baseline"]["provider"][use])
    via = np.array(view["baseline"]["via"][use])
    needs = np.zeros(len(provider), dtype=int)
    if key != "baseline":
        flat = np.array(view["scenarios"][key]["edges"][use], dtype=np.int64).reshape(-1, 4)
        provider[flat[:, 0]], via[flat[:, 0]], needs[flat[:, 0]] = flat[:, 1], flat[:, 2], flat[:, 3]
    return provider, via, needs


def plan_of(view, key, model):
    if key == "baseline":
        return {}, []
    s = view["scenarios"][key]
    return {model.site_ids[i]: view["uses"][u] for i, u in s["uses"]}, s["links"]


def reference(scene, uses, groups):
    """Walking metres per use with the plan's sites and links, from LinkModel.distances."""

    model, blocks = scene["model"], scene["blocks"]
    names = scene["view"]["groups"]
    active = sorted(i for g in names if g["name"] in groups for i in g["links"])
    d = blocks.distances(model, active)
    S = len(model.services)
    out = {}
    for use in scene["view"]["uses"]:
        k = model.services.index(use)
        row = d[k].copy()
        for site, u in uses.items():
            if u == use:
                row = np.minimum(row, d[S + model.site_ids.index(site)])
        out[use] = row
    return out, active


def test_bundle_shape_carries_no_object_ids(scene):
    view = scene["view"]
    assert view["schema"] == SCHEMA and view["label"] == "Test set" and view["threshold_m"] == 500
    assert len(view["homes"]["residents"]) == 12 and len(view["homes"]["lonlat"]) == 24
    assert [g["name"] for g in view["groups"]] == ["crossing/0", "crossing/1", "crossing/2"]
    assert view["groups"][2]["inert"] and not view["groups"][0]["inert"]
    assert view["links"][0]["name"] == "River" and view["links"][0]["saved_m"] == 700.0
    assert view["sites"][0]["kind"] == "bbed" and view["sites"][0]["area_m2"] == 500
    text = json.dumps(view)
    assert "bbed/0" not in text and "h0" not in text and "p0" not in text, "no site, home or place ids go to the page"
    lon, lat = view["homes"]["lonlat"][:2]
    assert 35.4 < lon < 35.7 and 33.8 < lat < 34.0
    assert view["check"]["max_abs_diff_m"] <= 1e-3 and view["check"]["scenarios"] == len(view["scenarios"]) + 1
    assert view["story"]["methods"]["exact"] in view["scenarios"]


def test_served_homes_and_nearest_providers_match_the_scorer(scene):
    view, model, g, ends = scene["view"], scene["model"], scene["g"], scene["ends"]
    from scipy.spatial import cKDTree

    tree = cKDTree(g.xy)
    home_snap, home_node = tree.query(model.home_xy)
    P = len(view["places"])
    places = [p for p in scene["places"] if p.service in view["uses"]]
    for key in ["baseline", *view["scenarios"]]:
        uses, groups = plan_of(view, key, model)
        ref, active = reference(scene, uses, groups)
        graph, _ = augmented_graph(ends, active)
        for use in view["uses"]:
            provider, via, _ = edges_of(view, key, use)
            served = ref[use] <= view["threshold_m"]
            assert np.array_equal(provider >= 0, served), f"{key} {use}: served homes are the scorer's"
            weight = model.residents / model.residents.sum()
            assert view["scenarios"].get(key, {"residents_within": view["baseline"]["residents_within"]})[
                "residents_within"][use] == pytest.approx(float(weight @ served), abs=1e-4)
            for h in np.flatnonzero(served):
                p = provider[h]
                x, y = (places[p].x, places[p].y) if p < P else (model.sites[p - P]["x"], model.sites[p - P]["y"])
                if p >= P:
                    assert uses[model.site_ids[p - P]] == use, "a site serves only in the use the plan gives it"
                snap, node = tree.query((x, y))
                own = _shortest(graph, [(int(node), float(snap))])[home_node[h]] + home_snap[h]
                assert own == pytest.approx(ref[use][h], abs=1e-3), f"{key} {use} home {h}: its provider is the nearest"


def test_via_marks_exactly_the_walks_that_cross_a_link(scene):
    view, model = scene["view"], scene["model"]
    crossed = 0
    for key in view["scenarios"]:
        uses, groups = plan_of(view, key, model)
        with_links, active = reference(scene, uses, groups)
        without, _ = reference(scene, uses, [])
        for use in view["uses"]:
            provider, via, needs = edges_of(view, key, use)
            served = provider >= 0
            assert set(via[served & (via >= 0)]) <= set(active), "only open links carry walks"
            assert 2 not in set(via), "a link repeating a street is never needed"
            uses_link = served & (via >= 0)
            crossed += int(uses_link.sum())
            assert np.all(with_links[use][uses_link] < without[use][uses_link] - 1e-6), "a crossed link shortens the walk"
            assert np.allclose(with_links[use][served & (via < 0)], without[use][served & (via < 0)], atol=1e-3)
            assert np.array_equal(needs.astype(bool), served & ~(without[use] <= view["threshold_m"]))
    assert crossed > 0, "the fixture has walks across the bridges"


def test_mismatched_results_stop_the_build(scene):
    bad = json.loads(json.dumps(scene["joint"]))
    first = next(r for r in bad["results"] if r["method"] == "exact")
    first["residents_within"] = {u: v + 0.01 for u, v in first["residents_within"].items()}
    with pytest.raises(ValueError, match="shares differ"):
        build_access_view(model=scene["model"], link_model=scene["blocks"], links=scene["links"], ends=scene["ends"],
                          original=scene["g"], places=scene["places"], joint=bad, crs=CRS, label="x", log=lambda *_: None)
    with pytest.raises(ValueError, match="not built from these links"):
        build_access_view(model=scene["model"], link_model=scene["blocks"], links=scene["links"][:2], ends=scene["ends"],
                          original=scene["g"], places=scene["places"], joint=scene["joint"], crs=CRS, label="x")


def test_forecast_section_uses_the_two_year_table(scene):
    from dynacity.models import LABELS

    class Table:
        model_version = "empirical-markov-v1"
        _matrices = {2: {s: np.eye(len(LABELS))[LABELS.index(s)] * 0.9 + 0.1 / len(LABELS) for s in LABELS}}

    area = box(X0 - 50, Y0 - 50, X0 + 1100, Y0 + 100)
    snapshot = {"objects": [
        {"object_id": "BULBuildingID:1|OBJECTID:7", "state": "stable_built", "centroid_x_m": X0 + 10, "centroid_y_m": Y0},
        {"object_id": "BULBuildingID:2|OBJECTID:0", "state": "stable_built", "centroid_x_m": X0 + 20, "centroid_y_m": Y0},
        {"object_id": "BULBuildingID:3|OBJECTID:8", "state": "vacant_or_evicted", "centroid_x_m": X0 + 30, "centroid_y_m": Y0},
        {"object_id": "BULBuildingID:4|OBJECTID:9", "state": "stable_built", "centroid_x_m": X0 + 9000, "centroid_y_m": Y0}]}
    out = forecast_section(Table(), snapshot, area, scene["model"].site_ids, CRS)
    assert [b["state"] for b in out["buildings"]] == ["stable_built"], "sites, free and outside buildings are left out"
    P = np.array([Table._matrices[2][s] for s in LABELS])
    free = [LABELS.index(s) for s in ("empty_or_parking", "stalled_or_cancelled", "vacant_or_evicted")]
    i = LABELS.index("stable_built")
    expect = [float(np.linalg.matrix_power(P, k)[i, free].sum()) for k in (1, 2, 3)]
    assert out["chance"]["stable_built"] == pytest.approx(expect, abs=1e-4) and out["years"] == [2, 4, 6]
    assert forecast_section(object(), snapshot, area, [], CRS) is None


def test_save_load_and_serving(scene, tmp_path):
    from fastapi.testclient import TestClient
    from dynacity.service import create_app
    from test_models_engine_api import engine_fixture

    path = tmp_path / "view.json.gz"
    save_view(scene["view"], path)
    label, data = load_view(path)
    assert label == "Test set" and json.loads(gzip.decompress(data))["schema"] == SCHEMA
    (tmp_path / "bad.json.gz").write_bytes(gzip.compress(b'{"schema": "other"}'))
    with pytest.raises(ValueError, match="not a build-access-view output"):
        load_view(tmp_path / "bad.json.gz")
    client = TestClient(create_app(engine_fixture(), access_views=[(label, data)]))
    assert client.get("/v1/access/views").json() == {"views": ["Test set"]}
    response = client.get("/v1/access/view/0")
    assert response.status_code == 200 and response.json()["label"] == "Test set"
    assert response.headers["content-encoding"] == "gzip" and response.headers["cache-control"] == "private, no-store"
    assert client.get("/v1/access/view/1").status_code == 404
    client = TestClient(create_app(engine_fixture()))
    assert client.get("/v1/access/views").status_code == 404 and client.get("/v1/access/view/0").status_code == 404


def test_serve_keeps_access_views_on_loopback(scene, tmp_path):
    from test_models_engine_api import engine_fixture

    bundle = tmp_path / "model.joblib"
    engine_fixture().bundle.save(bundle)
    path = tmp_path / "view.json.gz"
    save_view(scene["view"], path)
    with pytest.raises(SystemExit, match="--access-view requires a loopback host"):
        main(["serve", "--model", str(bundle), "--access-view", str(path), "--host", "0.0.0.0"])
