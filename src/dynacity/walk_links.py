"""New walking links (crossings, the rail bed as a walkway) and their exact effect on walking distances
(docs/rl-planning-gpu-plan.md, phase B).

A new link can change a walk only by being used, so a plan's new distance from home h to target t is

    d'(h, t) = min(d(h, t), min over end points a, b of D(h, a) + D'(a, b) + D(b, t))

where D are walks on the base network and D' are shortest walks between link end points once the plan's
links are added (Floyd-Warshall over the end points). This is exact, and its cost grows with the number
of end points, not with the network. Targets are the services (from their nearest mapped place) and the
candidate sites of an access model.

Candidate links are proposed here (`propose_crossings`, `railbed_links`) but enter plans only from a
reviewed file: each link carries its geometry, length, kind, source and the person who reviewed it.

End points that meet a way partway along it split that way. Homes, places and sites stay snapped to the
original network nodes, so splitting changes no base distance; split nodes only carry link end points.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, Point

from .access import LIMIT_M, AccessModel, Homes, Place, WalkGraph, _shortest
from .viewer import _utm_zone, lonlat_to_utm, utm_to_lonlat

SCHEMA = "dynacity-walk-links/1"
LINKS_SCHEMA = "dynacity-candidate-links/1"
BARRIER_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link"}
BARRIER_WATERWAYS = {"river", "canal"}
NODE_MERGE_M = 0.5  # end points closer than this to a node, or to each other, are the same point
ATTACH_M = 15.0  # a line end within this of a way joins it


# -- the candidate-links file ------------------------------------------------------


def link_feature(link_id: str, kind: str, coords_xy: list[tuple[float, float]], crs: str, *, source: str,
                 length_m: float | None = None, reviewed_by: str | None = None, **props: Any) -> dict:
    zone, north = _utm_zone(crs)
    lonlat = [list(map(lambda v: round(v, 7), utm_to_lonlat(x, y, zone, north))) for x, y in coords_xy]
    length = LineString(coords_xy).length if length_m is None else length_m
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": lonlat},
            "properties": {"id": link_id, "kind": kind, "length_m": round(float(length), 1), "source": source,
                           "reviewed_by": reviewed_by, **props}}


@dataclass
class Link:
    id: str
    kind: str
    xy: np.ndarray  # (points, 2) UTM metres
    length_m: float
    reviewed_by: str | None
    props: dict = field(default_factory=dict)


def read_links(collection: dict, crs: str, require_review: bool = True) -> list[Link]:
    if collection.get("schema") not in (None, LINKS_SCHEMA):
        raise ValueError(f"not a candidate-links file: schema {collection.get('schema')!r}")
    zone, north = _utm_zone(crs)
    out, seen = [], set()
    for feature in collection.get("features", []):
        p = dict(feature.get("properties") or {})
        if feature.get("geometry", {}).get("type") != "LineString":
            raise ValueError(f"link {p.get('id')!r} is not a LineString")
        missing = [k for k in ("id", "kind", "source") if not p.get(k)]
        if missing:
            raise ValueError(f"link {p.get('id')!r} lacks {missing}")
        if require_review and not p.get("reviewed_by"):
            raise ValueError(f"link {p['id']} has not been reviewed (reviewed_by is empty)")
        if p.get("rejected"):
            continue
        if p["id"] in seen:
            raise ValueError(f"duplicate link id {p['id']}")
        seen.add(p["id"])
        xy = np.array([lonlat_to_utm(lon, lat, zone, north) for lon, lat, *_ in feature["geometry"]["coordinates"]])
        length = float(p.pop("length_m", None) or LineString(xy).length)
        out.append(Link(p.pop("id"), p.pop("kind"), xy, length, p.pop("reviewed_by", None), p))
    return out


# -- end points on the network -------------------------------------------------------


@dataclass
class EndPoints:
    """The network with ways split at link end points, and each link's two end-point vertices.

    Vertices 0..n-1 are the original nodes, n..n+s-1 split nodes, and after that free end points (on no
    way, joined only through links)."""

    graph: WalkGraph  # split network: original nodes first, then split nodes
    n_original: int
    vertex: np.ndarray  # (E,) network node of each end point, or -1 when free
    xy: np.ndarray  # (E, 2)
    links: np.ndarray  # (L, 2) end-point index of each link's two ends
    length: np.ndarray  # (L,)
    snap: np.ndarray  # (E,) metres from the end point to the way it joins (0 for free points)


def _edges(graph: WalkGraph) -> np.ndarray:
    half = np.stack([graph.source, graph.target], 1)
    keep = half[:, 0] < half[:, 1]
    return half[keep], graph.length[keep]


def attach(graph: WalkGraph, links: list[Link], attach_m: float = ATTACH_M,
           free_points: set[tuple[float, float]] | None = None) -> EndPoints:
    """Join every link end to the network: to a node, or to a new node splitting the nearest way.

    An end farther than `attach_m` from any way, or within NODE_MERGE_M of a `free_points` entry, stays free.
    Ends within NODE_MERGE_M of each other are one end point."""

    edges, lengths = _edges(graph)
    segments = shapely.linestrings(np.stack([graph.xy[edges[:, 0]], graph.xy[edges[:, 1]]], 1))
    tree = shapely.STRtree(segments)
    free = np.array(sorted(free_points or []), dtype=float).reshape(-1, 2)
    points: list[np.ndarray] = []
    for link in links:
        points += [link.xy[0], link.xy[-1]]
    points = np.array(points).reshape(-1, 2)
    # merge coincident ends
    unique: list[np.ndarray] = []
    index = np.empty(len(points), dtype=int)
    for i, p in enumerate(points):
        for j, q in enumerate(unique):
            if math.dist(p, q) <= NODE_MERGE_M:
                index[i] = j
                break
        else:
            index[i] = len(unique)
            unique.append(p)
    unique = np.array(unique).reshape(-1, 2)
    E = len(unique)
    vertex = np.full(E, -1, dtype=np.int64)
    snap = np.zeros(E)
    splits: dict[int, list[tuple[float, int]]] = {}
    n = len(graph)
    new_xy: list[np.ndarray] = []
    for e, p in enumerate(unique):
        if len(free) and np.min(np.linalg.norm(free - p, axis=1)) <= NODE_MERGE_M:
            continue
        nearest = int(tree.nearest(Point(p)))
        seg = segments[nearest]
        d = seg.distance(Point(p))
        if d > attach_m:
            continue
        snap[e] = d
        t = seg.project(Point(p), normalized=True)
        u, v = edges[nearest]
        L = lengths[nearest]
        if t * L <= NODE_MERGE_M:
            vertex[e] = u
        elif (1 - t) * L <= NODE_MERGE_M:
            vertex[e] = v
        else:
            same = [node for tt, node in splits.get(nearest, []) if abs(tt - t) * L <= NODE_MERGE_M]
            if same:
                vertex[e] = same[0]
            else:
                node = n + len(new_xy)
                new_xy.append(np.asarray(seg.interpolate(t, normalized=True).coords[0]))
                splits.setdefault(nearest, []).append((t, node))
                vertex[e] = node
    # rebuild edges with the splits
    source, target, length = [], [], []
    for k, ((u, v), L) in enumerate(zip(edges, lengths)):
        chain = [(0.0, u)] + sorted(splits.get(k, [])) + [(1.0, v)]
        for (t0, a), (t1, b) in zip(chain, chain[1:]):
            d = (t1 - t0) * L
            source += [a, b]
            target += [b, a]
            length += [d, d]
    split = WalkGraph(np.vstack([graph.xy, np.array(new_xy).reshape(-1, 2)]), np.asarray(source, np.int64),
                      np.asarray(target, np.int64), np.asarray(length, float))
    pairs = index.reshape(-1, 2)
    link_length = np.array([lk.length_m for lk in links]) + snap[pairs[:, 0]] + snap[pairs[:, 1]]
    return EndPoints(split, n, vertex, unique, pairs, link_length, snap)


# -- distance blocks ---------------------------------------------------------------


def _targets(model_like_places: list[Place], services: tuple[str, ...], sites: list[dict], tree, graph: WalkGraph):
    """Rows of walking metres over the split network: one per service (from the nearest place, snap
    included) and one per site (snap included), as `build_access` computes them."""

    rows = []
    for service in services:
        own = [p for p in model_like_places if p.service == service]
        if not own:
            rows.append(np.full(len(graph), np.inf))
            continue
        snap, node = tree.query(np.array([(p.x, p.y) for p in own]))
        rows.append(_shortest(graph, list(zip(node.tolist(), snap.tolist()))))
    if sites:
        snap, node = tree.query(np.array([(s["x"], s["y"]) for s in sites]))
        for start in range(0, len(sites), 32):
            block = _shortest(graph, rows=node[start:start + 32], row_snap=snap[start:start + 32])
            rows.extend(block)
    return np.array(rows)


@dataclass
class LinkModel:
    """Distance blocks for exact walking distances once any set of candidate links is added."""

    link_ids: list[str]
    link_kind: list[str]
    links: np.ndarray  # (L, 2) end points
    length: np.ndarray  # (L,)
    end_xy: np.ndarray  # (E, 2)
    D_he: np.ndarray  # (H, E) home -> end point, home snap included
    D_et: np.ndarray  # (E, T) end point -> target (services, then sites)
    D_ee: np.ndarray  # (E, E) between end points on the base network
    targets: list[str]
    meta: dict = field(default_factory=dict)
    # snaps added after the LIMIT_M cut, as build_access does: homes always, sites too (services carry
    # their snap inside the search), so a walk is in reach when total - these <= LIMIT_M
    home_snap: np.ndarray | None = None  # (H,)
    target_snap: np.ndarray | None = None  # (T,)

    def _outside(self) -> np.ndarray:
        H, T = self.D_he.shape[0], self.D_et.shape[1]
        hs = np.zeros(H) if self.home_snap is None else self.home_snap
        ts = np.zeros(T) if self.target_snap is None else self.target_snap
        return hs[:, None] + ts[None, :]  # (H, T)

    # numpy reference, one plan at a time
    def distances(self, model: AccessModel, active: Iterable[int]) -> np.ndarray:
        """(T, H) walking metres to every target with the given links open."""

        active = list(active)
        base = np.vstack([model.base, model.site_dist])
        if not active:
            return base
        ends = sorted({int(e) for link in active for e in self.links[link]})
        pos = {e: i for i, e in enumerate(ends)}
        M = self.D_ee[np.ix_(ends, ends)].copy()
        for link in active:
            a, b = (pos[int(e)] for e in self.links[link])
            M[a, b] = M[b, a] = min(M[a, b], self.length[link])
        for k in range(len(ends)):
            M = np.minimum(M, M[:, k:k + 1] + M[k:k + 1, :])
        via = (self.D_he[:, ends][:, :, None] + M[None]).min(axis=1)  # (H, m)
        new = (via[:, :, None] + self.D_et[ends][None]).min(axis=1)  # (H, T)
        new[new - self._outside() > LIMIT_M] = np.inf  # as the base model: longer walks are out of reach
        return np.minimum(base, new.T)

    def save(self, path: str | Path) -> None:
        meta = {"schema": SCHEMA, "link_ids": self.link_ids, "link_kind": self.link_kind, "targets": self.targets,
                "meta": self.meta}
        np.savez_compressed(path, meta=np.array(json.dumps(meta)), links=self.links, length=self.length,
                            end_xy=self.end_xy, D_he=self.D_he.astype(np.float32), D_et=self.D_et.astype(np.float32),
                            D_ee=self.D_ee.astype(np.float32), home_snap=self.home_snap, target_snap=self.target_snap)

    @classmethod
    def load(cls, path: str | Path) -> LinkModel:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("schema") != SCHEMA:
                raise ValueError(f"{path} is not a build-links output")
            return cls(meta["link_ids"], meta["link_kind"], data["links"], data["length"], data["end_xy"],
                       data["D_he"].astype(float), data["D_et"].astype(float), data["D_ee"].astype(float),
                       meta["targets"], meta["meta"], data["home_snap"], data["target_snap"])


def build_links(graph: WalkGraph, homes: Homes, places: list[Place], sites: list[dict], services: tuple[str, ...],
                links: list[Link], *, free_points: set | None = None, meta: dict | None = None) -> tuple[LinkModel, EndPoints]:
    """Distance blocks for `links` on the access model built from the same graph, homes, places and sites."""

    from scipy.spatial import cKDTree

    ends = attach(graph, links, free_points=free_points)
    g = ends.graph
    tree = cKDTree(graph.xy)  # snapping to the original nodes only, as build_access
    home_snap, home_node = tree.query(homes.xy)
    E = len(ends.vertex)
    attached = np.flatnonzero(ends.vertex >= 0)
    from_ends = np.full((E, len(g)), np.inf)
    if len(attached):
        from_ends[attached] = _shortest(g, rows=ends.vertex[attached], row_snap=np.zeros(len(attached)))
    D_he = (from_ends[:, home_node] + home_snap[None]).T
    D_ee = np.full((E, E), np.inf)
    D_ee[:, attached] = from_ends[:, ends.vertex[attached]]
    np.fill_diagonal(D_ee, 0.0)
    rows = _targets(places, services, sites, tree, g)  # (T, nodes)
    D_et = np.full((E, len(rows)), np.inf)
    D_et[attached] = rows[:, ends.vertex[attached]].T
    site_snap = tree.query(np.array([(s["x"], s["y"]) for s in sites]))[0] if sites else np.zeros(0)
    target_snap = np.concatenate([np.zeros(len(services)), site_snap])
    model = LinkModel([lk.id for lk in links], [lk.kind for lk in links], ends.links, ends.length, ends.xy,
                      D_he, D_et, D_ee, list(services) + [s["id"] for s in sites],
                      {**(meta or {}), "end_points": E, "free_end_points": int((ends.vertex < 0).sum()),
                       "split_nodes": len(g) - ends.n_original,
                       "links": {k: sum(1 for lk in links if lk.kind == k) for k in sorted({lk.kind for lk in links})}},
                      home_snap, target_snap)
    return model, ends


def full_dijkstra(ends: EndPoints, homes: Homes, places: list[Place], sites: list[dict], services: tuple[str, ...],
                  active: Iterable[int], original: WalkGraph) -> np.ndarray:
    """(T, H) by plain Dijkstra on the split network with the active links added: the check on LinkModel."""

    from scipy.spatial import cKDTree

    g = ends.graph
    free = np.flatnonzero(ends.vertex < 0)
    vertex = ends.vertex.copy()
    vertex[free] = len(g) + np.arange(len(free))
    source, target, length = [g.source], [g.target], [g.length]
    for link in active:
        a, b = vertex[ends.links[link]]
        source.append(np.array([a, b])), target.append(np.array([b, a]))
        length.append(np.array([ends.length[link]] * 2))
    xy = np.vstack([g.xy, ends.xy[free]])
    augmented = WalkGraph(xy, np.concatenate(source), np.concatenate(target), np.concatenate(length))
    tree = cKDTree(original.xy)
    home_snap, home_node = tree.query(homes.xy)
    rows = _targets(places, services, sites, tree, augmented)
    return rows[:, home_node] + home_snap[None]


# -- batched on the GPU ----------------------------------------------------------------


class BatchLinks:
    """LinkModel.distances for a batch of link sets: (B, L) bool -> reach (B, T, H) at a threshold."""

    def __init__(self, links: LinkModel, model: AccessModel, device: str = "cuda"):
        import torch

        self.device = torch.device(device)
        f = lambda a: torch.as_tensor(a, dtype=torch.float32, device=self.device)  # noqa: E731
        E = len(links.end_xy)
        pad = lambda a, axis: np.concatenate([a, np.full([1 if i == axis else s for i, s in enumerate(a.shape)], np.inf)], axis)  # noqa: E731
        self.D_he = f(pad(links.D_he, 1))  # (H, E+1), last column a padding end point
        self.D_et = f(pad(links.D_et, 0))  # (E+1, T)
        D_ee = pad(pad(links.D_ee, 0), 1)
        self.D_ee = f(D_ee)
        self.ends = torch.as_tensor(links.links, device=self.device)
        self.length = f(links.length)
        self.base = f(np.vstack([model.base, model.site_dist]))  # (T, H)
        self.cut = f(links._outside() + LIMIT_M)  # (H, T): longer walks are out of reach
        self.E = E

    def distances(self, active):
        """(B, T, H) float32 metres with each plan's links open."""

        import torch

        active = torch.as_tensor(active, dtype=torch.bool, device=self.device)
        B = len(active)
        used = torch.zeros(B, self.E + 1, dtype=torch.bool, device=self.device)
        rows, cols = torch.nonzero(active, as_tuple=True)
        for side in (0, 1):
            used[rows, self.ends[cols, side]] = True
        m = max(int(used.sum(1).max()), 1)
        # active end points of each plan first, padded with the dummy end point E
        order = torch.argsort((~used).to(torch.int8), dim=1, stable=True)[:, :m]
        idx = torch.where(torch.gather(used, 1, order), order, torch.full_like(order, self.E))
        pos = torch.full((B, self.E + 1), -1, dtype=torch.long, device=self.device)
        pos.scatter_(1, idx, torch.arange(m, device=self.device).expand(B, m))
        M = self.D_ee[idx[:, :, None], idx[:, None, :]].clone()  # (B, m, m)
        a = pos[rows, self.ends[cols, 0]]
        b = pos[rows, self.ends[cols, 1]]
        L = self.length[cols]
        M[rows, a, b] = torch.minimum(M[rows, a, b], L)
        M[rows, b, a] = torch.minimum(M[rows, b, a], L)
        for k in range(m):
            M = torch.minimum(M, M[:, :, k:k + 1] + M[:, k:k + 1, :])
        Dh = self.D_he[:, idx].permute(1, 0, 2)  # (B, H, m)
        via = torch.full_like(Dh, math.inf)
        for k in range(m):
            via = torch.minimum(via, Dh[:, :, k:k + 1] + M[:, k:k + 1, :])
        Dt = self.D_et[idx]  # (B, m, T)
        out = self.base.T.expand(B, -1, -1).clone()  # (B, H, T)
        for k in range(m):
            step = via[:, :, k:k + 1] + Dt[:, k:k + 1, :]
            out = torch.minimum(out, torch.where(step > self.cut, torch.inf, step))
        return out.transpose(1, 2)


