from __future__ import annotations

import copy
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import laspy
import numpy as np
from pyproj import CRS

from .evaluation import evaluate_instances
from .matching import FootprintMatcher, property_values
from .models import Footprint
from .vector import feature_collection, json_safe, load_footprints


@dataclass
class BuildingStatistics:
    point_count: np.ndarray
    evaluation_point_count: np.ndarray
    z_min: np.ndarray
    z_max: np.ndarray
    z_sum: np.ndarray
    red_sum: np.ndarray
    green_sum: np.ndarray
    blue_sum: np.ndarray
    rgb_count: np.ndarray

    @classmethod
    def empty(cls, size: int) -> "BuildingStatistics":
        return cls(
            point_count=np.zeros(size, dtype=np.int64),
            evaluation_point_count=np.zeros(size, dtype=np.int64),
            z_min=np.full(size, np.inf, dtype=np.float64),
            z_max=np.full(size, -np.inf, dtype=np.float64),
            z_sum=np.zeros(size, dtype=np.float64),
            red_sum=np.zeros(size, dtype=np.float64),
            green_sum=np.zeros(size, dtype=np.float64),
            blue_sum=np.zeros(size, dtype=np.float64),
            rgb_count=np.zeros(size, dtype=np.int64),
        )

    def update(
        self,
        match_ids: np.ndarray,
        z: np.ndarray,
        evaluation_mask: np.ndarray,
        red: np.ndarray | None,
        green: np.ndarray | None,
        blue: np.ndarray | None,
    ) -> None:
        matched = match_ids >= 0
        if np.any(matched):
            self.point_count += np.bincount(
                match_ids[matched], minlength=self.point_count.size
            )

        selected = matched & evaluation_mask & np.isfinite(z)
        if not np.any(selected):
            return
        ids = match_ids[selected]
        selected_z = z[selected]
        self.evaluation_point_count += np.bincount(
            ids, minlength=self.evaluation_point_count.size
        )
        np.minimum.at(self.z_min, ids, selected_z)
        np.maximum.at(self.z_max, ids, selected_z)
        np.add.at(self.z_sum, ids, selected_z)

        if red is not None and green is not None and blue is not None:
            np.add.at(self.red_sum, ids, np.asarray(red)[selected])
            np.add.at(self.green_sum, ids, np.asarray(green)[selected])
            np.add.at(self.blue_sum, ids, np.asarray(blue)[selected])
            self.rgb_count += np.bincount(ids, minlength=self.rgb_count.size)

    def rows(self, footprints: list[Footprint]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, footprint in enumerate(footprints):
            count = int(self.evaluation_point_count[index])
            rgb_count = int(self.rgb_count[index])
            z_min = float(self.z_min[index]) if count else math.nan
            z_max = float(self.z_max[index]) if count else math.nan
            rows.append(
                {
                    "pc_point_count": int(self.point_count[index]),
                    "pc_evaluation_point_count": count,
                    "pc_point_density_m2": (
                        float(self.point_count[index] / footprint.geometry.area)
                        if footprint.geometry.area
                        else math.nan
                    ),
                    "pc_z_min": z_min,
                    "pc_z_max": z_max,
                    "pc_z_mean": float(self.z_sum[index] / count) if count else math.nan,
                    "pc_height_range": z_max - z_min if count else math.nan,
                    "pc_red_mean": (
                        float(self.red_sum[index] / rgb_count) if rgb_count else math.nan
                    ),
                    "pc_green_mean": (
                        float(self.green_sum[index] / rgb_count) if rgb_count else math.nan
                    ),
                    "pc_blue_mean": (
                        float(self.blue_sum[index] / rgb_count) if rgb_count else math.nan
                    ),
                }
            )
        return rows


def _header_crs(header: laspy.LasHeader) -> CRS | None:
    try:
        parsed = header.parse_crs()
    except Exception:
        parsed = None
    return CRS.from_user_input(parsed) if parsed else None


def _resolve_point_crs(header: laspy.LasHeader, explicit: str | None) -> CRS:
    embedded = _header_crs(header)
    if explicit:
        selected = CRS.from_user_input(explicit)
        if embedded and not embedded.equals(selected):
            raise ValueError(
                f"LAS CRS ({embedded.to_string()}) disagrees with --point-crs "
                f"({selected.to_string()})"
            )
        return selected
    if embedded:
        return embedded
    raise ValueError("LAS has no embedded CRS. Pass --point-crs explicitly; do not guess.")


def _dimension_names(point_format: laspy.PointFormat) -> set[str]:
    return set(point_format.dimension_names)


def _add_output_dimensions(header: laspy.LasHeader) -> None:
    names = _dimension_names(header.point_format)
    dimensions = (
        ("bbed_id", np.int32, "BBED lookup ID (-1 unmatched)"),
        ("bbed_oid", np.int32, "BBED ArcGIS object ID"),
        ("bbed_bldg", np.int32, "BBED BUL building ID"),
        ("bbed_floors", np.int16, "BBED floors (-1 unknown)"),
        ("bbed_h_m", np.float32, "BBED height metres"),
        ("bbed_amb", np.uint8, "Containing footprint count"),
    )
    collisions = [name for name, _, _ in dimensions if name in names]
    if collisions:
        raise ValueError(f"Output dimensions already exist in input LAS: {', '.join(collisions)}")
    for name, dtype, description in dimensions:
        header.add_extra_dim(
            laspy.ExtraBytesParams(name=name, type=dtype, description=description)
        )


def _copy_chunk(
    source: laspy.ScaleAwarePointRecord,
    output_header: laspy.LasHeader,
) -> laspy.ScaleAwarePointRecord:
    destination = laspy.ScaleAwarePointRecord.zeros(len(source), header=output_header)
    for name in source.point_format.dimension_names:
        destination[name] = source[name]
    return destination


def _mapped(values: np.ndarray, match_ids: np.ndarray, default: int | float) -> np.ndarray:
    output = np.full(match_ids.shape, default, dtype=values.dtype)
    valid = match_ids >= 0
    output[valid] = values[match_ids[valid]]
    return output


def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=True)
        handle.write("\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json_safe(row.get(key)) for key in keys})


def _lookup_rows(
    footprints: list[Footprint], statistics: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for footprint, derived in zip(footprints, statistics, strict=True):
        row = {"dynacity_match_id": footprint.match_id}
        row.update(footprint.properties)
        row.update(derived)
        rows.append(row)
    return rows


def _height_metrics(rows: list[dict[str, Any]], bbed_heights: np.ndarray) -> dict[str, Any]:
    observed = np.asarray([row["pc_height_range"] for row in rows], dtype=np.float64)
    valid = np.isfinite(observed) & np.isfinite(bbed_heights) & (bbed_heights > 0)
    if not np.any(valid):
        return {"comparable_buildings": 0, "mae_m": math.nan, "rmse_m": math.nan}
    errors = observed[valid] - bbed_heights[valid]
    return {
        "comparable_buildings": int(np.sum(valid)),
        "mae_m": float(np.mean(np.abs(errors))),
        "rmse_m": float(np.sqrt(np.mean(np.square(errors)))),
        "note": "Point z-range is an outlier-sensitive diagnostic, not a surveyed height.",
    }


def _write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    matching = report["matching"]
    instance = report.get("instance_evaluation")
    lines = [
        "# Task 2 quality report",
        "",
        f"- Point-cloud CRS: `{report['point_cloud']['crs']}`",
        f"- Input points: {report['point_cloud']['point_count']:,}",
        f"- BBED footprints: {matching['footprint_count']:,}",
        f"- Points inside a BBED footprint: {matching['matched_points']:,} "
        f"({matching['matched_point_ratio']:.2%})",
        f"- Footprints containing points: {matching['footprints_with_points']:,} "
        f"({matching['footprint_coverage']:.2%})",
        f"- Ambiguous points (overlapping footprints): {matching['ambiguous_points']:,}",
        "",
        "The point association is an exact 2D point-in-polygon operation after CRS validation. "
        "These coverage figures do not by themselves prove survey registration accuracy.",
    ]
    if instance:
        lines.extend(
            [
                "",
                "## Segmentation-instance evaluation",
                "",
                f"- IoU threshold: {instance['iou_threshold']}",
                f"- Precision: {instance['precision']:.3f}",
                f"- Recall: {instance['recall']:.3f}",
                f"- F1: {instance['f1']:.3f}",
                f"- Mean matched IoU: {instance['mean_iou']:.3f}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pipeline(
    input_las: Path,
    bbed_source: str,
    output_dir: Path,
    *,
    point_crs_value: str | None,
    bbed_crs_value: str | None = None,
    chunk_size: int = 1_000_000,
    bbox_buffer: float = 2.0,
    building_classes: tuple[int, ...] = (),
    instances_source: str | None = None,
    instance_crs_value: str | None = None,
    iou_threshold: float = 0.1,
    overwrite: bool = False,
) -> dict[str, Any]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    input_las = input_las.resolve()
    if not input_las.exists():
        raise FileNotFoundError(input_las)
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = input_las.suffix.lower()
    if extension not in {".las", ".laz"}:
        raise ValueError("Input must be a .las or .laz file")
    output_las = output_dir / f"enriched{extension}"
    expected = [
        output_las,
        output_dir / "registered_bbed.geojson",
        output_dir / "building_lookup.csv",
        output_dir / "quality_report.json",
        output_dir / "quality_report.md",
    ]
    existing = [path for path in expected if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Refusing to overwrite existing outputs: " + ", ".join(str(path) for path in existing)
        )

    with laspy.open(input_las) as reader:
        point_crs = _resolve_point_crs(reader.header, point_crs_value)
        bbox = (
            float(reader.header.mins[0]),
            float(reader.header.mins[1]),
            float(reader.header.maxs[0]),
            float(reader.header.maxs[1]),
        )
        point_count = int(reader.header.point_count)
        input_dimensions = _dimension_names(reader.header.point_format)

    footprints, source_metadata = load_footprints(
        bbed_source,
        bbox,
        point_crs,
        source_crs_override=bbed_crs_value,
        bbox_buffer=bbox_buffer,
    )
    matcher = FootprintMatcher(footprints)
    oid = property_values(footprints, ("OBJECTID", "objectid"), np.int32, -1)
    building_id = property_values(
        footprints, ("BULBuildingID", "BuildingID", "building_id"), np.int32, -1
    )
    floors = property_values(
        footprints, ("NoofFloor", "NoOfFloors", "floors"), np.int16, -1
    )
    heights = property_values(
        footprints,
        ("Building_Hight_m", "Building_Height_m", "height_m"),
        np.float32,
        np.nan,
    )

    statistics = BuildingStatistics.empty(len(footprints))
    matched_points = 0
    ambiguous_points = 0
    partial_output = output_las.with_name(f"{output_las.stem}.partial{output_las.suffix}")
    if partial_output.exists():
        partial_output.unlink()
    try:
        with laspy.open(input_las) as reader:
            output_header = copy.deepcopy(reader.header)
            _add_output_dimensions(output_header)
            with laspy.open(partial_output, mode="w", header=output_header) as writer:
                for chunk in reader.chunk_iterator(chunk_size):
                    result = matcher.match(np.asarray(chunk.x), np.asarray(chunk.y))
                    ids = result.match_ids
                    matched_points += int(np.sum(ids >= 0))
                    ambiguous_points += int(np.sum(result.overlap_counts > 1))

                    if building_classes:
                        if "classification" not in input_dimensions:
                            raise ValueError(
                                "--building-class was supplied but LAS has no classification dimension"
                            )
                        evaluation_mask = np.isin(
                            np.asarray(chunk.classification), np.asarray(building_classes)
                        )
                    else:
                        evaluation_mask = np.ones(len(chunk), dtype=bool)
                    has_rgb = {"red", "green", "blue"}.issubset(input_dimensions)
                    statistics.update(
                        ids,
                        np.asarray(chunk.z),
                        evaluation_mask,
                        np.asarray(chunk.red) if has_rgb else None,
                        np.asarray(chunk.green) if has_rgb else None,
                        np.asarray(chunk.blue) if has_rgb else None,
                    )

                    output_chunk = _copy_chunk(chunk, output_header)
                    output_chunk["bbed_id"] = ids
                    output_chunk["bbed_oid"] = _mapped(oid, ids, -1)
                    output_chunk["bbed_bldg"] = _mapped(building_id, ids, -1)
                    output_chunk["bbed_floors"] = _mapped(floors, ids, -1)
                    output_chunk["bbed_h_m"] = _mapped(heights, ids, np.nan)
                    output_chunk["bbed_amb"] = result.overlap_counts
                    writer.write_points(output_chunk)
        partial_output.replace(output_las)
    except Exception:
        if partial_output.exists():
            partial_output.unlink()
        raise

    derived_rows = statistics.rows(footprints)
    lookup_rows = _lookup_rows(footprints, derived_rows)
    _write_csv(output_dir / "building_lookup.csv", lookup_rows)
    _write_json(
        output_dir / "registered_bbed.geojson",
        feature_collection(footprints, point_crs, derived_rows),
    )

    footprints_with_points = int(np.sum(statistics.point_count > 0))
    report: dict[str, Any] = {
        "pipeline_version": "0.1.0",
        "point_cloud": {
            "input": str(input_las),
            "output": str(output_las.resolve()),
            "point_count": point_count,
            "crs": point_crs.to_string(),
            "bounds": list(bbox),
            "building_classes_used_for_statistics": list(building_classes),
        },
        "bbed": source_metadata,
        "matching": {
            "method": "2D point-in-polygon (boundary-inclusive); smallest footprint wins overlaps",
            "footprint_count": len(footprints),
            "matched_points": matched_points,
            "unmatched_points": point_count - matched_points,
            "matched_point_ratio": matched_points / point_count if point_count else 0.0,
            "ambiguous_points": ambiguous_points,
            "footprints_with_points": footprints_with_points,
            "footprint_coverage": footprints_with_points / len(footprints),
        },
        "height_diagnostic": _height_metrics(derived_rows, heights.astype(np.float64)),
        "limitations": [
            "A shared CRS is necessary but does not prove survey alignment.",
            "All points inside footprints are enriched; use --building-class for building-only statistics.",
            "String BBED fields live in building_lookup.csv and are joined through bbed_id.",
            "Registration offsets and segmentation IoU require independent control points or instances.",
        ],
    }

    if instances_source:
        predicted, predicted_metadata = load_footprints(
            instances_source,
            bbox,
            point_crs,
            source_crs_override=instance_crs_value,
            bbox_buffer=bbox_buffer,
        )
        report["instance_source"] = predicted_metadata
        report["instance_evaluation"] = evaluate_instances(
            footprints, predicted, iou_threshold=iou_threshold
        )

    _write_json(output_dir / "quality_report.json", report)
    _write_markdown_report(output_dir / "quality_report.md", report)
    return report
