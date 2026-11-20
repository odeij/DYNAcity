"""Plan Unreal canopy-estimate trees from the Meta/WRI crown-peak candidates (pure Python, no Unreal).

The editor script ``corridor_canopy_estimate.py`` consumes the plan written here, adds the checks that need the live
scene (ground trace, what the trace hits, a ring of clearance traces) and spawns one reversible instance group.

Coordinates follow the project's established path: EPSG:4326 -> EPSG:32636 -> city-local metres (subtract the city
``origin_utm_m``) -> Unreal centimetres through the scene basis (``dynacity_tools._basis``; for the Beirut city world
origin 0, X east 100 cm/m, Y *south* 100 cm/m, Z up 100 cm/m).  The candidates are model estimates of crown tops, not
observed trunks; positions are good to about 10 m and are never shifted here.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

MIN_HEIGHT_M = 3.0
MAX_SCALE = 6.0
LABEL_PREFIX = "Corridor canopy estimate (Meta/WRI 2019)"
TAG_GROUP = "DYNACITY_CORRIDOR_CANOPY_ESTIMATE"
TAGS = (TAG_GROUP, "ESTIMATED_NOT_SURVEYED", "META_WRI_CHM_2019_06_03")

# Surfaces a trunk may stand on, by hit-actor label family.  Everything else is a skip reason.
_GROUND_EXACT = {"source-ground", "StreetCover_TerrainExtension", "StreetCover_TerrainGapRepair"}
_GROUND_PREFIX = ("terrain-", "terrain_")
# StreetCover_* families that are paved vehicle surfaces rather than ground cover.
_PAVED_VEHICLE_COVER = ("parking", "cycleway", "lane_divider", "carriageway", "roadway")


@dataclass(frozen=True)
class Basis:
    """Scene basis: Unreal position = origin + east*x + north*y + up*z, all in centimetres per source metre."""

    origin: tuple[float, float, float]
    east: tuple[float, float, float]
    north: tuple[float, float, float]
    up: tuple[float, float, float]

    def to_unreal(self, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
        return tuple(self.origin[i] + self.east[i] * x + self.north[i] * y + self.up[i] * z for i in range(3))

    def to_local(self, ux: float, uy: float, uz: float = 0.0) -> tuple[float, float, float]:
        """Inverse for an axis-aligned basis (the only kind the project uses)."""
        d = (ux - self.origin[0], uy - self.origin[1], uz - self.origin[2])
        out = []
        for axis in (self.east, self.north, self.up):
            n2 = sum(a * a for a in axis)
            out.append(sum(d[i] * axis[i] for i in range(3)) / n2)
        return tuple(out)

    @classmethod
    def from_dict(cls, d: dict) -> "Basis":
        return cls(tuple(d["origin"]), tuple(d["east"]), tuple(d["north"]), tuple(d["up"]))

    def as_dict(self) -> dict:
        return {"origin": list(self.origin), "east": list(self.east), "north": list(self.north), "up": list(self.up)}


# The Beirut city world basis as read from the DYNACITY_BASIS_* markers in the live map (recorded in stage1.json).
CITY_BASIS = Basis((0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, -100.0, 0.0), (0.0, 0.0, 100.0))


def lonlat_to_local(lon: float, lat: float, origin_utm_m: Sequence[float], transformer=None) -> tuple[float, float]:
    """EPSG:4326 -> EPSG:32636 -> metres east/north of the city origin."""
    if transformer is None:
        from pyproj import Transformer
        transformer = Transformer.from_crs(4326, 32636, always_xy=True)
    e, n = transformer.transform(lon, lat)
    return e - origin_utm_m[0], n - origin_utm_m[1]


def mesh_scale(height_m: float, mesh_height_cm: float, max_scale: float = MAX_SCALE) -> float:
    """Uniform scale that makes the library mesh as tall as the estimated crown top."""
    if height_m <= 0 or mesh_height_cm <= 0:
        raise ValueError("height and mesh height must be positive")
    scale = height_m * 100.0 / mesh_height_cm
    if not 0.2 <= scale <= max_scale:
        raise ValueError(f"unreasonable tree scale {scale:.2f}")
    return scale


def select_candidates(features: Iterable[dict], min_height_m: float = MIN_HEIGHT_M) -> tuple[list[dict], Counter]:
    """Keep off-footprint candidates at least ``min_height_m`` tall; count what is dropped and why."""
    kept: list[dict] = []
    dropped: Counter = Counter()
    for f in features:
        p = f["properties"]
        if p.get("on_building_footprint"):
            dropped["on_building_footprint"] += 1
        elif float(p["canopy_height_at_peak_m"]) < min_height_m:
            dropped["below_min_height"] += 1
        else:
            kept.append(f)
    return kept, dropped


def classify_surface(label: str | None) -> tuple[str, str]:
    """Map the actor a downward trace first hit to (category, family).

    Categories: ``ground`` (a trunk may stand here), ``road`` (carriageway, ramp or bridge deck), ``building``,
    ``water_surface`` (the sea plane: no terrain above it), ``none`` (no hit) or ``other``.
    """
    if not label:
        return "none", "none"
    if label == "DynaCity_Sea":
        return "water_surface", label
    if label in _GROUND_EXACT or label.startswith(_GROUND_PREFIX):
        return "ground", "terrain" if label.startswith(_GROUND_PREFIX) else label
    if label.startswith(("roads_", "roads-")):
        return "road", "road mesh"
    if label.startswith(("structures-", "structures_")):
        return "road", "transport structure"
    if label.startswith(("buildings_", "buildings-")) or label.startswith(("Corridor visual - ", "Corridor reconstruction - ", "Captured ", "Reference visual")):
        return "building", "building mesh"
    if label.startswith("StreetCover_"):
        family = label[len("StreetCover_"):]
        lowered = family.lower()
        if any(k in lowered for k in _PAVED_VEHICLE_COVER):
            return "road", "paved vehicle cover"
        return "ground", "street cover"
    return "other", label


def clearance_verdict(centre: tuple[str, str], ring: Sequence[tuple[str, str]]) -> str | None:
    """Skip reason for one candidate from its trunk trace and a ring of clearance traces, or None if it passes.

    Order matters: a trunk on a carriageway or in a building is reported as such before the clearance rules.
    """
    cat = centre[0]
    if cat == "none":
        return "no_surface_hit"
    if cat == "water_surface":
        return "no_unreal_terrain"  # refined to ``water`` offline when the land mask says sea
    if cat == "road":
        return "trunk_on_carriageway"
    if cat == "building":
        return "trunk_inside_building"
    if cat == "other":
        return "trunk_on_other_object"
    cats = Counter(c for c, _ in ring)
    if cats["road"]:
        return "carriageway_within_clearance"
    if cats["building"]:
        return "building_within_clearance"
    return None


def ring_offsets_cm(radius_cm: float, n: int = 8) -> list[tuple[float, float]]:
    return [(radius_cm * math.cos(2 * math.pi * i / n), radius_cm * math.sin(2 * math.pi * i / n)) for i in range(n)]


def land_mask_is_water(ground, x: float, y: float) -> bool:
    """True when the nearest lattice cell of a ground model is sea/nodata (NaN) or off the grid."""
    import numpy as np
    col = int(math.floor((x - ground.x0) / ground.cell_m))
    row = int(math.floor((ground.y0 - y) / ground.cell_m))
    rows, cols = ground.ground.shape
    if not (0 <= row < rows and 0 <= col < cols):
        return True
    return not bool(np.isfinite(ground.ground[row, col]))


def carriageway_index(roads: Iterable[tuple[Sequence[tuple[float, float]], float]], origin_utm_m: Sequence[float]):
    """STRtree over carriageway polygons (OSM centreline buffered by half the lane-derived width), city-local metres.

    This is the same width rule that builds the Unreal road meshes (``traffic.road_widths``).  Returns (tree, polys).
    """
    from shapely import STRtree
    from shapely.geometry import LineString
    polys = []
    for xy, width in roads:
        if len(xy) < 2 or width <= 0:
            continue
        line = LineString([(x - origin_utm_m[0], y - origin_utm_m[1]) for x, y in xy])
        polys.append(line.buffer(width / 2.0, cap_style="flat"))
    return STRtree(polys), polys


def on_carriageway(index, x: float, y: float, margin_m: float = 0.0) -> bool:
    from shapely.geometry import Point
    tree, polys = index
    pt = Point(x, y)
    for i in tree.query(pt.buffer(margin_m + 0.01)):
        if polys[int(i)].distance(pt) <= margin_m:
            return True
    return False


def stable_choice(source_id: str, n: int, salt: str = "|mesh") -> int:
    """Index in range(n) from a stable hash of the source id (never random; the same id always gets the same mesh)."""
    if n < 1:
        raise ValueError("n must be at least 1")
    import hashlib
    return int.from_bytes(hashlib.sha256((source_id + salt).encode()).digest()[:4], "little") % n


def height_distribution(heights: Sequence[float]) -> dict:
    h = sorted(float(v) for v in heights)
    if not h:
        return {"count": 0}

    def pct(p):
        k = (len(h) - 1) * p
        lo, hi = int(math.floor(k)), int(math.ceil(k))
        return h[lo] + (h[hi] - h[lo]) * (k - lo)

    return {"count": len(h), "min": h[0], "p25": pct(.25), "median": pct(.5), "mean": sum(h) / len(h), "p75": pct(.75),
            "p95": pct(.95), "max": h[-1], "by_metre": {str(int(k)): v for k, v in sorted(Counter(int(v) for v in h).items())}}


def random_sample(items: Sequence, n: int, seed: int = 20261121) -> list:
    rng = random.Random(seed)
    return rng.sample(list(items), min(n, len(items)))


def build_plan(candidates_geojson: dict, origin_utm_m: Sequence[float], basis: Basis, *, ground_mask=None,
               carriageways=None, carriage_margin_m: float = 0.0, transformer=None) -> dict:
    """Offline part of the placement plan: filters, conversion, offline water and carriageway checks."""
    kept, dropped = select_candidates(candidates_geojson["features"])
    skipped: Counter = Counter(dropped)
    rows = []
    for f in kept:
        lon, lat = f["geometry"]["coordinates"]
        e, n = lonlat_to_local(lon, lat, origin_utm_m, transformer)
        utm_x, utm_y = origin_utm_m[0] + e, origin_utm_m[1] + n
        if ground_mask is not None and land_mask_is_water(ground_mask, utm_x, utm_y):
            skipped["water"] += 1
            continue
        if carriageways is not None and on_carriageway(carriageways, e, n, carriage_margin_m):
            skipped["trunk_on_carriageway_osm_width"] += 1
            continue
        ux, uy, _ = basis.to_unreal(e, n, 0.0)
        rows.append({"id": f["properties"]["id"], "lon": lon, "lat": lat, "height_m": float(f["properties"]["canopy_height_at_peak_m"]),
                     "local_e_m": round(e, 3), "local_n_m": round(n, 3), "unreal_x_cm": round(ux, 2), "unreal_y_cm": round(uy, 2)})
    return {"origin_utm_m": list(origin_utm_m), "basis": basis.as_dict(), "candidates_in": len(candidates_geojson["features"]),
            "offline_skipped": dict(skipped), "rows": rows}


def load_plan(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