# -- proposals for review ---------------------------------------------------------------


def barriers(osm: dict, area, crs: str) -> list[tuple[LineString, str, str]]:
    """Motorways, trunk roads and rivers as lines in metres, with their kind and name."""

    zone, north = _utm_zone(crs)
    out = []
    for element in osm.get("elements", []):
        tags = element.get("tags") or {}
        kind = None
        if tags.get("highway") in BARRIER_HIGHWAYS:
            kind = tags["highway"]
        elif tags.get("waterway") in BARRIER_WATERWAYS:
            kind = "river"
        if kind is None or element.get("type") != "way":
            continue
        pts = [lonlat_to_utm(p["lon"], p["lat"], zone, north) for p in element.get("geometry") or [] if p]
        if len(pts) > 1:
            line = LineString(pts)
            if line.intersects(area):
                out.append((line, kind, tags.get("name:en") or tags.get("name") or ""))
    return out


def propose_crossings(graph: WalkGraph, barrier_lines: list[tuple[LineString, str, str]], area, *,
                      max_gap_m: float = 60.0, min_gap_m: float = 8.0, ratio: float = 3.0,
                      spacing_m: float = 100.0, clear_m: float = 3.0, network_limit_m: float = 2000.0) -> list[dict]:
    """Places where walkable nodes on two sides of a barrier are close in a straight line but far apart along
    the network: a straight link would cut the walk by `ratio` or more. One per `spacing_m`, biggest saving
    first; walks over `network_limit_m` (or none at all) count as that long. Proposals only: a person keeps
    or drops each one."""

    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    from .access import _csr

    if not barrier_lines:
        return []
    lines = [b[0] for b in barrier_lines]
    btree = shapely.STRtree(lines)
    inside = shapely.contains_xy(area, graph.xy[:, 0], graph.xy[:, 1])
    # nodes on a barrier way are excluded as ends (trunk roads are walkable, so many are network nodes)
    clear = btree.query_nearest(shapely.points(graph.xy), max_distance=clear_m, all_matches=False)[0]
    usable = inside.copy()
    usable[clear] = False
    pairs = cKDTree(graph.xy).query_pairs(max_gap_m, output_type="ndarray")
    pairs = pairs[usable[pairs[:, 0]] & usable[pairs[:, 1]]]
    gap = np.linalg.norm(graph.xy[pairs[:, 0]] - graph.xy[pairs[:, 1]], axis=1)
    pairs, gap = pairs[gap >= min_gap_m], gap[gap >= min_gap_m]
    segs = shapely.linestrings(np.stack([graph.xy[pairs[:, 0]], graph.xy[pairs[:, 1]]], 1))
    hit_seg, hit_line = btree.query(segs, predicate="intersects")
    crossing = np.zeros(len(pairs), dtype=bool)
    crossing[hit_seg] = True
    # crossing a walkable network edge on the way means the segment is not a clean straight link
    first_barrier = {}
    for s, l in zip(hit_seg, hit_line):
        first_barrier.setdefault(int(s), int(l))
    pairs, gap, segs = pairs[crossing], gap[crossing], segs[crossing]
    keep_idx = np.flatnonzero(crossing)
    n = len(graph)
    matrix = _csr(graph.source, graph.target, graph.length, n)
    sources = np.unique(pairs[:, 0])
    network = np.full(len(pairs), np.inf)
    src = np.searchsorted(sources, pairs[:, 0])
    for start in range(0, len(sources), 512):
        block = sources[start:start + 512]
        d = dijkstra(matrix, directed=True, indices=block, limit=network_limit_m)
        sel = (src >= start) & (src < start + len(block))
        network[sel] = d[src[sel] - start, pairs[sel, 1]]
    good = network > ratio * gap
    candidates = []
    for j in np.flatnonzero(good):
        line, kind, name = barrier_lines[first_barrier[int(keep_idx[j])]]
        a, b = pairs[j]
        saved = (min(network[j], network_limit_m) - gap[j])
        candidates.append({"a": int(a), "b": int(b), "gap_m": float(gap[j]),
                           "network_m": None if not math.isfinite(network[j]) else float(network[j]),
                           "saved_at_least_m": float(saved), "barrier": kind, "barrier_name": name,
                           "mid": (graph.xy[a] + graph.xy[b]) / 2})
    candidates.sort(key=lambda c: (-c["saved_at_least_m"], c["gap_m"]))
    chosen: list[dict] = []
    for c in candidates:
        if all(math.dist(c["mid"], k["mid"]) >= spacing_m for k in chosen):
            chosen.append(c)
    return chosen


