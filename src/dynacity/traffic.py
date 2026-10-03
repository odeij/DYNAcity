"""Street traffic: an OpenStreetMap road graph with observed speeds, and what disasters do to it.

`build_traffic_model` joins two open sources over the snapshot's extent:

- the drivable OpenStreetMap ways (fetched from Overpass, or a saved Overpass
  JSON), split at shared nodes into a directed road graph in the snapshot CRS;
- the Tari'ak Lebanon traffic dataset: about six million crowd-sourced vehicle
  speeds map-matched to OSM ways, 2014-2019. Each way gets a median speed per
  hour of the day; ways with too few readings borrow their road class's hourly
  profile and are flagged `estimated`.

The result is a small JSON file kept under `DYNACITY_DATA_ROOT`.

What it can and cannot say. Tari'ak records *speed*, not how many vehicles
there are, so congestion comes from the data but volume does not: the number
of probe readings on a way stands in for how busy it is when trips are
sampled. The speeds also predate the 2019 crisis and the 2020 port blast, so
they are "typical pre-2020 conditions", not today's traffic.

Disasters act on roads through `road_disruption`:

- rubble: a building the hazard is likely to destroy blocks the streets within
  reach of its collapse (half its height plus half its footprint width); a road
  is closed when it is more likely blocked than not, and slowed below that;
- water: a flood closes a road where it stands deeper than `FLOOD_CLOSE_DEPTH_M`
  (following terrain when the snapshot carries ground elevations);
- cordons: fires, riots, blasts, crashes and strikes close the roads in the
  inner part of the footprint (exposure at least `CORDON_EXPOSURE`);
- weather: storms and rain slow the roads they reach.

`traffic_impact` then samples trips across the graph and routes each before
and after, reporting how many are cut off and how much longer the rest take.
Every closure rule and slowdown factor is an illustrative assumption, like the
fragility numbers in `hazards.PROFILES`; nothing here is calibrated against
observed disruptions. It is a picture of the consequence, not a traffic model.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import HazardEvent, HazardKind, UrbanObjectState, UrbanStateSnapshot
from .hazards import PROFILES, _distance_to_path, _exposure, flood_level, hazard_shock, magnitude_of, radius_of
from .viewer import _utm_zone, lonlat_to_utm, utm_to_lonlat

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = (
    "Traffic speeds: Lebanon Traffic Dataset (Tari'ak, 2014-2019), ODbL. "
    "Roads © OpenStreetMap contributors, ODbL."
)

# Drivable OSM road classes and a fallback speed (km/h) for a class with no
# readings at all in the extent. The fallbacks are assumptions.
ROAD_CLASSES: dict[str, float] = {
    "motorway": 70, "motorway_link": 40, "trunk": 50, "trunk_link": 35,
    "primary": 35, "primary_link": 30, "secondary": 30, "secondary_link": 25,
    "tertiary": 25, "tertiary_link": 20, "unclassified": 20, "residential": 18,
    "living_street": 10,
}
# A way needs this many readings in an hour before its own median is used.
MIN_READINGS = 10
# Readings below this are phones standing still (parked, or the app left on
# indoors): at 4 a.m. they would drag an empty road's median to zero. Slow
# traffic still counts, so congestion shows. Readings above the cap are GPS noise.
MIN_MOVING_KMH = 3.0
MAX_SPEED_KMH = 160.0

# Road disruption rules (assumptions, see the module docstring).
FLOOD_CLOSE_DEPTH_M = 0.3
RUBBLE_CLOSE_PROBABILITY = 0.5
# Below that, a road likely to carry some debris is slowed in proportion.
RUBBLE_SLOW_PROBABILITY = 0.15
# Cordons cover the part of the footprint where exposure is at least this.
CORDON_EXPOSURE = 0.5
CORDON_KINDS = frozenset({
    HazardKind.FIRE, HazardKind.RIOT, HazardKind.EXPLOSION, HazardKind.PLANE_CRASH, HazardKind.ORBITAL_STRIKE,
})
# Speed multiplier at full exposure; it eases back to 1 at the footprint edge.
WEATHER_SLOWDOWN = {HazardKind.STORM: 0.7, HazardKind.RAIN: 0.85}
# Edge geometry is sampled this often when testing it against a hazard.
SAMPLE_STEP_M = 10.0


@dataclass
class TrafficModel:
    """A directed-capable road graph in the snapshot CRS with an hourly speed profile per road.

    Roads are stored once each, as split OSM ways; `oneway` is 1 (drawn
    direction only), -1 (against it) or 0 (both ways).
    """

    crs: str
    geometry: list[list[tuple[float, float]]]
    u: np.ndarray
    v: np.ndarray
    length_m: np.ndarray
    oneway: np.ndarray
    highway: list[str]
    way_id: list[int]
    # [road, hour] median speed in km/h (Asia/Beirut local time).
    speed_kmh: np.ndarray
    # Free-flow speed: the fastest hourly median the road shows.
    free_kmh: np.ndarray
    readings: np.ndarray
    estimated: np.ndarray
    period: str = "2014-2019"
    source: str = ATTRIBUTION
    _graph_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __len__(self) -> int:
        return len(self.geometry)

    @property
    def node_count(self) -> int:
        return int(max(self.u.max(initial=-1), self.v.max(initial=-1)) + 1)

    def save(self, path: str | Path) -> None:
        payload = {
            "schema": "dynacity-traffic/1",
            "crs": self.crs,
            "period": self.period,
            "source": self.source,
            "roads": [
                {
                    "u": int(self.u[i]), "v": int(self.v[i]), "oneway": int(self.oneway[i]),
                    "highway": self.highway[i], "way": int(self.way_id[i]),
                    "length_m": round(float(self.length_m[i]), 1),
                    "xy": [[round(x, 1), round(y, 1)] for x, y in self.geometry[i]],
                    "speed": [round(float(s), 1) for s in self.speed_kmh[i]],
                    "readings": int(self.readings[i]), "estimated": bool(self.estimated[i]),
                }
                for i in range(len(self))
            ],
        }
        Path(path).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> TrafficModel:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != "dynacity-traffic/1":
            raise ValueError(f"{path} is not a dynacity traffic model")
        roads = payload["roads"]
        speed = np.asarray([r["speed"] for r in roads], dtype=np.float64).reshape(len(roads), 24)
        return cls(
            crs=payload["crs"],
            geometry=[[(float(x), float(y)) for x, y in r["xy"]] for r in roads],
            u=np.asarray([r["u"] for r in roads], dtype=np.int64),
            v=np.asarray([r["v"] for r in roads], dtype=np.int64),
            length_m=np.asarray([r["length_m"] for r in roads], dtype=np.float64),
            oneway=np.asarray([r["oneway"] for r in roads], dtype=np.int8),
            highway=[r["highway"] for r in roads],
            way_id=[int(r["way"]) for r in roads],
            speed_kmh=speed,
            free_kmh=speed.max(axis=1) if len(roads) else np.zeros(0),
            readings=np.asarray([r["readings"] for r in roads], dtype=np.int64),
            estimated=np.asarray([r["estimated"] for r in roads], dtype=bool),
            period=payload.get("period", "2014-2019"),
            source=payload.get("source", ATTRIBUTION),
        )


# --- building the model -----------------------------------------------------


def snapshot_extent(snapshot: UrbanStateSnapshot, margin_m: float) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees around the snapshot's building centroids."""

    zone, north = _utm_zone(snapshot.crs)
    xs = [o.centroid_x_m for o in snapshot.objects if o.centroid_x_m is not None]
    ys = [o.centroid_y_m for o in snapshot.objects if o.centroid_y_m is not None]
    if not xs:
        raise ValueError("snapshot has no building centroids to size the road network; rebuild it with build-snapshot")
    corners = [
        utm_to_lonlat(x, y, zone, north)
        for x in (min(xs) - margin_m, max(xs) + margin_m)
        for y in (min(ys) - margin_m, max(ys) + margin_m)
    ]
    lons, lats = [c[0] for c in corners], [c[1] for c in corners]
    return min(lons), min(lats), max(lons), max(lats)


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    classes = "|".join(ROAD_CLASSES)
    return (
        f'[out:json][timeout:120];way["highway"~"^({classes})$"]'
        f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});out body geom;"
    )


