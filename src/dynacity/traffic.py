"""Street traffic: an OpenStreetMap road graph with observed speeds, and what disasters do to it.

`build_traffic_model` joins two open sources over the snapshot's extent:

- the drivable OpenStreetMap ways (fetched from Overpass, or a saved Overpass
  JSON), split at shared nodes into a directed road graph in the snapshot CRS;
- the Tari'ak Lebanon traffic dataset: about six million crowd-sourced vehicle
  speeds map-matched to OSM ways, 2014-2019 (2015-2019 inside Beirut). Each way gets a median speed per
  hour of the day; ways with too few readings borrow their road class's hourly
  profile and are flagged `estimated`.

The result is a small JSON file kept under `DYNACITY_DATA_ROOT`.

Volume comes from a third source: the AM and PM peak-hour vehicle counts the
CDR's Bus Rapid Transit study made on named Beirut roads (`COUNT_STATIONS`).
They are matched to OSM ways by name and summarised per road class, spread
over the day with that class's hourly share of probe readings and bent to
pass through both peaks, then scaled by each road's lanes (`estimated_flow`).
The counts are 2016-17 road-level figures, not per-segment measurements.

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

Closures and vehicle crashes can also be placed on the map by hand
(`road_events`), God's Plan style, and combined with any disasters
(`combine_disruptions`).

`traffic_impact` then samples trips across the graph and routes each before
and after, reporting how many are cut off and how much longer the rest take.
The trips over closed roads are rerouted and balanced by the method of
successive averages: the roads they move onto slow down by the BPR
volume-delay curve (capacity from OSM lane counts), so a detour through a
narrow street jams, and the roads they leave speed up; trips are then re-timed
once on that network. Bus and van routes from OSM are kept
alongside, so a result can name the lines a closure cuts.
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
# Tried in turn when the main server is busy (it often answers 429/504).
OVERPASS_MIRRORS = ("https://overpass.kumi.systems/api/interpreter",)
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
# An OSM traffic signal this close to a junction controls it (signals are
# often tagged on the approach, a few metres before the stop line).
SIGNAL_SNAP_M = 25.0
# An OSM crossing node sits on the road it crosses; this allows for rounding
# and for a node on a parallel carriageway's centreline.
CROSSING_SNAP_M = 6.0
# OSM `crossing` values that say there is no painted crossing.
UNMARKED_CROSSINGS = frozenset({"unmarked", "no", "informal"})
# Shared by the SUMO exporter and the viewer's carriageway width.
LANE_WIDTH_M = 3.2
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
# A crash blocks its own segment and slows traffic queuing behind it: speeds
# drop to CRASH_SLOWDOWN × normal on the roads that drive into it, easing back
# to normal CRASH_QUEUE_M back along the network. Assumptions, like the rules above.
CRASH_SLOWDOWN = 0.5
CRASH_QUEUE_M = 250.0
# Lanes in each direction when OSM has no `lanes` tag, by road class.
DEFAULT_LANES = {"motorway": 3, "trunk": 2, "primary": 2, "secondary": 2}
# Vehicles per hour per lane a road can carry, by class (assumed: typical
# urban values, not measured in Beirut). Links carry 80% of their class.
CAPACITY_PER_LANE = {
    "motorway": 1900, "trunk": 1600, "primary": 1200, "secondary": 1000, "tertiary": 800,
    "unclassified": 600, "residential": 500, "living_street": 300,
}
# Classes the CDR survey did not count are assumed to run at this share of
# their capacity at 09:00, shaped over the day by their probe readings. The
# counted classes come out at 0.69 (trunk) and 0.76 (secondary).
UNCOUNTED_PEAK_VC = 0.6
# BPR volume-delay curve: travel time = free time × (1 + α (V/C)^β).
BPR_ALPHA, BPR_BETA = 0.15, 4.0
# Detoured trips are re-routed this many times on the network their traffic
# loads, and the results averaged (method of successive averages).
ASSIGNMENT_ITERATIONS = 6
# A trip delayed by more than this counts as delayed.
DELAYED_MIN = 0.5
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
    # OSM street name per road ("" when unnamed).
    name: list[str] = field(default_factory=list)
    # Probe readings per hour of day for each road class: how the day's
    # traffic is shaped, used to spread the peak-hour counts over 24 hours.
    class_hour_readings: dict[str, list[int]] = field(default_factory=dict)
    # Lanes in each direction from OSM (0 = untagged; see `lanes_of`).
    lanes: np.ndarray | None = None
    # Bus and van routes from OSM: label, operator, member way ids, and paths
    # in longitude/latitude.
    bus_routes: list[dict[str, Any]] = field(default_factory=list)
    # Junctions (graph nodes) with an OSM traffic signal; None when the roads
    # were fetched without signal nodes, and the simulator then guesses.
    signals: list[int] | None = None
    # Movements OSM turn restrictions forbid, as (from road, via node, to road).
    turn_bans: list[tuple[int, int, int]] = field(default_factory=list)
    # Painted crossings as (road, metres from its start, "signals" | "marked");
    # None when the roads were fetched without crossing nodes. Drawn only.
    crossings: list[tuple[int, float, str]] | None = None
    # Vertical level from OSM per road: 1, 2 … for bridges, -1 … for tunnels,
    # 0 at grade (`road_level`); None for road files built before this. Drawn
    # only: SUMO joins roads at shared nodes, which bridges do not share.
    level: np.ndarray | None = None
    mobility: Any = field(default=None, repr=False, compare=False)
    volume_reference: dict[str, dict[str, Any]] | None = None
    _graph_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __len__(self) -> int:
        return len(self.geometry)

    @property
    def node_count(self) -> int:
        return int(max(self.u.max(initial=-1), self.v.max(initial=-1)) + 1)

    def save(self, path: str | Path) -> None:
        payload = {
            "schema": "dynacity-traffic/2",
            "crs": self.crs,
            "period": self.period,
            "source": self.source,
            "class_hour_readings": self.class_hour_readings,
            "bus_routes": self.bus_routes,
            "signals": self.signals,
            "turn_bans": [list(ban) for ban in self.turn_bans],
            "crossings": [list(c) for c in self.crossings] if self.crossings is not None else None,
            "level": [int(x) for x in self.level] if self.level is not None else None,
            "volume_reference": self.volume_reference,
            "roads": [
                {
                    "u": int(self.u[i]), "v": int(self.v[i]), "oneway": int(self.oneway[i]),
                    "highway": self.highway[i], "way": int(self.way_id[i]),
                    "name": self.name[i] if self.name else "",
                    "lanes": float(self.lanes[i]) if self.lanes is not None else 0.0,
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
        if payload.get("schema") not in ("dynacity-traffic/1", "dynacity-traffic/2"):
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
            name=[r.get("name", "") for r in roads],
            class_hour_readings=payload.get("class_hour_readings", {}),
            lanes=np.asarray([r.get("lanes", 0.0) for r in roads], dtype=np.float64),
            bus_routes=payload.get("bus_routes", []),
            signals=payload.get("signals"),
            turn_bans=[tuple(ban) for ban in payload.get("turn_bans", [])],
            crossings=(
                [(int(i), float(d), str(kind)) for i, d, kind in payload["crossings"]]
                if payload.get("crossings") is not None else None
            ),
            level=np.asarray(payload["level"], dtype=np.int8) if payload.get("level") is not None else None,
            volume_reference=payload.get("volume_reference"),
        )


# --- building the model -----------------------------------------------------


def _cached(fn):
    """Memoise a model-only computation on the model (they are re-used per request)."""

    import functools

    @functools.wraps(fn)
    def wrapper(model: TrafficModel):
        key = f"fn:{fn.__name__}"
        if key not in model._graph_cache:
            model._graph_cache[key] = fn(model)
        value = model._graph_cache[key]
        return value.copy() if isinstance(value, np.ndarray) else value

    return wrapper


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
    """Drivable ways, traffic-signal and crossing nodes with geometry, then turn restrictions (members only)."""

    west, south, east, north = bbox
    classes = "|".join(ROAD_CLASSES)
    box = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    return (
        f'[out:json][timeout:120];(way["highway"~"^({classes})$"]{box};'
        f'node["highway"~"^(traffic_signals|crossing)$"]{box};);out body geom;'
        f'relation["type"="restriction"]{box};out body;'
    )


def _overpass(query: str, url: str) -> dict:
    """Run an Overpass query, falling back to the mirrors when a server is busy."""

    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt, server in enumerate((url, *OVERPASS_MIRRORS, url)):
        request = urllib.request.Request(server, data=body, headers={"User-Agent": "dynacity-forecasting/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"Overpass did not answer: {last}")


def fetch_osm_roads(bbox: tuple[float, float, float, float], *, url: str = OVERPASS_URL) -> dict:
    """Drivable OSM ways in the box, as Overpass JSON (ways carry node ids and geometry)."""

    return _overpass(overpass_query(bbox), url)


def _oneway(tags: dict) -> int:
    value = str(tags.get("oneway", "")).lower()
    if value in ("yes", "true", "1"):
        return 1
    if value == "-1":
        return -1
    if tags.get("highway") == "motorway" or tags.get("junction") == "roundabout":
        return 1 if value != "no" else 0
    return 0


def _lanes_per_direction(tags: dict, oneway: int) -> float:
    """Lanes in each direction from OSM `lanes` (a two-way total is halved); 0 when untagged."""

    try:
        total = float(str(tags.get("lanes", "")).split(";")[0])
    except ValueError:
        return 0.0
    if total <= 0:
        return 0.0
    return total if oneway else max(1.0, total / 2)


def _road_name(tags: dict) -> str:
    return str(tags.get("name:en") or tags.get("name:fr") or tags.get("name") or "")


def road_level(tags: dict) -> int:
    """OSM vertical level of a way: its `layer`, else 1 for a bridge, -1 for a tunnel, else 0."""
    try:
        layer = int(float(tags.get("layer", 0)))
    except ValueError:
        layer = 0
    if layer:
        return layer
    if tags.get("bridge", "no") != "no":
        return 1
    if tags.get("tunnel", "no") not in ("no", "building_passage"):
        return -1
    return 0


def _signal_junctions(
    overpass: dict, index: dict[int, int], u: Sequence[int], v: Sequence[int],
    geometry: Sequence[Sequence[tuple[float, float]]], zone: int, north: bool,
) -> list[int] | None:
    """Graph junctions controlled by an OSM `highway=traffic_signals` node; None when the JSON has none.

    A signal tagged on the junction node is used as is. One tagged on the
    approach (common on dual carriageways, a few metres before the stop line)
    is moved to the nearest junction of three or more roads within
    `SIGNAL_SNAP_M`; signals further from any junction, such as mid-block
    pedestrian crossings, are dropped.
    """

    signals = [
        e for e in overpass.get("elements", [])
        if e.get("type") == "node" and e.get("tags", {}).get("highway") == "traffic_signals" and "lat" in e
    ]
    if not signals:
        return None
    degree: dict[int, int] = {}
    where: dict[int, tuple[float, float]] = {}
    for a, b, line in zip(u, v, geometry, strict=True):
        for node, point in ((a, line[0]), (b, line[-1])):
            degree[node] = degree.get(node, 0) + 1
            where[node] = point
    junctions = [n for n, d in degree.items() if d >= 3]
    xy = np.asarray([where[n] for n in junctions], dtype=np.float64).reshape(-1, 2)
    found = set()
    for signal in signals:
        node = index.get(int(signal["id"]))
        if node is not None and degree.get(node, 0) >= 3:
            found.add(node)
            continue
        if not len(xy):
            continue
        point = np.asarray(lonlat_to_utm(signal["lon"], signal["lat"], zone, north))
        gap = np.linalg.norm(xy - point, axis=1)
        if gap.min() <= SIGNAL_SNAP_M:
            found.add(junctions[int(gap.argmin())])
    return sorted(found)


def road_crossings(
    overpass: dict, geometry: Sequence[Sequence[tuple[float, float]]], crs: str,
) -> list[tuple[int, float, str]] | None:
    """Painted pedestrian crossings as (road, metres from the road's start, kind); None when the JSON has none.

    Read from `highway=crossing` nodes and from traffic-signal nodes that
    also carry a `crossing` tag. Kind is "signals" for signal-controlled
    crossings and "marked" otherwise. Crossings OSM records as unmarked are
    left out, and so are nodes further than `CROSSING_SNAP_M` from any road
    (a crossing on a footway or a service road this model does not keep).
    Missing mapping is not evidence that a road has no crossing.
    """

    nodes = []
    for e in overpass.get("elements", []):
        tags = e.get("tags", {})
        if e.get("type") != "node" or "lat" not in e:
            continue
        if tags.get("highway") == "crossing" or (tags.get("highway") == "traffic_signals" and "crossing" in tags):
            nodes.append((e, tags))
    if not nodes:
        return None
    starts, ends, owner, offset = [], [], [], []
    for i, line in enumerate(geometry):
        travelled = 0.0
        for a, b in zip(line[:-1], line[1:], strict=True):
            starts.append(a)
            ends.append(b)
            owner.append(i)
            offset.append(travelled)
            travelled += math.dist(a, b)
    if not starts:
        return []
    a, b = np.asarray(starts, dtype=np.float64), np.asarray(ends, dtype=np.float64)
    ab = b - a
    length2 = np.maximum((ab ** 2).sum(axis=1), 1e-9)
    zone, north = _utm_zone(crs)
    found = []
    for node, tags in nodes:
        if str(tags.get("crossing", "")) in UNMARKED_CROSSINGS or tags.get("crossing:markings") == "no":
            continue
        point = np.asarray(lonlat_to_utm(node["lon"], node["lat"], zone, north))
        t = np.clip(((point - a) * ab).sum(axis=1) / length2, 0.0, 1.0)
        gap = np.linalg.norm(a + ab * t[:, None] - point, axis=1)
        k = int(gap.argmin())
        if gap[k] > CROSSING_SNAP_M:
            continue
        signalled = tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals"
        found.append((owner[k], round(offset[k] + float(t[k]) * math.sqrt(length2[k]), 1), "signals" if signalled else "marked"))
    return sorted(set(found))


def _turn_bans(
    overpass: dict, index: dict[int, int], u: Sequence[int], v: Sequence[int],
    oneway: Sequence[int], way_ids: Sequence[int],
) -> list[tuple[int, int, int]]:
    """OSM turn restrictions as (from road, via junction, to road) movements that are not allowed.

    `no_*` bans its own movement; `only_*` bans every other way out of the
    junction for that approach. Restrictions through a via-way, or on
    conditions, are left out.
    """

    bans: set[tuple[int, int, int]] = set()
    for rel in overpass.get("elements", []):
        tags = rel.get("tags", {})
        kind = str(tags.get("restriction") or tags.get("restriction:motorcar") or "")
        if rel.get("type") != "relation" or not kind.startswith(("no_", "only_")):
            continue
        members = rel.get("members", [])
        via = [m for m in members if m.get("role") == "via"]
        if len(via) != 1 or via[0].get("type") != "node" or int(via[0]["ref"]) not in index:
            continue
        node = index[int(via[0]["ref"])]

        def roads(role: str, arriving: bool) -> list[int]:
            refs = {int(m["ref"]) for m in members if m.get("role") == role and m.get("type") == "way"}
            out = []
            for i, w in enumerate(way_ids):
                if w not in refs:
                    continue
                # Arriving at the node means driving u→v with v the node (or the reverse).
                if (v[i] == node and (oneway[i] >= 0 if arriving else oneway[i] <= 0)) or (
                    u[i] == node and (oneway[i] <= 0 if arriving else oneway[i] >= 0)
                ):
                    out.append(i)
            return out

        sources, targets = roads("from", True), roads("to", False)
        if not sources or not targets:
            continue
        if kind.startswith("no_"):
            bans.update((s, node, t) for s in sources for t in targets if s != t)
        else:
            exits = [
                i for i in range(len(way_ids))
                if (u[i] == node and oneway[i] >= 0) or (v[i] == node and oneway[i] <= 0)
            ]
            bans.update((s, node, t) for s in sources for t in exits if t not in targets and t != s)
    return sorted(bans)


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
    geometry, u, v, oneway, highway, way_ids, names, lanes = [], [], [], [], [], [], [], []
    levels = []
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
                    names.append(_road_name(way["tags"]))
                    lanes.append(_lanes_per_direction(way["tags"], oneway[-1]))
                    levels.append(road_level(way["tags"]))
                start = i
    lengths = [
        sum(math.dist(a, b) for a, b in zip(g[:-1], g[1:], strict=True)) for g in geometry
    ]
    return {
        "geometry": geometry, "u": np.asarray(u, dtype=np.int64), "v": np.asarray(v, dtype=np.int64),
        "oneway": np.asarray(oneway, dtype=np.int8), "highway": highway, "way_id": way_ids, "name": names,
        "lanes": np.asarray(lanes, dtype=np.float64), "length_m": np.asarray(lengths, dtype=np.float64),
        "signals": _signal_junctions(overpass, index, u, v, geometry, zone, north),
        "turn_bans": _turn_bans(overpass, index, u, v, oneway, way_ids),
        "crossings": road_crossings(overpass, geometry, crs),
        "level": np.asarray(levels, dtype=np.int8),
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
    bus_overpass: dict | None = None,
) -> TrafficModel:
    """Roads over the snapshot extent (plus a margin) with Tari'ak hourly speeds.

    `velocity_unit` is how the CSV's Velocity column is recorded. The dataset's
    datapackage says metres per second, but its values only make sense as
    km/h (see docs/forecasting-engine.md), so km/h is the default.
    """

    import pandas as pd

    factor = {"kmh": 1.0, "ms": 3.6}[velocity_unit]
    if overpass is None:
        bbox = snapshot_extent(snapshot, margin_m)
        overpass = fetch_osm_roads(bbox)
        if bus_overpass is None:
            bus_overpass = fetch_bus_routes(bbox)
    graph = road_graph(overpass, snapshot.crs)
    if not graph["geometry"]:
        raise ValueError("no drivable roads in the snapshot extent")
    readings = pd.concat(
        list(_read_velocities(velocities_csv, set(graph["way_id"]), factor, chunksize))
        or [pd.DataFrame({"way": [], "hour": [], "kmh": []})],
        ignore_index=True,
    )
    speed, counts, estimated = hourly_speeds(readings, graph["way_id"], graph["highway"])
    cls_of_way = dict(zip(graph["way_id"], graph["highway"], strict=True))
    per_hour = readings.assign(cls=readings["way"].map(cls_of_way)).groupby(["cls", "hour"]).size()
    class_hour = {
        cls: [int(per_hour.get((cls, h), 0)) for h in range(24)] for cls in sorted(set(graph["highway"]))
    }
    return TrafficModel(
        crs=snapshot.crs, geometry=graph["geometry"], u=graph["u"], v=graph["v"],
        length_m=graph["length_m"], oneway=graph["oneway"], highway=graph["highway"], way_id=graph["way_id"],
        speed_kmh=speed, free_kmh=speed.max(axis=1), readings=counts, estimated=estimated,
        name=graph["name"], class_hour_readings=class_hour, lanes=graph["lanes"],
        signals=graph["signals"], turn_bans=graph["turn_bans"], crossings=graph["crossings"],
        level=graph["level"],
        bus_routes=(
            bus_routes_from_overpass(bus_overpass, snapshot_extent(snapshot, margin_m), overpass) if bus_overpass else []
        ),
    )


def fetch_bus_routes(bbox: tuple[float, float, float, float], *, url: str = OVERPASS_URL) -> dict:
    """Bus, minibus and shared-taxi route relations touching the box, with member geometry."""

    west, south, east, north = bbox
    # Members only, no geometry: route geometry comes from the road ways
    # already fetched (`bus_routes_from_overpass`), which keeps the query
    # light enough for the public servers.
    query = (
        f'[out:json][timeout:120];relation["route"~"^(bus|minibus|share_taxi)$"]'
        f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});out body;"
    )
    return _overpass(query, url)


def bus_routes_from_overpass(
    overpass: dict, bbox: tuple[float, float, float, float], roads: dict | None = None
) -> list[dict[str, Any]]:
    """Routes clipped to the ways that come within the box (long intercity lines are cut at the edge).

    A member's geometry is its own when the query returned it, else that of
    the same way in `roads` (the roads Overpass JSON); members found in
    neither are outside the extent and dropped.
    """

    west, south, east, north = bbox
    known = {
        int(w["id"]): w["geometry"] for w in (roads or {}).get("elements", [])
        if w.get("type") == "way" and w.get("geometry")
    }
    routes = []
    for rel in overpass.get("elements", []):
        if rel.get("type") != "relation":
            continue
        tags = rel.get("tags", {})
        ways, paths = [], []
        for member in rel.get("members", []):
            if member.get("type") != "way":
                continue
            geometry = member.get("geometry") or known.get(int(member["ref"])) or []
            if len(geometry) < 2 or any(p is None for p in geometry):
                continue
            if not any(west <= p["lon"] <= east and south <= p["lat"] <= north for p in geometry):
                continue
            ways.append(int(member["ref"]))
            paths.append([[round(p["lon"], 6), round(p["lat"], 6)] for p in geometry])
        if not paths:
            continue
        label = tags.get("ref") or tags.get("name:en") or tags.get("name") or (
            f"{tags['operator']} route" if tags.get("operator") else "Unnamed route"
        )
        routes.append({
            "id": int(rel["id"]), "label": str(label), "name": tags.get("name:en") or tags.get("name") or "",
            "operator": tags.get("operator", ""), "mode": tags.get("route", "bus"), "ways": ways, "paths": paths,
        })
    return routes


# --- measured volumes ----------------------------------------------------------

# Full citation: CDR, Environmental and Social Impact Assessment for the Bus
# Rapid Transit System between Tabarja and Beirut (ELARD, 2017), Table 4-4.
COUNT_SOURCE = "Peak counts: CDR BRT ESIA (ELARD, 2017)."
# Vehicles per hour in each direction on Beirut roads in that survey: AM peak
# 09:00-10:00 and PM peak 15:30-16:30. Numbered positions on one road are
# separate rows. Each road is matched to OSM ways by an English/French name
# pattern. Stations outside Beirut (the Jounieh coastal highway, Zouk,
# Harissa, Antelias) are left out: the snapshot extent never reaches them.
COUNT_STATIONS: tuple[tuple[str, str, tuple[int, int], tuple[int, int]], ...] = (
    ("General De Gaulle Road", r"de gaulle", (1161, 1095), (970, 1699)),
    ("Minet El Hosn Road", r"minet el[- ]?hosn", (1331, 2783), (1564, 2777)),
    ("Charles Helou Road", r"charles h[eé]lou", (3705, 7024), (4731, 4270)),
    ("Pierre Gemayel Road-1", r"pierre gemayel", (2366, 1744), (2354, 1540)),
    ("Pierre Gemayel Road-2", r"pierre gemayel", (2852, 3033), (2866, 2605)),
    ("Abdallah Elyafi", r"abdallah el[- ]?yafi", (2512, 2170), (2395, 1945)),
    ("Saeb Salem Road", r"sa[eë]b sala", (2284, 2378), (2835, 2490)),
    ("Michel Chiha Road", r"michel chiha", (2738, 1910), (3815, 1612)),
    ("Sagesse Road", r"sagesse", (1156, 1117), (1136, 1035)),
    ("Independence Street-1", r"^independence avenue", (1273, 1517), (1495, 1152)),
    ("Independence Street-2", r"^independence avenue", (973, 1036), (1005, 779)),
    ("Independence Street-3", r"^independence avenue", (2002, 1267), (1814, 1582)),
)
COUNT_HOUR = 9  # the AM peak window starts at 09:00


def match_stations(model: TrafficModel) -> list[dict[str, Any]]:
    """Count stations with the roads they were matched to (those of the road's main class)."""

    import re

    matched = []
    for label, pattern, am, pm in COUNT_STATIONS:
        roads = [i for i, n in enumerate(model.name) if n and re.search(pattern, n, re.I)]
        cls = None
        if roads:
            length: dict[str, float] = {}
            for i in roads:
                length[model.highway[i]] = length.get(model.highway[i], 0.0) + float(model.length_m[i])
            cls = max(length, key=length.get)
            roads = [i for i in roads if model.highway[i] == cls]
        matched.append({
            "label": label, "am": list(am), "pm": list(pm), "class": cls, "roads": roads,
            "am_per_direction": round(sum(am) / len(am)),
        })
    return matched


@_cached
def class_peak_volumes(model: TrafficModel) -> dict[str, dict[str, Any]]:
    """AM-peak vehicles per hour per direction for each counted road class.

    The median over that class's matched stations. `primary` was not counted;
    it gets the geometric mean of `trunk` and `secondary`, flagged
    `interpolated`. Other classes get nothing.
    """

    if model.volume_reference is not None:
        for ref in model.volume_reference.values():
            if any(not math.isfinite(float(ref[k])) or float(ref[k]) <= 0 for k in ('vph', 'pm_vph', 'ref_lanes')):
                raise ValueError('Invalid inherited traffic volume reference')
        return model.volume_reference
    per_class: dict[str, list[tuple[float, float, list[int]]]] = {}
    for station in match_stations(model):
        if station["class"] in ("motorway", "trunk", "primary", "secondary"):
            pm = sum(station["pm"]) / len(station["pm"])
            per_class.setdefault(station["class"], []).append((station["am_per_direction"], pm, station["roads"]))
    lanes = lanes_of(model)
    out = {}
    for cls, rows in per_class.items():
        counted = [r for _, _, roads in rows for r in roads]
        out[cls] = {
            "vph": float(np.median([am for am, _, _ in rows])),
            "pm_vph": float(np.median([pm for _, pm, _ in rows])),
            # Lanes each way on the counted roads: a road with more lanes than
            # this carries proportionally more (see `estimated_flow`).
            "ref_lanes": float(np.median(lanes[counted])) if counted else float(DEFAULT_LANES.get(cls, 1)),
            "stations": len(rows), "interpolated": False,
        }
    if "primary" not in out and "trunk" in out and "secondary" in out:
        out["primary"] = {
            "vph": float(math.sqrt(out["trunk"]["vph"] * out["secondary"]["vph"])),
            "pm_vph": float(math.sqrt(out["trunk"]["pm_vph"] * out["secondary"]["pm_vph"])),
            "ref_lanes": float(DEFAULT_LANES["primary"]), "stations": 0, "interpolated": True,
        }
    return out


def lanes_of(model: TrafficModel) -> np.ndarray:
    """Lanes in each direction per road: the OSM tag, else the class default (1 for links and minor roads)."""

    tagged = model.lanes if model.lanes is not None else np.zeros(len(model))
    default = np.asarray([DEFAULT_LANES.get(h, 1) for h in model.highway], dtype=np.float64)
    return np.where(tagged > 0, tagged, default)


def lane_counts(model: TrafficModel) -> np.ndarray:
    """The integer lane counts actually exported to the simulator."""

    return np.maximum(1, np.rint(lanes_of(model))).astype(np.int64)


def road_widths(model: TrafficModel) -> np.ndarray:
    """Full carriageway around the OSM centreline, in metres."""

    directions = np.where(model.oneway == 0, 2, 1)
    return lane_counts(model) * directions * LANE_WIDTH_M


def capacity_of(model: TrafficModel) -> np.ndarray:
    """Vehicles per hour per direction each road can carry (lanes × an assumed per-lane capacity)."""

    per_lane = np.asarray([
        CAPACITY_PER_LANE.get(h.removesuffix("_link"), 500) * (0.8 if h.endswith("_link") else 1.0)
        for h in model.highway
    ])
    return lanes_of(model) * per_lane


def _pm_correction(shape: np.ndarray, am: float, pm: float) -> np.ndarray:
    """Multiplier that makes the readings-shaped day hit the PM count as well as the AM one.

    1 up to 09:00, the PM factor from 16:00 on, linear in between. The PM
    window is 15:30-16:30, so its shape value is the mean of hours 15 and 16.
    """

    shape_pm = (shape[15] + shape[16]) / 2
    if shape_pm <= 0 or am <= 0:
        return np.ones(24)
    factor = (pm / am) / shape_pm
    hours = np.arange(24)
    return np.where(hours <= COUNT_HOUR, 1.0, np.where(hours >= 16, factor,
                    1.0 + (factor - 1.0) * (hours - COUNT_HOUR) / (16 - COUNT_HOUR)))


@_cached
def estimated_flow(model: TrafficModel) -> np.ndarray:
    """[road, hour] vehicles per hour per direction; NaN for classes without a count.

    The class's AM-peak count, scaled by the class's probe readings at each
    hour relative to 09:00. The probe app's users stand in for drivers' daily
    rhythm, which is an assumption.
    """

    flow = np.full((len(model), 24), np.nan)
    factor = lane_factor(model)
    for cls, ref in class_peak_volumes(model).items():
        curve = class_flow_curve(model, cls, ref)
        if curve is None:
            continue
        for i in np.flatnonzero(np.asarray(model.highway) == cls):
            flow[i] = curve * factor[i]
    return flow


def class_flow_curve(model: TrafficModel, cls: str, ref: dict[str, Any]) -> np.ndarray | None:
    """Vehicles per hour per direction over the day on a counted road of `ref_lanes` lanes."""

    hours = np.asarray(model.class_hour_readings.get(cls, []), dtype=np.float64)
    if hours.size != 24 or hours[COUNT_HOUR] <= 0:
        return None
    shape = hours / hours[COUNT_HOUR]
    return ref["vph"] * shape * _pm_correction(shape, ref["vph"], ref.get("pm_vph", ref["vph"]))


def lane_factor(model: TrafficModel) -> np.ndarray:
    """Each road's lanes over its class's counted lanes, kept within 0.5-2 (1 for uncounted classes)."""

    volumes = class_peak_volumes(model)
    lanes = lanes_of(model)
    ref = np.asarray([volumes.get(h, {}).get("ref_lanes", 0.0) for h in model.highway])
    return np.where(ref > 0, np.clip(lanes / np.where(ref > 0, ref, 1.0), 0.5, 2.0), 1.0)


@_cached
def background_flow(model: TrafficModel) -> np.ndarray:
    """[road, hour] vehicles per hour per direction on every road.

    Counted classes use `estimated_flow`; the rest run at `UNCOUNTED_PEAK_VC`
    of capacity at 09:00, shaped over the day by their own probe readings.
    """

    flow = estimated_flow(model) if model.class_hour_readings else np.full((len(model), 24), np.nan)
    cap = capacity_of(model)
    for cls in set(model.highway):
        hours = np.asarray(model.class_hour_readings.get(cls, []), dtype=np.float64)
        shape = hours / hours[COUNT_HOUR] if hours.size == 24 and hours[COUNT_HOUR] > 0 else np.ones(24)
        idx = np.flatnonzero((np.asarray(model.highway) == cls) & np.isnan(flow[:, 0]))
        flow[idx] = UNCOUNTED_PEAK_VC * cap[idx, None] * shape[None, :]
    return flow


# --- disasters on the road network -------------------------------------------


@dataclass(frozen=True)
class RoadDisruption:
    """Per-road outcome of a set of hazards, aligned with the traffic model's roads."""

    closed: np.ndarray
    # Speed multiplier in (0, 1]; 1 where untouched.
    speed_factor: np.ndarray
    # Why each closed road is closed: "rubble", "water", "cordon", "manual" or "crash".
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


def _feeders(model: TrafficModel) -> dict[int, list[tuple[int, int]]]:
    """For each node, the roads that drive into it and the node each comes from."""

    cached = model._graph_cache.get("feeders")
    if cached is not None:
        return cached
    into: dict[int, list[tuple[int, int]]] = {}
    for i in range(len(model)):
        u, v = int(model.u[i]), int(model.v[i])
        if model.oneway[i] >= 0:
            into.setdefault(v, []).append((i, u))
        if model.oneway[i] <= 0:
            into.setdefault(u, []).append((i, v))
    model._graph_cache["feeders"] = into
    return into


def _queue_distance(model: TrafficModel, road: int, reach: float) -> np.ndarray:
    """Metres back along the network from where traffic enters `road`; inf beyond `reach`.

    Only roads that lead into the blocked one are reached, against their
    direction of travel, so the far side of a crash and the streets that only
    run past it are left alone. A two-way road is entered from both ends.
    """

    import heapq

    into = _feeders(model)
    distance = np.full(len(model), np.inf)
    entries = [int(model.u[road])] if model.oneway[road] >= 0 else []
    if model.oneway[road] <= 0:
        entries.append(int(model.v[road]))
    best = {node: 0.0 for node in entries}
    heap = [(0.0, node) for node in entries]
    while heap:
        d, node = heapq.heappop(heap)
        if d > best[node]:
            continue
        for j, previous in into.get(node, ()):
            if j == road:
                continue
            distance[j] = min(distance[j], d)
            further = d + float(model.length_m[j])
            if further < reach and further < best.get(previous, np.inf):
                best[previous] = further
                heapq.heappush(heap, (further, previous))
    return distance


def road_events(model: TrafficModel, *, closed: Sequence[int] = (), crashes: Sequence[int] = ()) -> RoadDisruption:
    """Roads closed by hand, and crashes: each blocks its road and slows the queue behind it."""

    roads = len(model)
    bad = [i for i in (*closed, *crashes) if not 0 <= i < roads]
    if bad:
        raise ValueError(f"no road {bad[0]} in this traffic model ({roads} roads)")
    out_closed = np.zeros(roads, dtype=bool)
    factor = np.ones(roads)
    reason: list[str | None] = [None] * roads
    for i in closed:
        out_closed[i], reason[i] = True, "manual"
    for i in crashes:
        out_closed[i], reason[i] = True, "crash"
        distance = _queue_distance(model, i, CRASH_QUEUE_M)
        near = distance < CRASH_QUEUE_M
        slow = 1.0 - (1.0 - CRASH_SLOWDOWN) * (1.0 - distance[near] / CRASH_QUEUE_M)
        factor[near] = np.minimum(factor[near], slow)
    return RoadDisruption(closed=out_closed, speed_factor=factor, reason=reason)


def combine_disruptions(*parts: RoadDisruption) -> RoadDisruption:
    """Closed where any part closes a road (the first reason wins); speeds take the strongest slowdown."""

    closed = np.logical_or.reduce([p.closed for p in parts])
    factor = np.minimum.reduce([p.speed_factor for p in parts])
    reason = [next((p.reason[i] for p in parts if p.closed[i]), None) for i in range(len(closed))]
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


# Summary figures reported with their spread across independent trip samples.
RANGED = {
    "affected": lambda r: r["affected"],
    "cut_off": lambda r: r["cut_off"],
    "delay_p50": lambda r: r["delay_min"]["p50"],
    "delay_p90": lambda r: r["delay_min"]["p90"],
    "diverted_vph": lambda r: r["diverted_vph"],
    "spillover_trips": lambda r: r["spillover"]["trips"],
}


def traffic_impact(
    model: TrafficModel,
    disruption: RoadDisruption,
    *,
    hour: int = 8,
    origins: int = 100,
    per_origin: int = 10,
    seed: int = 0,
    congestion: bool = True,
    samples: int = 1,
) -> dict[str, Any]:
    """Trips routed before and after the disruption at one hour of the day.

    With `samples` > 1, that many independent trip samples are drawn (seeds
    `seed`, `seed + 1`, ...). The result is the sample with the median
    `delay_min.p50`, plus `ranges`: the lowest and highest value of each
    summary figure across samples, so the page can show how much of a number
    is sampling noise. One sample of ~1,000 trips moves the median delay by
    about ±15% and the 90th percentile by about ±35%.
    """

    if samples > 1:
        runs = [
            _traffic_impact(model, disruption, hour=hour, origins=origins, per_origin=per_origin,
                            seed=seed + k, congestion=congestion)
            for k in range(samples)
        ]
        order = sorted(range(samples), key=lambda k: runs[k]["delay_min"]["p50"])
        result = runs[order[samples // 2]]
        result["samples"] = samples
        result["ranges"] = {
            name: [min(get(r) for r in runs), max(get(r) for r in runs)] for name, get in RANGED.items()
        }
        return result
    result = _traffic_impact(model, disruption, hour=hour, origins=origins, per_origin=per_origin,
                             seed=seed, congestion=congestion)
    result["samples"] = 1
    return result


def _traffic_impact(
    model: TrafficModel,
    disruption: RoadDisruption,
    *,
    hour: int,
    origins: int,
    per_origin: int,
    seed: int,
    congestion: bool,
) -> dict[str, Any]:
    """One trip sample routed before and after the disruption (see `traffic_impact`).

    Trip ends are road nodes drawn in proportion to how busy their roads are
    (probe readings × length) from the largest strongly connected part of the
    undisrupted network.

    Every sampled trip stands for the same vehicles per hour (`vph_per_trip`,
    calibrated so the sample reproduces the volume on the counted roads). With
    `congestion`, the trips over a closed road leave their usual roads and are
    rerouted `ASSIGNMENT_ITERATIONS` times by the method of successive
    averages, each time on the network the averaged detour traffic has loaded.
    The volume change moves each road along the BPR curve against
    `capacity_of`: detours slow down, and the roads the detouring trips left
    speed up (to at most their free-flow speed). Every trip is then timed once
    more on that network. Only the detouring trips choose new routes, so this
    approaches an equilibrium for the diversion, not for the whole city.

    `affected` trips are those whose usual route crosses a closed or slowed
    road; `cut_off` and `delay_min` are over them, so a local disaster is not
    averaged away across the city. `spillover` counts the other trips the
    detour traffic delays.
    """

    from scipy.sparse.csgraph import connected_components, dijkstra

    hour = hour % 24
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
    trips = []  # (row, origin, target, usual route)
    load = np.zeros(len(model))
    for k, origin in enumerate(starts):
        for target in ends[k]:
            if target == origin or not np.isfinite(dist_before[k, target]):
                continue
            path = _path_roads(pred_before[k], origin, target, arcs_before)
            np.add.at(load, path, 1)
            trips.append((k, int(origin), int(target), path))

    # Every sampled trip stands for the same number of vehicles per hour,
    # chosen so the sample reproduces the flow on the counted roads:
    # vehicle-km per hour ÷ sampled trip-km. A trip over a closed road takes
    # its vehicles along its detour. (Giving each trip its closed road's flow
    # ÷ the trips sampled there instead lets a rarely sampled segment hand
    # one trip its whole flow, and inflates the diversion several-fold.)
    flow = background_flow(model)[:, hour]
    counted = estimated_flow(model)[:, hour] if model.class_hour_readings else np.full(len(model), np.nan)
    basis = np.isfinite(counted) & (load > 0)
    if not basis.any():
        basis = load > 0
    per_trip = float((flow[basis] * model.length_m[basis]).sum() / max((load[basis] * model.length_m[basis]).sum(), 1e-9))
    cap = np.maximum(capacity_of(model), 1.0)
    usual = 1.0 + BPR_ALPHA * (flow / cap) ** BPR_BETA
    # A road that traffic leaves speeds up, but no faster than its free-flow speed.
    ceiling = model.free_kmh / np.maximum(model.speed_kmh[:, hour], 1.0)

    def jam_of(change: np.ndarray) -> np.ndarray:
        # Speed multiplier from the BPR curve, the changed volume against the
        # usual one: below 1 where detours add traffic, above 1 where it left.
        return np.minimum(usual / (1.0 + BPR_ALPHA * (np.maximum(flow + change, 0.0) / cap) ** BPR_BETA), ceiling)

    def network(change: np.ndarray) -> tuple[Any, dict]:
        return _directed(model, hour, RoadDisruption(
            closed=disruption.closed, speed_factor=disruption.speed_factor * jam_of(change), reason=disruption.reason,
        ))

    rerouted = [t for t in trips if disruption.closed[t[3]].any() and np.isfinite(dist_after[t[0], t[2]])]
    cut_vph = per_trip * sum(1 for t in trips if disruption.closed[t[3]].any() and not np.isfinite(dist_after[t[0], t[2]]))
    diverted = per_trip * len(rerouted)
    # The detouring vehicles leave the open parts of their usual routes.
    left = np.zeros(len(model))
    for _k, _origin, _target, path in rerouted:
        np.add.at(left, path, per_trip)
    left[disruption.closed] = 0.0
    rows = sorted({t[0] for t in rerouted})
    row_of = {k: j for j, k in enumerate(rows)}

    def assign(graph: Any, arcs: dict) -> np.ndarray:
        """Vehicles per hour the detoured trips put on each road, each by its quickest route."""

        out = np.zeros(len(model))
        _, pred = dijkstra(graph, indices=starts[rows], return_predecessors=True)
        for k, origin, target, _path in rerouted:
            np.add.at(out, _path_roads(pred[row_of[k]], origin, target, arcs), per_trip)
        return out

    extra = assign(after, arcs_after) if rerouted else np.zeros(len(model))
    if congestion and rerouted:
        # Method of successive averages: route the detoured trips again on the
        # network their own traffic has loaded (and emptied), and average that
        # in, so the diversion settles over the alternatives instead of piling
        # onto the first one.
        for step in range(2, ASSIGNMENT_ITERATIONS + 1):
            extra += (assign(*network(extra - left)) - extra) / step

    final, arcs_final, dist_final, pred_final = after, arcs_after, dist_after, pred_after
    jam = np.ones(len(model))
    if congestion and rerouted:
        jam = jam_of(extra - left)
        final, arcs_final = network(extra - left)
        dist_final, pred_final = dijkstra(final, indices=starts, return_predecessors=True)

    load_after = np.zeros(len(model))
    delays, spill, cut, affected = [], [], 0, 0
    uncongested = []
    for k, origin, target, path in trips:
        direct = bool(touched[path].any())
        if direct:
            affected += 1
            if not np.isfinite(dist_final[k, target]):
                cut += 1
                continue
            uncongested.append(max(0.0, dist_after[k, target] - dist_before[k, target]) / 60.0)
        late = max(0.0, dist_final[k, target] - dist_before[k, target]) / 60.0
        if direct:
            delays.append(late)
        elif late > DELAYED_MIN:
            spill.append(late)
        if direct or late > 0:
            np.add.at(load_after, _path_roads(pred_final[k], origin, target, arcs_final), 1)
        else:
            np.add.at(load_after, path, 1)

    def stats(values: list[float]) -> dict[str, float]:
        arr = np.asarray(values) if values else np.zeros(1)
        return {
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "mean": round(float(arr.mean()), 2),
        }

    shift = load_after - load
    closed_flow = counted[disruption.closed]
    busiest = float(np.nanmax(closed_flow)) if np.isfinite(closed_flow).any() else None
    slowed = disruption.speed_factor < 0.999
    jammed = (jam < 0.95) & ~disruption.closed
    relieved = (jam > 1.05) & ~disruption.closed
    loaded_vc = np.maximum(flow + extra - left, 0.0) / cap
    reasons = {
        why: int(sum(1 for r in disruption.reason if r == why))
        for why in ("rubble", "water", "cordon", "manual", "crash")
    }
    closed_ways = {model.way_id[i] for i in np.flatnonzero(disruption.closed)}
    routes_hit = [
        {"id": route["id"], "label": route["label"], "name": route["name"]}
        for route in model.bus_routes if closed_ways.intersection(route["ways"])
    ]
    return {
        "hour": hour,
        "trips": len(trips),
        # Sampled trips whose usual route crosses a closed or slowed road;
        # `cut_off` and `delay_min` are over these.
        "affected": affected,
        "cut_off": cut,
        "delay_min": stats(delays),
        # The same trips with detours that never congest, for comparison.
        "delay_min_uncongested": stats(uncongested),
        # Other trips held up by the detour traffic.
        "spillover": {"trips": len(spill), "delay_min": stats(spill)},
        "closed_roads": int(disruption.closed.sum()),
        "closed_km": round(float(model.length_m[disruption.closed].sum()) / 1000.0, 2),
        "closed_by": reasons,
        # Estimated vehicles per hour per direction on the busiest closed road
        # of a counted class; None when no such road is closed.
        "busiest_closed_vph": None if busiest is None else int(round(busiest, -1)),
        # Vehicles per hour pushed onto detours, and those with no way through.
        "diverted_vph": int(round(diverted, -1)),
        "cut_off_vph": int(round(cut_vph, -1)),
        # Vehicles per hour each sampled trip stands for at this hour.
        "vph_per_trip": round(per_trip, 1),
        "jammed_km": round(float(model.length_m[jammed].sum()) / 1000.0, 2),
        # Roads that run faster because detouring traffic left them.
        "relieved_km": round(float(model.length_m[relieved].sum()) / 1000.0, 2),
        "max_vc": round(float(loaded_vc[extra > 0].max()), 2) if (extra > 0).any() else None,
        "slowed_km": round(float(model.length_m[slowed & ~disruption.closed].sum()) / 1000.0, 2),
        "bus_routes_hit": routes_hit,
        # Road indices into the viewer's road list.
        "closed": [int(i) for i in np.flatnonzero(disruption.closed)],
        "slowed": [[int(i), round(float(disruption.speed_factor[i]), 2)] for i in np.flatnonzero(slowed & ~disruption.closed)],
        "jammed": [[int(i), round(float(jam[i]), 2)] for i in np.flatnonzero(jammed)],
        "relieved": [[int(i), round(float(jam[i]), 2)] for i in np.flatnonzero(relieved)],
        "detour": [[int(i), int(shift[i])] for i in np.flatnonzero(shift > 0)],
        "assumptions": (
            "Closure rules, weather slowdowns, crash queues, lane capacities and the volume on uncounted roads are "
            "illustrative assumptions. Trips are sampled with probe readings standing in for demand, only the "
            "detouring trips choose new routes (balanced over a few rounds of successive averages), and speeds "
            "are typical pre-2020 conditions."
        ),
    }


# --- the viewer --------------------------------------------------------------


def traffic_payload(model: TrafficModel) -> dict[str, Any]:
    """Roads for the 3D page: longitude/latitude paths, hourly speeds, and how busy each road is.

    Aggregates only: per-road hourly medians and a reading count, never the
    individual probe readings.
    """

    zone, north = _utm_zone(model.crs)
    lanes = lanes_of(model)
    widths = road_widths(model)
    factor = lane_factor(model)
    crossings: dict[int, list[list[Any]]] = {}
    for road, at, kind in model.crossings or []:
        crossings.setdefault(road, []).append([at, kind])
    roads = []
    for i, line in enumerate(model.geometry):
        roads.append({
            "path": [[round(c, 6) for c in utm_to_lonlat(x, y, zone, north)] for x, y in line],
            "len": round(float(model.length_m[i]), 1),
            "cls": model.highway[i],
            "oneway": int(model.oneway[i]),
            "u": int(model.u[i]), "v": int(model.v[i]),
            "speed": [int(round(s)) for s in model.speed_kmh[i]],
            "free": int(round(model.free_kmh[i])),
            "n": int(model.readings[i]),
            "est": bool(model.estimated[i]),
            "name": model.name[i] if model.name else "",
            "lanes": float(lanes[i]),
            "width_m": float(widths[i]),
            # Multiplier on the class flow for this road's lanes (counted classes).
            "qf": round(float(factor[i]), 2),
        })
        if i in crossings:
            # [metres from the path's start, "signals" | "marked"]
            roads[-1]["xing"] = crossings[i]
        if model.level is not None and model.level[i]:
            # OSM level: >0 bridge, <0 tunnel; absent at grade.
            roads[-1]["lvl"] = int(model.level[i])
    stations = match_stations(model) if model.name else []
    volumes = class_peak_volumes(model) if model.name else {}
    by_class = {}
    for cls, ref in volumes.items():
        curve = class_flow_curve(model, cls, ref)
        if curve is not None:
            by_class[cls] = [int(round(q)) for q in curve]
    return {
        "schema": "dynacity-roads/1", "roads": roads, "period": model.period, "source": model.source,
        "mobility": model.mobility.public() if model.mobility is not None else None,
        "stations": [{k: st[k] for k in ("label", "am", "pm", "class", "roads", "am_per_direction")} for st in stations],
        "volumes": volumes,
        "bus_routes": [
            {k: route[k] for k in ("id", "label", "name", "operator", "mode", "paths")} for route in model.bus_routes
        ],
        # Vehicles per hour per direction by hour on a counted road of the
        # class's `ref_lanes`; a road's own figure is this × its `qf`.
        "flow": by_class,
        "volume_source": COUNT_SOURCE,
        # Junctions (the roads' u/v) with an OSM traffic signal, for stop lines.
        "signals": model.signals or [],
        "crossings_mapped": model.crossings is not None,
    }
