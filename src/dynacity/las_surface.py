"""Stream LAS observations into a review surface, never an inferred ground model.

Every populated cell records actual point extrema and support. Empty cells stay
NaN; no interpolation, datum conversion, ground classification or hydraulic
conditioning is performed. The output deliberately differs from TerrainGrid so
an unclassified roof surface cannot silently become flood terrain.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .las import LASHeader, iter_point_chunks


def export_las_surface(source: str | Path, output: str | Path, *, cell_m: float,
                       horizontal_crs: str, crs_evidence: str,
                       max_points: int = 10_000_000,
                       max_cells: int = 2_000_000) -> dict:
    """Write observed min/max/count grids plus an auditable JSON sidecar.

    CRS is explicitly asserted by the caller, not recovered from an empty LAS
    header. Ground-labelled counts are descriptive and do not certify labels.
    Only uncompressed LAS formats supported by the lightweight reader are used.
    """
    from pyproj import CRS

    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or output.suffix != ".npz":
        raise ValueError("output must be a separate .npz file")
    if output.exists() or output.with_suffix(".json").exists():
        raise ValueError("output or report already exists")
    if not math.isfinite(cell_m) or cell_m <= 0:
        raise ValueError("cell_m must be finite and positive")
    crs = CRS.from_user_input(horizontal_crs)
    if not crs.is_projected or any(not math.isclose(a.unit_conversion_factor, 1.) for a in crs.axis_info[:2]):
        raise ValueError("horizontal_crs must be projected with metre units")
    if not crs_evidence.strip():
        raise ValueError("crs_evidence is required for the asserted CRS")
    header = LASHeader.read(source)
    with source.open("rb") as stream:
        stream.seek(104)
        if stream.read(1)[0] & 0xC0:
            raise ValueError("compressed LAZ requires PDAL; not a raw LAS input")
    if source.stat().st_size < header.point_data_offset + header.point_count * header.point_record_length:
        raise ValueError("truncated LAS point data")
    if header.point_count < 1 or header.point_count > max_points:
        raise ValueError("point count outside explicit max_points guard")
    minx, maxx, miny, maxy, _, _ = header.bounds
    if not all(math.isfinite(v) for v in header.bounds) or maxx < minx or maxy < miny:
        raise ValueError("invalid LAS bounds")
    west, north = math.floor(minx / cell_m) * cell_m, math.ceil(maxy / cell_m) * cell_m
    # Include observations exactly on east/south edges without clipping them.
    cols, rows = math.floor((maxx - west) / cell_m) + 1, math.floor((north - miny) / cell_m) + 1
    if rows * cols > max_cells:
        raise ValueError("surface grid exceeds max_cells")
    n = rows * cols
    lo, hi = np.full(n, np.inf), np.full(n, -np.inf)
    count, ground_count = np.zeros(n, dtype=np.uint64), np.zeros(n, dtype=np.uint64)
    classes = np.zeros(32, dtype=np.uint64)
    total = 0
    for chunk in iter_point_chunks(source, max_points=max_points):
        if not (np.isfinite(chunk.x).all() and np.isfinite(chunk.y).all() and np.isfinite(chunk.z).all()):
            raise ValueError("nonfinite LAS observations")
        c = np.floor((chunk.x - west) / cell_m).astype(np.int64)
        r = np.floor((north - chunk.y) / cell_m).astype(np.int64)
        if np.any((c < 0) | (c >= cols) | (r < 0) | (r >= rows)):
            raise ValueError("points outside declared LAS bounds")
        keys = r * cols + c
        np.minimum.at(lo, keys, chunk.z)
        np.maximum.at(hi, keys, chunk.z)
        np.add.at(count, keys, 1)
        np.add.at(ground_count, keys[chunk.classification == 2], 1)
        classes += np.bincount(chunk.classification, minlength=32).astype(np.uint64)
        total += len(chunk)
    if total != header.point_count:
        raise ValueError("decoded count does not match LAS header")
    lo[count == 0], hi[count == 0] = np.nan, np.nan
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "schema_version": 1, "kind": "observed_point_surface_review",
        "source": json.loads(json.dumps(header.as_dict())), "source_sha256": digest,
        "horizontal_crs": crs.to_string(), "crs_evidence": crs_evidence,
        "crs_status": "caller_asserted", "vertical_reference": None,
        "elevation_unit_status": "source units assumed metres; not embedded or independently verified",
        "cell_m": cell_m, "spacing_is": "aggregation spacing, not survey accuracy",
        "west": west, "north": north, "rows": rows, "cols": cols,
        "bounds": [west, north - rows * cell_m, west + cols * cell_m, north],
        "points_processed": total,
        "classification_counts": {str(i): int(v) for i, v in enumerate(classes) if v},
        "populated_cells": int(np.count_nonzero(count)),
        "coverage_fraction": float(np.count_nonzero(count) / n),
        "verified_flood_terrain": False,
        "limitations": [
            "Point extrema include roofs, vegetation, vehicles and possible outliers.",
            "Minimum point elevation is not a verified ground elevation.",
            "Class 2 labels, if present, are unverified producer labels.",
            "No vertical datum or independent checkpoints supplied.",
            "Gaps stay nodata; no resampling, smoothing or gap filling performed.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, metadata=json.dumps(report),
                        min_observed_z=lo.reshape(rows, cols),
                        max_observed_z=hi.reshape(rows, cols),
                        point_count=count.reshape(rows, cols),
                        ground_label_count=ground_count.reshape(rows, cols))
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    return report
