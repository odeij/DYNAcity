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
World Imagery tiles, or with dated 2020 Maxar scenes from OpenAerialMap when
those are picked; imagery is only requested once it is switched on.
`basemap="none"` keeps the page fully offline
apart from the deck.gl script.

"""

from __future__ import annotations

import base64
import json
import math
import re
from importlib.resources import files
from typing import Any


from .coverage import coverage_report, geometry_issue
from .bbed import resolved_object_ids
from .contracts import ForecastResult, UrbanStateSnapshot
from .kpis import KPI_UNITS
from .status import CanonicalState

from .heights import STOREY_HEIGHT_M, bin_medians, resolve_height

ATTRIBUTION = (
    "Building data: Beirut Built Environment Database (BBED), Beirut Urban Lab, AUB — "
    "Open Database License (ODbL)."
)

_PLACEHOLDER = "/*__DYNACITY_DATA__*/null"

def _oam_tiles(image: str, options: str = "") -> str:
    """Tile URL for an OpenAerialMap image, from HOT's tiler directly.

    OpenAerialMap's own tile links redirect here without a CORS header, so the
    map's tile fetches would be blocked if the page used them. `options` are
    extra tiler query parameters.
    """
    cog = f"https://oin-hotosm-temp.s3.us-east-1.amazonaws.com/{image}.tif"
    return f"https://titiler.hotosm.org/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}@1x?url={cog}{options}"


BASEMAPS: dict[str, dict[str, Any] | None] = {
    "carto": {
        "style_light": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        "style_dark": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        # OpenStreetMap buildings with heights, drawn as pale 3D context.
        "context_tiles": "https://tiles-a.basemaps.cartocdn.com/vectortiles/carto.streets/v1/{z}/{x}/{y}.mvt",
        "attribution": "Basemap © CARTO, © OpenStreetMap contributors.",
        # Colour imagery the page can swap in for the street basemap; the first
        # entry is the default. Each entry stacks its layers bottom to top, and
        # `bounds` keeps a scene from requesting tiles outside its footprint.
        "imagery": [
            {
                "label": "Recent · Esri",
                "layers": [{"tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "maxzoom": 19}],
                "attribution": "Imagery © Esri, Maxar, Earthstar Geographics.",
            },
            # Maxar Open Data scenes of the 2020 port blast, hosted by
            # OpenAerialMap (CC BY-NC 4.0). Tiles take a second or two each.
            {
                "label": "Jul 2020 · before blast",
                "layers": [{"tiles": _oam_tiles("5f3350077ef1c4000631d8cd/2/5f3350077ef1c4000631d8d0"), "maxzoom": 18, "bounds": [35.3914, 33.813922, 35.658469, 34.068934]}],
                "attribution": "Imagery: Maxar Open Data, 31 Jul 2020, CC BY-NC 4.0, via OpenAerialMap.",
            },
            {
                "label": "Aug 2020 · after blast",
                "layers": [
                    {"tiles": _oam_tiles("5f2b60d3b0052e00067dbae0/2/5f2b60d3b0052e00067dbae3"), "maxzoom": 18, "bounds": [35.382533, 33.753481, 35.653243, 34.023247]},
                    # 12 cm drone orthophoto of the port area, 4 Aug 2020. It is
                    # 16-bit with a black collar, which the tiler cannot write
                    # as PNG edge tiles unless scaled to 8-bit and masked.
                    {"tiles": _oam_tiles("5f2bd274b0052e00067dbaed/0/5f2bd274b0052e00067dbaee", "&rescale=0,65535&bidx=1&bidx=2&bidx=3&nodata=0"), "maxzoom": 20, "bounds": [35.515748, 33.897378, 35.524059, 33.904185]},
                ],
                "attribution": "Imagery: Maxar Open Data, 5 Aug 2020, CC BY-NC 4.0; port drone orthophoto 4 Aug 2020 by Rabih El Zein / GeoConor, CC BY 4.0; via OpenAerialMap.",
            },
        ],
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


def _outer_rings(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry.get("type") == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


_UNBUILT = frozenset({CanonicalState.EMPTY_OR_PARKING, CanonicalState.DEMOLISHED})


def build_viewer_payload(
    collection: dict,
    snapshot: UrbanStateSnapshot,
    forecast: ForecastResult | None = None,
    *,
    api: bool = False,
    basemap: str = "carto",
    traffic: dict[str, Any] | None = None,
    landscape: dict[str, Any] | None = None,
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
            for ring in (_outer_rings(geometry) if geometry_issue(geometry) is None else [])
        ]
        for object_id, geometry in geometry_by_id.items()
    }
    lons = [p[0] for rings in rings_by_id.values() for ring in rings for p in ring]
    lats = [p[1] for rings in rings_by_id.values() for ring in rings for p in ring]
    if not lons:
        raise ValueError("BBED collection contains no polygon geometry")

    properties_by_id = dict(zip(resolved_object_ids(features),
                                (f.get("properties", {}) for f in features), strict=True))
    # Size-bin medians come from BBED's own recorded heights of built buildings
    # (held-out median error 9 m, against 21 m for a flat 6 m).
    measured = [o for o in snapshot.objects if o.height_m and o.state not in _UNBUILT]
    medians = bin_medians([o.footprint_area_m2 for o in measured], [o.height_m for o in measured])
    buildings, missing_geometry, estimated = [], 0, 0
    height_kinds: dict[str, int] = {}
    status_sources: dict[str, int] = {}
    for item in snapshot.objects:
        rings = rings_by_id.get(item.object_id)
        if not rings:
            missing_geometry += 1
            continue
        # Vacant lots and demolished sites get no size-bin guess from built heights.
        unbuilt = item.state in _UNBUILT
        resolved = resolve_height(recorded=item.height_m, recorded_label="Recorded height (BBED)",
                                  floors=item.floors, area_m2=None if unbuilt else item.footprint_area_m2,
                                  medians=medians, median_label="built BBED buildings of similar footprint")
        height, is_estimate, height_source = resolved.height_m, resolved.estimated, resolved.source
        height_kinds[resolved.kind] = height_kinds.get(resolved.kind, 0) + 1
        # Identify the source wave only when the raw record matches the snapshot.
        # Older snapshots can incorrectly label a 2022 fallback as 2024.
        props = properties_by_id.get(item.object_id, {})
        raw = props.get("F5__Current_status") or props.get("Status2022")
        status_source = item.provenance.get("status", "Source period unavailable")
        if item.state == CanonicalState.UNKNOWN:
            status_source = "Unknown / unmapped status"
        elif raw and raw == item.raw_state:
            status_source = "BBED 2024" if props.get("F5__Current_status") else "BBED 2022 fallback"
        status_sources[status_source] = status_sources.get(status_source, 0) + 1
        estimated += is_estimate
        record: dict[str, Any] = {
            "id": item.object_id,
            "rings": rings,
            "h": round(height, 2),
            "est": is_estimate,
            "height_kind": resolved.kind,
            "height_source": height_source,
            "status_source": status_source,
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
        "landscape": landscape,
        "states": [state.value for state in CanonicalState],
        "buildings": buildings,
        "storey_height_m": STOREY_HEIGHT_M,
        "coverage": {
            "drawn": len(buildings),
            "missing_geometry": missing_geometry,
            "estimated_height": estimated,
            "quality": coverage_report(collection, snapshot=snapshot),
        },
        "evidence": {
            "status_sources": status_sources,
            "height_recorded": sum(o.height_m is not None for o in snapshot.objects),
            "floors_recorded": sum(o.floors is not None for o in snapshot.objects),
            "height_kinds": height_kinds,
            "objects": len(snapshot.objects),
        },
        "forecast": None if forecast is None else forecast_summary(forecast),
        "api": api,
        "attribution": ATTRIBUTION,
        # Roads with hourly speeds from `traffic.traffic_payload`, or None.
        "traffic": traffic,
    }


def forecast_summary(forecast: ForecastResult) -> dict[str, Any]:
    """The per-result fields the viewer shows; shared by export and the live endpoint."""

    return {
        "scenario_id": forecast.scenario_id,
        "baseline_as_of": forecast.baseline_as_of.isoformat() if forecast.baseline_as_of else None,
        "training_intervals": forecast.training_intervals,
        "monte_carlo_draws": forecast.monte_carlo_draws,
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


def _brand_image(name: str) -> str:
    """An official DynaCITY logo from assets/brand as a data URI."""
    data = files("dynacity").joinpath(f"assets/brand/{name}").read_bytes()
    return "data:image/webp;base64," + base64.b64encode(data).decode("ascii")


def render_viewer(payload: dict[str, Any], *, title: str = "DynaCITY · Beirut") -> str:
    template = files("dynacity").joinpath("viewer_template.html").read_text(encoding="utf-8")
    # `</` is escaped so no string inside the data can close the script tag.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    if _PLACEHOLDER not in template:
        raise RuntimeError("viewer template is missing its data placeholder")
    car_models = files("dynacity").joinpath("assets/cars/meshes.json").read_text(encoding="utf-8") if payload.get("traffic") else "{}"
    # Inline detail modules so exported viewers remain a single portable HTML file.
    details = "\n".join(
        files("dynacity").joinpath(name).read_text(encoding="utf-8")
        for name in ("viewer_street_edges.js", "viewer_intersections.js", "viewer_building_details.js", "viewer_osm_context.js", "viewer_camera.js", "viewer_pedestrians.js")
    )
    return (template.replace(_PLACEHOLDER, data).replace("__DYNACITY_CARS__", car_models)
            .replace("/*__DYNACITY_DETAILS__*/", details).replace("__DYNACITY_TITLE__", title)
            .replace("__DYNACITY_LOGO__", _brand_image("dynacity-logo.webp"))
            .replace("__DYNACITY_ICON__", _brand_image("favicon.webp")))
