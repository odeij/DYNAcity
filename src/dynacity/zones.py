"""Decision zones: the areas an adaptation policy acts on, one choice per zone per step.

Costa et al. act on Copenhagen's 29 national transport-model zones (see
docs/rl-flood-transport.md). Beirut has no published transport zones, so the
zones here come from OpenStreetMap's administrative boundaries, finest level
first: the municipality's sectors (admin_level 8, about sixty), then any
coarser boundary (admin_level 7: the quarters, and the neighbouring
municipalities such as Burj Hammoud and Sinn El Fil) only where no sector
lies, then a regular grid for whatever the road model still reaches. Every
road then belongs to exactly one zone (by its midpoint), and so does every
building (by its centroid).

Sectors are administrative units, not traffic or drainage catchments: they
follow streets and property lines, and a flood or a detour crosses them
freely. `adjacency` lists the zones that touch, which a graph policy (the
2026 paper's GCN) uses as its edges.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from shapely import intersects_xy
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from .contracts import UrbanStateSnapshot
from .traffic import OVERPASS_URL, TrafficModel, _overpass
from .viewer import _utm_zone, lonlat_to_utm, utm_to_lonlat

ATTRIBUTION = "Zone boundaries © OpenStreetMap contributors, ODbL 1.0."
# Finest first: a coarser boundary only fills where no finer zone lies.
ADMIN_LEVELS = ("8", "7")
# Cell size of the grid that covers roads outside every boundary.
GRID_M = 500.0
# Pieces smaller than this (slivers left where boundaries almost meet) are dropped.
MIN_ZONE_M2 = 10_000.0
# Zones closer than this count as touching.
TOUCH_M = 1.0
# A building centroid, or a road midpoint without the grid, in no zone (a
# dropped sliver, a gap between boundaries) joins the nearest one up to this
# far away; farther, it lies beyond the zones.
SNAP_M = 100.0


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    # "osm admin_level 8", "grid 500 m", ...
    source: str
    # In the traffic model's CRS (metres).
    area: Polygon | MultiPolygon


@dataclass
class ZoneMap:
    crs: str
    zones: list[Zone]
    # Index into `zones` for each road of the traffic model it was built on.
    road_zone: np.ndarray
    # Pairs (i, j), i < j, of zones that touch.
    adjacency: list[tuple[int, int]]
    source: str = ATTRIBUTION

    def __len__(self) -> int:
        return len(self.zones)

    def check(self, model: TrafficModel) -> None:
        if model.crs != self.crs or len(model) != len(self.road_zone):
            raise ValueError(
                f"zones were built for {len(self.road_zone)} roads in {self.crs}, "
                f"not this traffic model ({len(model)} roads in {model.crs})"
            )

    def locate(self, x: np.ndarray, y: np.ndarray, *, snap_m: float = 0.0) -> np.ndarray:
        """Zone index of each point (the first zone wins on a shared border).

        A point in no zone goes to the nearest one within `snap_m` (a dropped
        sliver, a gap between boundaries), else -1.
        """

        out = np.full(len(x), -1, dtype=np.int64)
        for i, zone in enumerate(self.zones):
            free = out < 0
            if not free.any():
                break
            hit = intersects_xy(zone.area, x[free], y[free])
            out[np.flatnonzero(free)[hit]] = i
        lost = np.flatnonzero(out < 0)
        if len(lost) and self.zones and snap_m > 0:
            from shapely import points

            tree = STRtree([z.area for z in self.zones])
            near, gap = tree.query_nearest(points(x[lost], y[lost]), return_distance=True, all_matches=False)
            ok = gap <= snap_m
            out[lost[near[0][ok]]] = near[1][ok]
        return out

    def object_zones(self, snapshot: UrbanStateSnapshot) -> np.ndarray:
        """Zone index of each snapshot object by its centroid (snapped up to `SNAP_M`);
        -1 without a centroid or beyond the zones."""

        if snapshot.crs != self.crs:
            raise ValueError(f"zones are in {self.crs}, snapshot in {snapshot.crs}")
        xy = np.array([(o.centroid_x_m if o.centroid_x_m is not None else np.nan,
                        o.centroid_y_m if o.centroid_y_m is not None else np.nan) for o in snapshot.objects], float)
        out = np.full(len(xy), -1, dtype=np.int64)
        placed = ~np.isnan(xy).any(axis=1) if len(xy) else np.zeros(0, bool)
        if placed.any():
            out[placed] = self.locate(xy[placed, 0], xy[placed, 1], snap_m=SNAP_M)
        return out

    def summary(self, model: TrafficModel) -> list[dict[str, Any]]:
        """Per zone: id, name, source, area, and the roads it holds."""

        self.check(model)
        roads = np.bincount(self.road_zone[self.road_zone >= 0], minlength=len(self))
        km = np.bincount(self.road_zone[self.road_zone >= 0], weights=model.length_m[self.road_zone >= 0],
                         minlength=len(self)) / 1000.0
        return [
            {"zone_id": z.zone_id, "name": z.name, "source": z.source, "area_km2": round(z.area.area / 1e6, 3),
             "roads": int(roads[i]), "road_km": round(float(km[i]), 2)}
            for i, z in enumerate(self.zones)
        ]

    def save(self, path: str | Path) -> None:
        def rounded(geometry: Polygon | MultiPolygon) -> dict:
            return json.loads(json.dumps(mapping(geometry)), parse_float=lambda v: round(float(v), 1))

        payload = {
            "schema": "dynacity-zones/1",
            "crs": self.crs,
            "source": self.source,
            "zones": [{"id": z.zone_id, "name": z.name, "source": z.source, "geometry": rounded(z.area)}
                      for z in self.zones],
            "road_zone": [int(i) for i in self.road_zone],
            "adjacency": [list(pair) for pair in self.adjacency],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ZoneMap:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != "dynacity-zones/1":
            raise ValueError(f"{path} is not a dynacity zone map")
        return cls(
            crs=payload["crs"],
            zones=[Zone(z["id"], z["name"], z["source"], shape(z["geometry"])) for z in payload["zones"]],
            road_zone=np.asarray(payload["road_zone"], dtype=np.int64),
            adjacency=[(int(a), int(b)) for a, b in payload["adjacency"]],
            source=payload.get("source", ATTRIBUTION),
        )


def model_extent(model: TrafficModel) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees around the traffic model's roads."""

    points = np.concatenate([np.asarray(line, float) for line in model.geometry])
    (x0, y0), (x1, y1) = points.min(axis=0), points.max(axis=0)
    zone, north = _utm_zone(model.crs)
    corners = [utm_to_lonlat(x, y, zone, north) for x in (x0, x1) for y in (y0, y1)]
    return min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners)


