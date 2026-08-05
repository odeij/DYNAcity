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


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    baseline_snapshot_id: str
    horizon_years: Literal[2, 4, 6] = 2
    interventions: list[TransitionAdjustment] = Field(default_factory=list)
    requested_kpis: list[str] = Field(default_factory=list)


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

