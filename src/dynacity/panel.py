"""Build leakage-safe BBED transition panels across the three survey waves.

The panel is the single tabular fact table the rest of the pipeline trains and
reports on. One row = one building observed over one interval, carrying the
state it started in, the state it ended in, and the covariates that were
*knowable at the start of that interval*.

That last constraint is the whole point of this module. Two distinct leakage
channels are guarded here:

1. Temporal feature leakage — a covariate must not encode information from
   after `start_year`. Permit and completion years are therefore only surfaced
   when they precede the interval start, and the point-cloud modality is only
   attached to intervals beginning on or after its acquisition year.
2. Interval contamination — the panel contains a descriptive (2018, 2024)
   interval alongside the two modelling intervals. It shares an end year with
   (2022, 2024) and a start year with (2018, 2022), so any consumer selecting
   rows by a single endpoint will silently mix it into train or test. Consumers
   must pin both endpoints; see `models.temporal_benchmark`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from shapely.geometry import shape

from .bbed import resolved_object_ids
from .status import canonicalize_status


# Which BBED property holds the status for each survey wave. The 2024 wave uses
# a different, non-obvious field name because it is the layer's "current"
# column rather than a year-stamped historical one.
STATUS_FIELDS = {2018: "Status2018", 2022: "Status2022", 2024: "F5__Current_status"}


def _number(value: object) -> float:
    """Coerce a BBED property to float, mapping anything unparseable to NaN.

    BBED numeric columns arrive as a mix of numbers, numeric strings, empty
    strings, and nulls. NaN (rather than 0) is the correct target: 0 floors is a
    factual claim, while a blank means "not recorded", and the downstream
    imputer distinguishes the two via an added missingness indicator.
    """

    try:
        if value is None or value == "":
            return np.nan
        return float(value)
    except (TypeError, ValueError):
        return np.nan


@dataclass(frozen=True)
class PanelBuilder:
    """Expand a BBED feature collection into one row per (building, interval).

    `lidar_acquisition_year` is the year the point-cloud survey was flown. It is
    the cutoff that decides which intervals may see morphology features at all;
    the 2020 default reflects the post-port-blast AUB capture. (Despite the
    `lidar` naming kept for API continuity, that capture is photogrammetric —
    see the provenance note in README. The leakage rule is identical either way.)

    `intervals` is ordered (start_year, target_year) pairs. The first two are the
    modelling intervals; (2018, 2024) is descriptive-only and must never reach a
    model — it overlaps the others on one endpoint each, so it is only safe as
    long as consumers filter on both years.
    """

    lidar_acquisition_year: int = 2020
    intervals: tuple[tuple[int, int], ...] = (
        (2018, 2022),
        (2022, 2024),
        (2018, 2024),
    )

    def build(
        self,
        collection: dict,
        lidar_features: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Emit the transition panel for every building × interval combination.

        `lidar_features` is the optional morphology table from
        `features.extract_building_features`, joined on the composite object_id.
        Its columns are prefixed `lidar_` so they stay distinguishable from BBED
        attributes in the flat CSV.

        Rows are skipped when either endpoint's status is absent, since a
        transition needs both ends to exist. Raises if no interval survives.
        """
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
            # Prefer BBED's published area; fall back to computing it from the
            # polygon so buildings with a missing attribute still get a usable
            # footprint rather than NaN.
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
                # Leakage guard: a permit filed in 2023 is not knowable to a
                # model forecasting from 2018. Only dates at or before the
                # interval start count as observed; anything later is treated as
                # unknown for this row, even though the value exists in BBED.
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
                # The second leakage channel, and a physical one: the point
                # clouds were captured after the 2020 port blast. Attaching them
                # to a 2018-start row would hand the model post-blast evidence
                # about a pre-blast period. Features are blanked to NaN rather
                # than the row being dropped, so the interval keeps its BBED
                # signal and the model sees an honest missing modality.
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
    """Fail loudly if any row claims a modality it could not have had.

    This is the independent check on `PanelBuilder`'s own gating, and it is run
    again at train time (`cli.command_train`) because panels are round-tripped
    through CSV and may be hand-edited or produced by an older build between
    those points. It is an assertion, not a filter: leakage is a defect to fix
    upstream, not something to quietly drop rows over.

    A panel with no point-cloud columns at all is trivially safe and returns
    early.
    """

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
