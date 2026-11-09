"""Street plans run headless in SUMO against a paired baseline.

Phase 1 of docs/simulator-learning-layer.md: plan in, paired runs over seeds,
results out, with no viewer. A plan closes roads to cars and adds lanes from the
start of the run. Each seed is one `traffic_sumo.Simulation`, whose baseline
shares the scenario's trips, destinations and entry schedule: it is forked at
the first closure, or started on the original roads when lanes are added. After
a warm-up both runs are sampled on the same clock, every road's cars and speed
and the network totals, so a plan's effect is the difference between the two.

The runs are as uncalibrated as the simulation itself (docs/traffic-sumo.md):
results compare a plan with the same synthetic demand without it, and say
nothing about measured Beirut traffic.
"""

from __future__ import annotations

import warnings as warnings_
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .traffic import RoadDisruption, TrafficModel, road_events

# A baseline whose entry queue grows faster than this over the measured window
# has not settled: the plan's effect is then mixed with the drift.
UNSETTLED_QUEUE_GROWTH_PER_MIN = 50


@dataclass(frozen=True)
class StreetPlan:
    plan_id: str
    # Roads closed to cars for the whole run.
    closed: tuple[int, ...] = ()
    # Road -> lanes added in each direction.
    lanes: dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any], model: TrafficModel) -> StreetPlan:
        """`close_roads` (indices), `close_streets` (OSM names, every piece) and `add_lanes` ({road: lanes})."""
        unknown = set(payload) - {'plan_id', 'close_roads', 'close_streets', 'add_lanes'}
        if unknown:
            raise ValueError(f'unknown plan fields: {sorted(unknown)}')
        closed = {int(r) for r in payload.get('close_roads', [])}
        wanted = {str(n).strip().lower() for n in payload.get('close_streets', [])}
        found = set()
        for road, name in enumerate(model.name):
            if name and name.lower() in wanted:
                closed.add(road)
                found.add(name.lower())
        if wanted - found:
            raise ValueError(f'no road named {sorted(wanted - found)} in this traffic model')
        lanes = {int(road): int(added) for road, added in payload.get('add_lanes', {}).items()}
        plan = cls(plan_id=str(payload.get('plan_id', 'plan')), closed=tuple(sorted(closed)), lanes=lanes)
        plan.disruption(model)
        return plan

    def disruption(self, model: TrafficModel) -> RoadDisruption:
        return road_events(model, closed=self.closed)

    @property
    def empty(self) -> bool:
        return not self.closed and not self.lanes


@dataclass(frozen=True)
class RunSettings:
    hour: int = 8
    seeds: tuple[int, ...] = (42,)
    warmup_s: int = 600
    measure_s: int = 1800
    # Roads are sampled every this many simulated seconds; each sample is one
    # SUMO request, so shorter samples cost more round trips.
    sample_s: int = 10

    def __post_init__(self):
        if not self.seeds:
            raise ValueError('give at least one seed')
        if self.sample_s < 1 or self.measure_s < 2 * self.sample_s or self.warmup_s < 0:
            raise ValueError('measure at least two samples, after a non-negative warm-up')


class _RoadSampler:
    """Cars and speed on every road of one SUMO run, read through edge subscriptions."""

    def __init__(self, connection, traci, roads: int):
        self.c, self.constants = connection, traci.constants
        self.edges = [e for e in connection.edge.getIDList() if not e.startswith(':')]
        self.road = np.array([int(e.split(':')[0]) for e in self.edges])
        for edge in self.edges:
            connection.edge.subscribe(edge, (self.constants.LAST_STEP_VEHICLE_NUMBER,
                                             self.constants.LAST_STEP_MEAN_SPEED))
        self.vehicles = np.zeros(roads)
        self.speed_sum = np.zeros(roads)

    def sample(self, seconds: float) -> None:
        results = self.c.edge.getAllSubscriptionResults()
        n = np.array([results.get(e, {}).get(self.constants.LAST_STEP_VEHICLE_NUMBER, 0) for e in self.edges], float)
        speed = np.array([results.get(e, {}).get(self.constants.LAST_STEP_MEAN_SPEED, 0.0) for e in self.edges])
        # An empty edge reports its speed limit; weighting by cars ignores it.
        np.add.at(self.vehicles, self.road, n * seconds)
        np.add.at(self.speed_sum, self.road, n * seconds * speed * 3.6)


