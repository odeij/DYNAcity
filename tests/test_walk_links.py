import numpy as np
import pytest
from shapely.geometry import LineString, box

from dynacity.access import Homes, Place, WalkGraph, _shortest, build_access
from dynacity.walk_links import (
    Link, attach, build_links, full_dijkstra, link_feature, propose_crossings, railbed_links, read_links,
)

CRS = "EPSG:32636"
X0, Y0 = 733000.0, 3752000.0


def graph_from(edges, xy):
    source, target, length = [], [], []
    for a, b in edges:
        d = float(np.hypot(*(np.subtract(xy[a], xy[b]))))
        source += [a, b]
        target += [b, a]
        length += [d, d]
    return WalkGraph(np.asarray(xy, float), np.array(source), np.array(target), np.array(length))


def two_banks():
    """Two streets 40 m apart (a river between), joined only by a bridge at the far east end."""

    xy = [(X0 + 100 * i, Y0) for i in range(11)] + [(X0 + 100 * i, Y0 + 40) for i in range(11)]
    edges = [(i, i + 1) for i in range(10)] + [(11 + i, 12 + i) for i in range(10)] + [(10, 21)]
    return graph_from(edges, xy)


def setup():
    g = two_banks()
    homes = Homes(np.array(["h0", "h1", "h2"]), np.array([(X0, Y0 - 10), (X0 + 500, Y0 - 5), (X0 + 950, Y0 + 50)]),
                  np.array([10.0, 20.0, 5.0]))
    places = [Place("p0", "medical", X0 + 5, Y0 + 45, "test", ""), Place("p1", "parks", X0 + 900, Y0 - 3, "test", "")]
    sites = [{"id": "s0", "x": X0 + 300, "y": Y0 + 42, "allowed": ["medical", "parks"]},
             {"id": "s1", "x": X0 + 700, "y": Y0 - 2, "allowed": ["parks"]}]
    model = build_access(g, homes, places, sites, services=("medical", "parks"), score_services=("medical",))
    return g, homes, places, sites, model


def link(id_, a, b, kind="crossing", **props):
    xy = np.array([a, b], float)
    return Link(id_, kind, xy, float(np.hypot(*(xy[1] - xy[0]))), "reviewer", props)


def test_repeated_edges_keep_the_shorter_length():
    g = graph_from([(0, 1)], [(0, 0), (100, 0)])
    doubled = WalkGraph(g.xy, np.concatenate([g.source, [0, 1]]), np.concatenate([g.target, [1, 0]]),
                        np.concatenate([g.length, [30.0, 30.0]]))
    assert _shortest(doubled, [(0, 0.0)])[1] == pytest.approx(30.0), "not 100 + 30"
    assert _shortest(doubled, [(0, 5.0), (0, 7.0)])[1] == pytest.approx(35.0), "two places on one node: the nearer snap"


def test_ends_split_ways_and_stay_free_when_far():
    g = two_banks()
    links = [link("a", (X0 + 250, Y0), (X0 + 250, Y0 + 40)),  # mid-way on both streets: two splits
             link("b", (X0 + 300, Y0), (X0 + 300, Y0 + 200))]  # onto a node, and nowhere
    ends = attach(g, links)
    assert len(ends.graph) == len(g) + 2
    assert ends.vertex[ends.links[1, 0]] == 3 and ends.vertex[ends.links[1, 1]] == -1
    # splitting keeps every original walk
    before = _shortest(g, [(0, 0.0)])
    after = _shortest(ends.graph, [(0, 0.0)])[:len(g)]
    assert np.allclose(before, after)


