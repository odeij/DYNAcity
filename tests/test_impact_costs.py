import numpy as np
import pytest

from dynacity.impact_costs import (
    GDP_PER_CAPITA_EU28_2015,
    Prices,
    construction_cost,
    damage_fraction,
    event_cost,
    road_type,
    trip_costs,
)
from dynacity.traffic import road_events, traffic_impact

from test_traffic import model, roads_of_way  # noqa: F401 (fixture)


def test_road_types_follow_van_ginkel():
    assert road_type("motorway_link") == "motorway"
    assert road_type("secondary") == "secondary"
    assert road_type("residential") == road_type("unclassified") == "other"


def test_damage_curves_read_table_2():
    depth = np.array([0.0, 0.5, 1.0, 2.0, 6.0, 9.0])
    assert damage_fraction("primary", depth) == pytest.approx([0, 0.015, 0.025, 0.035, 0.05, 0.05])
    assert damage_fraction("trunk", np.array([0.5, 1.5])) == pytest.approx([0.002, 0.025])
    lit = Prices(sophisticated_highways=True)
    assert damage_fraction("trunk", np.array([1.5]), lit) == pytest.approx([0.075])
    mean = Prices(flow="mean")
    assert damage_fraction("primary", np.array([1.0]), mean) == pytest.approx([(0.025 + 0.20) / 2])


def test_construction_cost_scales_by_type_lanes_and_gdp(model):
    prices = Prices()
    cost = construction_cost(model, prices)
    # Untagged secondary: 2 lanes each way, 4 against the type's 2 → +50%;
    # the middle of its 0.5-1.5 M€/km range.
    road = roads_of_way(model, 1)[0]
    assert cost[road] == pytest.approx(1.0e6 * 1.5 * model.length_m[road] / 1000 * prices.road_cost_scale)
    # Residential counts as "other": 1 lane each way, 2 against 1 → +25%, 0.2 M€/km.
    road = roads_of_way(model, 10)[0]
    assert cost[road] == pytest.approx(0.2e6 * 1.25 * model.length_m[road] / 1000 * prices.road_cost_scale)
    assert 32_000 < GDP_PER_CAPITA_EU28_2015 < 33_000
    assert prices.road_cost_scale == pytest.approx(0.2624, abs=1e-3)


def test_trip_costs_price_vehicle_hours(model):
    out = road_events(model, closed=roads_of_way(model, 1))
    impact = traffic_impact(model, out, origins=6, per_origin=6)
    hours = impact["vehicle_hours"]
    assert set(hours) == {"delay", "spillover", "cut_off_usual"}
    assert all(v >= 0 for v in hours.values())
    prices = Prices(value_of_time=2.0, occupancy=1.5, cut_off_share=0.5)
    cost = trip_costs(impact, prices)
    assert cost["delay"] == pytest.approx(hours["delay"] * 3.0)
    assert cost["cut_off"] == pytest.approx(hours["cut_off_usual"] * 3.0 * 0.5)


def test_event_cost_sums_trips_each_hour(model):
    out = road_events(model, closed=roads_of_way(model, 1))
    one = event_cost(model, out, start_hour=8, duration_h=1, origins=6, per_origin=6)
    three = event_cost(model, out, start_hour=8, duration_h=3, origins=6, per_origin=6)
    assert one["currency"] == "USD" and set(one["sources"]) >= {"value_of_time", "road_cost"}
    assert [h["hour"] for h in three["hourly"]] == [8, 9, 10]
    assert three["delay"] == pytest.approx(sum(h["delay"] for h in three["hourly"]))
    parts = ("delay", "spillover", "cut_off")
    assert three["total"] == pytest.approx(sum(three[p] for p in parts))
    with pytest.raises(ValueError):
        event_cost(model, out, duration_h=0)


def test_prices_reject_bad_values():
    with pytest.raises(ValueError):
        Prices(flow="medium")
    with pytest.raises(ValueError):
        Prices(value_of_time=-1)
