from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import requests


DEFAULT_BBED_LAYER = (
    "https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/"
    "BuildingLayer_New_forMasar_8_04_2024/FeatureServer/0"
)

DEFAULT_FIELDS = (
    "OBJECTID",
    "DataSource",
    "FootprintSource",
    "Cadastral",
    "Sector",
    "ParcelID",
    "BULBuildingID",
    "EnglishName",
    "Building_Use",
    "UseDescription",
    "NoofFloor",
    "TypicalFloorHeight",
    "Ground_Floor_Height",
    "Building_Hight_m",
    "NoofApartments",
    "YearCompleted",
    "PermitNumber",
    "PermitYear",
    "GroundFloorUse",
    "GroundFloorCommercialUse",
    "NoofBasementFloor",
    "BasementFloorUse",
    "RooftopFloorUse",
    "Status2018",
    "Status2022",
)


class ArcGISError(RuntimeError):
    pass


def _request_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        error = payload["error"]
        details = "; ".join(error.get("details") or [])
        raise ArcGISError(
            f"ArcGIS error {error.get('code', '?')}: {error.get('message', 'unknown')}"
            + (f" ({details})" if details else "")
        )
    return payload


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_arcgis_features(
    layer_url: str,
    bbox: tuple[float, float, float, float],
    bbox_epsg: int,
    out_epsg: int,
    *,
    fields: tuple[str, ...] = DEFAULT_FIELDS,
    timeout: float = 60.0,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Download all ArcGIS features intersecting ``bbox`` without record truncation."""

    own_session = session is None
    session = session or requests.Session()
    layer_url = layer_url.rstrip("/")
    try:
        metadata = _request_json(session, layer_url, {"f": "json"}, timeout)
        oid_field = metadata.get("objectIdField") or metadata.get("objectIdFieldName")
        if not oid_field:
            raise ArcGISError("Layer metadata does not declare an object ID field")

        available = {field["name"] for field in metadata.get("fields", [])}
        selected = [field for field in fields if field in available]
        if oid_field not in selected:
            selected.insert(0, oid_field)

        envelope = {
            "xmin": bbox[0],
            "ymin": bbox[1],
            "xmax": bbox[2],
            "ymax": bbox[3],
            "spatialReference": {"wkid": bbox_epsg},
        }
        spatial_params = {
            "geometry": json.dumps(envelope, separators=(",", ":")),
            "geometryType": "esriGeometryEnvelope",
            "inSR": bbox_epsg,
            "spatialRel": "esriSpatialRelIntersects",
        }
        ids_payload = _request_json(
            session,
            f"{layer_url}/query",
            {"f": "json", "returnIdsOnly": "true", **spatial_params},
            timeout,
        )
        object_ids = sorted(ids_payload.get("objectIds") or [])
        features: list[dict[str, Any]] = []

        # Fetching by explicit IDs avoids silently stopping at maxRecordCount.
        for object_id_batch in _chunks(object_ids, 200):
            page = _request_json(
                session,
                f"{layer_url}/query",
                {
                    "f": "geojson",
                    "objectIds": ",".join(str(value) for value in object_id_batch),
                    "outFields": ",".join(selected),
                    "returnGeometry": "true",
                    "outSR": out_epsg,
                },
                timeout,
            )
            features.extend(page.get("features") or [])

        return features, {
            "layer_name": metadata.get("name"),
            "layer_url": layer_url,
            "object_id_field": oid_field,
            "available_fields": sorted(available),
            "selected_fields": selected,
            "source_feature_count": len(features),
            "crs": f"EPSG:{out_epsg}",
        }
    finally:
        if own_session:
            session.close()