def fetch_osm_roads(bbox: tuple[float, float, float, float], *, url: str = OVERPASS_URL) -> dict:
    """Drivable OSM ways in the box, as Overpass JSON (ways carry node ids and geometry)."""

    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode({"data": overpass_query(bbox)}).encode()
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "dynacity-forecasting/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _oneway(tags: dict) -> int:
    value = str(tags.get("oneway", "")).lower()
    if value in ("yes", "true", "1"):
        return 1
    if value == "-1":
        return -1
    if tags.get("highway") == "motorway" or tags.get("junction") == "roundabout":
        return 1 if value != "no" else 0
    return 0


def road_graph(overpass: dict, crs: str) -> dict[str, Any]:
    """Split OSM ways at every node shared with another way (or at their ends) into graph edges."""

    zone, north = _utm_zone(crs)
    ways = [
        w for w in overpass.get("elements", [])
        if w.get("type") == "way" and w.get("tags", {}).get("highway") in ROAD_CLASSES
        and len(w.get("nodes", [])) >= 2 and len(w.get("geometry", [])) == len(w.get("nodes", []))
    ]
    uses: dict[int, int] = {}
    for way in ways:
        for node in way["nodes"]:
            uses[node] = uses.get(node, 0) + 1
    index: dict[int, int] = {}
    geometry, u, v, oneway, highway, way_ids = [], [], [], [], [], []
    for way in ways:
        nodes = way["nodes"]
        points = [lonlat_to_utm(p["lon"], p["lat"], zone, north) for p in way["geometry"]]
        start = 0
        for i in range(1, len(nodes)):
            if i == len(nodes) - 1 or uses[nodes[i]] > 1:
                piece = points[start:i + 1]
                if nodes[start] != nodes[i] or len(piece) > 2:
                    geometry.append(piece)
                    u.append(index.setdefault(nodes[start], len(index)))
                    v.append(index.setdefault(nodes[i], len(index)))
                    oneway.append(_oneway(way.get("tags", {})))
                    highway.append(way["tags"]["highway"])
                    way_ids.append(int(way["id"]))
                start = i
    lengths = [
        sum(math.dist(a, b) for a, b in zip(g[:-1], g[1:], strict=True)) for g in geometry
    ]
    return {
        "geometry": geometry, "u": np.asarray(u, dtype=np.int64), "v": np.asarray(v, dtype=np.int64),
        "oneway": np.asarray(oneway, dtype=np.int8), "highway": highway, "way_id": way_ids,
        "length_m": np.asarray(lengths, dtype=np.float64),
    }


