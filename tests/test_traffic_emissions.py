"""Exhaust accounting of the paired SUMO runs (`traffic_emissions`)."""
import pytest

from dynacity.traffic import road_events
from dynacity.traffic_emissions import EMISSION_ASSUMPTIONS, MIN_WINDOW_S, PETROL_KG_PER_L, EmissionLedger
from dynacity.traffic_sumo import Simulation, Trip, runtime
from test_traffic_sumo import network  # noqa: F401  (fixture)


def test_ledger_waits_for_the_split_and_integrates_rates_over_time():
    ledger = EmissionLedger(roads=3)
    ledger.record(0, 5.0, [(10.0, 2000.0, 700.0, 5.0, 0.1, '1:0')])
    assert ledger.summary(5.0, [1]) is None

    ledger.start(10.0)
    # Scenario: one car on road 1 for 4 s; comparison: the same car twice as long on road 2.
    ledger.record(0, 14.0, [(10.0, 2000.0, 700.0, 5.0, 0.1, '1:0')])
    ledger.record(1, 14.0, [(10.0, 2000.0, 700.0, 5.0, 0.1, '2:0'), (10.0, 2000.0, 700.0, 5.0, 0.1, ':junction')])
    ledger.record(0, 14.0, [(10.0, 9e9, 0, 0, 0, '1:0')])  # no time passed: ignored
    s = ledger.summary(14.0, [1], stranded={'scenario': 3, 'baseline': 1}, emission_classes={'car': 'HBEFA4/x'})

    assert s['window_s'] == 4.0 and not s['settled']
    assert s['scenario']['co2_g'] == 8.0 and s['baseline']['co2_g'] == 16.0
    assert s['saved']['co2_g'] == 8.0 and s['saved_percent']['co2_g'] == 50.0
    assert s['saved_per_hour']['co2_g'] == 8.0 * 3600 / 4
    assert s['scenario']['vehicle_km'] == 0.04 and s['baseline']['vehicle_km'] == 0.08
    assert s['scenario']['fuel_l'] == round(2.8 / 1000 / PETROL_KG_PER_L, 3)
    assert s['co2_per_km'] == {'scenario': 200.0, 'baseline': 200.0}
    # Road 1 is planned: its exhaust in each run; junction exhaust counts only in the total.
    assert s['planned_streets']['co2_g'] == {'scenario': 8.0, 'baseline': 0.0}
    assert s['planned_streets']['elsewhere_co2_g'] == {'scenario': 0.0, 'baseline': 16.0}
    assert s['stranded'] == {'scenario': 3, 'baseline': 1, 'extra': 2}
    assert s['emission_classes'] == {'car': 'HBEFA4/x'}


def test_settled_after_the_minimum_window_and_empty_baseline_has_no_percent():
    ledger = EmissionLedger(roads=1)
    ledger.start(0.0)
    s = ledger.summary(MIN_WINDOW_S, [])
    assert s['settled'] and s['saved_percent']['co2_g'] is None and s['co2_per_km']['baseline'] is None


def test_assumptions_name_the_main_limits():
    text = ' '.join(EMISSION_ASSUMPTIONS).lower()
    for phrase in ('exhaust only', 'hbefa', 'same trips', 'traffic evaporation', 'per-hour', 'not computed'):
        assert phrase in text


def _sim(network, **kw):
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    return Simulation(network, **kw)


def test_unchanged_comparison_saves_exactly_nothing(network):
    sim = _sim(network, trips=[], continuous=True, seed=11)
    try:
        result = sim.advance(road_events(network), 5)
        assert all(f['metrics']['emissions'] is None for f in result['frames'])
        sim.fork()
        for _ in range(8):
            result = sim.advance(road_events(network), 5)
        e = result['frames'][-1]['metrics']['emissions']
        assert e['scenario']['co2_g'] > 0 and e['scenario'] == e['baseline']
        assert e['saved']['co2_g'] == 0 and e['window_s'] == 40
        assert all(c and c.startswith('HBEFA') for c in e['emission_classes'].values())
        assert result['emission_assumptions'] == EMISSION_ASSUMPTIONS
    finally:
        sim.close()


