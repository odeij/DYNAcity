"""Versioned public contracts shared by training, CLI, and the HTTP service.

One schema, three consumers. The CLI's `forecast` command, `ForecastEngine`,
and the FastAPI service all validate against these exact models, so a request
file that works offline works unchanged over HTTP.

Every model sets `extra="forbid"`. That is a deliberate choice against silent
failure: a misspelled field (`log_odds_del7a`) raises instead of being ignored
and leaving the caller believing an intervention was applied when it was not.

The types also encode the project's epistemic commitments rather than just its
data shapes — `TransitionAdjustment` cannot be constructed without an
`effect_source`, and `ForecastResult` cannot omit its `evidence_level`.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .status import CanonicalState


class EvidenceLevel(StrEnum):
    """How much epistemic weight a result can carry.

    EMPIRICAL_BAU — business as usual, extrapolated from observed transitions
    only. Still observational, so not causal, but grounded in data.

    ASSUMPTION_BASED_SCENARIO — a caller-supplied intervention was applied. The
    output is a what-if conditioned on an effect size the engine cannot verify.
    """

    EMPIRICAL_BAU = "empirical_bau"
    ASSUMPTION_BASED_SCENARIO = "assumption_based_scenario"


class ConfidenceGrade(StrEnum):
    """Caller's self-declared strength of evidence for an intervention's effect size.

    Recorded and echoed back, never acted on — the engine does not scale the
    adjustment by grade. It exists so a scenario's provenance travels with it.
    Defaults to LOW so an unstated grade is pessimistic rather than flattering.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


NonNegativeFloat = Annotated[float, Field(ge=0)]


class HazardKind(StrEnum):
    """Physical shocks a scenario can throw at the city (see `hazards.PROFILES`).

    The set follows God's Plan's disasters. Gas leaks map to `explosion`.
    Road closures and vehicle crashes are absent: the engine has no street
    network for them to act on.
    """

    TORNADO = "tornado"
    STORM = "storm"
    RAIN = "rain"
    FLOOD = "flood"
    FIRE = "fire"
    HEAT = "heat"
    EARTHQUAKE = "earthquake"
    EXPLOSION = "explosion"
    PLANE_CRASH = "plane_crash"
    ORBITAL_STRIKE = "orbital_strike"
    RIOT = "riot"


DEFAULT_HAZARD_SOURCE = "illustrative default fragility (dynacity.hazards), uncalibrated"


class UrbanObjectState(BaseModel):
    """One building at one point in time — the atom of a snapshot.

    `features` is the open-ended bag the engine flattens back into model
    columns (`years_since_permit`, `lidar_*`, …); `provenance` records where
    each part of the record came from. Keeping provenance on the object rather
    than only on the snapshot means a mixed-source city — some buildings with
    morphology coverage, some without — stays self-describing.
    """

    model_config = ConfigDict(extra="forbid")

    object_id: str
    parcel_id: str | None = None
    state: CanonicalState
    raw_state: str | None = None
    footprint_area_m2: NonNegativeFloat = 0.0
    floors: NonNegativeFloat | None = None
    height_m: NonNegativeFloat | None = None
    building_use: str | None = None
    sector: str | None = None
    features: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)
    # Footprint centroid in the snapshot CRS (metres). Optional so older
    # snapshot files stay valid; hazards cannot reach a building without it.
    centroid_x_m: float | None = None
    centroid_y_m: float | None = None
    # Approximate ground level at the centroid, metres above the EGM2008 geoid
    # (`terrain.attach_ground_elevation`). Optional; without it a flood is flat.
    ground_elevation_m: float | None = None


class UrbanStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    as_of: date
    crs: str = "EPSG:32636"
    objects: list[UrbanObjectState]
    data_version: str

    @model_validator(mode="after")
    def unique_object_ids(self) -> "UrbanStateSnapshot":
        """Reject duplicate ids.

        The rollout indexes objects positionally and interventions select them
        by id, so a duplicate would make an intervention's scope ambiguous and
        double-count the object in every KPI. BBED's composite ids
        (`bbed.resolved_object_ids`) are built to satisfy this even where raw
        building ids repeat.
        """

        ids = [item.object_id for item in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot object_id values must be unique")
        return self


class TransitionAdjustment(BaseModel):
    """A single scenario lever: push some objects toward some target state.

    Sensitivity analysis, not a causal estimator. The caller asserts the effect
    size; the engine applies it faithfully and labels the result accordingly.

    Scope: empty `object_ids` means all objects, empty `eligible_from_states`
    means any origin state. Both filters apply together, so leaving both empty
    is maximally broad — under-specifying widens an intervention rather than
    disabling it.
    """

    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    object_ids: list[str] = Field(default_factory=list)
    eligible_from_states: list[CanonicalState] = Field(default_factory=list)
    target_state: CanonicalState
    # Bounded at ±8: beyond roughly this magnitude the softmax saturates and the
    # adjustment becomes indistinguishable from forcing the outcome outright,
    # which would be asserting a result rather than testing a sensitivity.
    log_odds_delta: float = Field(ge=-8.0, le=8.0)
    # Which rollout step this activates on; capped at 3 to match the longest
    # supported horizon (6 years / 2-year steps).
    active_step: int = Field(default=1, ge=1, le=3)
    # Required, non-trivial free text. There is no valid way to specify an
    # intervention without saying where its effect size came from — that is the
    # difference between a documented assumption and an invented number.
    effect_source: str = Field(min_length=3)
    confidence_grade: ConfidenceGrade = ConfidenceGrade.LOW


class HazardEvent(BaseModel):
    """A physical shock that strikes at the start of one rollout step.

    Located in exactly one way: `citywide`, a point (`center_x_m`/`center_y_m`
    with an optional `radius_m`), or a track (`path_m`, tornadoes). Coordinates
    are in the snapshot CRS. `magnitude` is in the kind's own unit (EF scale,
    Mw, TNT tonnes, …; see `hazards.PROFILES`) and defaults to its moderate
    level; `radius_m` defaults to what that magnitude implies.

    The damage curves are illustrative assumptions, so `effect_source`
    defaults to saying exactly that, and any result carrying a hazard is
    `assumption_based_scenario`.
    """

    model_config = ConfigDict(extra="forbid")

    hazard_id: str
    kind: HazardKind
    magnitude: float | None = None
    citywide: bool = False
    center_x_m: float | None = None
    center_y_m: float | None = None
    path_m: list[tuple[float, float]] = Field(default_factory=list)
    radius_m: float | None = Field(default=None, gt=0, le=100_000)
    occurs_step: int = Field(default=1, ge=1, le=3)
    effect_source: str = Field(default=DEFAULT_HAZARD_SOURCE, min_length=3)
    confidence_grade: ConfidenceGrade = ConfidenceGrade.LOW

    @model_validator(mode="after")
    def located_and_in_range(self) -> "HazardEvent":
        # Imported here because hazards.py imports this module.
        from .hazards import PROFILES

        has_center = self.center_x_m is not None or self.center_y_m is not None
        if has_center and (self.center_x_m is None or self.center_y_m is None):
            raise ValueError("give both center_x_m and center_y_m")
        if len(self.path_m) == 1:
            raise ValueError("path_m needs at least two points")
        modes = [name for name, given in
                 (("citywide", self.citywide), ("point", has_center), ("path", bool(self.path_m))) if given]
        if len(modes) != 1:
            raise ValueError("locate the hazard with exactly one of citywide, center_x_m/center_y_m, or path_m")
        profile = PROFILES[self.kind]
        if modes[0] not in profile.footprints:
            raise ValueError(f"{self.kind.value} cannot be {modes[0]}; use one of {sorted(profile.footprints)}")
        if self.citywide and self.radius_m is not None:
            raise ValueError("radius_m does not apply to a citywide hazard")
        lo, hi = profile.magnitude_range
        if self.magnitude is not None and not lo <= self.magnitude <= hi:
            raise ValueError(f"{self.kind.value} magnitude must be within {lo}..{hi} {profile.unit}")
        return self


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    baseline_snapshot_id: str
    horizon_years: Literal[2, 4, 6] = 2
    interventions: list[TransitionAdjustment] = Field(default_factory=list)
    requested_kpis: list[str] = Field(default_factory=list)
    # Id of the frozen `evidence.EvidenceBundle` that `evidence:` effect
    # sources refer to. Optional so existing scenario files stay valid.
    evidence_bundle_id: str | None = None
    hazards: list[HazardEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def hazards_fit_horizon(self) -> "ScenarioSpec":
        """A hazard past the horizon would silently never strike, so reject it."""

        ids = [item.hazard_id for item in self.hazards]
        if len(ids) != len(set(ids)):
            raise ValueError("hazard_id values must be unique")
        for item in self.hazards:
            if item.occurs_step > self.horizon_years // 2:
                raise ValueError(
                    f"hazard {item.hazard_id!r} strikes on step {item.occurs_step}, "
                    f"beyond the {self.horizon_years}-year horizon"
                )
        return self


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: UrbanStateSnapshot
    scenario: ScenarioSpec
    # Monte Carlo sample count. The floor of 50 exists because p05/p95 from
    # fewer draws is noise; the ceiling bounds request cost, since work scales
    # with draws × objects × steps.
    draws: int = Field(default=500, ge=50, le=5000)
    # Explicit and defaulted so that repeating a request reproduces its bands
    # exactly. Vary it deliberately to check a result is not seed-dependent.
    seed: int = Field(default=42, ge=0)

    @model_validator(mode="after")
    def snapshot_matches_scenario(self) -> "ForecastRequest":
        """Refuse a scenario written against a different baseline.

        A scenario's interventions reference object ids and assume a starting
        city. Running it against an unrelated snapshot would silently produce a
        result that answers nobody's question, so the mismatch is rejected up
        front rather than discovered in the output.
        """

        if self.snapshot.snapshot_id != self.scenario.baseline_snapshot_id:
            raise ValueError("scenario baseline_snapshot_id does not match snapshot_id")
        return self


class ObjectTransitionForecast(BaseModel):
    object_id: str
    probabilities: dict[CanonicalState, float]


class KpiEstimate(BaseModel):
    year_offset: int
    kpi: str
    unit: str
    p05: float
    p50: float
    p95: float


class CountBand(BaseModel):
    p05: float
    p50: float
    p95: float


class HazardImpact(BaseModel):
    """What one hazard did across the Monte Carlo draws, on the step it struck.

    Counts are buildings this hazard destroyed (→ demolished) or damaged
    (→ vacant, or stalled for sites). A building hit by two hazards on the same
    step counts toward both.
    """

    hazard_id: str
    kind: HazardKind
    occurs_step: int
    year_offset: int
    magnitude: float
    magnitude_unit: str
    radius_m: float | None
    exposed_buildings: int
    unlocated_buildings: int
    destroyed: CountBand
    damaged: CountBand
    effect_source: str
    confidence_grade: ConfidenceGrade


class ForecastResult(BaseModel):
    """The full response — predictions plus everything needed to judge them.

    `first_step_transitions` are exact model probabilities (no sampling), so a
    reviewer can audit the model without reasoning through the Monte Carlo
    layer. `kpis` are the sampled bands over the horizon. The remaining fields
    are the honesty apparatus: model/data versions for reproducibility,
    `evidence_level` for whether this is empirical or assumed,
    `out_of_distribution_score` for how far the input strayed from training
    coverage, and `warnings` for caveats that apply to this specific call.
    """

    schema_version: str = "1.0"
    scenario_id: str
    baseline_snapshot_id: str
    model_version: str
    data_version: str
    evidence_level: EvidenceLevel
    first_step_transitions: list[ObjectTransitionForecast]
    kpis: list[KpiEstimate]
    out_of_distribution_score: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    evidence_bundle_id: str | None = None
    hazard_impacts: list[HazardImpact] = Field(default_factory=list)