def _read_velocities(path: str | Path, way_ids: set[int], unit_factor: float, chunksize: int) -> Iterable[Any]:
    """Tari'ak readings on the given ways, as (way, hour, km/h) frames.

    Columns are taken by position because the header's coordinate column name
    contains a stray tab.
    """

    import pandas as pd

    wanted = {f"way/{w}" for w in way_ids}
    reader = pd.read_csv(
        path, usecols=[1, 4, 5], header=0, names=["time", "velocity", "way"],
        dtype={"time": str, "velocity": float, "way": str}, chunksize=chunksize,
        skipinitialspace=True, on_bad_lines="skip",
    )
    for chunk in reader:
        chunk = chunk[chunk["way"].isin(wanted)]
        if chunk.empty:
            continue
        speed = chunk["velocity"] * unit_factor
        keep = speed.between(MIN_MOVING_KMH, MAX_SPEED_KMH)
        yield pd.DataFrame({
            "way": chunk.loc[keep, "way"].str.slice(4).astype("int64"),
            "hour": chunk.loc[keep, "time"].str.slice(0, 2).astype("int64") % 24,
            "kmh": speed[keep],
        })


def hourly_speeds(
    readings: Any, way_ids: Sequence[int], highway: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(speed[road, hour], readings per road, estimated per road) from a (way, hour, kmh) frame.

    A road's own hourly median is used where it has `MIN_READINGS`; otherwise
    its all-day median shaped by its class's hourly profile; otherwise the
    class's hourly median; otherwise the class fallback speed.
    """

    import pandas as pd

    frame = readings if len(readings) else pd.DataFrame({"way": [], "hour": [], "kmh": []})
    cls_of_way = dict(zip(way_ids, highway, strict=True))
    frame = frame.assign(cls=frame["way"].map(cls_of_way))
    by_way_hour = frame.groupby(["way", "hour"])["kmh"].agg(["median", "size"])
    by_way = frame.groupby("way")["kmh"].agg(["median", "size"])
    by_cls_hour = frame.groupby(["cls", "hour"])["kmh"].median()
    by_cls = frame.groupby("cls")["kmh"].median()

    way_hour = {key: (row["median"], row["size"]) for key, row in by_way_hour.iterrows()}
    way_all = {key: (row["median"], row["size"]) for key, row in by_way.iterrows()}

    def class_profile(cls: str) -> np.ndarray:
        base = by_cls.get(cls, ROAD_CLASSES[cls])
        return np.asarray([by_cls_hour.get((cls, h), base) for h in range(24)], dtype=np.float64)

    profiles = {cls: class_profile(cls) for cls in set(highway)}
    speed = np.zeros((len(way_ids), 24))
    counts = np.zeros(len(way_ids), dtype=np.int64)
    estimated = np.zeros(len(way_ids), dtype=bool)
    for i, (way, cls) in enumerate(zip(way_ids, highway, strict=True)):
        profile = profiles[cls]
        overall, total = way_all.get(way, (None, 0))
        counts[i] = total
        own = total >= MIN_READINGS
        estimated[i] = not own
        shaped = profile * (overall / np.median(profile)) if own and np.median(profile) > 0 else profile
        for h in range(24):
            median, size = way_hour.get((way, h), (None, 0))
            speed[i, h] = median if size >= MIN_READINGS else shaped[h]
    return np.clip(speed, 3.0, MAX_SPEED_KMH), counts, estimated


def build_traffic_model(
    snapshot: UrbanStateSnapshot,
    velocities_csv: str | Path,
    *,
    overpass: dict | None = None,
    margin_m: float = 800.0,
    velocity_unit: str = "kmh",
    chunksize: int = 500_000,
) -> TrafficModel:
    """Roads over the snapshot extent (plus a margin) with Tari'ak hourly speeds.

    `velocity_unit` is how the CSV's Velocity column is recorded. The dataset's
    datapackage says metres per second, but its values only make sense as
    km/h (see docs/forecasting-engine.md), so km/h is the default.
    """

    import pandas as pd

    factor = {"kmh": 1.0, "ms": 3.6}[velocity_unit]
    if overpass is None:
        overpass = fetch_osm_roads(snapshot_extent(snapshot, margin_m))
    graph = road_graph(overpass, snapshot.crs)
    if not graph["geometry"]:
        raise ValueError("no drivable roads in the snapshot extent")
    readings = pd.concat(
        list(_read_velocities(velocities_csv, set(graph["way_id"]), factor, chunksize))
        or [pd.DataFrame({"way": [], "hour": [], "kmh": []})],
        ignore_index=True,
    )
    speed, counts, estimated = hourly_speeds(readings, graph["way_id"], graph["highway"])
    return TrafficModel(
        crs=snapshot.crs, geometry=graph["geometry"], u=graph["u"], v=graph["v"],
        length_m=graph["length_m"], oneway=graph["oneway"], highway=graph["highway"], way_id=graph["way_id"],
        speed_kmh=speed, free_kmh=speed.max(axis=1), readings=counts, estimated=estimated,
    )


# --- disasters on the road network -------------------------------------------


@dataclass(frozen=True)
class RoadDisruption:
    """Per-road outcome of a set of hazards, aligned with the traffic model's roads."""

    closed: np.ndarray
    # Speed multiplier in (0, 1]; 1 where untouched.
    speed_factor: np.ndarray
    # Why each closed road is closed: "rubble", "water" or "cordon".
    reason: list[str | None]


def _samples(model: TrafficModel) -> tuple[np.ndarray, np.ndarray]:
    """Points along every road at most `SAMPLE_STEP_M` apart, and the road each belongs to."""

    cached = model._graph_cache.get("samples")
    if cached is not None:
        return cached
    points, owner = [], []
    for i, line in enumerate(model.geometry):
        for a, b in zip(line[:-1], line[1:], strict=True):
            steps = max(1, math.ceil(math.dist(a, b) / SAMPLE_STEP_M))
            for k in range(steps + 1):
                t = k / steps
                points.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                owner.append(i)
    result = (np.asarray(points, dtype=np.float64).reshape(-1, 2), np.asarray(owner, dtype=np.int64))
    model._graph_cache["samples"] = result
    return result


def _hazard_exposure(hazard: HazardEvent, xy: np.ndarray) -> np.ndarray:
    if hazard.citywide:
        return np.ones(len(xy))
    if hazard.path_m:
        distance = _distance_to_path(xy, hazard.path_m)
    else:
        distance = np.linalg.norm(xy - np.asarray([hazard.center_x_m, hazard.center_y_m]), axis=1)
    return _exposure(distance, radius_of(hazard), PROFILES[hazard.kind].core_fraction)


def _collapse_reach(item: UrbanObjectState) -> float:
    height = item.height_m or (item.floors * 3.2 if item.floors else 6.0)
    width = math.sqrt(item.footprint_area_m2) if item.footprint_area_m2 else 10.0
    return 0.5 * height + 0.5 * width


def road_disruption(
    hazards: Sequence[HazardEvent],
    snapshot: UrbanStateSnapshot,
    model: TrafficModel,
    *,
    ground: Any | None = None,
) -> RoadDisruption:
    """Which roads the hazards close or slow (see the module docstring for the rules)."""

    from scipy.spatial import cKDTree

    if model.crs != snapshot.crs:
        raise ValueError(f"traffic model is in {model.crs}, snapshot in {snapshot.crs}")
    xy, owner = _samples(model)
    roads = len(model)
    closed = np.zeros(roads, dtype=bool)
    factor = np.ones(roads)
    reason: list[str | None] = [None] * roads

    def close(mask_points: np.ndarray, why: str) -> None:
        for i in np.unique(owner[mask_points]):
            if not closed[i]:
                closed[i], reason[i] = True, why

    # Rubble: P(road blocked) = 1 - Π(1 - P(destroyed)) over buildings whose
    # collapse reaches it, combined across every hazard.
    survive = np.ones(len(snapshot.objects))
    for hazard in hazards:
        survive *= 1.0 - hazard_shock(hazard, snapshot.objects).destroy
    destroyed = 1.0 - survive
    blocked_free = np.ones(roads)
    tree = cKDTree(xy) if len(xy) else None
    for j in np.flatnonzero(destroyed > 0.01):
        item = snapshot.objects[j]
        if item.centroid_x_m is None or tree is None:
            continue
        near = tree.query_ball_point((item.centroid_x_m, item.centroid_y_m), _collapse_reach(item))
        for i in np.unique(owner[near]):
            blocked_free[i] *= 1.0 - destroyed[j]
    blocked = 1.0 - blocked_free
    for i in np.flatnonzero(blocked >= RUBBLE_CLOSE_PROBABILITY):
        closed[i], reason[i] = True, "rubble"
    partly = (blocked >= RUBBLE_SLOW_PROBABILITY) & (blocked < RUBBLE_CLOSE_PROBABILITY)
    factor[partly] = 1.0 - blocked[partly]

    for hazard in hazards:
        exposure = _hazard_exposure(hazard, xy)
        if hazard.kind is HazardKind.FLOOD:
            level = flood_level(hazard, snapshot.objects) if ground is not None else None
            if level is None:
                depth = magnitude_of(hazard) * exposure
            else:
                with np.errstate(invalid="ignore"):
                    depth = np.nan_to_num(level - ground.sample(xy[:, 0], xy[:, 1]), nan=0.0).clip(0.0) * exposure
            close(depth > FLOOD_CLOSE_DEPTH_M, "water")
        elif hazard.kind in CORDON_KINDS:
            close(exposure >= CORDON_EXPOSURE, "cordon")
        elif hazard.kind in WEATHER_SLOWDOWN:
            slow = 1.0 - (1.0 - WEATHER_SLOWDOWN[hazard.kind]) * exposure
            per_road = np.ones(roads)
            np.minimum.at(per_road, owner, slow)
            factor = np.minimum(factor, per_road)
    return RoadDisruption(closed=closed, speed_factor=factor, reason=reason)


def _directed(model: TrafficModel, hour: int, disruption: RoadDisruption | None) -> tuple[Any, dict]:
    """Sparse travel-time graph (seconds) and the road behind each directed arc."""

    from scipy.sparse import csr_matrix

    speed = model.speed_kmh[:, hour % 24].copy()
    keep = np.ones(len(model), dtype=bool)
    if disruption is not None:
        speed *= disruption.speed_factor
        keep &= ~disruption.closed
    seconds = model.length_m / (np.maximum(speed, 1.0) / 3.6)
    forward = keep & (model.oneway >= 0)
    backward = keep & (model.oneway <= 0)
    rows = np.concatenate([model.u[forward], model.v[backward]])
    cols = np.concatenate([model.v[forward], model.u[backward]])
    cost = np.concatenate([seconds[forward], seconds[backward]])
    road = np.concatenate([np.flatnonzero(forward), np.flatnonzero(backward)])
    n = model.node_count
    # Parallel arcs between the same nodes: keep the quickest.
    order = np.lexsort((cost, cols, rows))
    rows, cols, cost, road = rows[order], cols[order], cost[order], road[order]
    first = np.ones(len(rows), dtype=bool)
    first[1:] = (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1])
    rows, cols, cost, road = rows[first], cols[first], cost[first], road[first]
    graph = csr_matrix((np.maximum(cost, 1e-3), (rows, cols)), shape=(n, n))
    arc_road = {(int(a), int(b)): int(r) for a, b, r in zip(rows, cols, road, strict=True)}
    return graph, arc_road


