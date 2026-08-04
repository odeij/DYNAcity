"""Scenario-conditioned probabilistic rollout engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import (
    EvidenceLevel,
    ForecastRequest,
    ForecastResult,
    KpiEstimate,
    ObjectTransitionForecast,
    UrbanObjectState,
)
from .kpis import DEFAULT_KPIS, KPI_UNITS, compute_kpis
from .models import LABELS, ModelBundle
from .status import CanonicalState
from .uncertainty import apply_log_odds_adjustment, sample_categorical


class ForecastEngine:
    def __init__(self, bundle: ModelBundle):
        self.bundle = bundle

    @staticmethod
    def _frame(objects: list[UrbanObjectState], states: list[str], interval: int = 2) -> pd.DataFrame:
        rows = []
        for item, state in zip(objects, states, strict=True):
            row = {
                "object_id": item.object_id,
                "parcel_id": item.parcel_id,
                "current_state": state,
                "sector": item.sector,
                "building_use": item.building_use,
                "interval_years": interval,
                "footprint_area_m2": item.footprint_area_m2,
                "floors": item.floors,
                "height_m": item.height_m,
                "years_since_permit": item.features.get("years_since_permit"),
                "years_since_completion": item.features.get("years_since_completion"),
                "permit_known": item.features.get("permit_known", 0),
                "completion_known": item.features.get("completion_known", 0),
                "lidar_available": item.features.get("lidar_available", 0),
            }
            for key, value in item.features.items():
                if key.startswith("lidar_"):
                    row[key] = value
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def _apply_interventions(
        probabilities: np.ndarray,
        request: ForecastRequest,
        *,
        step: int,
        object_ids: np.ndarray,
        current_states: np.ndarray,
    ) -> np.ndarray:
        adjusted = probabilities
        for intervention in request.scenario.interventions:
            if intervention.active_step != step:
                continue
            object_mask = np.ones(len(object_ids), dtype=bool)
            if intervention.object_ids:
                object_mask = np.isin(object_ids, intervention.object_ids)
            state_mask = np.ones(len(current_states), dtype=bool)
            if intervention.eligible_from_states:
                eligible = [state.value for state in intervention.eligible_from_states]
                state_mask = np.isin(current_states, eligible)
            adjusted = apply_log_odds_adjustment(
                adjusted,
                LABELS.index(intervention.target_state.value),
                intervention.log_odds_delta,
                object_mask & state_mask,
            )
        return adjusted

    @staticmethod
    def _ood_score(objects: list[UrbanObjectState]) -> float:
        if not objects:
            return 1.0
        unknown = np.mean([item.state == CanonicalState.UNKNOWN for item in objects])
        missing_geometry = np.mean(
            [item.floors is None and item.height_m is None for item in objects]
        )
        return float(np.clip(0.6 * unknown + 0.4 * missing_geometry, 0.0, 1.0))

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        objects = request.snapshot.objects
        if not objects:
            raise ValueError("forecast snapshot contains no urban objects")
        initial_states = [item.state.value for item in objects]
        base = self._frame(objects, initial_states)
        object_ids = base["object_id"].astype(str).to_numpy()
        first = self.bundle.forecaster.predict_proba(base).reindex(columns=LABELS).to_numpy()
        first = self._apply_interventions(
            first,
            request,
            step=1,
            object_ids=object_ids,
            current_states=np.asarray(initial_states),
        )
        transitions = [
            ObjectTransitionForecast(
                object_id=item.object_id,
                probabilities={
                    CanonicalState(label): float(first[row, index])
                    for index, label in enumerate(LABELS)
                },
            )
            for row, item in enumerate(objects)
        ]

        rng = np.random.default_rng(request.seed)
        draws = request.draws
        object_count = len(objects)
        state_matrix = np.tile(np.asarray(initial_states, dtype=object), (draws, 1))
        requested_kpis = request.scenario.requested_kpis or list(DEFAULT_KPIS)
        unknown_kpis = sorted(set(requested_kpis) - set(DEFAULT_KPIS))
        if unknown_kpis:
            raise ValueError(f"unsupported KPIs: {unknown_kpis}")
        sampled_kpis: dict[tuple[int, str], list[float]] = {}
        tiled_ids = np.tile(object_ids, draws)
        for step in range(1, request.scenario.horizon_years // 2 + 1):
            flat_states = state_matrix.reshape(-1)
            expanded = pd.concat([base] * draws, ignore_index=True)
            expanded["current_state"] = flat_states
            probabilities = self.bundle.forecaster.predict_proba(expanded).reindex(
                columns=LABELS
            ).to_numpy()
            probabilities = self._apply_interventions(
                probabilities,
                request,
                step=step,
                object_ids=tiled_ids,
                current_states=flat_states,
            )
            sampled = sample_categorical(probabilities, rng)
            state_matrix = np.asarray(LABELS, dtype=object)[sampled].reshape(
                draws, object_count
            )
            for draw_states in state_matrix:
                values = compute_kpis(objects, draw_states)
                for kpi in requested_kpis:
                    sampled_kpis.setdefault((step * 2, kpi), []).append(values[kpi])

        estimates = []
        for (year_offset, kpi), values in sorted(sampled_kpis.items()):
            p05, p50, p95 = np.quantile(values, [0.05, 0.50, 0.95])
            estimates.append(
                KpiEstimate(
                    year_offset=year_offset,
                    kpi=kpi,
                    unit=KPI_UNITS[kpi],
                    p05=float(p05),
                    p50=float(p50),
                    p95=float(p95),
                )
            )

        warnings = []
        if request.scenario.interventions:
            warnings.append(
                "Intervention effects are versioned assumptions, not causal estimates."
            )
        if request.scenario.horizon_years > 2:
            warnings.append("Multi-step uncertainty compounds beyond the observed two-year interval.")
        ood = self._ood_score(objects)
        if ood >= 0.4:
            warnings.append("Input has substantial missing or unknown core morphology data.")
        return ForecastResult(
            scenario_id=request.scenario.scenario_id,
            baseline_snapshot_id=request.snapshot.snapshot_id,
            model_version=self.bundle.model_version,
            data_version=request.snapshot.data_version,
            evidence_level=(
                EvidenceLevel.ASSUMPTION_BASED_SCENARIO
                if request.scenario.interventions
                else EvidenceLevel.EMPIRICAL_BAU
            ),
            first_step_transitions=transitions,
            kpis=estimates,
            out_of_distribution_score=ood,
            warnings=warnings,
        )

