"""Reviewable crosswalk from BBED survey labels to modeling states.

BBED records building status as free-text survey labels that are not stable
between waves: the same unchanged building can be written "Complete
Residential" in 2018 and "Complete Building" in 2022. Modelling those strings
directly would score label edits as real urban change.

This module collapses raw labels onto a small closed set of canonical states.
The mapping is deliberately a literal dict rather than fuzzy matching so that a
domain reviewer can audit every decision, and so that an unrecognised label
fails loudly into UNKNOWN instead of being silently guessed at.
"""

from __future__ import annotations

import re
from enum import StrEnum


class CanonicalState(StrEnum):
    """The 8 modeling states. This set is a public contract.

    These values appear in the trained model's class labels, in the panel CSV,
    in the JSON API, and in persisted ModelBundles. Adding, removing, or
    renaming a member invalidates every artifact trained before the change, so
    treat it as a breaking schema change rather than a refactor.
    """

    STABLE_BUILT = "stable_built"
    ACTIVE_CONSTRUCTION = "active_construction"
    STALLED_OR_CANCELLED = "stalled_or_cancelled"
    RENOVATED = "renovated"
    VACANT_OR_EVICTED = "vacant_or_evicted"
    EMPTY_OR_PARKING = "empty_or_parking"
    DEMOLISHED = "demolished"
    UNKNOWN = "unknown"


def _key(value: object) -> str:
    """Normalise a raw label for lookup.

    Lowercases and reduces every run of non-alphanumeric characters to a single
    space, so "Old-Bldg-Inhabited", "old bldg inhabited", and
    "Old_Bldg__Inhabited" all collapse to the same key. This absorbs punctuation
    and casing drift between survey waves without hiding genuine label changes.
    """

    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


# Keys are already _key()-normalised. Several raw labels intentionally collapse
# onto one state: the distinction they carry (e.g. residential vs
# non-residential) is captured by the separate Building_Use feature, not by the
# status label, so keeping them apart here would only fragment the classes.
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
    """Map a BBED raw label while retaining an explicit unknown fallback.

    Unrecognised, missing, and empty labels all become UNKNOWN rather than
    raising or being dropped. That is deliberate: a building whose status could
    not be surveyed is a real observation about the city, and discarding those
    rows would bias the transition counts toward well-surveyed areas. UNKNOWN is
    a first-class modelling state, not an error code.
    """

    return _STATUS_MAP.get(_key(value), CanonicalState.UNKNOWN)


def known_raw_statuses() -> dict[str, str]:
    """Expose the full crosswalk for review and documentation.

    Returned keys are normalised (lowercase, punctuation flattened), not the
    verbatim BBED strings.
    """

    return {raw: state.value for raw, state in sorted(_STATUS_MAP.items())}

