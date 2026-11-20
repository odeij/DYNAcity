"""Walking access to the 15-minute services on a corridor, and what candidate sites would add.

After Zheng et al. (2023), "Spatial planning of urban communities via deep
reinforcement learning", Nature Computational Science 3, 748-762: a home is
served for a service when one of that service's places is within reach, and the
plan's service score is the share of services each home reaches, averaged over
homes. The paper uses five services (education, medical care, shopping, work,
entertainment) and 500 m in a straight line. Here distances run along the OSM
walking network, the score is weighted by estimated residents, and two
thresholds are reported: the paper's 500 m and a 15-minute walk.

`build_access` does the slow part once: shortest walks from every service and
every candidate site to every home. After that `AccessModel.score` scores any
plan (candidate site -> new use) with a few array minimums, so search and RL can
call it millions of times.

Everything is a proxy and is labelled so in the output:
- homes are model buildings with residents estimated from floor area, as for
  the UNA pedestrian model (`scripts/prepare_pedestrian_inputs.py`);
- destinations are mapped places (the key-buildings layer and OSM); operation,
  capacity and opening hours are not known, and missing mapping is not absence;
- a site's new use is assumed to open with the same pull as any mapped place.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import shapely
from shapely import contains_xy, prepare
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .status import CanonicalState, canonicalize_status
from .viewer import _utm_zone, lonlat_to_utm

SCHEMA = "dynacity-access/1"

WALK_SPEED_MS = 1.3  # the walkers' speed in the 3D city (docs/pedestrian-flow-model.md)
FIFTEEN_MIN_M = round(15 * 60 * WALK_SPEED_MS)  # 1,170 m
PAPER_M = 500  # Zheng et al.: 500 m in a straight line stands for 15 minutes
# Exponential decay per metre, as in the UNA pedestrian model (Sevtsuk et al. 2024).
BETA_PER_M = 0.002
# Residents per m2 of floor area, as in the UNA inputs: about 450,000 people in Beirut's model buildings.
M2_PER_RESIDENT = 100.0
# Walks longer than this are treated as out of reach (their decayed weight is below 0.0003).
LIMIT_M = 4000.0


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    # Counted in the paper's service score; parks are scored apart, as the paper's ecology term.
    in_score: bool = True


SERVICES: tuple[Service, ...] = (
    Service("education", "Education (schools, kindergartens)"),
    Service("medical", "Medical care (hospitals, clinics)"),
    Service("shopping", "Daily shopping (food shops, markets)"),
    Service("work", "Work (offices, government offices)"),
    Service("entertainment", "Entertainment (sport, play, culture)"),
    Service("parks", "Parks and gardens", in_score=False),
)
SERVICE_KEYS = tuple(s.key for s in SERVICES)
# Scored by default. Work is reported but left out: OSM maps very few offices here, and
# government offices alone do not stand for jobs.
DEFAULT_SCORED = ("education", "medical", "shopping", "entertainment")

# Key-buildings layer (facilities.py) kind -> service.
FACILITY_SERVICE = {"school": "education", "kindergarten": "education", "hospital": "medical",
                    "clinic": "medical", "government_office": "work"}
DAILY_SHOPS = {"supermarket", "convenience", "bakery", "greengrocer", "butcher", "general", "variety_store",
               "mall", "department_store", "deli", "seafood", "dairy", "grocery", "pastry", "frozen_food"}
ENTERTAINMENT_AMENITY = {"cinema", "theatre", "arts_centre"}
ENTERTAINMENT_LEISURE = {"pitch", "sports_centre", "fitness_centre", "swimming_pool", "playground", "stadium",
                         "sports_hall"}
PARK_LEISURE = {"park", "garden"}
PARK_LANDUSE = {"village_green", "recreation_ground"}
# Motorways are not walked. Trunk roads are, unlike in the UNA inputs: in Beirut they have
# pavements, and in OSM they are the only links across the river and into Karantina
# (without them 145 of the corridor's 1,925 homes sat on a cut-off piece of network; with them, 20).
NOT_WALKABLE = {"motorway", "motorway_link", "construction", "proposed", "raceway", "bus_guideway", "escape",
                "platform", "abandoned", "razed"}
FOOT_ALLOWED = {"yes", "designated", "permissive"}
# BBED states whose site could take a new use.
CANDIDATE_STATES = {CanonicalState.EMPTY_OR_PARKING, CanonicalState.STALLED_OR_CANCELLED,
                    CanonicalState.VACANT_OR_EVICTED}


def osm_service(tags: dict[str, str]) -> str | None:
    """The service an OSM place counts for, or None. Private places count for nothing."""

    if tags.get("access") in ("private", "no"):
        return None
    if tags.get("shop") in DAILY_SHOPS or tags.get("amenity") == "marketplace":
        return "shopping"
    if tags.get("amenity") in ENTERTAINMENT_AMENITY or tags.get("leisure") in ENTERTAINMENT_LEISURE:
        return "entertainment"
    if tags.get("leisure") in PARK_LEISURE or tags.get("landuse") in PARK_LANDUSE:
        return "parks"
    if "office" in tags:
        return "work"
    return None


def _osm_lonlat(element: dict) -> tuple[float, float] | None:
    if element.get("type") == "node" and "lon" in element:
        return float(element["lon"]), float(element["lat"])
    if element.get("center"):
        return float(element["center"]["lon"]), float(element["center"]["lat"])
    points = [p for p in element.get("geometry") or [] if p]
    if points:
        return sum(p["lon"] for p in points) / len(points), sum(p["lat"] for p in points) / len(points)
    bounds = element.get("bounds")
    if bounds:
        return (bounds["minlon"] + bounds["maxlon"]) / 2, (bounds["minlat"] + bounds["maxlat"]) / 2
    return None


@dataclass
class Place:
    id: str
    service: str
    x: float
    y: float
    source: str
    name: str = ""


def destinations(facilities: dict | None, osm_extracts: Iterable[dict], area: BaseGeometry,
                 crs: str) -> list[Place]:
    """Mapped places inside `area` for every service, one per mapped object.

    Education, medical care and government offices come from the key-buildings
    layer; shops, offices, entertainment and parks from OSM extracts. An object in
    several extracts is kept once.
    """

    zone, north = _utm_zone(crs)
    prepare(area)
    out: list[Place] = []
    seen: set[str] = set()

    def add(identifier: str, service: str, lon: float, lat: float, source: str, name: str) -> None:
        key = f"{service}:{identifier}"
        if key in seen:
            return
        x, y = lonlat_to_utm(lon, lat, zone, north)
        if contains_xy(area, x, y):
            seen.add(key)
            out.append(Place(identifier, service, x, y, source, name))

    for feature in (facilities or {}).get("features", []):
        props = feature["properties"]
        service = FACILITY_SERVICE.get(props.get("facility"))
        if service:
            lon, lat = feature["geometry"]["coordinates"][:2]
            add(str(props["id"]), service, lon, lat, props.get("source", "key buildings"),
                props.get("name", "") if props.get("named") else "")
    for osm in osm_extracts:
        for element in osm.get("elements", []):
            tags = element.get("tags") or {}
            service = osm_service(tags)
            if service is None or (service == "work" and tags.get("office") == "government" and facilities):
                continue
            lonlat = _osm_lonlat(element)
            if lonlat:
                add(f"{element['type']}/{element['id']}", service, *lonlat, "OpenStreetMap", tags.get("name", ""))
    return out


@dataclass
class WalkGraph:
    """Walkable OSM ways as an undirected graph in metres."""

    xy: np.ndarray  # (nodes, 2)
    source: np.ndarray  # edge endpoints, both directions
    target: np.ndarray
    length: np.ndarray

    def __len__(self) -> int:
        return len(self.xy)


def walk_graph(osm: dict, area: BaseGeometry, crs: str) -> WalkGraph:
    """Every walkable way piece with both ends in `area`.

    Ways are cut at every OSM node, so crossings are shared nodes, not
    rounded coordinates. Motorways and their links are not walkable unless
    tagged `foot=yes`; trunk roads, steps and footways are (see NOT_WALKABLE).
    """

    zone, north = _utm_zone(crs)
    prepare(area)
    index: dict[int, int] = {}
    xy: list[tuple[float, float]] = []
    source, target, length = [], [], []
    for element in osm.get("elements", []):
        tags = element.get("tags") or {}
        if element.get("type") != "way" or "highway" not in tags or tags.get("foot") == "no":
            continue
        if tags.get("foot") not in FOOT_ALLOWED and (tags["highway"] in NOT_WALKABLE
                                                     or tags.get("access") in ("private", "no")):
            continue
        nodes, geometry = element.get("nodes") or [], element.get("geometry") or []
        if len(nodes) != len(geometry):
            continue
        previous = None
        for node, point in zip(nodes, geometry):
            if not point:
                previous = None
                continue
            if node not in index:
                x, y = lonlat_to_utm(point["lon"], point["lat"], zone, north)
                if not contains_xy(area, x, y):
                    previous = None
                    continue
                index[node] = len(xy)
                xy.append((x, y))
            current = index[node]
            if previous is not None and previous != current:
                d = math.dist(xy[previous], xy[current])
                source += [previous, current]
                target += [current, previous]
                length += [d, d]
            previous = current
    if not xy:
        raise ValueError("no walkable way lies in the area")
    return WalkGraph(np.asarray(xy, dtype=float), np.asarray(source, dtype=np.int64),
                     np.asarray(target, dtype=np.int64), np.asarray(length, dtype=float))


@dataclass
class Homes:
    ids: np.ndarray
    xy: np.ndarray
    residents: np.ndarray
    source: str = ""


def homes_from_pack(pack: Any, area: BaseGeometry) -> Homes:
    """Model buildings inside `area` as homes, residents from floor area (the UNA rule).

    Every building counts, whatever its use, because use is not recorded for
    most of them; buildings under 2.5 m or 20 m2 are left out.
    """

    prepare(area)
    heights = pack.heights()
    ids, xy, residents = [], [], []
    for i, outline in enumerate(pack.buildings):
        h = float(heights[i])
        if not math.isfinite(h) or h < 2.5 or outline.area < 20:
            continue
        point = outline.representative_point()
        if not contains_xy(area, point.x, point.y):
            continue
        floors = max(1, round(h / 3.0))
        ids.append(str(pack.building_ids[i]))
        xy.append((point.x, point.y))
        residents.append(outline.area * floors / M2_PER_RESIDENT)
    return Homes(np.asarray(ids), np.asarray(xy, dtype=float).reshape(-1, 2), np.asarray(residents, dtype=float),
                 "Beirut 001 model buildings; residents = floor area / 100 m2 (proxy)")


def sites_from_bbed(bbed: dict, area: BaseGeometry, crs: str) -> list[dict]:
    """BBED lots and buildings in `area` that could take a new use: empty or parking, stalled, vacant."""

    zone, north = _utm_zone(crs)
    prepare(area)
    out = []
    for feature in bbed.get("features", []):
        props = feature.get("properties") or {}
        state = canonicalize_status(props.get("F5__Current_status"))
        if state not in CANDIDATE_STATES or not feature.get("geometry"):
            continue
        outline = shape(feature["geometry"])
        if outline.is_empty:
            continue
        if outline.centroid.x < 1000:  # longitude/latitude
            outline = shapely.transform(outline, lambda c: np.array([lonlat_to_utm(x, y, zone, north) for x, y in c]))
        point = outline.representative_point()
        if not contains_xy(area, point.x, point.y):
            continue
        out.append({"id": f"bbed/{props.get('OBJECTID')}", "x": point.x, "y": point.y,
                    "area_m2": round(outline.area, 1), "state": state.value, "parcel": props.get("ParcelID"),
                    "recorded_use": props.get("Building_Use"), "source": "BBED 2024",
                    "allowed": list(SERVICE_KEYS), "allowed_source": "not yet known: every use allowed"})
    return out


def sites_from_geojson(collection: dict, crs: str) -> list[dict]:
    """Extra candidate sites (rail bed pieces, station land, sites from government lists).

    Each feature needs `properties.id`; `allowed` (a list of services) limits its uses,
    and `name`, `owner`, `source` are kept. Geometry in longitude/latitude.
    """

    zone, north = _utm_zone(crs)
    out = []
    for feature in collection.get("features", []):
        props = feature.get("properties") or {}
        if not props.get("id"):
            raise ValueError("every extra site needs properties.id")
        geometry = shapely.transform(shape(feature["geometry"]),
                                     lambda c: np.array([lonlat_to_utm(x, y, zone, north) for x, y in c]))
        point = geometry.representative_point()
        x, y = point.x, point.y
        area = props.get("area_m2", round(geometry.area, 1) if geometry.area > 0 else None)
        allowed = props.get("allowed") or list(SERVICE_KEYS)
        unknown = set(allowed) - set(SERVICE_KEYS)
        if unknown:
            raise ValueError(f"site {props['id']}: unknown uses {sorted(unknown)}")
        out.append({"id": str(props["id"]), "x": x, "y": y, "area_m2": area,
                    "name": props.get("name", ""), "owner": props.get("owner"), "state": props.get("state"),
                    "source": props.get("source", "extra sites"), "allowed": list(allowed),
                    "allowed_source": props.get("allowed_source", "given" if props.get("allowed") else
                                                "not yet known: every use allowed")})
    return out


def limit_by_size(sites: list[dict], min_area_m2: dict[str, float]) -> int:
    """Drop uses from sites smaller than the use's minimum; sites of unknown size keep them.

    Returns how many (site, use) pairs were removed. There is no default: no
    Lebanese size standard has been given yet.
    """

    removed = 0
    for site in sites:
        area = site.get("area_m2")
        if area is None:
            continue
        keep = [u for u in site["allowed"] if area >= min_area_m2.get(u, 0)]
        removed += len(site["allowed"]) - len(keep)
        if len(keep) != len(site["allowed"]):
            site["allowed"] = keep
            site["allowed_source"] = f"{site.get('allowed_source', '')}; minimum sizes {min_area_m2}"
    return removed


def _shortest(graph: WalkGraph, origins: list[tuple[int, float]] | None = None, *,
              rows: np.ndarray | None = None, row_snap: np.ndarray | None = None) -> np.ndarray:
    """Walking metres to every node: from the nearest of `origins` (node, snap metres), or per row."""

    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    n = len(graph)
    if origins is not None:
        # A virtual origin joined to each origin node by its snap distance gives
        # the nearest origin's walk, snap included, in one search.
        source = np.concatenate([graph.source, np.full(len(origins), n)])
        target = np.concatenate([graph.target, [node for node, _ in origins]])
        weight = np.concatenate([graph.length, [max(snap, 1e-6) for _, snap in origins]])
        matrix = coo_matrix((weight, (source, target)), shape=(n + 1, n + 1)).tocsr()
        return dijkstra(matrix, directed=True, indices=n, limit=LIMIT_M)[:n]
    matrix = coo_matrix((graph.length, (graph.source, graph.target)), shape=(n, n)).tocsr()
    out = dijkstra(matrix, directed=True, indices=rows, limit=LIMIT_M)
    return out + row_snap[:, None]


@dataclass
class AccessModel:
    """Walking distance from every home to each service, now and from each candidate site."""

    services: tuple[str, ...]
    home_ids: np.ndarray
    home_xy: np.ndarray
    residents: np.ndarray
    base: np.ndarray  # (services, homes) metres to the nearest mapped place, inf beyond LIMIT_M
    site_ids: list[str]
    site_dist: np.ndarray  # (sites, homes) metres from each candidate site
    sites: list[dict]
    destination_counts: dict[str, int]
    thresholds: tuple[float, ...] = (PAPER_M, FIFTEEN_MIN_M)
    beta: float = BETA_PER_M
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self._service = {k: i for i, k in enumerate(self.services)}
        self._site = {k: i for i, k in enumerate(self.site_ids)}
        self._allowed = {s["id"]: set(s["allowed"]) for s in self.sites}
        total = float(self.residents.sum())
        self._weight = self.residents / total if total > 0 else np.full(len(self.residents), 1 / max(len(self.residents), 1))
        chosen = self.meta.get("score_services") or list(DEFAULT_SCORED)
        self._scored = np.array([self._service[k] for k in chosen if k in self._service], dtype=int)

    # -- plans -------------------------------------------------------------

    def distances(self, uses: dict[str, str] | None = None) -> np.ndarray:
        """(services, homes) walking metres once the plan's sites take their new uses."""

        d = self.base.copy()
        for site, service in (uses or {}).items():
            if site not in self._site:
                raise ValueError(f"unknown site {site!r}")
            if service not in self._service:
                raise ValueError(f"unknown service {service!r}; use one of {list(self.services)}")
            if service not in self._allowed[site]:
                raise ValueError(f"site {site} may not take {service}")
            k = self._service[service]
            d[k] = np.minimum(d[k], self.site_dist[self._site[site]])
        return d

    def service_score(self, d: np.ndarray, threshold: float) -> float:
        """The paper's service score: share of scored services each home reaches, resident-weighted."""

        if len(self._scored) == 0:
            return 0.0
        reached = (d[self._scored] <= threshold).mean(axis=0)
        return float(self._weight @ reached)

    def objective(self, d: np.ndarray, alpha: float = 1.0) -> float:
        """The paper's land-use reward, alpha x service score + green score, both at the first
        threshold (the paper's 500 m). The green score is the share of residents near a park;
        the paper used alpha 2 for service-led and 0.5 for green-led plans.

        At 15 minutes most corridor homes already reach every service, so plans could
        hardly differ; the paper's stricter distance keeps them apart.
        """

        threshold = self.thresholds[0]
        score = alpha * self.service_score(d, threshold)
        if "parks" in self._service:
            score += float(self._weight @ (d[self._service["parks"]] <= threshold))
        return score

    def score(self, uses: dict[str, str] | None = None, alpha: float = 1.0) -> dict:
        d = self.distances(uses)
        out = {"service_score": {_label(t): round(self.service_score(d, t), 4) for t in self.thresholds},
               "objective": round(self.objective(d, alpha), 4), "alpha": alpha,
               "scored_services": [self.services[k] for k in self._scored], "services": {}}
        reached15 = (d[self._scored] <= self.thresholds[-1]).mean(axis=0) if len(self._scored) else np.zeros(d.shape[1])
        out["worst_served_20pct"] = round(_weighted_low_mean(reached15, self._weight, 0.2), 4)
        added = {}
        for service in (uses or {}).values():
            added[service] = added.get(service, 0) + 1
        for key, k in self._service.items():
            row = d[k]
            out["services"][key] = {
                "places": self.destination_counts.get(key, 0) + added.get(key, 0),
                "residents_within": {_label(t): round(float(self._weight @ (row <= t)), 4) for t in self.thresholds},
                "access_index": round(float(self._weight @ np.exp(-self.beta * row)), 4),
                "p80_walk_m": _weighted_quantile(row, self._weight, 0.8),
                "in_score": bool(k in self._scored),
            }
        return out

    # -- search baselines --------------------------------------------------

    def gains(self, alpha: float = 1.0) -> list[dict]:
        """For each site alone: the objective's gain for every allowed use, best first."""

        d = self.base
        start = self.objective(d, alpha)
        out = []
        for site in self.sites:
            row = {}
            for service in site["allowed"]:
                row[service] = round(self.objective(self.distances({site["id"]: service}), alpha) - start, 5)
            best = max(row, key=row.get) if row else None
            out.append({"site": site["id"], "area_m2": site.get("area_m2"), "state": site.get("state"),
                        "best_use": best, "best_gain": row.get(best, 0.0), "gain_by_use": row})
        return sorted(out, key=lambda r: -r["best_gain"])

    def greedy(self, steps: int = 10, alpha: float = 1.0) -> list[dict]:
        """Add the single best (site, use) each step until `steps` or no gain: the simplest search."""

        uses: dict[str, str] = {}
        current = self.objective(self.base, alpha)
        out = []
        for step in range(1, steps + 1):
            best = None
            for site in self.sites:
                if site["id"] in uses:
                    continue
                for service in site["allowed"]:
                    value = self.objective(self.distances({**uses, site["id"]: service}), alpha)
                    if best is None or value > best[0] + 1e-12:
                        best = (value, site["id"], service)
            if best is None or best[0] <= current + 1e-9:
                break
            current, site_id, service = best
            uses[site_id] = service
            site = self.sites[self._site[site_id]]
            out.append({"step": step, "site": site_id, "use": service, "area_m2": site.get("area_m2"),
                        "state": site.get("state"), "objective": round(current, 4)})
        return out

    # -- files -------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        meta = {"schema": SCHEMA, "services": list(self.services), "site_ids": self.site_ids, "sites": self.sites,
                "destination_counts": self.destination_counts, "thresholds": list(self.thresholds),
                "beta": self.beta, "meta": self.meta}
        np.savez_compressed(path, meta=np.array(json.dumps(meta)), home_ids=self.home_ids.astype("<U64"),
                            home_xy=self.home_xy, residents=self.residents, base=self.base.astype(np.float32),
                            site_dist=self.site_dist.astype(np.float32))

    @classmethod
    def load(cls, path: str | Path) -> AccessModel:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("schema") != SCHEMA:
                raise ValueError(f"{path} is not a build-access model")
            return cls(services=tuple(meta["services"]), home_ids=data["home_ids"], home_xy=data["home_xy"],
                       residents=data["residents"], base=data["base"].astype(float), site_ids=meta["site_ids"],
                       site_dist=data["site_dist"].astype(float), sites=meta["sites"],
                       destination_counts=meta["destination_counts"], thresholds=tuple(meta["thresholds"]),
                       beta=meta["beta"], meta=meta["meta"])


