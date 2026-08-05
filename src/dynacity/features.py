"""Urban morphology features derived from LAS points inside BBED polygons.

Turns a point cloud into one row of shape descriptors per BBED building. BBED
polygons define what a building *is*; the cloud only measures the points falling
inside them. Segmenting buildings from the cloud itself was rejected — Beirut's
attached fabric cannot be reliably split at party walls from geometry alone.

Every feature here is static morphology (heights, roughness, vegetation cover,
point coverage). None of it encodes status, and none of it is time-varying, so
the output can be joined to any interval — subject to the acquisition-year
leakage rule enforced in `panel.PanelBuilder`.

Buildings the cloud does not cover yield NaN rather than being dropped, so the
absence of coverage stays visible downstream as an explicit missing modality.
"""

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
    """Read a whole cloud into memory as flat arrays.

    Fully materialised — the polygon join below needs random access across all
    points, not a stream. Budget roughly 30 bytes per point: 10M points is
    ~300 MB, and the guard in `iter_point_chunks` exists as much for this as for
    read time. Larger clouds should be tiled to COPC first.
    """

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
    """Normalise elevations to height above a locally-estimated ground surface.

    Raw z is elevation above the CRS datum, which conflates building height with
    terrain — and Beirut is steep enough that this matters. Ground is estimated
    per `grid_size_m` cell as a low quantile of the elevations in that cell,
    which is a standard cheap surrogate for a DTM and needs no ground
    classification (this project's clouds are unclassified).

    `grid_size_m` trades terrain fidelity against robustness: cells must stay
    large enough to contain some near-ground return, or a cell covering only a
    rooftop would treat the roof as ground and flatten the building to zero
    height. `ground_quantile` above 0 rather than the minimum resists outlying
    low points.

    Clamped at 0 — negative heights are measurement artifacts, not basements.
    """

    if not len(x):
        return np.array([], dtype=np.float64)
    # Flatten the 2D cell grid to a single integer key, then sort so each
    # cell's points are contiguous — this replaces a per-cell mask (O(cells ×
    # points)) with one sort plus a linear scan over run boundaries.
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
    """Flag likely vegetation points using the Excess Green index (2G − R − B).

    A standard RGB-only greenness index, used because these clouds carry colour
    but no near-infrared band (which NDVI would require). Vegetation is
    separated so that tree canopy overhanging a footprint does not get measured
    as roof height.

    Returns None when the cloud has no colour at all, which callers treat as
    "no vegetation information" rather than "no vegetation".

    Channel depth is inferred from the observed maximum: LAS stores colour as
    16-bit, but 8-bit values are common in practice, and normalising by the
    wrong scale would push every point below the threshold.
    """

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
    """Produce one morphology row per BBED building.

    Height normalisation runs once over the whole cloud rather than per
    building, so ground estimation uses the full surrounding terrain instead of
    only the points within a footprint — a building whose polygon contains no
    ground return still gets a sane height.
    """

    hag = height_above_ground(points.x, points.y, points.z)
    vegetation = vegetation_mask(points.red, points.green, points.blue)
    rows: list[dict] = []
    features = bbed_collection.get("features", [])
    object_ids = resolved_object_ids(features, preferred_field=id_field)
    for feature, object_id in zip(features, object_ids, strict=True):
        if not feature.get("geometry"):
            continue
        # Two-stage point-in-polygon: cheap bounding-box filter first, exact
        # containment only on the survivors. Testing every point against every
        # polygon directly would be intractable at these point counts.
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
            # Measure the structure, not the canopy over it. If excluding
            # vegetation would leave nothing at all, fall back to using every
            # point — a fully "green" footprint is more likely a misclassified
            # roof than an empty lot.
            solid = local_hag[~local_vegetation]
            if not len(solid):
                solid = local_hag
            # Roughness is measured on the upper quartile of heights — i.e. the
            # roof surface — so that facade returns down the building's sides do
            # not register as roof articulation.
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
            # No points inside this footprint: the cloud does not cover this
            # building. NaN rather than 0 — zero height is a measurement, absence
            # is not — and `point_count` of 0 records why.
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
