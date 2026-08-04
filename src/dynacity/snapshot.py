"""Convert an allowlisted BBED snapshot into the inference contract."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from shapely.geometry import shape

from .bbed import resolved_object_ids
from .contracts import UrbanObjectState, UrbanStateSnapshot
from .status import canonicalize_status


def _optional_nonnegative(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or number < 0:
        return None
    return number


def snapshot_from_bbed(
    collection: dict,
    *,
    snapshot_id: str,
    as_of: date,
    data_version: str,
    lidar_features: pd.DataFrame | None = None,
) -> UrbanStateSnapshot:
    lidar_lookup: dict[str, dict] = {}
    if lidar_features is not None and not lidar_features.empty:
        lidar_lookup = {
            str(row["object_id"]): row.to_dict()
            for _, row in lidar_features.iterrows()
        }
    objects = []
    bbed_features = collection.get("features", [])
    object_ids = resolved_object_ids(bbed_features)
    for feature, object_id in zip(bbed_features, object_ids, strict=True):
        props = feature.get("properties", {})
        geometry_area = 0.0
        if feature.get("geometry"):
            geometry_area = max(float(shape(feature["geometry"]).area), 0.0)
        area = _optional_nonnegative(props.get("Shape__Area"))
        if area is None:
            area = geometry_area
        permit_year = _optional_nonnegative(props.get("PermitYear"))
        completed_year = _optional_nonnegative(props.get("YearCompleted"))
        features: dict[str, float | int | str | bool | None] = {
            "permit_known": int(permit_year is not None and permit_year <= as_of.year),
            "completion_known": int(
                completed_year is not None and completed_year <= as_of.year
            ),
            "years_since_permit": (
                as_of.year - permit_year
                if permit_year is not None and permit_year <= as_of.year
                else None
            ),
            "years_since_completion": (
                as_of.year - completed_year
                if completed_year is not None and completed_year <= as_of.year
                else None
            ),
            "lidar_available": int(object_id in lidar_lookup),
        }
        for key, value in lidar_lookup.get(object_id, {}).items():
            if key != "object_id":
                features[f"lidar_{key}"] = value
        raw_state = props.get("F5__Current_status") or props.get("Status2022")
        objects.append(
            UrbanObjectState(
                object_id=object_id,
                parcel_id=(str(props["ParcelID"]) if props.get("ParcelID") else None),
                state=canonicalize_status(raw_state),
                raw_state=raw_state,
                footprint_area_m2=area,
                floors=_optional_nonnegative(props.get("NoofFloor")),
                height_m=_optional_nonnegative(props.get("Building_Hight_m")),
                building_use=props.get("Building_Use"),
                sector=props.get("Sector"),
                features=features,
                provenance={
                    "geometry": "BBED",
                    "status": "BBED 2024 current status",
                    "lidar": "AUB post-port-blast 2020" if object_id in lidar_lookup else "none",
                },
            )
        )
    return UrbanStateSnapshot(
        snapshot_id=snapshot_id,
        as_of=as_of,
        crs="EPSG:32636",
        objects=objects,
        data_version=data_version,
    )
