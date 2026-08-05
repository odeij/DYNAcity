"""Urban-form KPI definitions used by every forecast rollout.

Reduces one realised city — a state per building — to the aggregate indicators
planners actually ask about. Called once per Monte Carlo draw per step, so the
spread of each KPI across draws becomes its uncertainty band.

Two conventions hold throughout:

- Geometry is static. Only the *state* changes across a rollout; footprint,
  floors, and height stay as observed. A demolished building keeps its recorded
  footprint and simply stops counting toward built metrics.
- Nothing is imputed. Buildings with no recorded height are excluded from the
  height quantiles rather than filled in, so those KPIs describe the measured
  subset and are not diluted by invented values.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .contracts import UrbanObjectState
from .status import CanonicalState


DEFAULT_KPIS = (
    "completed_count",
    "active_construction_count",
    "stalled_count",
    "vacant_count",
    "demolished_count",
    "empty_or_parking_area_m2",
    "estimated_gross_floor_area_m2",
    "height_p50_m",
    "height_p90_m",
    "built_land_fraction",
)

KPI_UNITS = {
    "completed_count": "buildings",
    "active_construction_count": "buildings",
    "stalled_count": "buildings",
    "vacant_count": "buildings",
    "demolished_count": "buildings",
    "empty_or_parking_area_m2": "m2",
    "estimated_gross_floor_area_m2": "m2",
    "height_p50_m": "m",
    "height_p90_m": "m",
    "built_land_fraction": "ratio",
}


def compute_kpis(
    objects: Sequence[UrbanObjectState],
    states: Sequence[str | CanonicalState],
) -> dict[str, float]:
    """Aggregate one realised assignment of states over the object set.

    `states` is positionally aligned with `objects` — element i is the state
    building i holds in this particular draw. Every value is returned as a float
    (including the counts) so the caller can take quantiles across draws without
    dtype juggling.
    """

    state_values = [str(state) for state in states]
    areas = np.asarray([item.footprint_area_m2 for item in objects], dtype=np.float64)
    floors = np.asarray(
        [item.floors if item.floors is not None else 0.0 for item in objects],
        dtype=np.float64,
    )
    heights = np.asarray(
        [item.height_m if item.height_m is not None else np.nan for item in objects],
        dtype=np.float64,
    )
    stable = np.asarray([value == CanonicalState.STABLE_BUILT.value for value in state_values])
    active = np.asarray([value == CanonicalState.ACTIVE_CONSTRUCTION.value for value in state_values])
    stalled = np.asarray([value == CanonicalState.STALLED_OR_CANCELLED.value for value in state_values])
    vacant = np.asarray([value == CanonicalState.VACANT_OR_EVICTED.value for value in state_values])
    demolished = np.asarray([value == CanonicalState.DEMOLISHED.value for value in state_values])
    empty = np.asarray([value == CanonicalState.EMPTY_OR_PARKING.value for value in state_values])
    # "Built" is defined by exclusion — anything not empty, demolished, or
    # unknown counts as standing. Excluding unknown is the conservative choice:
    # a building whose state could not be determined is left out of built-area
    # totals rather than assumed to be standing.
    built = ~(empty | demolished | np.asarray([value == "unknown" for value in state_values]))
    known_heights = heights[built & ~np.isnan(heights)]
    total_area = float(areas.sum())
    return {
        "completed_count": float(stable.sum()),
        "active_construction_count": float(active.sum()),
        "stalled_count": float(stalled.sum()),
        "vacant_count": float(vacant.sum()),
        "demolished_count": float(demolished.sum()),
        "empty_or_parking_area_m2": float(areas[empty].sum()),
        # footprint × floors — an estimate, not a survey measurement. Buildings
        # with no recorded floor count contribute 0 here (see the `floors`
        # coercion above), so this reads as a lower bound.
        "estimated_gross_floor_area_m2": float((areas[built] * floors[built]).sum()),
        # Guarded against an all-unknown-height draw, where np.quantile on an
        # empty array would otherwise raise mid-rollout.
        "height_p50_m": float(np.quantile(known_heights, 0.50)) if len(known_heights) else 0.0,
        "height_p90_m": float(np.quantile(known_heights, 0.90)) if len(known_heights) else 0.0,
        # Denominator is the total footprint of every object in the snapshot,
        # including demolished and empty ones, so this stays comparable across
        # steps as buildings leave the built set.
        "built_land_fraction": float(areas[built].sum() / total_area) if total_area else 0.0,
    }