@pytest.mark.parametrize("active", [[], [0], [1], [0, 1], [0, 1, 2], [2, 3]])
def test_link_distances_match_full_dijkstra(active):
    g, homes, places, sites, model = setup()
    links = [link("bridge-west", (X0 + 50, Y0), (X0 + 50, Y0 + 40)),
             link("bridge-mid", (X0 + 500, Y0), (X0 + 500, Y0 + 40)),
             link("walk-1", (X0 + 200, Y0 + 40), (X0 + 260, Y0 + 120), kind="railbed_walkway"),
             link("walk-2", (X0 + 260, Y0 + 120), (X0 + 320, Y0 + 40), kind="railbed_walkway")]
    blocks, ends = build_links(g, homes, places, sites, model.services, links)
    fast = blocks.distances(model, active)
    slow = full_dijkstra(ends, homes, places, sites, model.services, active, g)
    assert np.array_equal(np.isfinite(fast), np.isfinite(slow))
    ok = np.isfinite(fast)
    assert np.abs(fast[ok] - slow[ok]).max() < 1e-6
    if 0 in active:
        # h0 at the west end now crosses straight to medical care
        assert fast[0, 0] < model.base[0, 0] - 500


def test_chained_free_links_join_through_their_shared_end():
    g, homes, places, sites, model = setup()
    mid = (X0 + 600, Y0 + 20)  # between the banks, on no way
    links = [link("half-1", (X0 + 600, Y0), mid), link("half-2", mid, (X0 + 600, Y0 + 40))]
    blocks, ends = build_links(g, homes, places, sites, model.services, links, free_points={mid})
    assert blocks.meta["free_end_points"] == 1
    one = blocks.distances(model, [0])
    both = blocks.distances(model, [0, 1])
    base = np.vstack([model.base, model.site_dist])
    assert np.allclose(one, base), "half a bridge changes nothing"
    assert (both < base - 1).any()
    assert np.allclose(both, full_dijkstra(ends, homes, places, sites, model.services, [0, 1], g))


def test_batched_distances_match_the_reference():
    torch = pytest.importorskip("torch")
    from dynacity.walk_links import BatchLinks

    g, homes, places, sites, model = setup()
    links = [link(f"l{i}", (X0 + 50 + 100 * i, Y0), (X0 + 50 + 100 * i, Y0 + 40)) for i in range(6)]
    blocks, _ = build_links(g, homes, places, sites, model.services, links)
    rng = np.random.default_rng(0)
    active = rng.random((40, 6)) < 0.3
    active[0] = False
    out = BatchLinks(blocks, model, "cpu").distances(torch.as_tensor(active)).numpy()
    for row in range(40):
        ref = blocks.distances(model, np.flatnonzero(active[row]))
        assert np.array_equal(np.isfinite(ref), np.isfinite(out[row]))
        ok = np.isfinite(ref)
        assert np.abs(ref[ok] - out[row][ok]).max() < 1e-3


def test_link_blocks_round_trip(tmp_path):
    g, homes, places, sites, model = setup()
    blocks, _ = build_links(g, homes, places, sites, model.services, [link("x", (X0 + 50, Y0), (X0 + 50, Y0 + 40))])
    blocks.save(tmp_path / "links.npz")
    from dynacity.walk_links import LinkModel

    again = LinkModel.load(tmp_path / "links.npz")
    assert np.allclose(again.distances(model, [0]), blocks.distances(model, [0]), atol=1e-3)


def test_crossings_are_proposed_where_the_river_forces_a_detour():
    g = two_banks()
    river = [(LineString([(X0 - 50, Y0 + 20), (X0 + 1050, Y0 + 20)]), "river", "Test River")]
    found = propose_crossings(g, river, box(X0 - 100, Y0 - 100, X0 + 1100, Y0 + 140), spacing_m=200)
    assert found and all(c["barrier"] == "river" for c in found)
    assert found[0]["gap_m"] == pytest.approx(40)
    assert found[0]["network_m"] is None, "2,040 m around: over the 2 km search, recorded as unknown"
    assert all(c["network_m"] is None or c["network_m"] > 3 * 40 for c in found)
    xs = sorted(g.xy[c["a"]][0] - X0 for c in found)
    assert xs[0] == pytest.approx(0) and all(b - a >= 200 for a, b in zip(xs, xs[1:])), "biggest detour first, spaced"
    assert all(g.xy[c["a"]][0] - X0 < 900 for c in found), "near the bridge the walk is short"


