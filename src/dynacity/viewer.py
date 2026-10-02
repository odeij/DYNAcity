"""Self-contained 3D viewer: BBED footprints extruded to height, coloured by forecast.

`build_viewer_payload` joins three things by composite object id — BBED
geometry, the snapshot's states and heights, and (optionally) a forecast's
first-step transition probabilities and KPI bands — into one JSON payload.
`render_viewer` embeds that payload into `viewer_template.html`, producing a
single HTML file that renders with deck.gl in any browser.

Served by `dynacity serve --snapshot … --bbed …`, the same page also runs the
compile → check → forecast loop: the prompt bar posts planner prose to
`/v1/viewer/scenario`, which compiles it against the server-side snapshot and
returns both the audit trail and the scenario forecast.

What is embedded is deliberately narrow: outer footprint rings (converted from
UTM to longitude/latitude), height, canonical state, sector, building use, and
model outputs. Per-object
`features` — including the AUB point-cloud morphology — are never written into
the page. The output still contains BBED-derived building data and forecasts,
so write it under `DYNACITY_ARTIFACT_ROOT`, not into the repository.

With a basemap (the default), the page loads CARTO/OpenStreetMap tiles for
streets, water, parks, and pale 3D context buildings, which tells that tile
server the area being viewed. The satellite toggle does the same with Esri's
World Imagery tiles, which are only requested once it is switched on.
`basemap="none"` keeps the page fully offline
apart from the deck.gl script.

`basemap="google"` (served pages only) streams Google Photorealistic 3D Tiles
through the server's `/v1/3dtiles/` proxy, which holds the Maps key, and
drapes the building colours onto that real 3D mesh. A static export cannot use
it: the key would have to be written into the file.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from importlib.resources import files
from typing import TYPE_CHECKING, Any

import numpy as np

from .bbed import resolved_object_ids
from .contracts import ForecastResult, HazardEvent, UrbanStateSnapshot
from .hazards import PROFILES, _exposure, flood_level, hazard_shock, magnitude_of, radius_of
from .kpis import KPI_UNITS
from .status import CanonicalState

if TYPE_CHECKING:
    from .terrain import GroundModel

# Water drawn for a terrain-following flood is capped at this many cells;
# beyond it the grid is thinned (coarser cells), never truncated.
MAX_WATER_CELLS = 40_000

# Used only when a building has neither a recorded height nor a floor count;
# such buildings are flagged `estimated` in the payload and the viewer says so.
STOREY_HEIGHT_M = 3.2
FALLBACK_HEIGHT_M = 6.0

ATTRIBUTION = (
    "Building data: Beirut Built Environment Database (BBED), Beirut Urban Lab, AUB — "
    "Open Database License (ODbL)."
)

_PLACEHOLDER = "/*__DYNACITY_DATA__*/null"

BASEMAPS: dict[str, dict[str, str] | None] = {
    "carto": {
        "style_light": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        "style_dark": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        # OpenStreetMap buildings with heights, drawn as pale 3D context.
        "context_tiles": "https://tiles-a.basemaps.cartocdn.com/vectortiles/carto.streets/v1/{z}/{x}/{y}.mvt",
        "attribution": "Basemap © CARTO, © OpenStreetMap contributors.",
        # Optional colour imagery the page can swap in for the street basemap.
        "satellite_tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "satellite_attribution": "Imagery © Esri, Maxar, Earthstar Geographics.",
    },
    # Photorealistic 3D Tiles via the server's proxy (see google_tiles.py).
    # Terrain and buildings come from Google's mesh, so BBED footprints are
    # draped onto it instead of extruded, and the page shows Google's credits.
    "google": {
        "tiles_3d": "/v1/3dtiles/root.json",
        "attribution": "Google",
    },
    "none": None,
}


def _utm_zone(crs: str) -> tuple[int, bool]:
    match = re.fullmatch(r"EPSG:32([67])(\d\d)", crs.strip().upper())
    if not match:
        raise ValueError(f"viewer needs a WGS84 UTM CRS (EPSG:326xx/327xx), got {crs!r}")
    return int(match.group(2)), match.group(1) == "6"


def utm_to_lonlat(x: float, y: float, zone: int, north: bool = True) -> tuple[float, float]:
    """Inverse transverse Mercator on WGS84 (Snyder 1987, eqs. 8-12 to 8-25).

    Sub-metre across a UTM zone, which is ample for drawing footprints on a
    basemap. A metric offset from the centroid would not do: Beirut sits 2.5°
    east of zone 36's central meridian, where grid north is rotated ~1.4° from
    true north, enough to slide footprints off their streets.
    """

    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x -= 500000.0
    if not north:
        y -= 10000000.0
    mu = (y / k0) / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * sin1**2)
    r1 = a * (1 - e2) / (1 - e2 * sin1**2) ** 1.5
    t1, c1 = tan1**2, ep2 * cos1**2
    d = x / (n1 * k0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / cos1
    return math.degrees(lon) + (zone * 6 - 183), math.degrees(lat)


def lonlat_to_utm(lon: float, lat: float, zone: int, north: bool = True) -> tuple[float, float]:
    """Forward transverse Mercator on WGS84 (Snyder 1987, eqs. 8-9 to 8-10).

    The inverse of `utm_to_lonlat`, for turning a point dropped on the map
    back into snapshot metres.
    """

    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi = math.radians(lat)
    sin_phi, cos_phi, tan_phi = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1 - e2 * sin_phi**2)
    t, c = tan_phi**2, ep2 * cos_phi**2
    big_a = cos_phi * math.radians(lon - (zone * 6 - 183))
    m = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
        - (35 * e2**3 / 3072) * math.sin(6 * phi)
    )
    x = k0 * n * (
        big_a
        + (1 - t + c) * big_a**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * big_a**5 / 120
    ) + 500000.0
    y = k0 * (m + n * tan_phi * (
        big_a**2 / 2
        + (5 - t + 9 * c + 4 * c**2) * big_a**4 / 24
        + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * big_a**6 / 720
    ))
    return x, y if north else y + 10000000.0


def drop_to_metres(points: Sequence[tuple[float, float]], crs: str) -> list[tuple[float, float]]:
    """[longitude, latitude] points from the page → snapshot CRS metres."""

    zone, north = _utm_zone(crs)
    return [lonlat_to_utm(lon, lat, zone, north) for lon, lat in points]


def _outer_rings(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry.get("type") == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def build_viewer_payload(
    collection: dict,
    snapshot: UrbanStateSnapshot,
    forecast: ForecastResult | None = None,
    *,
    api: bool = False,
    basemap: str = "carto",
) -> dict[str, Any]:
    """Join geometry, snapshot, and forecast into the viewer's data contract.

    Footprints are converted from the snapshot's UTM CRS to longitude/latitude
    here, so the page needs no projection library. Buildings in the snapshot
    without geometry are counted and reported, not drawn.
    """

    if basemap not in BASEMAPS:
        raise ValueError(f"unknown basemap {basemap!r}; choose from {sorted(BASEMAPS)}")
    if basemap == "google" and not api:
        raise ValueError(
            "the google basemap needs `dynacity serve`, which keeps the Maps key on the server; "
            "a static export would have to embed it"
        )
    zone, north = _utm_zone(snapshot.crs)

    if forecast is not None and forecast.baseline_snapshot_id != snapshot.snapshot_id:
        raise ValueError("forecast was produced for a different snapshot")
    features = collection.get("features", [])
    geometry_by_id = dict(zip(resolved_object_ids(features), (f.get("geometry") for f in features), strict=True))

    rings_by_id = {
        object_id: [
            [[round(v, 7) for v in utm_to_lonlat(x, y, zone, north)] for x, y, *_ in ring]
            for ring in _outer_rings(geometry)
        ]
        for object_id, geometry in geometry_by_id.items()
    }
    lons = [p[0] for rings in rings_by_id.values() for ring in rings for p in ring]
    lats = [p[1] for rings in rings_by_id.values() for ring in rings for p in ring]
    if not lons:
        raise ValueError("BBED collection contains no polygon geometry")

    buildings, missing_geometry, estimated = [], 0, 0
    for item in snapshot.objects:
        rings = rings_by_id.get(item.object_id)
        if not rings:
            missing_geometry += 1
            continue
        if item.height_m:
            height, is_estimate = item.height_m, False
        elif item.floors:
            height, is_estimate = item.floors * STOREY_HEIGHT_M, True
        else:
            height, is_estimate = FALLBACK_HEIGHT_M, True
        estimated += is_estimate
        record: dict[str, Any] = {
            "id": item.object_id,
            "rings": rings,
            "h": round(height, 2),
            "est": is_estimate,
            "state": item.state.value,
            "sector": item.sector,
            "use": item.building_use,
            "floors": item.floors,
        }
        buildings.append(record)

    return {
        "schema": "dynacity-viewer/1",
        "snapshot": {
            "id": snapshot.snapshot_id,
            "as_of": snapshot.as_of.isoformat(),
            "data_version": snapshot.data_version,
            "crs": snapshot.crs,
        },
        # [[west, south], [east, north]] of the drawn footprints.
        "bounds": [[min(lons), min(lats)], [max(lons), max(lats)]],
        "basemap": BASEMAPS[basemap],
        "states": [state.value for state in CanonicalState],
        "buildings": buildings,
        "coverage": {
            "drawn": len(buildings),
            "missing_geometry": missing_geometry,
            "estimated_height": estimated,
        },
        "forecast": None if forecast is None else forecast_summary(forecast),
        "api": api,
        "attribution": ATTRIBUTION,
    }


def forecast_summary(forecast: ForecastResult) -> dict[str, Any]:
    """The per-result fields the viewer shows; shared by export and the live endpoint."""

    return {
        "scenario_id": forecast.scenario_id,
        "model_version": forecast.model_version,
        "evidence_level": forecast.evidence_level.value,
        "evidence_bundle_id": forecast.evidence_bundle_id,
        "ood": round(forecast.out_of_distribution_score, 3),
        "warnings": forecast.warnings,
        "kpis": [
            {**estimate.model_dump(), "unit": KPI_UNITS.get(estimate.kpi, estimate.unit)}
            for estimate in forecast.kpis
        ],
        "p": {
            item.object_id: {
                state.value: round(value, 4) for state, value in item.probabilities.items() if value >= 0.0005
            }
            for item in forecast.first_step_transitions
        },
        # Aggregate counts per hazard (no per-object data).
        "hazard_impacts": [impact.model_dump(mode="json") for impact in forecast.hazard_impacts],
    }


def hazard_focus(
    hazards: Sequence[HazardEvent],
    snapshot: UrbanStateSnapshot,
    *,
    ground: GroundModel | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Where the camera flies for each hazard, and where to draw it.

    `ids` are the buildings it hits, ranked by the hazard's own destroy +
    damage probability, so a local shock frames its footprint and a citywide
    one frames its most vulnerable buildings; the page also animates the top
    of that ranking falling. Ids only — nothing else about the objects reaches
    the page. `center`/`path` are in longitude and latitude for the page's
    effects; both are None for a citywide hazard. A flood that follows terrain
    also carries `water`: the flooded ground cells and their depth.
    """

    zone, north = _utm_zone(snapshot.crs)
    focus = []
    for hazard in hazards:
        shock = hazard_shock(hazard, snapshot.objects)
        hit = shock.destroy + shock.damage
        order = [int(i) for i in np.argsort(-hit, kind="stable")[:limit] if hit[i] > 0]
        focus.append({
            "hazard_id": hazard.hazard_id,
            "kind": hazard.kind.value,
            "occurs_step": hazard.occurs_step,
            "citywide": hazard.citywide,
            "center": None if hazard.center_x_m is None
            else list(utm_to_lonlat(hazard.center_x_m, hazard.center_y_m, zone, north)),
            "path": [list(utm_to_lonlat(x, y, zone, north)) for x, y in hazard.path_m] or None,
            "radius_m": radius_of(hazard),
            "ids": [snapshot.objects[i].object_id for i in order],
            "water": None if ground is None else flood_water(hazard, snapshot, ground),
        })
    return focus


