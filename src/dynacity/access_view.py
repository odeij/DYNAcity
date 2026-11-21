"""The planning graph behind the corridor access results, for the 3D viewer's Access plans lens.

A view bundle is built once per site set (`dynacity build-access-view`) and served privately by
`dynacity serve --access-view`. It is a graph:
- nodes: homes, the mapped services, the candidate sites, and the reviewed walking links as hubs;
- edges: from each home to the nearest provider (a mapped place or a plan's site) that serves it
  within the threshold, for each service and each scenario (the baseline and every plan in a
  joint-search result). An edge passes through a link when the home's shortest walk uses one.

Edges come from one multi-source Dijkstra per scenario and service, with predecessors, on the network
with the scenario's links added (as `walk_links.full_dijkstra` builds it). Before the bundle is
written, every home's distance is checked against `LinkModel.distances` (the numbers the scorers use)
and every scenario's shares against the search result. Which homes count as served is the scorer's
decision, so the picture shows the same numbers as the results.

Home positions come from the Beirut 001 model, which names no licence for web serving: the bundle is
served only on a loopback host and never cached. It carries no building, parcel or OSM ids.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .access import LIMIT_M, AccessModel, Homes, Place, WalkGraph, _csr
from .viewer import _utm_zone, utm_to_lonlat
from .walk_links import EndPoints, Link, LinkModel, augmented_graph

SCHEMA = "dynacity-access-view/1"
TOLERANCE_M = 1e-3  # agreement with LinkModel.distances, whose blocks are stored as float32
SHARE_TOLERANCE = 6e-5  # the search results round shares to 4 decimals
FREE_STATES = ("empty_or_parking", "stalled_or_cancelled", "vacant_or_evicted")
SEARCH_MARGIN_M = 20.0  # the Dijkstra runs a little past the threshold, so boundary homes keep their edge


@dataclass
class Edges:
    provider: np.ndarray  # (H,) provider index; -1 when nothing is within reach
    via: np.ndarray  # (H,) the link nearest the home on its walk; -1 when the walk uses none
    distance: np.ndarray  # (H,) metres to the nearest provider, snaps included


def link_keys(graph: WalkGraph, vertex: np.ndarray, ends: EndPoints, active: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Sorted directed edge keys (a * (n + 1) + b) of the active links, and the link of each key.

    A link whose two ends already share a network edge at least as short is left out: a walk along that
    pair needs no link."""

    n = len(graph)
    network = _csr(ends.graph.source, ends.graph.target, ends.graph.length, n)
    keys, owner = [], []
    for link in active:
        a, b = (int(v) for v in vertex[ends.links[link]])
        if a == b:
            continue
        existing = network[a, b] if max(a, b) < network.shape[0] else 0.0
        if existing and existing <= ends.length[link]:
            continue
        keys += [a * (n + 1) + b, b * (n + 1) + a]
        owner += [link, link]
    keys, owner = np.asarray(keys, np.int64), np.asarray(owner, np.int64)
    order = np.argsort(keys, kind="stable")
    return keys[order], owner[order]


