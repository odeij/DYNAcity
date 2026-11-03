"""Money on what a flood does to the streets: road damage, delays and trips cut off.

This is the cost side of the Copenhagen reinforcement-learning setup (Costa et
al. 2024, 2026; see docs/rl-flood-transport.md) for Beirut. It prices the
outputs the traffic model already gives:

- road damage: van Ginkel et al. (2021). A road's construction cost per km
  comes from its type's range (Table 1), moved 25% per lane above or below the
  type's default, and the damage is a share of that cost read off a
  depth-damage curve (Table 2) over the flooded length at its mean depth.
  Costs are an EU-28 average at 2015 prices; as the paper does, they are
  scaled to the local economy by GDP per capita, then converted to US dollars.
- delay: vehicle-hours lost (`traffic_impact`'s `vehicle_hours`) × occupancy ×
  a value of time;
- trips cut off: a share of their usual trip cost, as Costa et al. (2026) do.

Every unit value sits in `Prices` with its source. They are transferred from
studies made for other places or purposes, with price years from 2015 to 2016;
none is calibrated against observed Beirut losses, so a total is a scale of
magnitude for comparing options, not a loss estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .traffic import RoadDisruption, TrafficModel, lane_counts, traffic_impact

# van Ginkel et al. (2021), Table 1: construction cost range per road type,
# million euros per km (EU-28 average, 2015 prices), for its default lanes.
CONSTRUCTION_COST_MEUR_KM = {
    "motorway": (3.5, 35.0), "trunk": (2.5, 7.5), "primary": (1.0, 3.0),
    "secondary": (0.5, 1.5), "tertiary": (0.2, 0.6), "other": (0.1, 0.3),
}
# Lanes over both directions the ranges are for (2 × 2, 2 × 1, 1); each lane
# more or fewer adds or removes `LANE_STEP` of the cost (Sect. 2.4.1).
DEFAULT_LANES_TOTAL = {"motorway": 4, "trunk": 4, "primary": 2, "secondary": 2, "tertiary": 2, "other": 1}
LANE_STEP = 0.25
# Table 2: damage as % of construction cost at water depth in cm. C1/C2 are
# motorways and trunks with lighting and signalling, C3/C4 without, C5/C6 all
# other roads; the odd curves are for low flow velocity, the even ones high.
DAMAGE_CURVES: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "C1": ((0, 50, 100, 150, 200, 600), (0, 1, 3, 7.5, 10, 20)),
    "C2": ((0, 50, 100, 150, 200, 600), (0, 2, 6, 10, 12, 22)),
    "C3": ((0, 50, 100, 150, 200, 600), (0, 0.2, 0.4, 2.5, 3, 4)),
    "C4": ((0, 50, 100, 150, 200, 600), (0, 1.5, 4, 20, 25, 35)),
    "C5": ((0, 50, 100, 200, 600), (0, 1.5, 2.5, 3.5, 5)),
    "C6": ((0, 50, 100, 200, 600), (0, 12, 20, 28, 35)),
}
# The paper's deterministic estimate (Sect. 2.5): where in the cost range a
# road sits, and the low/high-flow curve pair it uses.
_DESIGNS = {
    "sophisticated": (0.75, ("C1", "C2")),
    "simple": (0.25, ("C3", "C4")),
    "other": (0.50, ("C5", "C6")),
}

# GDP per capita, current US$, 2015 (World Bank API NY.GDP.PCAP.CD; the EU-28
# from NY.GDP.MKTP.CD and SP.POP.TOTL for the EU-27 plus the United Kingdom),
# and US$ per euro in 2015 (1 / PA.NUS.FCRF for the euro area).
GDP_PER_CAPITA_LEBANON_2015 = 7714.11
GDP_PER_CAPITA_EU28_2015 = (13_656_255_577_702.6 + 2_945_579_890_258.46) / (443_793_218 + 65_087_000)
USD_PER_EUR_2015 = 1.0 / 0.901296423367095


def road_type(highway: str) -> str:
    """van Ginkel's road type for an OSM `highway` value; links count as their road."""

    base = highway.removesuffix("_link")
    return base if base in CONSTRUCTION_COST_MEUR_KM else "other"