def _starting_fleet(model: TrafficModel, hour: int, seed: int, closed: tuple[int, ...]):
    """The simulation's own initial fleet, less the cars that would start on a closed road.

    A plan closes its roads for good, so no car is on them when the run
    starts. Left in, those cars would stand on the closed road for the whole
    run (`Simulation.apply` holds them) and count against the plan. They are
    left out of both runs, which keeps the pair identical.
    """
    import tempfile
    from pathlib import Path

    import sumolib

    from .traffic_sumo import EdgeRouter, demand, export_network, runtime

    _, _, converter = runtime()
    with tempfile.TemporaryDirectory(prefix='dynacity-plan-') as directory:
        net = sumolib.net.readNet(str(export_network(model, Path(directory), converter)))
    trips = demand(model, net, hour, seed, EdgeRouter(net))
    shut = {str(road) for road in closed}
    kept = [t for t in trips if t.route[0].split(':')[0] not in shut]
    return kept, len(trips) - len(kept)


def run_seed(model: TrafficModel, plan: StreetPlan, settings: RunSettings, seed: int) -> dict[str, Any]:
    """One paired run: the scenario with the plan and its baseline without, sampled after the warm-up."""
    from .traffic_sumo import Simulation

    trips, dropped = _starting_fleet(model, settings.hour, seed, plan.closed) if plan.closed else (None, 0)
    sim = Simulation(model, hour=settings.hour, seed=seed, continuous=True, lanes=plan.lanes, trips=trips)
    disruption = plan.disruption(model)
    totals = {'scenario': {}, 'baseline': {}}
    samplers: list[_RoadSampler] = []
    try:
        elapsed, first, first_queue = 0, None, 0
        while elapsed < settings.warmup_s + settings.measure_s:
            step = min(settings.sample_s, settings.warmup_s + settings.measure_s - elapsed)
            metrics = sim.advance(disruption, seconds=step, include_initial=False, limit=0)['frames'][-1]['metrics']
            elapsed += step
            if elapsed <= settings.warmup_s:
                continue
            if not samplers:
                runs = sim.connections if sim.forked else sim.connections[:1]
                samplers = [_RoadSampler(c, sim.traci, len(model)) for c in runs]
                first, first_queue = metrics, len(sim.entry_queue)
                continue
            baseline = metrics.get('baseline') or metrics
            for run, m in (('scenario', metrics), ('baseline', baseline)):
                t = totals[run]
                t['vehicle_hours'] = t.get('vehicle_hours', 0.0) + m['active'] * step / 3600
                t['stopped_vehicle_hours'] = t.get('stopped_vehicle_hours', 0.0) + m['stopped'] * step / 3600
            for sampler in samplers:
                sampler.sample(step)
        last = metrics
        # The window runs from the first sample after the warm-up to the end.
        window_min = max(settings.measure_s - settings.sample_s, settings.sample_s) / 60
        out = {'seed': seed, 'starting_cars_on_closed_roads': dropped}
        # New trips whose origin the plan closes wait in the explicit entry
        # queue for good (scenario only); they are counted apart from the cars
        # waiting at an open origin.
        queued = {'scenario': (first_queue, len(sim.entry_queue)), 'baseline': (0, 0)}
        for run, end, start in (('scenario', last, first),
                                ('baseline', last.get('baseline') or last, first.get('baseline') or first)):
            sampler = samplers[0] if run == 'scenario' or len(samplers) == 1 else samplers[1]
            (q0, q1) = queued[run]
            out[run] = {
                'arrived': end['arrived'] - start['arrived'],
                'origin_closed_end': q1,
                'waiting_to_enter_end': end['pending'] - q1,
                'waiting_to_enter_growth_per_min': (end['pending'] - q1 - start['pending'] + q0) / window_min,
                'vehicle_hours': totals[run].get('vehicle_hours', 0.0),
                'stopped_vehicle_hours': totals[run].get('stopped_vehicle_hours', 0.0),
                'unstuck': end['unstuck'],
                'road_vehicle_seconds': sampler.vehicles,
                'road_speed_kmh': np.divide(sampler.speed_sum, sampler.vehicles, out=np.full(len(model), np.nan),
                                            where=sampler.vehicles > 0),
            }
        out['paired'] = {key: last.get(key) for key in ('mean_completed_delay_s', 'paired_arrivals', 'overdue',
                                                         'no_detour', 'rerouted', 'stopped_on_closed')}
        return out
    finally:
        sim.close()


