from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import shapefile
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import transform

from .arcgis import fetch_arcgis_features
from .models import Footprint


def _geojson_crs(payload: dict[str, Any]) -> str | None:
    crs = payload.get("crs")
    if not crs:
        return None
    properties = crs.get("properties") or {}
    return properties.get("name") or properties.get("href")


def _read_geojson(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("type") == "Feature":
        features = [payload]
    elif payload.get("type") == "FeatureCollection":
        features = payload.get("features") or []
    else:
        raise ValueError(f"{path} is not a GeoJSON Feature or FeatureCollection")
    return features, _geojson_crs(payload)


def _read_shapefile(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    reader = shapefile.Reader(str(path))
    field_names = [field[0] for field in reader.fields[1:]]
    features: list[dict[str, Any]] = []
    for item in reader.iterShapeRecords():
        features.append(
            {
                "type": "Feature",
                "geometry": item.shape.__geo_interface__,
                "properties": dict(zip(field_names, item.record, strict=True)),
            }
        )
    projection_path = path.with_suffix(".prj")
    source_crs = projection_path.read_text(encoding="utf-8") if projection_path.exists() else None
    return features, source_crs


def _polygonal(geometry: Any) -> Polygon | MultiPolygon | None:
    if geometry is None or geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    polygon_parts = [
        part for part in getattr(geometry, "geoms", []) if isinstance(part, (Polygon, MultiPolygon))
    ]
    if not polygon_parts:
        return None
    flattened = []
    for part in polygon_parts:
        flattened.extend(part.geoms if isinstance(part, MultiPolygon) else [part])
    return MultiPolygon(flattened)


def load_footprints(
    source: str,
    bbox: tuple[float, float, float, float],
    point_crs: CRS,
    *,
    source_crs_override: str | None = None,
    bbox_buffer: float = 0.0,
) -> tuple[list[Footprint], dict[str, Any]]:
    """Load, reproject, validate, and spatially subset footprint features."""

    buffered_bbox = (
        bbox[0] - bbox_buffer,
        bbox[1] - bbox_buffer,
        bbox[2] + bbox_buffer,
        bbox[3] + bbox_buffer,
    )
    target_epsg = point_crs.to_epsg()
    if target_epsg is None:
        raise ValueError("The point-cloud CRS must have an EPSG code for ArcGIS queries")

    if source.lower().startswith(("http://", "https://")):
        features, metadata = fetch_arcgis_features(
            source,
            buffered_bbox,
            target_epsg,
            target_epsg,
        )
        source_crs = point_crs
        source_kind = "arcgis"
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() in {".geojson", ".json"}:
            features, declared_crs = _read_geojson(path)
        elif path.suffix.lower() == ".shp":
            features, declared_crs = _read_shapefile(path)
        else:
            raise ValueError("BBED source must be an ArcGIS layer URL, GeoJSON, JSON, or Shapefile")
        crs_value = source_crs_override or declared_crs
        if not crs_value:
            raise ValueError(
                "The local BBED file has no CRS. Pass --bbed-crs explicitly; do not guess."
            )
        source_crs = CRS.from_user_input(crs_value)
        metadata = {
            "layer_name": path.stem,
            "layer_url": None,
            "source_path": str(path.resolve()),
            "source_feature_count": len(features),
        }
        source_kind = "local"

    transformer = None
    if not source_crs.equals(point_crs):
        transformer = Transformer.from_crs(source_crs, point_crs, always_xy=True)

    bounds_polygon = box(*buffered_bbox)
    footprints: list[Footprint] = []
    invalid_or_nonpolygon = 0
    outside_bbox = 0
    for feature in features:
        geometry_payload = feature.get("geometry")
        geometry = _polygonal(shape(geometry_payload)) if geometry_payload else None
        if geometry is None:
            invalid_or_nonpolygon += 1
            continue
        if transformer:
            geometry = transform(transformer.transform, geometry)
        if not geometry.intersects(bounds_polygon):
            outside_bbox += 1
            continue
        footprints.append(
            Footprint(
                match_id=len(footprints),
                geometry=geometry,
                properties=dict(feature.get("properties") or {}),
            )
        )

    if not footprints:
        raise ValueError("No valid BBED building footprints intersect the point-cloud bounds")

    metadata.update(
        {
            "source_kind": source_kind,
            "source_crs": source_crs.to_string(),
            "registered_crs": point_crs.to_string(),
            "registered_feature_count": len(footprints),
            "invalid_or_nonpolygon_count": invalid_or_nonpolygon,
            "outside_bbox_count": outside_bbox,
        }
    )
    return footprints, metadata


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def feature_collection(
    footprints: list[Footprint],
    crs: CRS,
    additional_properties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    features = []
    for index, footprint in enumerate(footprints):
        properties = {key: json_safe(value) for key, value in footprint.properties.items()}
        properties["dynacity_match_id"] = footprint.match_id
        if additional_properties:
            properties.update(
                {key: json_safe(value) for key, value in additional_properties[index].items()}
            )
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(footprint.geometry),
                "properties": properties,
            }
        )
    return {
        "type": "FeatureCollection",
        # BBED uses projected GeoJSON. This member preserves the otherwise ambiguous CRS.
        "crs": {"type": "name", "properties": {"name": crs.to_string()}},
        "features": features,
    }