@dataclass(frozen=True)
class Prices:
    """Unit values for pricing traffic impacts, each with where it comes from."""

    currency: str = "USD"
    # Value of a person's travel time, per hour.
    value_of_time: float = 2.62
    # Persons per vehicle.
    occupancy: float = 1.3
    # Share of its usual trip cost a trip that cannot be made costs.
    cut_off_share: float = 0.8
    # Currency units per euro of EU-28-average 2015 road cost.
    road_cost_scale: float = GDP_PER_CAPITA_LEBANON_2015 / GDP_PER_CAPITA_EU28_2015 * USD_PER_EUR_2015
    # Flow velocity for the damage curves: "low", "high", or "mean" of the two
    # (the paper's deterministic choice). Rain ponding in streets is slow, and
    # Costa et al. use the low-flow curves.
    flow: str = "low"
    # Treat motorways and trunks as lit and signalled (C1/C2) instead of
    # simple (C3/C4). The road model has no OSM `lit` tag to tell them apart.
    sophisticated_highways: bool = False
    sources: dict[str, str] = field(default_factory=lambda: {
        "value_of_time": (
            "US$2.62/h 'Weighted VOT' for Beirut urban roads, World Bank ICR3868 (2016), Lebanon Urban "
            "Transport Development Project, Annex 3, from the Beirut Peripherique Highway Feasibility Study "
            "(2011); price year not stated; read as per passenger"
        ),
        "occupancy": "about 1.3 persons per vehicle, World Bank PAD2574 (2018), Greater Beirut Public Transport Project, para. 7",
        "cut_off_share": "80% of the usual trip cost, Costa et al. (2026), arXiv:2603.06278, Sect. 3.4; the literature uses 50-100%",
        "road_cost": (
            "van Ginkel et al. (2021), NHESS 21:1011, Tables 1-2 and Sect. 2.5, EU-28 average 2015 euros, scaled "
            "by Lebanon / EU-28 GDP per capita in 2015 and converted at the 2015 euro rate (World Bank data)"
        ),
    })

    def __post_init__(self) -> None:
        if self.flow not in ("low", "high", "mean"):
            raise ValueError(f"flow must be 'low', 'high' or 'mean', not {self.flow!r}")
        for name in ("value_of_time", "occupancy", "cut_off_share", "road_cost_scale"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number, not {value}")

    @property
    def per_vehicle_hour(self) -> float:
        return self.value_of_time * self.occupancy


def _design(kind: str, prices: Prices) -> str:
    if kind in ("motorway", "trunk"):
        return "sophisticated" if prices.sophisticated_highways else "simple"
    return "other"


def construction_cost(model: TrafficModel, prices: Prices | None = None) -> np.ndarray:
    """What building each road would cost, in `prices.currency` (van Ginkel Table 1, scaled)."""

    prices = prices or Prices()
    lanes = lane_counts(model) * np.where(model.oneway == 0, 2, 1)
    out = np.zeros(len(model))
    for i, highway in enumerate(model.highway):
        kind = road_type(highway)
        low, high = CONSTRUCTION_COST_MEUR_KM[kind]
        percentile, _ = _DESIGNS[_design(kind, prices)]
        per_km = (low + percentile * (high - low)) * 1e6
        lane_factor = max(LANE_STEP, 1.0 + LANE_STEP * (lanes[i] - DEFAULT_LANES_TOTAL[kind]))
        out[i] = per_km * lane_factor * model.length_m[i] / 1000.0
    return out * prices.road_cost_scale


def damage_fraction(highway: str, depth_m: np.ndarray, prices: Prices | None = None) -> np.ndarray:
    """Share of construction cost a road of this type loses at each water depth (van Ginkel Table 2)."""

    prices = prices or Prices()
    _, (low_curve, high_curve) = _DESIGNS[_design(road_type(highway), prices)]
    cm = np.asarray(depth_m, dtype=np.float64) * 100.0

    def read(curve: str) -> np.ndarray:
        depths, percent = DAMAGE_CURVES[curve]
        # Beyond the deepest tabulated depth the curve stays at its last value.
        return np.interp(cm, depths, percent) / 100.0

    if prices.flow == "low":
        return read(low_curve)
    if prices.flow == "high":
        return read(high_curve)
    return 0.5 * (read(low_curve) + read(high_curve))


def road_damage(model: TrafficModel, disruption: RoadDisruption, prices: Prices | None = None) -> np.ndarray:
    """Damage to each road from the disruption's flood water, in `prices.currency`; zeros without a flood."""

    prices = prices or Prices()
    if disruption.flood_depth_m is None or disruption.flood_wet_fraction is None:
        return np.zeros(len(model))
    cost = construction_cost(model, prices)
    out = np.zeros(len(model))
    for i in np.flatnonzero(disruption.flood_wet_fraction > 0):
        share = damage_fraction(model.highway[i], np.asarray([disruption.flood_depth_m[i]]), prices)[0]
        out[i] = cost[i] * disruption.flood_wet_fraction[i] * share
    return out


def trip_costs(impact: dict[str, Any], prices: Prices | None = None) -> dict[str, float]:
    """Cost of one hour of a disruption from a `traffic_impact` result, in `prices.currency`."""

    prices = prices or Prices()
    hours = impact["vehicle_hours"]
    rate = prices.per_vehicle_hour
    return {
        "delay": hours["delay"] * rate,
        "spillover": hours["spillover"] * rate,
        "cut_off": hours["cut_off_usual"] * rate * prices.cut_off_share,
    }


def event_cost(
    model: TrafficModel,
    disruption: RoadDisruption,
    prices: Prices | None = None,
    *,
    start_hour: int = 8,
    duration_h: int = 1,
    **impact_options: Any,
) -> dict[str, Any]:
    """Total cost of a disruption lasting `duration_h` whole hours from `start_hour`.

    Road damage is counted once; trip costs are summed over each hour, routed
    at that hour of day (`impact_options` go to `traffic_impact`). The road
    network is assumed to stay as `disruption` leaves it for the whole time:
    water does not rise or recede within the event.
    """

    prices = prices or Prices()
    if duration_h < 1:
        raise ValueError("duration_h must be at least one hour")
    damage = road_damage(model, disruption, prices)
    hourly = []
    for step in range(duration_h):
        hour = (start_hour + step) % 24
        impact = traffic_impact(model, disruption, hour=hour, **impact_options)
        hourly.append({"hour": hour, "vehicle_hours": impact["vehicle_hours"], **trip_costs(impact, prices)})
    costs = {
        "road_damage": float(damage.sum()),
        "delay": sum(h["delay"] for h in hourly),
        "spillover": sum(h["spillover"] for h in hourly),
        "cut_off": sum(h["cut_off"] for h in hourly),
    }
    return {
        "currency": prices.currency,
        "total": sum(costs.values()),
        **costs,
        "hourly": hourly,
        # Road indices and their damage, largest first.
        "damaged_roads": [[int(i), round(float(damage[i]), 2)] for i in np.argsort(-damage) if damage[i] > 0],
        "prices": {
            "value_of_time": prices.value_of_time, "occupancy": prices.occupancy,
            "cut_off_share": prices.cut_off_share, "road_cost_scale": round(prices.road_cost_scale, 4),
            "flow": prices.flow, "sophisticated_highways": prices.sophisticated_highways,
        },
        "sources": dict(prices.sources),
    }
