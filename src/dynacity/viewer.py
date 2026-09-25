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
server the area being viewed. `basemap="none"` keeps the page fully offline
apart from the deck.gl script.
"""

from __future__ import annotations

import json
import math
import re
from importlib.resources import files
from typing import Any

from .bbed import resolved_object_ids
from .contracts import ForecastResult, UrbanStateSnapshot
from .kpis import KPI_UNITS
from .status import CanonicalState

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
    }


def render_viewer(payload: dict[str, Any], *, title: str = "DynaCITY · Beirut") -> str:
    template = files("dynacity").joinpath("viewer_template.html").read_text(encoding="utf-8")
    # `</` is escaped so no string inside the data can close the script tag.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    if _PLACEHOLDER not in template:
        raise RuntimeError("viewer template is missing its data placeholder")
    return template.replace(_PLACEHOLDER, data).replace("__DYNACITY_TITLE__", title)
