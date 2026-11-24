"""The street-planning table: a 2D map page where a planner edits streets and runs SUMO.

The page (`planner_template.html`) draws today's OpenStreetMap basemap inside an
old-map frame, lets the planner pick road sections of the traffic model and
close them, take a lane away or slow them, and then runs the paired SUMO
comparison through the existing `/v1/viewer/traffic/simulation` routes.

`planner_roads` is the only data the page gets from the server: each road's
path, name, class, motor lanes per direction, one-way flag and free-flow
speed. No probe readings or per-object attributes are sent.
"""
from __future__ import annotations

from importlib.resources import files
from typing import Any

from .traffic import TrafficModel, lane_counts
from .viewer import _utm_zone, utm_to_lonlat


def planner_roads(model: TrafficModel) -> dict[str, Any]:
    """GeoJSON of the model's roads; each feature's `id` is the road index the planning API takes."""
    zone, north = _utm_zone(model.crs)
    lanes = lane_counts(model)
    features = []
    for i, line in enumerate(model.geometry):
        props: dict[str, Any] = {
            "name": model.name[i] if model.name else "",
            "cls": model.highway[i],
            "lanes": int(lanes[i]),
            "oneway": int(model.oneway[i]),
            "free": int(round(float(model.free_kmh[i]))),
        }
        if model.level is not None and model.level[i]:
            props["lvl"] = int(model.level[i])
        features.append({
            "type": "Feature",
            "id": i,
            "properties": props,
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(c, 6) for c in utm_to_lonlat(x, y, zone, north)] for x, y in line],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def planner_trees(landscape: dict[str, Any] | None) -> dict[str, Any]:
    """The same tree points the 3D viewer draws (`landscape.landscape_payload`), stripped for a 2D map.

    Each point keeps only whether it is an individually mapped OSM tree (`row: 0`) or
    one placed at illustrative spacing along a mapped tree row (`row: 1`), and its crown
    width in metres when OSM tags one. Positions are already longitude/latitude.
    """
    features = []
    for f in (landscape or {}).get("features", []):
        p = f.get("properties", {})
        if p.get("kind") != "tree" or f.get("geometry", {}).get("type") != "Point":
            continue
        props: dict[str, Any] = {"row": 1 if str(p.get("id", "")).startswith("way/") else 0}
        if p.get("crown_diameter_m"):
            props["crown"] = round(float(p["crown_diameter_m"]), 1)
        lon, lat = f["geometry"]["coordinates"][:2]
        features.append({"type": "Feature", "properties": props,
                         "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}})
    return {"type": "FeatureCollection", "features": features,
            "attribution": "Trees © OpenStreetMap contributors (ODbL)"}


CONTEXT_MARK = "/*PLANNER_CONTEXT*/false"


def planner_page(context: bool = False) -> str:
    """The page. `context` switches on the optional population and places layers (`planner_context.py`).

    Without it the page is the one it always was: the layers' code stays dormant and
    asks the server for nothing.
    """
    page = files("dynacity").joinpath("planner_template.html").read_text(encoding="utf-8")
    return page.replace(CONTEXT_MARK, "true") if context else page