def build_access(graph: WalkGraph, homes: Homes, places: list[Place], sites: list[dict], *,
                 services: tuple[str, ...] = SERVICE_KEYS, score_services: tuple[str, ...] | None = None,
                 thresholds: tuple[float, ...] = (PAPER_M, FIFTEEN_MIN_M), meta: dict | None = None) -> AccessModel:
    """Shortest walks from every service and every candidate site to every home."""

    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    if len(homes.ids) == 0:
        raise ValueError("no homes in the area")
    tree = cKDTree(graph.xy)
    home_snap, home_node = tree.query(homes.xy)
    n = len(graph)
    _, label = connected_components(coo_matrix((graph.length, (graph.source, graph.target)), shape=(n, n)),
                                    directed=False)
    main = np.bincount(label).argmax()
    base = np.full((len(services), len(homes.ids)), np.inf)
    counts = {}
    for k, service in enumerate(services):
        own = [p for p in places if p.service == service]
        counts[service] = len(own)
        if not own:
            continue
        snap, node = tree.query(np.array([(p.x, p.y) for p in own]))
        to_nodes = _shortest(graph, list(zip(node.tolist(), snap.tolist())))
        base[k] = to_nodes[home_node] + home_snap
    site_dist = np.full((len(sites), len(homes.ids)), np.inf)
    if sites:
        snap, node = tree.query(np.array([(s["x"], s["y"]) for s in sites]))
        for start in range(0, len(sites), 32):
            rows = node[start:start + 32]
            block = _shortest(graph, rows=rows, row_snap=snap[start:start + 32])
            site_dist[start:start + 32] = block[:, home_node] + home_snap[None, :]
        for site, metres in zip(sites, snap):
            site["snap_m"] = round(float(metres), 1)
    meta = dict(meta or {})
    meta["score_services"] = [k for k in (score_services or DEFAULT_SCORED) if k in services]
    meta["home_snap_over_100m"] = int((home_snap > 100).sum())
    meta["homes_off_main_network"] = int((label[home_node] != main).sum())
    meta["network"] = {"nodes": n, "edges": int(len(graph.length) // 2)}
    return AccessModel(services=tuple(services), home_ids=homes.ids, home_xy=homes.xy, residents=homes.residents,
                       base=base, site_ids=[s["id"] for s in sites], site_dist=site_dist, sites=sites,
                       destination_counts=counts, thresholds=tuple(thresholds), meta=meta)


def _label(threshold: float) -> str:
    return f"{round(threshold)}m"


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float | None:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    i = int(np.searchsorted(cumulative, q * cumulative[-1]))
    value = values[order][min(i, len(order) - 1)]
    return round(float(value), 1) if math.isfinite(value) else None


def _weighted_low_mean(values: np.ndarray, weights: np.ndarray, share: float) -> float:
    """Resident-weighted mean of `values` over the lowest `share` of residents."""

    order = np.argsort(values, kind="stable")
    w = weights[order]
    keep = np.cumsum(w) - w < share * w.sum()
    return float((values[order][keep] * w[keep]).sum() / w[keep].sum()) if keep.any() else 0.0