def _path_roads(predecessors: np.ndarray, origin: int, target: int, arc_road: dict) -> list[int]:
    roads, node = [], int(target)
    while node != origin:
        prev = int(predecessors[node])
        if prev < 0:
            return []
        roads.append(arc_road[(prev, node)])
        node = prev
    return roads


def traffic_impact(
    model: TrafficModel,
    disruption: RoadDisruption,
    *,
    hour: int = 8,
    origins: int = 100,
    per_origin: int = 10,
    seed: int = 0,
) -> dict[str, Any]:
    """Trips routed before and after the disruption at one hour of the day.

    Delays are over the affected trips only (those whose usual route
    crosses a closed or slowed road), so a local disaster is not averaged
    away across the whole city. Trip ends are road nodes drawn in proportion to how busy their roads are
    (probe readings × length, a stand-in for volume) from the largest
    strongly connected part of the undisrupted network. Returns aggregate
    counts and delay percentiles, plus the roads that lose or gain sampled
    trips, for drawing.
    """

    from scipy.sparse.csgraph import connected_components, dijkstra

    before, arcs_before = _directed(model, hour, None)
    after, arcs_after = _directed(model, hour, disruption)
    _, labels = connected_components(before, directed=True, connection="strong")
    main = np.bincount(labels).argmax()
    weight = np.zeros(model.node_count)
    busy = (model.readings + 1.0) * model.length_m
    np.add.at(weight, model.u, busy)
    np.add.at(weight, model.v, busy)
    weight[labels != main] = 0.0
    rng = np.random.default_rng(seed)
    candidates = np.flatnonzero(weight > 0)
    if len(candidates) < 2:
        raise ValueError("road network too small to sample trips")
    p = weight[candidates] / weight[candidates].sum()
    starts = rng.choice(candidates, size=min(origins, len(candidates)), replace=False, p=p)
    ends = rng.choice(candidates, size=(len(starts), per_origin), p=p)

    dist_before, pred_before = dijkstra(before, indices=starts, return_predecessors=True)
    dist_after, pred_after = dijkstra(after, indices=starts, return_predecessors=True)

    touched = disruption.closed | (disruption.speed_factor < 0.999)
    load = np.zeros(len(model))
    load_after = np.zeros(len(model))
    delays, cut, trips, affected = [], 0, 0, 0
    for k, origin in enumerate(starts):
        for target in ends[k]:
            if target == origin or not np.isfinite(dist_before[k, target]):
                continue
            trips += 1
            path = _path_roads(pred_before[k], origin, target, arcs_before)
            for r in path:
                load[r] += 1
            # Only trips whose usual route crosses a closed or slowed road are
            # counted; a closure can only ever lengthen a trip.
            if not touched[path].any():
                for r in path:
                    load_after[r] += 1
                continue
            affected += 1
            if not np.isfinite(dist_after[k, target]):
                cut += 1
                continue
            delays.append(max(0.0, dist_after[k, target] - dist_before[k, target]) / 60.0)
            for r in _path_roads(pred_after[k], origin, target, arcs_after):
                load_after[r] += 1
    delays_arr = np.asarray(delays) if delays else np.zeros(1)
    shift = load_after - load
    closed_m = float(model.length_m[disruption.closed].sum())
    slowed = disruption.speed_factor < 0.999
    reasons = {why: int(sum(1 for r in disruption.reason if r == why)) for why in ("rubble", "water", "cordon")}
    return {
        "hour": int(hour % 24),
        "trips": trips,
        # Sampled trips whose usual route crosses a closed or slowed road;
        # `cut_off` and `delay_min` are over these.
        "affected": affected,
        "cut_off": cut,
        "delay_min": {
            "p50": round(float(np.percentile(delays_arr, 50)), 2),
            "p90": round(float(np.percentile(delays_arr, 90)), 2),
            "mean": round(float(delays_arr.mean()), 2),
        },
        "closed_roads": int(disruption.closed.sum()),
        "closed_km": round(closed_m / 1000.0, 2),
        "closed_by": reasons,
        "slowed_km": round(float(model.length_m[slowed & ~disruption.closed].sum()) / 1000.0, 2),
        # Road indices into the viewer's road list.
        "closed": [int(i) for i in np.flatnonzero(disruption.closed)],
        "slowed": [[int(i), round(float(disruption.speed_factor[i]), 2)] for i in np.flatnonzero(slowed & ~disruption.closed)],
        "detour": [[int(i), int(shift[i])] for i in np.flatnonzero(shift > 0)],
        "assumptions": (
            "Closure rules and weather slowdowns are illustrative assumptions; trips are sampled with probe readings "
            "standing in for volume, and speeds are typical pre-2020 conditions."
        ),
    }


# --- the viewer --------------------------------------------------------------


def traffic_payload(model: TrafficModel) -> dict[str, Any]:
    """Roads for the 3D page: longitude/latitude paths, hourly speeds, and how busy each road is.

    Aggregates only: per-road hourly medians and a reading count, never the
    individual probe readings.
    """

    zone, north = _utm_zone(model.crs)
    roads = []
    for i, line in enumerate(model.geometry):
        roads.append({
            "path": [[round(c, 6) for c in utm_to_lonlat(x, y, zone, north)] for x, y in line],
            "len": round(float(model.length_m[i]), 1),
            "cls": model.highway[i],
            "oneway": int(model.oneway[i]),
            "speed": [int(round(s)) for s in model.speed_kmh[i]],
            "free": int(round(model.free_kmh[i])),
            "n": int(model.readings[i]),
            "est": bool(model.estimated[i]),
        })
    return {"schema": "dynacity-roads/1", "roads": roads, "period": model.period, "source": model.source}
