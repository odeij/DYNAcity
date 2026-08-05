from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Footprint:
    """A polygon and its source attributes, identified within one pipeline run."""

    match_id: int
    geometry: BaseGeometry
    properties: dict[str, Any]


@dataclass(frozen=True)
class MatchResult:
    """Point-to-footprint result arrays."""

    match_ids: Any
    overlap_counts: Any

