"""Exhaust emissions of the paired SUMO runs: what a street plan saves, or costs.

Each SUMO vehicle carries an emission class (SUMO's built-in HBEFA-based
model) and reports its instantaneous CO2, fuel, NOx and particulate rates
from its current speed and acceleration. `EmissionLedger` turns those rates
into totals for each run: the scenario (with the plan) and the comparison
(roads as they are today, same fleet and same incoming trips). The totals start
when the two runs split (`Simulation.fork`), so both cover the same simulated
time and the difference is what the plan changed.

The rates are read whenever the whole fleet is read: every simulation step
without a view, or once per request with a view (the planner page asks for 4 s
at a time). Each rate is held over the time since the last reading (right
Riemann sum). Both runs are read at the same moments, so the sampling affects
both totals alike.

The ledger also keeps CO2 per road, so the page can show how much exhaust
leaves the planned streets and how much lands on the streets drivers detour
onto.

Noise: SUMO gives each vehicle a road-traffic noise level in dB(A) (its
Harmonoise model, from speed and acceleration). Levels are added as sound
energy, 10^(L/10), per road and over time, so each road gets an equivalent
continuous level (Leq) over the compared window, the usual way road noise is
reported. A city background level (`BACKGROUND_DB`) is added to every road so
a street with no cars reads as background, not silence. `noise_reading` turns
a change in dB into words: 3 dB is about the smallest change most people notice,
and 10 dB less sounds about half as loud.

Everything here is a modelled estimate: see `EMISSION_ASSUMPTIONS`, which the
API sends with every result and the planner page lists in full.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np

# Petrol density used to turn fuel mass into litres (EN 228 petrol is
# 720–775 kg/m3 at 15 °C; this is mid-range). Diesel vehicles in the mix
# make litres slightly off; the mass figure is SUMO's own.
PETROL_KG_PER_L = 0.745

# A plan's saving is reported only after the comparison has run this long;
# before that it is dominated by which cars happened to move first.
MIN_WINDOW_S = 60.0

# City background sound with no motor traffic on the street itself, dB(A):
# distant traffic, people, shops, air conditioners. An assumption, not a
# Beirut measurement; quiet urban areas are often quoted at 45-55 dB(A).
BACKGROUND_DB = 50.0

# How many roads with the largest noise increase and decrease outside the plan to report.
NOISE_TOP = 12

# Other streets are compared only near the plan, and only where the louder run
# carries real traffic: further away, or on near-empty streets, the two runs
# differ by chance (one car more or less swings a quiet street by 10 dB or more).
NEAR_M = 800.0
NOISE_FLOOR_DB = 55.0


def leq_db(energy_s: float, window_s: float, background_db: float = BACKGROUND_DB) -> float | None:
    """Equivalent continuous level of summed energy 10^(L/10)·s over a window, plus background."""
    if window_s <= 0:
        return None
    return 10 * np.log10(energy_s / window_s + 10 ** (background_db / 10))


def noise_reading(delta_db: float | None) -> dict | None:
    """Plain words and perceived loudness for a change in level (scenario minus today).

    Perceived loudness doubles with every +10 dB (the usual rule of thumb), so the
    ratio is 2^(delta/10). Thresholds follow common guidance: under 1 dB no one
    hears it, about 3 dB is just noticeable, 5 dB clearly noticeable.
    """
    if delta_db is None:
        return None
    a = abs(delta_db)
    louder = delta_db > 0
    if a < 1:
        words = 'No audible change'
    elif a < 3:
        words = 'Barely noticeable: ' + ('slightly louder' if louder else 'slightly quieter')
    elif a < 5:
        words = 'Noticeable: ' + ('louder' if louder else 'quieter')
    elif a < 10:
        words = 'Clearly ' + ('louder' if louder else 'quieter')
    else:
        words = 'Much louder' if louder else 'Much quieter'
    return {'delta_db': round(delta_db, 1), 'loudness_ratio': round(float(2 ** (delta_db / 10)), 2),
            'words': words}

EMISSION_ASSUMPTIONS = (
    "Exhaust only (tank-to-wheel). Fuel production, electricity generation, tyre and brake wear, "
    "road dust and building the street change are not counted.",
    "Emission rates are SUMO's built-in HBEFA-based model for each simulated vehicle's emission class "
    "(listed below), driven by its simulated speed and acceleration. They are European vehicle "
    "averages, not measurements of Beirut's fleet, which is older on average and so probably emits more per km.",
    "Every simulated car uses the same class unless the vehicle mix is switched on; then buses, "
    "motorcycles and vans take SUMO's default class for their type. No electric or hybrid vehicles are modelled.",
    "Both runs carry the same trips. Closing a street never makes a trip disappear or switch to "
    "walking, cycling or transit. Cities that closed streets have often seen some traffic vanish "
    "('traffic evaporation'), so real savings could be larger; induced trips elsewhere could also make them smaller.",
    "Cars caught on a street at the moment it closes stay parked there in the simulation until it "
    "reopens; their engines are treated as off, so they add no exhaust to the pedestrian street.",
    "A car that cannot start, or waits because a closure cuts off its destination, emits little "
    "or nothing while it waits. That shows up as a saving it is not: the page warns when the plan leaves "
    "more cars stranded than today.",
    "Totals cover only the simulated minutes since the comparison began. The per-hour figure "
    "assumes that pace continues; it is not a yearly or all-day estimate. Effects take a few minutes to spread.",
    "Rates are sampled every few seconds and held in between; both runs are sampled at the same moments.",
    "Fuel litres use a petrol density of 0.745 kg/L. Air-quality concentrations and health effects "
    "are not computed: CO2 on the changed streets shows where exhaust moves, not what people breathe.",
    "Noise is SUMO's Harmonoise level for each car (from its speed and acceleration), added up as sound energy per "
    "street over the compared minutes (an equivalent level, Leq, in dB(A)). It is a level near the road, not at a "
    "window or inside a home: no distance, buildings, reflections or road surface are modelled.",
    "Every street also gets a city background of 50 dB(A) (distant traffic, people, shops), so a street with no cars "
    "reads 50 dB(A), not silence. Voices, cafés, deliveries and events on a pedestrian street are not modelled.",
    "Noise on other streets is compared only within 800 m of the changed streets and only where one of the runs "
    "reaches 55 dB(A). Further away, or on near-empty streets, the two runs differ by chance, not because of the plan.",
    "Reading noise changes: under 1 dB nobody hears a difference, about 3 dB is just noticeable, 5 dB is clearly "
    "noticeable, and 10 dB less sounds about half as loud (+10 dB about twice as loud). Motorcycles and horns, "
    "often the loudest sources in Beirut, are only included when the vehicle mix is on, and horns never.",
    "Traffic itself carries the assumptions of the SUMO comparison (synthetic destinations, uncalibrated "
    "demand, SUMO driver behaviour). The direction of a change is more reliable than its size.",
)

# Pollutants read from SUMO, with TraCI's unit for each rate (mg/s in SUMO ≥ 1.14).
POLLUTANTS = ('co2', 'fuel', 'nox', 'pmx')


class EmissionLedger:
    """Running exhaust totals for the scenario run (index 0) and the comparison run (index 1)."""

    def __init__(self, roads: int):
        self.roads = roads
        self.start_s: float | None = None
        self.last_s = [None, None]
        self.totals = [dict.fromkeys(POLLUTANTS, 0.0) | {'km': 0.0, 'vehicle_s': 0.0} for _ in range(2)]
        self.road_co2_g = [np.zeros(roads), np.zeros(roads)]
        # Noise energy per road, sum of 10^(L/10)·dt over vehicles and time.
        self.road_noise = [np.zeros(roads), np.zeros(roads)]

    def start(self, now_s: float) -> None:
        """Open the comparison window: both runs are identical at this moment."""
        self.start_s = now_s
        self.last_s = [now_s, now_s]

    @property
    def started(self) -> bool:
        return self.start_s is not None

    def record(self, index: int, now_s: float, samples: Iterable[tuple]) -> None:
        """Add one fleet reading of run `index`.

        `samples` holds (speed m/s, CO2 mg/s, fuel mg/s, NOx mg/s, PMx mg/s, road id
        [, noise dB(A)]) per vehicle. Each rate counts for the time since this run's
        last reading.
        """
        if self.start_s is None or self.last_s[index] is None:
            return
        dt = now_s - self.last_s[index]
        if dt <= 0:
            return
        self.last_s[index] = now_s
        total, per_road, noise = self.totals[index], self.road_co2_g[index], self.road_noise[index]
        for speed, co2, fuel, nox, pmx, road, *rest in samples:
            total['co2'] += co2 * dt / 1000
            total['fuel'] += fuel * dt / 1000
            total['nox'] += nox * dt / 1000
            total['pmx'] += pmx * dt / 1000
            total['km'] += max(speed, 0.0) * dt / 1000
            total['vehicle_s'] += dt
            if road and not road.startswith(':'):
                i = int(road.split(':')[0])
                if 0 <= i < self.roads:
                    per_road[i] += co2 * dt / 1000
                    if rest and rest[0] and rest[0] > 0:
                        noise[i] += 10 ** (rest[0] / 10) * dt

    def summary(self, now_s: float, planned: Iterable[int], *, stranded: Mapping[str, int] | None = None,
                emission_classes: Mapping[str, str] | None = None, nearby: Iterable[int] | None = None) -> dict | None:
        """Scenario against comparison, in grams; None before the runs split.

        `planned` are the roads the plan touches (closed, narrowed or slowed).
        `stranded` gives cars not driving because of the plan, per run
        (`scenario`, `baseline`), to flag savings that are really cars stuck.
        `nearby` limits the other-street noise comparison to these roads
        (within `NEAR_M` of the plan); None compares every road.
        """
        if self.start_s is None:
            return None
        window = max(0.0, now_s - self.start_s)
        planned = sorted({int(r) for r in planned if 0 <= int(r) < self.roads})
        scenario, baseline = self.totals

        def grams(total):
            return {'co2_g': round(total['co2'], 1), 'fuel_g': round(total['fuel'], 1),
                    'fuel_l': round(total['fuel'] / 1000 / PETROL_KG_PER_L, 3),
                    'nox_g': round(total['nox'], 3), 'pmx_g': round(total['pmx'], 4),
                    'vehicle_km': round(total['km'], 2), 'vehicle_h': round(total['vehicle_s'] / 3600, 2)}

        s, b = grams(scenario), grams(baseline)
        saved = {k: round(b[k] - s[k], 4) for k in s}
        percent = {k: (round(100 * saved[k] / b[k], 1) if b[k] > 0 else None) for k in s}
        per_hour = ({k: round(saved[k] * 3600 / window, 3) for k in ('co2_g', 'fuel_l', 'nox_g', 'pmx_g', 'vehicle_km')}
                    if window > 0 else None)
        on_planned = [round(float(run[planned].sum()), 1) if planned else 0.0 for run in self.road_co2_g]
        elsewhere = [round(t['co2'] - p, 1) for t, p in zip(self.totals, on_planned)]
        noise = self.noise_summary(window, planned, None if nearby is None else sorted({int(r) for r in nearby}))
        stranded = dict(stranded or {})
        extra_stranded = max(0, stranded.get('scenario', 0) - stranded.get('baseline', 0))
        return {
            'window_s': round(window, 1),
            'settled': window >= MIN_WINDOW_S,
            'scenario': s,
            'baseline': b,
            'saved': saved,
            'saved_percent': percent,
            'saved_per_hour': per_hour,
            'co2_per_km': {'scenario': round(scenario['co2'] / scenario['km'], 1) if scenario['km'] else None,
                           'baseline': round(baseline['co2'] / baseline['km'], 1) if baseline['km'] else None},
            'planned_streets': {'roads': len(planned),
                                'co2_g': {'scenario': on_planned[0], 'baseline': on_planned[1]},
                                'elsewhere_co2_g': {'scenario': elsewhere[0], 'baseline': elsewhere[1]}},
            'stranded': {'scenario': stranded.get('scenario', 0), 'baseline': stranded.get('baseline', 0),
                         'extra': extra_stranded},
            'noise': noise,
            'emission_classes': dict(emission_classes or {}),
            'method': 'SUMO HBEFA-based instantaneous rates, integrated over the paired runs since they split',
        }

    def noise_summary(self, window: float, planned: list[int], nearby: list[int] | None = None) -> dict | None:
        """Traffic noise (Leq, dB(A)) on the planned streets, and the streets that change most elsewhere."""
        if window <= 0:
            return None
        scenario, baseline = self.road_noise
        out = {'background_db': BACKGROUND_DB, 'planned': None, 'louder': [], 'quieter': [],
               'near_m': NEAR_M if nearby is not None else None, 'floor_db': NOISE_FLOOR_DB}
        if planned:
            # Energy-averaged over the planned pieces: the level of a typical spot along them.
            b = leq_db(float(baseline[planned].mean()), window)
            s = leq_db(float(scenario[planned].mean()), window)
            car_free = bool((scenario[planned] == 0).all())
            out['planned'] = {'baseline_db': round(b, 1), 'scenario_db': round(s, 1),
                              'car_free': car_free, **noise_reading(s - b)}
        heard = np.flatnonzero((scenario > 0) | (baseline > 0))
        others = np.setdiff1d(heard, planned)
        if nearby is not None:
            others = np.intersect1d(others, np.asarray(nearby, dtype=int))
        if len(others):
            base = 10 * np.log10(baseline[others] / window + 10 ** (BACKGROUND_DB / 10))
            scen = 10 * np.log10(scenario[others] / window + 10 ** (BACKGROUND_DB / 10))
            busy = np.maximum(base, scen) >= NOISE_FLOOR_DB
            others, base, scen = others[busy], base[busy], scen[busy]
        if len(others):
            delta = scen - base
            order = np.argsort(delta)
            pick = lambda idx: [{'road': int(others[i]), 'baseline_db': round(float(base[i]), 1),
                                 'scenario_db': round(float(scen[i]), 1), **noise_reading(float(delta[i]))}
                                for i in idx]
            out['louder'] = pick([i for i in order[::-1][:NOISE_TOP] if delta[i] >= 1])
            out['quieter'] = pick([i for i in order[:NOISE_TOP] if delta[i] <= -1])
            out['streets_louder_3db'] = int((delta >= 3).sum())
            out['streets_quieter_3db'] = int((delta <= -3).sum())
        return out