def railbed_links(corridor_lines_xy: list[np.ndarray], graph: WalkGraph, piece_m: float = 100.0,
                  attach_m: float = ATTACH_M) -> tuple[list[dict], set]:
    """The corridor line as a walkway: cut where it crosses a walkable way (where people get on and off) and
    at the rail-bed site boundaries (as `sites_from_line`), each piece tied to its rail-bed site. Returns
    the pieces (as link dicts in metres) and the cut points that join no way, to keep free."""

    edges, _ = _edges(graph)
    segments = shapely.linestrings(np.stack([graph.xy[edges[:, 0]], graph.xy[edges[:, 1]]], 1))
    tree = shapely.STRtree(segments)
    out, free = [], set()
    for k, xy in enumerate(corridor_lines_xy):
        line = LineString(xy)
        pieces = max(1, round(line.length / piece_m))
        cuts = {i / pieces: "site" for i in range(pieces + 1)}
        for s in tree.query(line, predicate="intersects"):
            inter = line.intersection(segments[s])
            for p in getattr(inter, "geoms", [inter]):
                if p.geom_type == "Point":
                    cuts[line.project(p, normalized=True)] = "street"
        order = sorted(cuts)
        for t0, t1 in zip(order, order[1:]):
            if (t1 - t0) * line.length < NODE_MERGE_M:
                continue
            piece = shapely.ops.substring(line, t0, t1, normalized=True)
            site = min(int(((t0 + t1) / 2) * pieces), pieces - 1)
            out.append({"line": k, "xy": np.asarray(piece.coords), "site": f"railbed/{k}-{site}",
                        "ends": (cuts[t0], cuts[t1])})
            for t, why in ((t0, cuts[t0]), (t1, cuts[t1])):
                p = line.interpolate(t, normalized=True)
                if why == "site" and t not in (0.0, 1.0):
                    free.add((round(p.x, 1), round(p.y, 1)))
                elif why == "site" and segments[int(tree.nearest(p))].distance(p) > attach_m:
                    free.add((round(p.x, 1), round(p.y, 1)))
    return out, free
