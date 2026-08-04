"""Versioned public contracts shared by training, CLI, and the HTTP service."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .status import CanonicalState


class EvidenceLevel(StrEnum):
    EMPIRICAL_BAU = "empirical_bau"
    ASSUMPTION_BASED_SCENARIO = "assumption_based_scenario"


class ConfidenceGrade(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


NonNegativeFloat = Annotated[float, Field(ge=0)]


class UrbanObjectState(BaseModel):
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
        ids = [item.object_id for item in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot object_id values must be unique")
        return self


class TransitionAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    object_ids: list[str] = Field(default_factory=list)
    eligible_from_states: list[CanonicalState] = Field(default_factory=list)
    target_state: CanonicalState
    log_odds_delta: float = Field(ge=-8.0, le=8.0)
    active_step: int = Field(default=1, ge=1, le=3)
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
    draws: int = Field(default=500, ge=50, le=5000)
    seed: int = Field(default=42, ge=0)

    @model_validator(mode="after")
    def snapshot_matches_scenario(self) -> "ForecastRequest":
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

