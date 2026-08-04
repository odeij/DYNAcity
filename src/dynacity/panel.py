"""Build leakage-safe BBED transition panels for 2018→2022 and 2022→2024."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from shapely.geometry import shape

from .bbed import resolved_object_ids
from .status import canonicalize_status


STATUS_FIELDS = {2018: "Status2018", 2022: "Status2022", 2024: "F5__Current_status"}


def _number(value: object) -> float:
    try:
        if value is None or value == "":
            return np.nan
        return float(value)
    except (TypeError, ValueError):
        return np.nan


@dataclass(frozen=True)
class PanelBuilder:
    lidar_acquisition_year: int = 2020
    intervals: tuple[tuple[int, int], ...] = ((2018, 2022), (2022, 2024))

    def build(
        self,
        collection: dict,
        lidar_features: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        lidar_by_id: dict[str, dict] = {}
        lidar_columns: list[str] = []
        if lidar_features is not None and not lidar_features.empty:
            if "object_id" not in lidar_features:
                raise ValueError("lidar_features requires object_id")
            lidar_columns = [column for column in lidar_features if column != "object_id"]
            lidar_by_id = {
                str(row["object_id"]): row.to_dict()
                for _, row in lidar_features.iterrows()
            }

        rows: list[dict] = []
        features = collection.get("features", [])
        object_ids = resolved_object_ids(features)
        for feature, object_id in zip(features, object_ids, strict=True):
            props = feature.get("properties", {})
            geometry_area = np.nan
            if feature.get("geometry"):
                geometry_area = float(shape(feature["geometry"]).area)
            footprint_area = _number(props.get("Shape__Area"))
            if np.isnan(footprint_area):
                footprint_area = geometry_area
            for start_year, target_year in self.intervals:
                raw_current = props.get(STATUS_FIELDS[start_year])
                raw_target = props.get(STATUS_FIELDS[target_year])
                if raw_current is None or raw_target is None:
                    continue
                permit_year = _number(props.get("PermitYear"))
                completed_year = _number(props.get("YearCompleted"))
                permit_known = not np.isnan(permit_year) and permit_year <= start_year
                completion_known = (
                    not np.isnan(completed_year) and completed_year <= start_year
                )
                row = {
                    "object_id": object_id,
                    "parcel_id": props.get("ParcelID"),
                    "sector": props.get("Sector"),
                    "building_use": props.get("Building_Use"),
                    "current_state": canonicalize_status(raw_current).value,
                    "target_state": canonicalize_status(raw_target).value,
                    "raw_current_state": raw_current,
                    "raw_target_state": raw_target,
                    "start_year": start_year,
                    "target_year": target_year,
                    "interval_years": target_year - start_year,
                    "footprint_area_m2": footprint_area,
                    "floors": _number(props.get("NoofFloor")),
                    "height_m": _number(props.get("Building_Hight_m")),
                    "years_since_permit": (
                        float(start_year - permit_year) if permit_known else np.nan
                    ),
                    "years_since_completion": (
                        float(start_year - completed_year)
                        if completion_known
                        else np.nan
                    ),
                    "permit_known": int(permit_known),
                    "completion_known": int(completion_known),
                    "lidar_available": 0,
                    "lidar_acquisition_year": np.nan,
                    "transitioned": int(
                        canonicalize_status(raw_current)
                        != canonicalize_status(raw_target)
                    ),
                }
                lidar = lidar_by_id.get(object_id)
                lidar_is_temporally_valid = (
                    lidar is not None and self.lidar_acquisition_year <= start_year
                )
                for column in lidar_columns:
                    row[f"lidar_{column}"] = (
                        lidar.get(column) if lidar_is_temporally_valid else np.nan
                    )
                if lidar_is_temporally_valid:
                    row["lidar_available"] = 1
                    row["lidar_acquisition_year"] = self.lidar_acquisition_year
                rows.append(row)
        panel = pd.DataFrame(rows)
        if panel.empty:
            raise ValueError("no complete BBED status transitions were found")
        return panel


def assert_no_temporal_leakage(panel: pd.DataFrame) -> None:
    if "lidar_available" not in panel:
        return
    invalid = panel[
        (panel["lidar_available"] == 1)
        & (panel["lidar_acquisition_year"] > panel["start_year"])
    ]
    if not invalid.empty:
        raise ValueError(
            f"LiDAR temporal leakage detected in {len(invalid)} training rows"
        )