def overpass_zone_query(bbox: tuple[float, float, float, float], levels: tuple[str, ...] = ADMIN_LEVELS) -> str:
    west, south, east, north = bbox
    pattern = "|".join(levels)
    return (
        f'[out:json][timeout:120];relation["boundary"="administrative"]["admin_level"~"^({pattern})$"]'
        f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});out body geom;"
    )


def fetch_osm_zones(bbox: tuple[float, float, float, float], *, levels: tuple[str, ...] = ADMIN_LEVELS,
                    url: str = OVERPASS_URL) -> dict:
    """Administrative boundary relations touching the box, as Overpass JSON with member geometry."""

    return _overpass(overpass_zone_query(bbox, levels), url)


def boundary_polygon(relation: dict, crs: str) -> Polygon | MultiPolygon | None:
    """A boundary relation's outer rings less its inner ones, in `crs`; None when the rings do not close."""

    zone, north = _utm_zone(crs)

    def lines(role: str) -> list[LineString]:
        out = []
        for member in relation.get("members", []):
            points = member.get("geometry") or []
            if member.get("type") == "way" and member.get("role", "outer") == role and len(points) > 1:
                out.append(LineString([lonlat_to_utm(p["lon"], p["lat"], zone, north) for p in points]))
        return out

    outer = list(polygonize(unary_union(lines("outer"))))
    if not outer:
        return None
    area = unary_union(outer)
    inner = list(polygonize(unary_union(lines("inner"))))
    if inner:
        area = _polygonal(area.difference(unary_union(inner)))
    return area if not area.is_empty else None