@pytest.mark.parametrize('view', [None, [35.0, 33.0, 36.0, 34.0]])
def test_closing_the_direct_road_moves_exhaust_off_it_and_detours_cost_more(network, view):
    # Five cars bound 0 -> 1 -> 2 -> 5; closing road 1 (1 -> 2) sends them round 1 -> 3 -> 4 -> 2.
    trips = [Trip(f'c{i}', ('0:1', '1:1', '5:1'), 0, 5 + 18 * i) for i in range(5)]
    sim = _sim(network, trips=trips)
    try:
        for _ in range(10):
            result = sim.advance(road_events(network, closed=[1]), 5, view=view)
        e = result['frames'][-1]['metrics']['emissions']
        assert e['planned_streets']['roads'] == 1
        assert e['planned_streets']['co2_g']['scenario'] < e['planned_streets']['co2_g']['baseline']
        assert e['scenario']['vehicle_km'] > e['baseline']['vehicle_km']
        assert e['saved']['co2_g'] < 0  # longer detours: more exhaust, not less
        n = e['noise']['planned']
        assert n['scenario_db'] < n['baseline_db'] and n['delta_db'] < 0
    finally:
        sim.close()


def test_noise_adds_as_energy_with_background_and_reads_in_plain_words():
    from dynacity.traffic_emissions import BACKGROUND_DB, leq_db, noise_reading
    # Two 70 dB cars for the whole window: +3 dB over one car; background barely matters.
    assert round(leq_db(2 * 10 ** 7 * 10, 10, background_db=0) - leq_db(10 ** 7 * 10, 10, background_db=0), 1) == 3.0
    assert leq_db(0, 10) == BACKGROUND_DB
    assert noise_reading(0.4)['words'] == 'No audible change'
    assert noise_reading(-3.5)['words'] == 'Noticeable: quieter'
    assert noise_reading(-10)['loudness_ratio'] == 0.5 and noise_reading(-10)['words'] == 'Much quieter'
    assert noise_reading(10)['loudness_ratio'] == 2.0


def test_ledger_reports_noise_on_planned_and_other_streets():
    ledger = EmissionLedger(roads=3)
    ledger.start(0.0)
    car = lambda road, db: (10.0, 0, 0, 0, 0, road, db)
    ledger.record(0, 10.0, [car('2:0', 75.0)])                    # plan: the car moved to road 2
    ledger.record(1, 10.0, [car('1:0', 75.0)])                    # today: on road 1
    n = ledger.summary(10.0, [1])['noise']
    assert n['planned']['car_free'] and n['planned']['scenario_db'] == 50.0
    assert n['planned']['baseline_db'] == 75.0 and n['planned']['words'] == 'Much quieter'
    assert [s['road'] for s in n['louder']] == [2] and n['louder'][0]['delta_db'] == 25.0
    assert n['streets_louder_3db'] == 1 and n['quieter'] == []


def test_other_street_noise_only_near_the_plan_and_on_busy_streets():
    ledger = EmissionLedger(roads=4)
    ledger.start(0.0)
    car = lambda road, db: (10.0, 0, 0, 0, 0, road, db)
    ledger.record(0, 10.0, [car('2:0', 75.0), car('3:0', 75.0), car('0:0', 52.0)])
    ledger.record(1, 10.0, [car('1:0', 75.0)])
    n = ledger.summary(10.0, [1], nearby=[0, 1, 2])['noise']
    # Road 3 is too far away; road 0 never reaches the 55 dB floor.
    assert [s['road'] for s in n['louder']] == [2] and n['streets_louder_3db'] == 1 and n['near_m'] == 800.0