def _band(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, float)
    return {'low': float(np.nanmin(a)), 'median': float(np.nanmedian(a)), 'high': float(np.nanmax(a))}


def run_plan(model: TrafficModel, plan: StreetPlan, settings: RunSettings = RunSettings()) -> dict[str, Any]:
    """The plan over every seed: per-seed results, low/median/high across seeds, and per-road changes."""
    from .traffic_sumo import road_identity

    seeds = [run_seed(model, plan, settings, seed) for seed in settings.seeds]
    summary, warnings = {}, []
    for key in ('arrived', 'vehicle_hours', 'stopped_vehicle_hours', 'waiting_to_enter_end',
                'waiting_to_enter_growth_per_min', 'origin_closed_end'):
        summary[key] = {run: _band([s[run][key] for s in seeds]) for run in ('scenario', 'baseline')}
        summary[key]['change'] = _band([s['scenario'][key] - s['baseline'][key] for s in seeds])
    growth = summary['waiting_to_enter_growth_per_min']['baseline']['median']
    if growth > UNSETTLED_QUEUE_GROWTH_PER_MIN:
        warnings.append(f'The baseline has not settled: cars waiting to enter grow by about {growth:.0f} a minute, '
                        'so the change mixes the plan\'s effect with that drift.')
    if plan.empty:
        warnings.append('An empty plan: the scenario is its own baseline, so every change is zero.')
    vehicles = {run: np.mean([s[run]['road_vehicle_seconds'] for s in seeds], axis=0) for run in ('scenario', 'baseline')}
    with warnings_.catch_warnings():
        # Roads no car used in any seed stay NaN.
        warnings_.simplefilter('ignore', RuntimeWarning)
        speed = {run: np.nanmean([s[run]['road_speed_kmh'] for s in seeds], axis=0) for run in ('scenario', 'baseline')}
    sampled = np.flatnonzero((vehicles['scenario'] > 0) | (vehicles['baseline'] > 0))
    roads = [{'road': int(i), 'name': model.name[i] if model.name else '',
              'vehicle_seconds': [round(float(vehicles['scenario'][i]), 1), round(float(vehicles['baseline'][i]), 1)],
              'speed_kmh': [None if np.isnan(speed[r][i]) else round(float(speed[r][i]), 1)
                            for r in ('scenario', 'baseline')]}
             for i in sampled]
    return {
        'plan': {'plan_id': plan.plan_id, 'closed_roads': list(plan.closed),
                 'added_lanes': {str(k): v for k, v in plan.lanes.items()}},
        'settings': {'hour': settings.hour, 'seeds': list(settings.seeds), 'warmup_s': settings.warmup_s,
                     'measure_s': settings.measure_s, 'sample_s': settings.sample_s},
        'network': {'roads': len(model), 'road_identity_sha256': road_identity(model)},
        'summary': summary,
        'paired': [s['paired'] | {'seed': s['seed'], 'starting_cars_on_closed_roads': s['starting_cars_on_closed_roads']}
                   for s in seeds],
        'roads': roads,
        'road_columns': {'vehicle_seconds': ['scenario', 'baseline'], 'speed_kmh': ['scenario', 'baseline']},
        'warnings': warnings,
        'assumptions': 'Synthetic, uncalibrated demand (docs/traffic-sumo.md); the plan is compared with the '
                       'same demand without it. Not a measurement of Beirut traffic.',
    }