def _polygonal(geometry: Any) -> Polygon | MultiPolygon:
    """The polygon parts of an overlay result (which can carry stray lines or points)."""

    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    parts = [g for g in getattr(geometry, "geoms", ()) if isinstance(g, (Polygon, MultiPolygon))]
    return unary_union(parts) if parts else Polygon()


def _named(tags: dict) -> str:
    return tags.get("name:en") or tags.get("name:fr") or tags.get("name") or ""


def build_zones(
    model: TrafficModel,
    boundaries: dict | None = None,
    *,
    levels: tuple[str, ...] = ADMIN_LEVELS,
    grid_m: float | None = GRID_M,
) -> ZoneMap:
    """Zones over the traffic model's roads from OSM boundaries (fetched when `boundaries` is None).

    Only zones that hold at least one road are kept, clipped to the roads'
    bounding box. With `grid_m` None, roads outside every boundary get no zone.
    """

    if boundaries is None:
        boundaries = fetch_osm_zones(model_extent(model), levels=levels)
    points = np.concatenate([np.asarray(line, float) for line in model.geometry])
    (x0, y0), (x1, y1) = points.min(axis=0), points.max(axis=0)
    frame = box(x0, y0, x1, y1).buffer(50.0, join_style="mitre")
    mid = np.asarray([LineString(line).interpolate(0.5, normalized=True).coords[0] for line in model.geometry])

    zones: list[Zone] = []
    taken = Polygon()
    for level in levels:
        relations = sorted(
            (e for e in boundaries.get("elements", [])
             if e.get("type") == "relation" and e.get("tags", {}).get("admin_level") == level),
            key=lambda e: e["id"],
        )
        found = []
        for relation in relations:
            area = boundary_polygon(relation, model.crs)
            if area is None:
                continue
            area = _polygonal(area.intersection(frame).difference(taken))
            if area.area >= MIN_ZONE_M2:
                found.append(Zone(f"osm:r{relation['id']}", _named(relation.get("tags", {})),
                                  f"osm admin_level {level}", area))
        zones += found
        if found:
            taken = unary_union([taken, *(z.area for z in found)])

    if grid_m:
        rest = _polygonal(frame.difference(taken))
        for i in range(math.ceil((x1 - x0 + 100) / grid_m)):
            for j in range(math.ceil((y1 - y0 + 100) / grid_m)):
                cell = _polygonal(box(x0 - 50 + i * grid_m, y0 - 50 + j * grid_m,
                                      x0 - 50 + (i + 1) * grid_m, y0 - 50 + (j + 1) * grid_m).intersection(rest))
                if cell.area >= MIN_ZONE_M2:
                    zones.append(Zone(f"grid:{i}_{j}", "", f"grid {grid_m:g} m", cell))

    probe = ZoneMap(crs=model.crs, zones=zones, road_zone=np.zeros(0, np.int64), adjacency=[])
    # With the grid, the zones fill the frame but for dropped slivers, and
    # every midpoint lies inside it, so snapping places them all.
    located = probe.locate(mid[:, 0], mid[:, 1], snap_m=math.inf if grid_m else SNAP_M)
    keep = np.unique(located[located >= 0])
    renumber = np.full(len(zones) + 1, -1, dtype=np.int64)
    renumber[keep] = np.arange(len(keep))
    zones = [zones[i] for i in keep]
    return ZoneMap(crs=model.crs, zones=zones, road_zone=renumber[located], adjacency=_adjacency(zones))


def _adjacency(zones: list[Zone]) -> list[tuple[int, int]]:
    if not zones:
        return []
    tree = STRtree([z.area for z in zones])
    pairs = set()
    for i, zone in enumerate(zones):
        for j in tree.query(zone.area.buffer(TOUCH_M), predicate="intersects"):
            if i < int(j):
                pairs.add((i, int(j)))
    return sorted(pairs)
