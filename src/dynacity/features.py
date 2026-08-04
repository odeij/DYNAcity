"""Urban morphology features derived from LAS points inside BBED polygons."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import contains_xy
from shapely.geometry import shape

from .bbed import resolved_object_ids
from .las import iter_point_chunks


@dataclass(frozen=True)
class PointArrays:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    red: np.ndarray | None
    green: np.ndarray | None
    blue: np.ndarray | None


def load_points(path: str | Path, *, max_points: int = 10_000_000) -> PointArrays:
    chunks = list(iter_point_chunks(path, max_points=max_points))
    if not chunks:
        raise ValueError(f"no points found in {path}")

    def combine(name: str) -> np.ndarray | None:
        values = [getattr(chunk, name) for chunk in chunks]
        if values[0] is None:
            return None
        return np.concatenate(values)

    return PointArrays(
        x=np.concatenate([chunk.x for chunk in chunks]),
        y=np.concatenate([chunk.y for chunk in chunks]),
        z=np.concatenate([chunk.z for chunk in chunks]),
        red=combine("red"),
        green=combine("green"),
        blue=combine("blue"),
    )


def height_above_ground(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    *,
    grid_size_m: float = 3.0,
    ground_quantile: float = 0.05,
) -> np.ndarray:
    if not len(x):
        return np.array([], dtype=np.float64)
    gx = np.floor((x - x.min()) / grid_size_m).astype(np.int64)
    gy = np.floor((y - y.min()) / grid_size_m).astype(np.int64)
    width = int(gx.max()) + 1
    cell = gy * width + gx
    order = np.argsort(cell, kind="stable")
    sorted_cell = cell[order]
    boundaries = np.flatnonzero(np.r_[True, sorted_cell[1:] != sorted_cell[:-1], True])
    ground = np.empty(len(z), dtype=np.float64)
    for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
        indexes = order[start:end]
        ground[indexes] = np.quantile(z[indexes], ground_quantile)
    return np.maximum(z - ground, 0.0)


def vegetation_mask(
    red: np.ndarray | None,
    green: np.ndarray | None,
    blue: np.ndarray | None,
    *,
    threshold: float = 0.08,
) -> np.ndarray | None:
    if red is None or green is None or blue is None:
        return None
    maximum = max(float(red.max()), float(green.max()), float(blue.max()), 1.0)
    scale = 65535.0 if maximum > 255.0 else 255.0
    r = red.astype(np.float64) / scale
    g = green.astype(np.float64) / scale
    b = blue.astype(np.float64) / scale
    excess_green = 2.0 * g - r - b
    return excess_green > threshold


def extract_building_features(
    points: PointArrays,
    bbed_collection: dict,
    *,
    id_field: str = "BULBuildingID",
) -> pd.DataFrame:
    hag = height_above_ground(points.x, points.y, points.z)
    vegetation = vegetation_mask(points.red, points.green, points.blue)
    rows: list[dict] = []
    features = bbed_collection.get("features", [])
    object_ids = resolved_object_ids(features, preferred_field=id_field)
    for feature, object_id in zip(features, object_ids, strict=True):
        if not feature.get("geometry"):
            continue
        polygon = shape(feature["geometry"])
        minx, miny, maxx, maxy = polygon.bounds
        bbox = (
            (points.x >= minx)
            & (points.x <= maxx)
            & (points.y >= miny)
            & (points.y <= maxy)
        )
        candidates = np.flatnonzero(bbox)
        if len(candidates):
            inside = contains_xy(polygon, points.x[candidates], points.y[candidates])
            indexes = candidates[inside]
        else:
            indexes = candidates
        area = float(polygon.area)
        row = {
            "object_id": object_id,
            "point_count": int(len(indexes)),
            "footprint_area_m2": area,
            "point_density_m2": float(len(indexes) / area) if area else 0.0,
        }
        if len(indexes):
            local_hag = hag[indexes]
            local_vegetation = (
                vegetation[indexes]
                if vegetation is not None
                else np.zeros(len(indexes), dtype=bool)
            )
            solid = local_hag[~local_vegetation]
            if not len(solid):
                solid = local_hag
            roof_cutoff = np.quantile(solid, 0.75)
            roof = solid[solid >= roof_cutoff]
            row.update(
                height_p50_m=float(np.quantile(solid, 0.50)),
                height_p90_m=float(np.quantile(solid, 0.90)),
                height_p95_m=float(np.quantile(solid, 0.95)),
                height_max_m=float(solid.max()),
                roof_roughness_m=float(np.std(roof)),
                vegetation_ratio=float(local_vegetation.mean()),
            )
        else:
            row.update(
                height_p50_m=np.nan,
                height_p90_m=np.nan,
                height_p95_m=np.nan,
                height_max_m=np.nan,
                roof_roughness_m=np.nan,
                vegetation_ratio=np.nan,
            )
        rows.append(row)
    return pd.DataFrame(rows)
