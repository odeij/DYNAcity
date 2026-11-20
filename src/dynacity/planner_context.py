"""Optional context layers for the street-planning table: who lives near a street, and what is on it.

Two open datasets, each served only when its file is given to `dynacity serve`:

* WorldPop modelled population (CC BY 4.0), 100 m cells. Residents within 200 m
  of a road are summed from sub-cells of each 100 m cell (a 4 x 4 grid, so about
  25 m), counting a sub-cell when its centre lies within 200 m of the road. A set
  of roads is counted as a union, so a resident near two of them is counted once.
  It is a modelled estimate, not a census count.
* Overture Maps Places. Only an allowlist is kept: id, primary name, primary
  category, confidence and the point. Places within 25 m of a road belong to it.

Both are prepared once into small JSON files (`prepare_population`,
`prepare_places`, driven by `scripts/prepare_planner_context.py`); `PlannerContext`
joins them to the traffic model's roads at serve time, so the road indices always
match the model being served, whether it is citywide or cut to a corridor.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Point

from .traffic import TrafficModel
from .viewer import _utm_zone, lonlat_to_utm

POPULATION_SCHEMA = "dynacity-planner-population/1"
PLACES_SCHEMA = "dynacity-planner-places/1"

RESIDENT_RADIUS_M = 200.0
PLACE_RADIUS_M = 25.0
SUBCELLS = 4  # per side: a 100 m cell is counted as 16 sub-cells of about 25 m
MAX_NAMES = 8
TOP_CATEGORIES = 4

# The only Overture place fields that are ever read from a download.
PLACE_FIELDS = ("id", "name", "category", "confidence", "lon", "lat")


# ---------------------------------------------------------------- preparation
def prepare_population(tif_path: str | Path, output: str | Path, bounds: Sequence[float], *,
                       product: str, year: int, source_url: str, licence: str, attribution: str,
                       doi: str = "") -> dict[str, Any]:
    """Cut a WorldPop GeoTIFF (people per cell, EPSG:4326) to `bounds` and keep its non-zero cells."""
    import rasterio
    from rasterio.windows import from_bounds

    west, south, east, north = (float(v) for v in bounds)
    with rasterio.open(tif_path) as src:
        if src.crs is None or src.crs.to_epsg() != 4326:
            raise ValueError("expected a WorldPop raster in EPSG:4326")
        window = from_bounds(west, south, east, north, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=window, masked=True).astype("float64")
        transform = src.window_transform(window)
    data = np.ma.filled(data, 0.0)
    data[~np.isfinite(data)] = 0.0
    data[data < 0] = 0.0
    rows, cols = np.nonzero(data > 0)
    cells = [[round(float(transform.c + (c + 0.5) * transform.a), 6),
              round(float(transform.f + (r + 0.5) * transform.e), 6),
              round(float(data[r, c]), 3)] for r, c in zip(rows, cols)]
    payload = {
        "schema": POPULATION_SCHEMA, "product": product, "year": int(year), "source_url": source_url,
        "licence": licence, "attribution": attribution, "doi": doi,
        "cell_deg": [abs(float(transform.a)), abs(float(transform.e))],
        "bounds": [west, south, east, north],
        "total": round(float(data.sum()), 1),
        # [longitude, latitude, people] of each non-zero cell's centre.
        "cells": cells,
    }
    Path(output).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return {k: v for k, v in payload.items() if k != "cells"} | {"cell_count": len(cells)}


def allowlisted_place(feature: dict, min_confidence: float = 0.0) -> dict[str, Any] | None:
    """One Overture place feature reduced to the allowlist: id, primary name, primary category, confidence, point.

    Contact details, addresses, brands and social links in the download are never read.
    """
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Point":
        return None
    props = feature.get("properties") or {}
    # Newer releases give the primary category as `taxonomy.primary`; older ones as `categories.primary`.
    category = (props.get("taxonomy") or {}).get("primary") or (props.get("categories") or {}).get("primary")
    pid, confidence = props.get("id") or feature.get("id"), props.get("confidence")
    if not pid or not category or confidence is None or float(confidence) < min_confidence:
        return None
    lon, lat = geometry["coordinates"][:2]
    return {"id": str(pid), "name": str((props.get("names") or {}).get("primary") or ""), "category": str(category),
            "confidence": round(float(confidence), 3), "lon": round(float(lon), 7), "lat": round(float(lat), 7)}


def allowlisted_places(features: Iterable[dict], min_confidence: float = 0.0) -> list[dict[str, Any]]:
    return [row for row in (allowlisted_place(f, min_confidence) for f in features) if row]


def prepare_places(geojson_path: str | Path, output: str | Path, *, release: str, source_url: str,
                   licence: str, attribution: str, min_confidence: float = 0.5) -> dict[str, Any]:
    collection = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    features = collection.get("features", [])
    places, providers = [], Counter()
    for feature in features:
        row = allowlisted_place(feature, min_confidence)
        if row is None:
            continue
        places.append(row)
        # Which providers the kept places came from, for the credits; counted before the rest is dropped.
        for source in (feature.get("properties") or {}).get("sources") or []:
            if source.get("dataset") and source.get("dataset") != "Overture":
                providers[f"{source['dataset']} ({source.get('license', '?')})"] += 1
    payload = {"schema": PLACES_SCHEMA, "release": release, "source_url": source_url, "licence": licence,
               "attribution": attribution, "min_confidence": min_confidence, "downloaded": len(features),
               "providers": dict(providers.most_common()), "places": places}
    Path(output).write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return {k: v for k, v in payload.items() if k != "places"} | {"kept": len(places)}


# ---------------------------------------------------------------- rounding
def round_residents(n: float) -> int:
    """Round a modelled head-count the way its precision deserves: coarser as it grows."""
    if n < 1:
        return 0
    step = 10 if n < 100 else 50 if n < 1000 else 100 if n < 10000 else 500
    return int(round(n / step) * step) or step


def pretty_category(category: str) -> str:
    return category.replace("_", " ")


# ---------------------------------------------------------------- the join
class PlannerContext:
    """Residents near each road and the places on it, for the model being served."""

    def __init__(self, model: TrafficModel, population: dict[str, Any] | None, places: dict[str, Any] | None):
        if population is not None and population.get("schema") != POPULATION_SCHEMA:
            raise ValueError("not a planner population file")
        if places is not None and places.get("schema") != PLACES_SCHEMA:
            raise ValueError("not a planner places file")
        self.population, self.places = population, places
        self._lines = [LineString(g) for g in model.geometry]
        self._road_count = len(self._lines)
        self._zone = _utm_zone(model.crs)
        self._resident_cache: dict[int, np.ndarray] = {}
        self._summary_cache: dict[tuple[int, ...], dict[str, Any]] = {}
        self._pop_tree: STRtree | None = None
        self._pop_weight = np.zeros(0)
        if population is not None:
            self._index_population()
        self._place_rows: list[dict[str, Any]] = []
        self._place_roads: list[list[int]] = []
        self._by_road: dict[int, list[int]] = {}
        if places is not None:
            self._index_places()

    @classmethod
    def load(cls, model: TrafficModel, population_path: str | Path | None = None,
             places_path: str | Path | None = None) -> PlannerContext:
        read = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None  # noqa: E731
        return cls(model, read(population_path), read(places_path))

    # -- indexing
    def _xy(self, lon: float, lat: float) -> tuple[float, float]:
        return lonlat_to_utm(lon, lat, *self._zone)

    def _index_population(self) -> None:
        dlon, dlat = self.population["cell_deg"]
        offsets = [((i + 0.5) / SUBCELLS - 0.5) for i in range(SUBCELLS)]
        xs, ys, weights = [], [], []
        per = 1.0 / (SUBCELLS * SUBCELLS)
        for lon, lat, people in self.population["cells"]:
            for oy in offsets:
                for ox in offsets:
                    x, y = self._xy(lon + ox * dlon, lat + oy * dlat)
                    xs.append(x); ys.append(y); weights.append(people * per)
        self._pop_xy = np.column_stack([xs, ys]) if xs else np.zeros((0, 2))
        self._pop_weight = np.asarray(weights, dtype=np.float64)
        self._pop_tree = STRtree([Point(x, y) for x, y in self._pop_xy]) if xs else None

    def _index_places(self) -> None:
        rows = self.places["places"]
        points = [Point(*self._xy(p["lon"], p["lat"])) for p in rows]
        roads = STRtree(self._lines)
        near = [[] for _ in rows]
        if points:
            point_idx, road_idx = roads.query(points, predicate="dwithin", distance=PLACE_RADIUS_M)
            for p, r in zip(point_idx.tolist(), road_idx.tolist()):
                near[p].append(r)
        self._place_rows, self._place_roads = rows, near
        for p, rs in enumerate(near):
            for r in rs:
                self._by_road.setdefault(r, []).append(p)

    # -- per road
    def _residents_near(self, road: int) -> np.ndarray:
        """Indices of the population sub-cells within 200 m of one road."""
        if road not in self._resident_cache:
            if self._pop_tree is None:
                self._resident_cache[road] = np.zeros(0, dtype=np.int64)
            else:
                self._resident_cache[road] = np.sort(
                    self._pop_tree.query(self._lines[road], predicate="dwithin", distance=RESIDENT_RADIUS_M))
        return self._resident_cache[road]

    def road_residents(self, road: int) -> float:
        return float(self._pop_weight[self._residents_near(road)].sum())

    def road_places(self, road: int) -> list[int]:
        return self._by_road.get(road, [])

    # -- for a set of roads
    def summary(self, roads: Iterable[int]) -> dict[str, Any]:
        """Residents (union, counted once) and places for a set of road indices."""
        ids = tuple(sorted({int(r) for r in roads if 0 <= int(r) < self._road_count}))
        if ids in self._summary_cache:
            return self._summary_cache[ids]
        out: dict[str, Any] = {"roads": len(ids)}
        if self.population is not None:
            parts = [self._residents_near(r) for r in ids]
            union = np.unique(np.concatenate(parts)) if parts else np.zeros(0, dtype=np.int64)
            residents = float(self._pop_weight[union].sum())
            out["residents"] = round(residents, 1)
            out["residents_rounded"] = round_residents(residents)
        if self.places is not None:
            seen = sorted({p for r in ids for p in self.road_places(r)},
                          key=lambda p: (-self._place_rows[p]["confidence"], self._place_rows[p]["name"].lower()))
            rows = [self._place_rows[p] for p in seen]
            cats = Counter(r["category"] for r in rows).most_common()
            out["places"] = {
                "count": len(rows),
                "categories": [[pretty_category(c), n] for c, n in cats[:TOP_CATEGORIES]],
                "other_categories": max(0, len(cats) - TOP_CATEGORIES),
                "names": [r["name"] for r in rows if r["name"]][:MAX_NAMES],
                "unnamed": sum(1 for r in rows if not r["name"]),
            }
        if len(self._summary_cache) > 2000:
            self._summary_cache.clear()
        self._summary_cache[ids] = out
        return out

    # -- for the page
    def meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {"residents_radius_m": RESIDENT_RADIUS_M, "places_radius_m": PLACE_RADIUS_M}
        if self.population is not None:
            p = self.population
            out["population"] = {k: p.get(k) for k in ("product", "year", "licence", "attribution", "source_url", "doi")}
        if self.places is not None:
            p = self.places
            out["places"] = {k: p.get(k) for k in ("release", "licence", "attribution", "source_url", "providers")}
            # Only places that sit on a served street are drawn, with the allowlisted fields only.
            out["places"]["points"] = [
                [self._place_rows[i]["lon"], self._place_rows[i]["lat"], self._place_rows[i]["name"],
                 pretty_category(self._place_rows[i]["category"])]
                for i, rs in enumerate(self._place_roads) if rs]
        return out

    # -- for checks and reports
    def distribution(self) -> dict[str, Any]:
        """Per-road residents and place counts over every road, for reports."""
        out: dict[str, Any] = {"roads": self._road_count}
        if self.population is not None:
            res = np.asarray([self.road_residents(r) for r in range(self._road_count)])
            q = np.percentile(res, [0, 10, 25, 50, 75, 90, 99, 100]) if len(res) else np.zeros(8)
            out["residents_within_200m"] = {
                "min": round(float(q[0]), 1), "p10": round(float(q[1]), 1), "p25": round(float(q[2]), 1),
                "median": round(float(q[3]), 1), "p75": round(float(q[4]), 1), "p90": round(float(q[5]), 1),
                "p99": round(float(q[6]), 1), "max": round(float(q[7]), 1), "roads_with_zero": int((res < 1).sum())}
            everyone = self.summary(range(self._road_count))
            out["union_all_roads"] = everyone.get("residents")
        if self.places is not None:
            counts = [len(self.road_places(r)) for r in range(self._road_count)]
            out["roads_with_a_place"] = int(sum(1 for c in counts if c))
            out["places_per_road_max"] = max(counts) if counts else 0
            on_street = [self._place_rows[i] for i, rs in enumerate(self._place_roads) if rs]
            out["places_on_a_street"] = len(on_street)
            out["places_in_file"] = len(self._place_rows)
            out["places_by_category"] = dict(Counter(pretty_category(r["category"]) for r in on_street).most_common())
        return out

