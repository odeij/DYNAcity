"""Planner interventions on the street network: speed limits and lanes handed to pedestrians.

A `RoadPlanning` entry names a road index of the traffic model. A speed limit
caps the road's simulated speed (never raises it); `lanes_removed` takes that
many lanes in each direction away from motor traffic, curb side first, to
reserve the space for people on foot. A road left with no motor lane routes as
closed. The solver (`traffic_sumo.Simulation.apply`) enforces both through
SUMO lane permissions and edge speed limits, on the scenario run only.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .traffic import TrafficModel, lane_counts

MAX_PLANNED_ROADS = 1000
MIN_SPEED_LIMIT_KMH, MAX_SPEED_LIMIT_KMH = 5.0, 80.0
MAX_LANES_REMOVED = 4


class RoadPlanning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    road: int = Field(ge=0)
    speed_limit_kmh: float | None = Field(default=None, ge=MIN_SPEED_LIMIT_KMH, le=MAX_SPEED_LIMIT_KMH, allow_inf_nan=False)
    lanes_removed: int = Field(default=0, ge=0, le=MAX_LANES_REMOVED)


# road index -> (speed cap in km/h or None, lanes removed per direction)
Plan = dict[int, tuple[float | None, int]]


def resolve_planning(model: TrafficModel, planning: Iterable[RoadPlanning | Mapping] | None) -> Plan:
    """Validate planner entries against the model; raise ValueError (HTTP 422) on any bad one.

    Entries that change nothing are dropped, so an empty or all-default list
    is the unplanned network.
    """
    items = [p if isinstance(p, RoadPlanning) else RoadPlanning.model_validate(p) for p in planning or ()]
    if len(items) > MAX_PLANNED_ROADS:
        raise ValueError(f"at most {MAX_PLANNED_ROADS} planned roads per request")
    roads = len(model)
    seen: set[int] = set()
    plan: Plan = {}
    for item in items:
        if not 0 <= item.road < roads:
            raise ValueError(f"no road {item.road} in this traffic model ({roads} roads)")
        if item.road in seen:
            raise ValueError(f"road {item.road} is planned more than once")
        seen.add(item.road)
        if item.speed_limit_kmh is not None or item.lanes_removed:
            plan[item.road] = (item.speed_limit_kmh, item.lanes_removed)
    return plan


def lanes_left(model: TrafficModel, plan: Plan) -> dict[int, int]:
    """Motor lanes remaining per direction on every planned road with lanes removed (0 = closed)."""
    lanes = lane_counts(model)
    return {road: max(0, int(lanes[road]) - removed) for road, (_, removed) in plan.items() if removed}


def speed_caps(model: TrafficModel, plan: Plan) -> np.ndarray:
    """Per-road speed cap in km/h; infinite where none is planned."""
    caps = np.full(len(model), np.inf)
    for road, (cap, _) in plan.items():
        if cap is not None:
            caps[road] = cap
    return caps


def summarize(model: TrafficModel, plan: Plan) -> dict:
    left = lanes_left(model, plan)
    return {"roads": len(plan),
            "speed_limited": sum(cap is not None for cap, _ in plan.values()),
            "lanes_removed_roads": len(left),
            "closed_by_lane_removal": sorted(road for road, n in left.items() if n == 0)}