def nearest_providers(graph: WalkGraph, nodes, snaps, ids, home_node: np.ndarray, home_snap: np.ndarray,
                      threshold: float, keys: tuple[np.ndarray, np.ndarray] | None = None) -> Edges:
    """Each home's nearest provider within `threshold` (+ a margin), and the link nearest the home on that walk.

    Providers are (node, snap metres, id); several at one node keep the one with the shortest snap, as a
    virtual origin joined to each node does in `access._shortest`."""

    from scipy.sparse.csgraph import dijkstra

    H = len(home_node)
    if len(nodes) == 0:
        return Edges(np.full(H, -1, np.int64), np.full(H, -1, np.int64), np.full(H, np.inf))
    n, origin = len(graph), len(graph)
    nodes = np.asarray(nodes, np.int64)
    snaps = np.maximum(np.asarray(snaps, float), 1e-6)
    ids = np.asarray(ids, np.int64)
    order = np.lexsort((ids, snaps, nodes))
    nodes, snaps, ids = nodes[order], snaps[order], ids[order]
    first = np.ones(len(nodes), bool)
    first[1:] = nodes[1:] != nodes[:-1]
    at = np.full(n, -1, np.int64)
    at[nodes[first]] = ids[first]
    matrix = _csr(np.concatenate([graph.source, np.full(int(first.sum()), origin)]),
                  np.concatenate([graph.target, nodes[first]]),
                  np.concatenate([graph.length, snaps[first]]), n + 1)
    dist, pred = dijkstra(matrix, directed=True, indices=origin, limit=min(LIMIT_M, threshold + SEARCH_MARGIN_M),
                          return_predecessors=True)
    distance = dist[home_node] + home_snap
    provider = np.full(H, -1, np.int64)
    via = np.full(H, -1, np.int64)
    key, owner = keys if keys is not None else (np.zeros(0, np.int64), np.zeros(0, np.int64))
    cur = np.asarray(home_node, np.int64).copy()
    todo = distance <= threshold + SEARCH_MARGIN_M / 2
    for _ in range(n + 2):
        if not todo.any():
            break
        idx = np.flatnonzero(todo)
        p = pred[cur[idx]]
        done = p == origin
        provider[idx[done]] = at[cur[idx[done]]]
        todo[idx[done]] = False
        step, p = idx[~done], p[~done]
        if len(key) and len(step):
            open_ = via[step] < 0
            k = p[open_] * (n + 1) + cur[step[open_]]
            pos = np.minimum(np.searchsorted(key, k), len(key) - 1)
            hit = key[pos] == k
            via[step[open_][hit]] = owner[pos[hit]]
        cur[step] = p
    else:
        raise RuntimeError("a walk did not lead back to a provider")
    return Edges(provider, via, distance)


def _lonlat(xy: np.ndarray, crs: str, digits: int = 6) -> list[list[float]]:
    zone, north = _utm_zone(crs)
    return [[round(v, digits) for v in utm_to_lonlat(float(x), float(y), zone, north)] for x, y in np.asarray(xy).reshape(-1, 2)]


def _site_kind(site: dict) -> str:
    sid = site["id"]
    for prefix, kind in (("bbed/", "bbed"), ("osm/", "parking"), ("railbed/", "railbed"), ("station/", "station")):
        if sid.startswith(prefix):
            return kind
    return "other"


def _key(method: str, budget: int, link_budget: int, alpha: float) -> str:
    return f"{method}|{int(budget)}|{int(link_budget)}|{float(alpha):g}"


def forecast_section(forecaster: Any, snapshot: dict, area: Any, site_ids: list[str], crs: str) -> dict | None:
    """BBED buildings in the planning area that are not yet sites, and per state the chance of being empty,
    stalled or vacant after 2, 4 and 6 years, from the 2-year transition table (P, P², P³, as the
    forecast engine steps). Only an empirical Markov table qualifies; it gives every building in a state
    the same chance, which the page says."""

    from shapely.geometry import Point

    from .models import LABELS

    matrices = getattr(forecaster, "_matrices", None)
    if not matrices or 2 not in matrices:
        return None
    P = np.array([matrices[2][s] for s in LABELS])
    free = [LABELS.index(s) for s in FREE_STATES]
    chance, step = {}, np.eye(len(LABELS))
    for years in (2, 4, 6):
        step = step @ P
        for i, state in enumerate(LABELS):
            chance.setdefault(state, []).append(round(float(step[i, free].sum()), 4))
    taken = {sid.split("/", 1)[1] for sid in site_ids if sid.startswith("bbed/")}
    buildings = []
    for obj in snapshot.get("objects", []):
        x, y = obj.get("centroid_x_m"), obj.get("centroid_y_m")
        objectid = str(obj.get("object_id", "")).rpartition("OBJECTID:")[2]
        if x is None or y is None or obj.get("state") in FREE_STATES or objectid in taken:
            continue
        if not area.contains(Point(x, y)):
            continue
        buildings.append({"lonlat": _lonlat([[x, y]], crs)[0], "state": obj.get("state")})
    return {"model": getattr(forecaster, "model_version", type(forecaster).__name__), "years": [2, 4, 6],
            "free_states": list(FREE_STATES), "chance": chance, "buildings": buildings,
            "note": "Every building in a state gets the same chance: the table has no building covariates."}


