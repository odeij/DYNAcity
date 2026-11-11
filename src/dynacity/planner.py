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


def planner_page() -> str:
    return files("dynacity").joinpath("planner_template.html").read_text(encoding="utf-8")
