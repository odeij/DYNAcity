from dataclasses import replace

import numpy as np
import pytest

from dynacity.plan_runs import RunSettings, StreetPlan, run_plan
from dynacity.traffic import TrafficModel
from dynacity.traffic_sumo import runtime


@pytest.fixture
def network():
    # The diamond of test_traffic_sumo: two routes from 1 to 2, one via 3 and 4.
    xy = {0: (733000, 3750000), 1: (733100, 3750000), 2: (733200, 3750000),
          3: (733100, 3750100), 4: (733200, 3750100), 5: (733300, 3750000), 6: (732900, 3750000)}
    arcs = [(0, 1), (1, 2), (1, 3), (3, 4), (4, 2), (2, 5), (6, 0)]
    return TrafficModel(crs='EPSG:32636', geometry=[[xy[u], xy[v]] for u, v in arcs],
                        u=np.array([a for a, _ in arcs]), v=np.array([b for _, b in arcs]),
                        length_m=np.array([100.] * 7), oneway=np.ones(7), highway=['residential'] * 7,
                        way_id=list(range(7)), speed_kmh=np.full((7, 24), 36.), free_kmh=np.full(7, 36.),
                        readings=np.full(7, 100), estimated=np.zeros(7, dtype=bool), lanes=np.ones(7),
                        name=['', 'Main Street', '', '', '', 'Main Street', ''])


def _sumo():
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_plan_closes_every_piece_of_a_named_street_and_rejects_unknowns(network):
    plan = StreetPlan.from_json({'plan_id': 'p', 'close_streets': ['main street'], 'close_roads': [3],
                                 'add_lanes': {'0': 1}}, network)
    assert plan.closed == (1, 3, 5) and plan.lanes == {0: 1}
    assert list(np.flatnonzero(plan.disruption(network).closed)) == [1, 3, 5]
    with pytest.raises(ValueError, match='no road named'):
        StreetPlan.from_json({'close_streets': ['Nowhere']}, network)
    with pytest.raises(ValueError, match='no road 99'):
        StreetPlan.from_json({'close_roads': [99]}, network)
    with pytest.raises(ValueError, match='unknown plan fields'):
        StreetPlan.from_json({'close': [1]}, network)
    with pytest.raises(ValueError):
        RunSettings(measure_s=10, sample_s=10)


def test_empty_plan_changes_nothing(network):
    _sumo()
    result = run_plan(network, StreetPlan('none'), RunSettings(seeds=(3,), warmup_s=20, measure_s=40))
    for key, bands in result['summary'].items():
        assert bands['change'] == {'low': 0.0, 'median': 0.0, 'high': 0.0}, key
    assert any('empty plan' in w for w in result['warnings'])
    assert result['roads'] and all(r['vehicle_seconds'][0] == r['vehicle_seconds'][1] for r in result['roads'])


def test_closing_a_branch_moves_its_cars_to_the_other_and_keeps_the_baseline(network):
    _sumo()
    plan = StreetPlan('close-upper', closed=(2,))
    result = run_plan(network, plan, RunSettings(seeds=(3, 4), warmup_s=20, measure_s=60))
    roads = {r['road']: r for r in result['roads']}
    # The closed branch carries no cars in the scenario, and some in the baseline.
    assert roads[2]['vehicle_seconds'][0] == 0 and roads[2]['vehicle_seconds'][1] > 0
    assert len(result['paired']) == 2 and result['summary']['arrived']['baseline']['median'] > 0


def test_added_lanes_are_compared_with_the_original_roads(network):
    _sumo()
    result = run_plan(network, StreetPlan('wider', lanes={1: 1}), RunSettings(seeds=(3,), warmup_s=20, measure_s=40))
    assert result['plan']['added_lanes'] == {'1': 1}
    assert result['summary']['arrived']['scenario']['median'] > 0