def build_access_view(*, model: AccessModel, link_model: LinkModel, links: list[Link], ends: EndPoints,
                      original: WalkGraph, places: list[Place], joint: dict, crs: str, label: str,
                      forecast: dict | None = None, log: Callable[[str], None] = print) -> dict:
    """The view bundle for one site set (see the module docstring). Raises ValueError when a home's
    distance disagrees with `LinkModel.distances` or a scenario's shares with the search result."""

    from scipy.spatial import cKDTree

    from .access_joint import link_groups

    if link_model.link_ids != [lk.id for lk in links]:
        raise ValueError("the link model was not built from these links")
    if link_model.targets != list(model.services) + list(model.site_ids):
        raise ValueError("the link model was not built on this access model")
    if joint.get("sites") != len(model.site_ids):
        raise ValueError(f"the joint result has {joint.get('sites')} sites, the access model {len(model.site_ids)}")
    groups, group_names = link_groups(link_model)
    if list(joint.get("groups", group_names)) != group_names:
        raise ValueError("the joint result's link groups differ from the link model's")
    threshold = float(joint.get("threshold_m", model.thresholds[0]))
    uses = list(joint["uses"])
    S = len(model.services)
    tree = cKDTree(original.xy)  # snapped to the original nodes only, as build_access
    home_snap, home_node = tree.query(model.home_xy)
    weight = model.residents / model.residents.sum()

    # providers: mapped places of the search uses, then candidate sites
    kept = [p for p in places if p.service in uses]
    place_snap, place_node = tree.query(np.array([(p.x, p.y) for p in kept])) if kept else (np.zeros(0), np.zeros(0, int))
    site_snap, site_node = tree.query(np.array([(s["x"], s["y"]) for s in model.sites]))
    P = len(kept)
    place_use = np.array([uses.index(p.service) for p in kept], dtype=np.int64)

    def providers(use: str, plan: dict[int, str]):
        mine = np.flatnonzero(place_use == uses.index(use))
        chosen = np.array([s for s, u in plan.items() if u == use], dtype=np.int64)
        return (np.concatenate([place_node[mine], site_node[chosen]]),
                np.concatenate([place_snap[mine], site_snap[chosen]]),
                np.concatenate([mine, P + chosen]))

    site_index = {sid: i for i, sid in enumerate(model.site_ids)}
    members = {name: g for name, g in zip(group_names, groups)}

    def scenario(plan_uses: dict[str, str], group_list: list[str], expect: dict | None, key: str):
        plan = {site_index[sid]: use for sid, use in plan_uses.items()}
        active = sorted(i for name in group_list for i in members[name])
        graph, vertex = augmented_graph(ends, active)
        keys = link_keys(graph, vertex, ends, active)
        ref = link_model.distances(model, active)
        no_links = model.distances(plan_uses)
        out, worst, shares = {}, 0.0, {}
        for use in uses:
            k = model.services.index(use)
            d_ref = ref[k].copy()
            for s, u in plan.items():
                if u == use:
                    d_ref = np.minimum(d_ref, ref[S + s])
            nodes, snaps, ids = providers(use, plan)
            edges = nearest_providers(graph, nodes, snaps, ids, home_node, home_snap, threshold, keys)
            near = np.minimum(d_ref, edges.distance) <= threshold + 1.0
            if near.any():
                diff = np.abs(edges.distance[near] - d_ref[near])
                worst = max(worst, float(np.nan_to_num(diff, nan=np.inf).max()))
            served = d_ref <= threshold
            if (served & (edges.provider < 0)).any():
                raise ValueError(f"{key} {use}: a served home has no provider within reach")
            provider = np.where(served, edges.provider, -1)
            via = np.where(served, edges.via, -1)
            needs = served & ~(no_links[k] <= threshold)
            out[use] = (provider, via, needs)
            shares[use] = float(weight @ served)
        if worst > TOLERANCE_M:
            raise ValueError(f"{key}: home distances differ from LinkModel.distances by {worst:.4f} m")
        if expect is not None:
            gap = max(abs(shares[u] - float(expect[u])) for u in uses if u in expect)
            if gap > SHARE_TOLERANCE:
                raise ValueError(f"{key}: shares differ from the search result by {gap:.6f}")
        return out, worst, shares, plan

    base, worst_all, _, _ = scenario({}, [], joint.get("baseline_residents_within"), "baseline")
    scenarios: dict[str, Any] = {}
    results = [r for r in joint["results"] if r["method"] in ("exact", "exact_no_links", "greedy", "nsga2")]
    for n, r in enumerate(results, 1):
        key = _key(r["method"], r["budget"], r["link_budget"], r["alpha"])
        edges, worst, shares, plan = scenario(r.get("uses", {}), r.get("links", []), r.get("residents_within"), key)
        worst_all = max(worst_all, worst)
        diff = {}
        for use in uses:
            provider, via, needs = edges[use]
            bp, bv, _ = base[use]
            rows = np.flatnonzero((provider != bp) | (via != bv) | needs)
            flat = np.stack([rows, provider[rows], via[rows], needs[rows].astype(np.int64)], 1).reshape(-1)
            diff[use] = flat.tolist()
        scenarios[key] = {"method": r["method"], "budget": r["budget"], "link_budget": r["link_budget"],
                          "alpha": r["alpha"], "uses": sorted([s, uses.index(u)] for s, u in plan.items()),
                          "links": list(r.get("links", [])), "objective": round(float(r["objective"]), 4),
                          "gap_pct": r.get("gap_pct"), "optimal": r.get("optimal"),
                          "residents_within": {u: round(shares[u], 4) for u in uses}, "edges": diff}
        if n % 40 == 0:
            log(f"  {n} of {len(results)} scenarios")

    # the story: the best ten-site plan, the same with two links, and the case greedy misses most
    def pick(method, N, K, alpha):
        key = _key(method, N, K, alpha)
        return key if key in scenarios else None

    no_link_keys = [k for k, s in scenarios.items() if s["method"] == "exact_no_links"]
    plan_key = pick("exact_no_links", 10, 0, 1.0) or (no_link_keys[0] if no_link_keys else None)
    plan_s = scenarios.get(plan_key) if plan_key else None
    links_key = None
    if plan_s:
        links_key = pick("exact", plan_s["budget"], 2, plan_s["alpha"]) or pick("exact", plan_s["budget"], 1, plan_s["alpha"])
    greedy = [s for s in scenarios.values() if s["method"] == "greedy" and s["link_budget"] > 0 and s.get("gap_pct") is not None]
    methods = None
    if greedy:
        w = max(greedy, key=lambda s: s["gap_pct"])
        methods = {m: pick(m, w["budget"], w["link_budget"], w["alpha"]) for m in ("exact", "greedy", "nsga2")}

    summary = {}
    for m in ("greedy", "nsga2"):
        gaps = [s["gap_pct"] for s in scenarios.values() if s["method"] == m and s.get("gap_pct") is not None]
        if gaps:
            summary[m] = {"cases": len(gaps), "median": round(float(np.median(gaps)), 3),
                          "worst": round(float(max(gaps)), 3), "over_1pct": int(sum(g > 1 for g in gaps)),
                          "at_optimum": int(sum(g < 1e-3 for g in gaps))}
    exact = [s for s in scenarios.values() if s["method"] == "exact"]
    summary["exact"] = {"cases": len(exact), "all_optimal": all(s.get("optimal") for s in exact)}
    group_of = {i: g for g, m in enumerate(groups) for i in m}

    def midpoint(xy: np.ndarray) -> np.ndarray:
        from shapely.geometry import LineString

        line = LineString(xy)
        return np.asarray(line.interpolate(0.5, normalized=True).coords[0])

    inert = set(joint.get("inert_groups", []))
    bundle = {
        "schema": SCHEMA, "label": label, "threshold_m": threshold, "uses": uses,
        "homes": {"lonlat": [v for p in _lonlat(model.home_xy, crs) for v in p],
                  "residents": [round(float(r), 1) for r in model.residents],
                  "total_residents": round(float(model.residents.sum()), 1)},
        "places": [{"lonlat": ll, "use": int(u), "name": p.name or "", "source": p.source}
                   for ll, u, p in zip(_lonlat(np.array([(p.x, p.y) for p in kept]), crs), place_use, kept)],
        "sites": [{"lonlat": ll, "kind": _site_kind(s), "state": s.get("state"),
                   "area_m2": round(float(s["area_m2"])) if s.get("area_m2") is not None else None,
                   "length_m": round(float(s["length_m"])) if s.get("length_m") is not None else None,
                   "allowed": [uses.index(u) for u in s.get("allowed", []) if u in uses]}
                  for ll, s in zip(_lonlat(np.array([(s["x"], s["y"]) for s in model.sites]), crs), model.sites)],
        "links": [{"group": group_of[i], "kind": lk.kind, "path": _lonlat(lk.xy, crs),
                   "mid": _lonlat([midpoint(lk.xy)], crs)[0], "length_m": round(float(lk.length_m), 1),
                   "name": lk.props.get("barrier_name") or "", "saved_m": lk.props.get("saved_at_least_m")}
                  for i, lk in enumerate(links)],
        "groups": [{"name": name, "inert": name in inert, "links": list(map(int, g))} for name, g in zip(group_names, groups)],
        "patterns": joint.get("patterns"),
        "search_space": [np.flatnonzero(model.site_dist[s] <= threshold).tolist() for s in range(len(model.site_ids))],
        "baseline": {"residents_within": {u: round(float(weight @ (base[u][0] >= 0)), 4) for u in uses},
                     "provider": {u: base[u][0].tolist() for u in uses}, "via": {u: base[u][1].tolist() for u in uses}},
        "scenarios": scenarios,
        "summary": {**summary, "site_use_choices": int(sum(len([u for u in s.get("allowed", []) if u in uses]) for s in model.sites)),
                    "sites": len(model.site_ids), "link_groups": len(groups), "inert_groups": sorted(inert),
                    "patterns": joint.get("patterns")},
        "story": {"plan": plan_key, "links": links_key, "methods": methods},
        "forecast": forecast,
        "check": {"max_abs_diff_m": round(worst_all, 6), "tolerance_m": TOLERANCE_M,
                  "scenarios": len(scenarios) + 1, "share_tolerance": SHARE_TOLERANCE},
    }
    return bundle


def save_view(bundle: dict, path: str | Path) -> int:
    data = gzip.compress(json.dumps(bundle, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), mtime=0)
    Path(path).write_bytes(data)
    return len(data)


def load_view(path: str | Path) -> tuple[str, bytes]:
    """(label, gzip bytes) of a saved bundle, checked to be one."""

    data = Path(path).read_bytes()
    try:
        bundle = json.loads(gzip.decompress(data))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path} is not a gzip JSON access view: {error}") from None
    if bundle.get("schema") != SCHEMA:
        raise ValueError(f"{path} is not a build-access-view output (schema {bundle.get('schema')!r})")
    return str(bundle.get("label") or Path(path).stem), data
