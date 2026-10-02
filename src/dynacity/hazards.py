"""Hazard shocks: tornadoes, floods, earthquakes, blasts and the rest of God's Plan's disasters.

A `HazardEvent` strikes the city at the start of one 2-year rollout step. For
each building it yields two probabilities — destroyed, or damaged but standing
— and the engine forces those outcomes before the transition model runs for
the buildings the hazard missed. From the next step on, recovery is whatever
the fitted transition model does with demolished and vacant buildings.

    local intensity  I = strength(magnitude) × exposure(distance) × vulnerability(building)
    P(destroyed)     = destroy_ceiling × I²
    P(damaged)       = damage_ceiling × I − P(destroyed)

Squaring I concentrates destruction near the core while damage reaches the
edge of the footprint. Exposure is 1 inside `core_fraction × radius`, eases to
0 at the radius (smoothstep), and is 1 everywhere for a citywide hazard.

A flood follows terrain when buildings carry `ground_elevation_m` (see
`terrain.py`): the water fills to `magnitude` metres above the low ground in
reach (the 5th percentile of the reached buildings' ground), so each building's
own depth is the water level minus its ground, and buildings above the water
stay dry. `strength` is then read at that local depth. Without elevations the
depth is uniform inside the footprint, as before.

Outcomes map onto the existing states rather than inventing new ones, so the
trained model, the KPIs and every stored artifact stay valid:

- destroyed                         → demolished
- damaged active construction       → stalled_or_cancelled
- damaged stalled site              → stays stalled_or_cancelled
- damaged building (built/renovated/vacant) → vacant_or_evicted

Empty lots, demolished and unknown buildings have nothing to damage.

Every number in `PROFILES` is an illustrative default, not a calibrated
fragility curve: BBED records no hazard losses to fit them to (the 2020 port
blast predates the panel's post-blast modality and is not labelled as damage).
A result carrying hazards is therefore stamped `assumption_based_scenario`,
like any lever. Change the numbers here, in review.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .contracts import HazardEvent, HazardKind, UrbanObjectState
from .status import CanonicalState

STOREY_HEIGHT_M = 3.2

AFFECTABLE_STATES = frozenset({
    CanonicalState.STABLE_BUILT.value,
    CanonicalState.RENOVATED.value,
    CanonicalState.VACANT_OR_EVICTED.value,
    CanonicalState.ACTIVE_CONSTRUCTION.value,
    CanonicalState.STALLED_OR_CANCELLED.value,
})

SEVERITIES = ("minor", "moderate", "severe", "extreme")


def damaged_state(state: str) -> str:
    """Where a damaged-but-standing building ends the step."""

    if state in (CanonicalState.ACTIVE_CONSTRUCTION.value, CanonicalState.STALLED_OR_CANCELLED.value):
        return CanonicalState.STALLED_OR_CANCELLED.value
    return CanonicalState.VACANT_OR_EVICTED.value


def _floors(item: UrbanObjectState) -> float | None:
    if item.floors:
        return item.floors
    if item.height_m:
        return item.height_m / STOREY_HEIGHT_M
    return None


def _by_floors(low: float, mid: float, high: float, *, low_max: float = 3, mid_max: float = 8):
    """Vulnerability banded by storeys; buildings of unknown height get the middle band."""

    def vulnerability(item: UrbanObjectState) -> float:
        floors = _floors(item)
        if floors is None:
            return mid
        return low if floors <= low_max else mid if floors <= mid_max else high

    return vulnerability


def _uniform(item: UrbanObjectState) -> float:
    return 1.0


def _commercial_frontage(item: UrbanObjectState) -> float:
    use = (item.building_use or "").lower()
    return 1.0 if any(word in use for word in ("commerc", "retail", "shop", "mixed")) else 0.5


def _ramp(lo: float, hi: float) -> Callable[[float], float]:
    return lambda m: float(np.clip((m - lo) / (hi - lo), 0.0, 1.0))


@dataclass(frozen=True)
class HazardProfile:
    unit: str
    magnitude_range: tuple[float, float]
    # Magnitude for each qualitative severity; the scenario compiler maps a
    # planner's wording onto these, and "moderate" is the default.
    severity_magnitudes: dict[str, float]
    # Which location forms the hazard accepts: "point", "path", "citywide".
    footprints: frozenset[str]
    radius_m: Callable[[float], float]
    strength: Callable[[float], float]
    destroy_ceiling: float
    damage_ceiling: float
    vulnerability: Callable[[UrbanObjectState], float]
    caveat: str
    core_fraction: float = 0.25

    @property
    def default_magnitude(self) -> float:
        return self.severity_magnitudes["moderate"]


PROFILES: dict[HazardKind, HazardProfile] = {
    HazardKind.TORNADO: HazardProfile(
        unit="EF scale",
        magnitude_range=(0, 5),
        severity_magnitudes={"minor": 1, "moderate": 2, "severe": 3, "extreme": 5},
        footprints=frozenset({"path", "point"}),
        # Half-width of the damage track: ~180 m either side at EF3.
        radius_m=lambda ef: 40 + 45 * ef,
        strength=lambda ef: (ef + 1) / 6,
        destroy_ceiling=0.6,
        damage_ceiling=0.9,
        # Light low-rise fails first; reinforced towers mostly lose facades.
        vulnerability=_by_floors(1.0, 0.75, 0.5),
        caveat="Tornado damage follows distance from the track only; debris and building frame are not modelled.",
    ),
    HazardKind.STORM: HazardProfile(
        unit="peak gust km/h",
        magnitude_range=(60, 250),
        severity_magnitudes={"minor": 80, "moderate": 110, "severe": 150, "extreme": 200},
        footprints=frozenset({"point", "citywide"}),
        radius_m=lambda gust: 3000,
        strength=_ramp(60, 250),
        destroy_ceiling=0.05,
        damage_ceiling=0.35,
        # Wind load grows with height.
        vulnerability=_by_floors(0.5, 0.7, 1.0),
        caveat="Storm damage ignores exposure, roof type and lightning strike location.",
    ),
    HazardKind.RAIN: HazardProfile(
        unit="rainfall mm in 24 h",
        magnitude_range=(20, 500),
        severity_magnitudes={"minor": 40, "moderate": 80, "severe": 150, "extreme": 300},
        footprints=frozenset({"point", "citywide"}),
        radius_m=lambda mm: 3000,
        strength=_ramp(20, 320),
        destroy_ceiling=0.0,
        damage_ceiling=0.2,
        vulnerability=_by_floors(1.0, 0.4, 0.3),
        caveat="Rain has no structural pathway here; it only halts sites and empties low-rise buildings.",
    ),
    HazardKind.FLOOD: HazardProfile(
        unit="water depth m",
        magnitude_range=(0.1, 6),
        severity_magnitudes={"minor": 0.3, "moderate": 1.0, "severe": 2.0, "extreme": 4.0},
        footprints=frozenset({"point", "citywide"}),
        radius_m=lambda depth: 600,
        strength=_ramp(0, 3),
        destroy_ceiling=0.2,
        damage_ceiling=0.9,
        # A flooded ground floor matters less the more floors stay dry above it.
        vulnerability=_by_floors(1.0, 0.6, 0.35, low_max=2, mid_max=6),
        caveat="Flood depth follows approximate ground level (Copernicus 30 m surface model with roofs filtered out) "
        "when buildings carry it, and is uniform otherwise; flow, drainage, defences and sea level are not modelled.",
    ),
    HazardKind.FIRE: HazardProfile(
        unit="fire intensity class 1-5",
        magnitude_range=(1, 5),
        severity_magnitudes={"minor": 1, "moderate": 2, "severe": 4, "extreme": 5},
        footprints=frozenset({"point"}),
        radius_m=lambda cls: 30 + 50 * cls,
        strength=lambda cls: cls / 5,
        destroy_ceiling=0.5,
        damage_ceiling=0.85,
        vulnerability=_uniform,
        caveat="Fire burns a fixed circle; spread by wind, materials and firefighting is not modelled.",
    ),
    HazardKind.HEAT: HazardProfile(
        unit="peak temperature °C",
        magnitude_range=(35, 55),
        severity_magnitudes={"minor": 38, "moderate": 42, "severe": 47, "extreme": 52},
        footprints=frozenset({"point", "citywide"}),
        radius_m=lambda t: 3000,
        strength=_ramp(35, 55),
        destroy_ceiling=0.0,
        damage_ceiling=0.12,
        vulnerability=_uniform,
        caveat="Heat has no structural pathway here; it only halts sites and empties buildings.",
    ),
    HazardKind.EARTHQUAKE: HazardProfile(
        unit="moment magnitude Mw",
        magnitude_range=(4, 9),
        severity_magnitudes={"minor": 5, "moderate": 6, "severe": 7, "extreme": 8},
        footprints=frozenset({"point", "citywide"}),
        # Felt-damage radius: ~10 km at Mw 5, ~100 km at Mw 7.
        radius_m=lambda mw: 1000 * 10 ** (0.5 * mw - 1.5),
        strength=_ramp(4.5, 8),
        destroy_ceiling=0.35,
        damage_ceiling=0.8,
        # Mid-rise frames resonate with typical shaking periods.
        vulnerability=_by_floors(0.7, 1.0, 0.8, mid_max=10),
        caveat="Shaking falls off with distance only; soil, construction age and code compliance are not modelled.",
    ),
    HazardKind.EXPLOSION: HazardProfile(
        unit="TNT-equivalent tonnes",
        magnitude_range=(0.001, 5000),
        severity_magnitudes={"minor": 0.01, "moderate": 1, "severe": 100, "extreme": 1000},
        footprints=frozenset({"point"}),
        # Hopkinson-Cranz scaling: damage distance grows with the cube root of
        # the charge. 1,000 t (order of the 2020 port blast) reaches ~2 km.
        radius_m=lambda tonnes: 20 * (tonnes * 1000) ** (1 / 3),
        strength=lambda tonnes: 1.0,
        destroy_ceiling=0.55,
        damage_ceiling=0.95,
        vulnerability=_uniform,
        caveat="Blast damage uses cube-root distance scaling only; shielding by other buildings is ignored.",
        core_fraction=0.15,
    ),
    HazardKind.PLANE_CRASH: HazardProfile(
        unit="aircraft mass t",
        magnitude_range=(1, 600),
        severity_magnitudes={"minor": 5, "moderate": 80, "severe": 250, "extreme": 575},
        footprints=frozenset({"point"}),
        radius_m=lambda mass: 25 * math.sqrt(mass),
        strength=lambda mass: 1.0,
        destroy_ceiling=0.7,
        damage_ceiling=0.9,
        vulnerability=_uniform,
        caveat="Plane crash is a circular impact-and-fuel-fire zone; approach path and debris field are ignored.",
    ),
    HazardKind.ORBITAL_STRIKE: HazardProfile(
        unit="beam radius m",
        magnitude_range=(5, 1000),
        severity_magnitudes={"minor": 15, "moderate": 50, "severe": 150, "extreme": 500},
        footprints=frozenset({"point"}),
        radius_m=lambda beam: 1.25 * beam,
        strength=lambda beam: 1.0,
        destroy_ceiling=0.98,
        damage_ceiling=1.0,
        vulnerability=_uniform,
        caveat="Orbital strike is fictional (from God's Plan): a stress test of recovery, not a real threat model.",
        core_fraction=0.8,
    ),
    HazardKind.RIOT: HazardProfile(
        unit="unrest intensity class 1-5",
        magnitude_range=(1, 5),
        severity_magnitudes={"minor": 1, "moderate": 2, "severe": 4, "extreme": 5},
        footprints=frozenset({"point"}),
        radius_m=lambda cls: 150 + 100 * cls,
        strength=lambda cls: cls / 5,
        destroy_ceiling=0.03,
        damage_ceiling=0.3,
        # Street-front commercial takes the damage.
        vulnerability=_commercial_frontage,
        caveat="Riot damage is by distance and building use only; the model has no streets or crowd movement.",
    ),
}


# Low-ground reference for a terrain-following flood: this percentile of the
# ground under the buildings the footprint reaches (a percentile, not the
# minimum, so one mis-sampled building cannot sink the whole water level).
FLOOD_LOW_GROUND_PERCENTILE = 5


def magnitude_of(hazard: HazardEvent) -> float:
    return hazard.magnitude if hazard.magnitude is not None else PROFILES[hazard.kind].default_magnitude


def radius_of(hazard: HazardEvent) -> float | None:
    """Footprint radius in metres (half-width for a track); None when citywide."""

    if hazard.citywide:
        return None
    if hazard.radius_m is not None:
        return hazard.radius_m
    return float(PROFILES[hazard.kind].radius_m(magnitude_of(hazard)))


def _distance_to_path(xy: np.ndarray, path: Sequence[tuple[float, float]]) -> np.ndarray:
    points = np.asarray(path, dtype=np.float64)
    best = np.full(len(xy), np.inf)
    for a, b in zip(points[:-1], points[1:], strict=True):
        segment = b - a
        length2 = float(segment @ segment)
        t = np.zeros(len(xy)) if length2 == 0 else np.clip(((xy - a) @ segment) / length2, 0.0, 1.0)
        nearest = a + t[:, None] * segment
        best = np.minimum(best, np.linalg.norm(xy - nearest, axis=1))
    return best


def _exposure(distance: np.ndarray, radius: float, core_fraction: float) -> np.ndarray:
    core = core_fraction * radius
    t = np.clip((distance - core) / max(radius - core, 1e-9), 0.0, 1.0)
    return 1.0 - (3 * t**2 - 2 * t**3)


@dataclass(frozen=True)
class HazardShock:
    """Per-building outcome probabilities for one hazard, aligned with the objects."""

    hazard: HazardEvent
    destroy: np.ndarray
    damage: np.ndarray
    # Buildings the footprint reaches at all (intensity > 0).
    exposed: np.ndarray
    unlocated: int


def _footprint(hazard: HazardEvent, objects: Sequence[UrbanObjectState]) -> tuple[np.ndarray, int]:
    """Exposure in [0, 1] per building, and how many buildings have no centroid."""

    count = len(objects)
    if hazard.citywide:
        return np.ones(count), 0
    located = np.asarray(
        [item.centroid_x_m is not None and item.centroid_y_m is not None for item in objects], dtype=bool
    )
    xy = np.asarray(
        [(item.centroid_x_m or 0.0, item.centroid_y_m or 0.0) for item in objects], dtype=np.float64
    ).reshape(count, 2)
    if hazard.path_m:
        distance = _distance_to_path(xy, hazard.path_m)
    else:
        distance = np.linalg.norm(xy - np.asarray([hazard.center_x_m, hazard.center_y_m]), axis=1)
    exposure = _exposure(distance, radius_of(hazard), PROFILES[hazard.kind].core_fraction)
    return np.where(located, exposure, 0.0), int((~located).sum())


def _ground(objects: Sequence[UrbanObjectState]) -> np.ndarray:
    return np.asarray(
        [np.nan if item.ground_elevation_m is None else item.ground_elevation_m for item in objects], dtype=np.float64
    )


def flood_level(
    hazard: HazardEvent, objects: Sequence[UrbanObjectState], exposure: np.ndarray | None = None
) -> float | None:
    """Water surface in metres above sea level for a terrain-following flood; None when it is flat.

    It is flat when the hazard is not a flood or no building it reaches has a
    ground elevation.
    """

    if hazard.kind is not HazardKind.FLOOD:
        return None
    if exposure is None:
        exposure = _footprint(hazard, objects)[0]
    ground = _ground(objects)
    reached = (exposure > 0) & ~np.isnan(ground)
    if not reached.any():
        return None
    return float(np.percentile(ground[reached], FLOOD_LOW_GROUND_PERCENTILE)) + magnitude_of(hazard)


def hazard_shock(hazard: HazardEvent, objects: Sequence[UrbanObjectState]) -> HazardShock:
    """Destroy/damage probabilities per building, before state eligibility is applied.

    Buildings without a centroid cannot be placed inside a local footprint and
    are treated as outside it; the count is returned so the engine can say so.
    """

    profile = PROFILES[hazard.kind]
    exposure, unlocated = _footprint(hazard, objects)
    magnitude = magnitude_of(hazard)
    strength: float | np.ndarray = profile.strength(magnitude)
    level = flood_level(hazard, objects, exposure)
    if level is not None:
        # Each building's own water depth; one without a ground elevation
        # keeps the flat depth rather than being silently left dry.
        ground = _ground(objects)
        depth = np.where(np.isnan(ground), magnitude, np.clip(level - ground, 0.0, None))
        strength = np.asarray([profile.strength(float(d)) for d in depth])
    vulnerability = np.asarray([profile.vulnerability(item) for item in objects], dtype=np.float64)
    intensity = np.clip(strength * exposure * vulnerability, 0.0, 1.0)
    destroy = profile.destroy_ceiling * intensity**2
    damage = np.clip(profile.damage_ceiling * intensity - destroy, 0.0, 1.0 - destroy)
    return HazardShock(hazard=hazard, destroy=destroy, damage=damage, exposed=intensity > 0, unlocated=unlocated)


def combined_outcomes(
    shocks: Sequence[HazardShock], current_states: np.ndarray, tile: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """P(destroyed by any hazard) and P(damaged by some, destroyed by none) per row.

    Hazards striking on the same step act independently on the start-of-step
    state. Rows in a state with nothing to damage get zero for both.
    """

    eligible = np.isin(current_states, list(AFFECTABLE_STATES))
    survive_destroy = np.ones(len(current_states))
    untouched = np.ones(len(current_states))
    for shock in shocks:
        destroy = np.tile(shock.destroy, tile)
        damage = np.tile(shock.damage, tile)
        survive_destroy *= 1.0 - destroy
        untouched *= 1.0 - destroy - damage
    destroyed = np.where(eligible, 1.0 - survive_destroy, 0.0)
    damaged = np.where(eligible, survive_destroy - untouched, 0.0)
    return destroyed, damaged


def apply_to_probabilities(
    probabilities: np.ndarray, shocks: Sequence[HazardShock], current_states: np.ndarray, labels: Sequence[str]
) -> np.ndarray:
    """Exact end-of-step distribution with the hazards mixed in (used for the audited first step)."""

    destroyed, damaged = combined_outcomes(shocks, current_states)
    result = probabilities * (1.0 - destroyed - damaged)[:, None]
    result[:, labels.index(CanonicalState.DEMOLISHED.value)] += destroyed
    for row, state in enumerate(current_states):
        if damaged[row]:
            result[row, labels.index(damaged_state(str(state)))] += damaged[row]
    return result


def sample_hits(
    shocks: Sequence[HazardShock], current_states: np.ndarray, draws: int, rng: np.random.Generator
) -> tuple[np.ndarray, list[tuple[np.ndarray, np.ndarray]]]:
    """Sample which rows each hazard destroys or damages this step.

    Returns the forced end state per row (None where the transition model
    decides) and, per hazard, its own destroyed/damaged row masks so impacts
    can be counted per hazard. Overlapping hits count toward every hazard that
    landed them; destruction by any hazard wins over damage.
    """

    eligible = np.isin(current_states, list(AFFECTABLE_STATES))
    any_destroyed = np.zeros(len(current_states), dtype=bool)
    any_damaged = np.zeros(len(current_states), dtype=bool)
    per_hazard = []
    for shock in shocks:
        destroy = np.tile(shock.destroy, draws)
        damage = np.tile(shock.damage, draws)
        u = rng.random(len(current_states))
        destroyed = eligible & (u < destroy)
        damaged = eligible & ~destroyed & (u < destroy + damage)
        any_destroyed |= destroyed
        any_damaged |= damaged
        per_hazard.append((destroyed, damaged))
    forced = np.full(len(current_states), None, dtype=object)
    for row in np.flatnonzero(any_damaged & ~any_destroyed):
        forced[row] = damaged_state(str(current_states[row]))
    forced[any_destroyed] = CanonicalState.DEMOLISHED.value
    return forced, per_hazard
