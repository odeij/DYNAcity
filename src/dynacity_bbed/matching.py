from __future__ import annotations

import numpy as np
from shapely import STRtree, points

from .models import Footprint, MatchResult


class FootprintMatcher:
    """Vectorized point-in-polygon matching backed by a spatial index."""

    def __init__(self, footprints: list[Footprint]):
        if not footprints:
            raise ValueError("At least one footprint is required")
        self.footprints = footprints
        self.geometries = [footprint.geometry for footprint in footprints]
        self.areas = np.asarray([geometry.area for geometry in self.geometries], dtype=np.float64)
        self.tree = STRtree(self.geometries)

    def match(self, x: np.ndarray, y: np.ndarray) -> MatchResult:
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if x.shape != y.shape:
            raise ValueError("x and y arrays must have the same shape")

        match_ids = np.full(x.shape, -1, dtype=np.int32)
        overlap_counts = np.zeros(x.shape, dtype=np.uint8)
        if x.size == 0:
            return MatchResult(match_ids, overlap_counts)

        point_geometries = points(x, y)
        pairs = self.tree.query(point_geometries, predicate="covered_by")
        if pairs.size == 0:
            return MatchResult(match_ids, overlap_counts)

        point_indices = pairs[0]
        polygon_indices = pairs[1]
        counts = np.bincount(point_indices, minlength=x.size)
        overlap_counts[:] = np.minimum(counts, np.iinfo(np.uint8).max).astype(np.uint8)

        # Overlapping footprints are resolved deterministically to the smallest polygon.
        order = np.lexsort((self.areas[polygon_indices], point_indices))
        sorted_points = point_indices[order]
        sorted_polygons = polygon_indices[order]
        first = np.r_[True, sorted_points[1:] != sorted_points[:-1]]
        match_ids[sorted_points[first]] = sorted_polygons[first].astype(np.int32)
        return MatchResult(match_ids, overlap_counts)


def property_values(
    footprints: list[Footprint],
    candidates: tuple[str, ...],
    dtype: np.dtype,
    default: int | float,
) -> np.ndarray:
    output = np.full(len(footprints), default, dtype=dtype)
    for index, footprint in enumerate(footprints):
        properties = {key.casefold(): value for key, value in footprint.properties.items()}
        for candidate in candidates:
            value = properties.get(candidate.casefold())
            if value in (None, ""):
                continue
            try:
                output[index] = value
                break
            except (TypeError, ValueError, OverflowError):
                continue
    return output