def flood_water(hazard: HazardEvent, snapshot: UrbanStateSnapshot, ground: GroundModel) -> dict[str, Any] | None:
    """Flooded ground cells for a terrain-following flood: [longitude, latitude, depth m] each.

    The same water level the engine uses (`hazards.flood_level`), applied to
    the ground grid instead of to buildings, and thinned towards the
    footprint's edge by the same exposure falloff. None when the flood is flat.
    """

    level = flood_level(hazard, snapshot.objects)
    if level is None:
        return None
    if hazard.citywide:
        xs = [o.centroid_x_m for o in snapshot.objects if o.centroid_x_m is not None]
        ys = [o.centroid_y_m for o in snapshot.objects if o.centroid_y_m is not None]
        box = (min(xs) - 300, min(ys) - 300, max(xs) + 300, max(ys) + 300)
    else:
        radius = radius_of(hazard)
        cx, cy = hazard.center_x_m, hazard.center_y_m
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
    x, y, g = ground.centres(*box)
    step = max(1, math.ceil(math.sqrt(x.size / MAX_WATER_CELLS)))
    x, y, g = x[::step, ::step].ravel(), y[::step, ::step].ravel(), g[::step, ::step].ravel()
    if hazard.citywide:
        exposure = np.ones(x.size)
    else:
        distance = np.hypot(x - hazard.center_x_m, y - hazard.center_y_m)
        exposure = _exposure(distance, radius, PROFILES[hazard.kind].core_fraction)
    with np.errstate(invalid="ignore"):
        depth = np.clip(level - g, 0.0, 1.5 * magnitude_of(hazard)) * exposure
        wet = np.flatnonzero(depth > 0.05)
    zone, north = _utm_zone(snapshot.crs)
    cells = []
    for i in wet:
        lon, lat = utm_to_lonlat(float(x[i]), float(y[i]), zone, north)
        cells.append([round(lon, 6), round(lat, 6), round(float(depth[i]), 2)])
    return {"level_m": round(level, 2), "cell_m": ground.cell_m * step, "cells": cells, "source": ground.source}


def render_viewer(payload: dict[str, Any], *, title: str = "DynaCITY · Beirut") -> str:
    template = files("dynacity").joinpath("viewer_template.html").read_text(encoding="utf-8")
    # `</` is escaped so no string inside the data can close the script tag.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    if _PLACEHOLDER not in template:
        raise RuntimeError("viewer template is missing its data placeholder")
    return template.replace(_PLACEHOLDER, data).replace("__DYNACITY_TITLE__", title)
