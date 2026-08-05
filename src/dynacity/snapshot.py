"""Convert an allowlisted BBED snapshot into the inference contract.

The bridge from stored data to a forecastable city: BBED GeoJSON in, a validated
`UrbanStateSnapshot` out.

Where `panel.PanelBuilder` builds *historical intervals* for training, this
builds a *single present-day state* for inference. The two must agree on
feature semantics — same names, same "knowable as of" rule — or the model would
be served covariates that mean something different from what it learned. The
difference is the reference year: the panel anchors on each interval's start,
this anchors on the snapshot's `as_of` date.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from shapely.geometry import shape

from .bbed import resolved_object_ids
from .contracts import UrbanObjectState, UrbanStateSnapshot
from .status import canonicalize_status


def _optional_nonnegative(value: object) -> float | None:
    """Coerce to a non-negative float, or None if the value is unusable.

    None (not NaN) because these feed pydantic fields typed `float | None`.
    Negative values are rejected rather than clamped: a negative floor count or
    height is a data error, and preserving it as 0 would disguise that.
    """

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
    """Build a validated present-state snapshot from a BBED collection.

    `snapshot_id` and `data_version` are caller-supplied and echoed into every
    `ForecastResult`, which is what makes a forecast traceable back to the exact
    input city it was produced from.

    Unlike the panel, morphology is attached unconditionally when present: this
    describes the current state, so there is no earlier interval for the
    post-acquisition data to leak into.
    """

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
        # Prefer the 2024 current-status column; fall back to 2022 where the
        # newest wave has no record for this building, so a stale-but-real state
        # is used rather than defaulting the object to unknown.
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