def test_railbed_pieces_are_cut_at_streets_and_tied_to_sites():
    g = graph_from([(0, 1)], [(X0 + 150, Y0 - 50), (X0 + 150, Y0 + 50)])  # one street across the line
    line = np.array([(X0, Y0), (X0 + 300, Y0)])
    pieces, free = railbed_links([line], g, piece_m=100)
    cuts = sorted({round(p["xy"][0][0] - X0) for p in pieces} | {round(p["xy"][-1][0] - X0) for p in pieces})
    assert cuts == [0, 100, 150, 200, 300]
    assert [p["site"] for p in pieces] == ["railbed/0-0", "railbed/0-1", "railbed/0-1", "railbed/0-2"]
    assert (round(X0 + 150, 1), round(Y0, 1)) not in free and len(free) == 4, "the street cut joins; the rest are free"


def test_only_reviewed_links_are_read():
    feature = link_feature("crossing/0", "crossing", [(X0, Y0), (X0, Y0 + 40)], CRS, source="test")
    collection = {"type": "FeatureCollection", "features": [feature]}
    with pytest.raises(ValueError, match="not been reviewed"):
        read_links(collection, CRS)
    assert len(read_links(collection, CRS, require_review=False)) == 1
    feature["properties"]["reviewed_by"] = "a planner"
    (lk,) = read_links(collection, CRS)
    assert lk.length_m == pytest.approx(40, abs=0.5) and lk.reviewed_by == "a planner"
    feature["properties"]["rejected"] = True
    assert read_links(collection, CRS) == []


def test_build_access_and_build_links_run_from_the_command_line(tmp_path, monkeypatch):
    """Everything after input assembly, for both commands: report, warnings, link blocks and the check."""

    import json

    from dynacity import cli
    from dynacity.corridor import Corridor

    g, homes, places, sites, _ = setup()
    line = tmp_path / "corridor.geojson"
    line.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                    "geometry": {"type": "LineString", "coordinates": [[35.5, 33.89], [35.51, 33.89]]}}]}))
    corridor = Corridor.load(line, CRS, 250.0)

    def inputs(args):
        return {"crs": CRS, "corridor": corridor, "reach_m": 1170.0, "reach": corridor.area.buffer(1170),
                "graph": g, "facilities": None, "extracts": [], "places": places, "homes": homes,
                "sites": [dict(s, allowed_source="not yet known") for s in sites],
                "size_rules": {"parks": {"m2": 383, "source": "test"}}, "rules": {"parks": 383}, "removed": 0}

    monkeypatch.setattr(cli, "_access_inputs", inputs)
    common = ["--corridor", str(line), "--network", "unused.json", "--beirut001", "unused.npz",
              "--score-service", "medical"]
    model = tmp_path / "access.npz"
    cli.main(["build-access", *common, "--output", str(model)])
    report = json.loads(model.with_suffix(".report.json").read_text())
    assert report["sites"]["count"] == 2 and any("Minimum site sizes" in w for w in report["warnings"])
    links = tmp_path / "links.geojson"
    links.write_text(json.dumps({"type": "FeatureCollection", "features": [
        link_feature("crossing/0", "crossing", [(X0 + 50, Y0), (X0 + 50, Y0 + 40)], CRS, source="test")]}))
    out = tmp_path / "blocks.npz"
    with pytest.raises(SystemExit, match="not been reviewed"):
        cli.main(["build-links", *common, "--access", str(model), "--links", str(links), "--output", str(out)])
    cli.main(["build-links", *common, "--access", str(model), "--links", str(links), "--allow-unreviewed",
              "--check", "5", "--output", str(out)])
    check = json.loads(out.with_suffix(".report.json").read_text())["check"]
    assert check["max_abs_diff_m_vs_dijkstra"] < 1e-3 and check["sets_that_change_a_walk"] == 5
