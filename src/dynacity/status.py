"""Reviewable crosswalk from BBED survey labels to modeling states."""

from __future__ import annotations

import re
from enum import StrEnum


class CanonicalState(StrEnum):
    STABLE_BUILT = "stable_built"
    ACTIVE_CONSTRUCTION = "active_construction"
    STALLED_OR_CANCELLED = "stalled_or_cancelled"
    RENOVATED = "renovated"
    VACANT_OR_EVICTED = "vacant_or_evicted"
    EMPTY_OR_PARKING = "empty_or_parking"
    DEMOLISHED = "demolished"
    UNKNOWN = "unknown"


def _key(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


_STATUS_MAP: dict[str, CanonicalState] = {
    "complete residential": CanonicalState.STABLE_BUILT,
    "complete building": CanonicalState.STABLE_BUILT,
    "non residential building": CanonicalState.STABLE_BUILT,
    "non residential": CanonicalState.STABLE_BUILT,
    "old bldg inhabited": CanonicalState.STABLE_BUILT,
    "under construction": CanonicalState.ACTIVE_CONSTRUCTION,
    "construction site": CanonicalState.ACTIVE_CONSTRUCTION,
    "construction on hold": CanonicalState.STALLED_OR_CANCELLED,
    "cancelled construction": CanonicalState.STALLED_OR_CANCELLED,
    "renovated": CanonicalState.RENOVATED,
    "old threat of eviction": CanonicalState.VACANT_OR_EVICTED,
    "evicted building": CanonicalState.VACANT_OR_EVICTED,
    "old bldg uninhabited": CanonicalState.VACANT_OR_EVICTED,
    "empty lot": CanonicalState.EMPTY_OR_PARKING,
    "parking lot": CanonicalState.EMPTY_OR_PARKING,
    "demolished": CanonicalState.DEMOLISHED,
    "not available": CanonicalState.UNKNOWN,
}


def canonicalize_status(value: object) -> CanonicalState:
    """Map a BBED raw label while retaining an explicit unknown fallback."""

    return _STATUS_MAP.get(_key(value), CanonicalState.UNKNOWN)


def known_raw_statuses() -> dict[str, str]:
    return {raw: state.value for raw, state in sorted(_STATUS_MAP.items())}

