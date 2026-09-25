"""Scenario-conditioned probabilistic rollout engine.

Turns a one-step transition model into a multi-year forecast with uncertainty
bands. The mechanism is deliberately simple:

    for each 2-year step:
        predict P(next state) for every object, in every Monte Carlo draw
        apply any scenario interventions active at this step
        sample a concrete state per object per draw
        compute KPIs for each draw's sampled city

Two properties follow from that loop and are the reason the design is worth
stating explicitly:

- Uncertainty compounds honestly. Step 2 predicts from step 1's *sampled*
  state, not from the true state or from a probability-weighted average, so
  early-step error propagates instead of being averaged away. Wider bands at
  6 years than at 2 years are the model being truthful about horizon, not a
  defect.
- Interventions are sensitivity analysis, never causal claims. They shift
  target-state log-odds by an amount the caller supplies; the engine has no way
  to verify that number. Any result carrying interventions is stamped
  `assumption_based_scenario` and warned about.

The one non-sampled output is `first_step_transitions` — the raw, unsampled
probabilities from step 1, exposed so callers can audit the model directly
without the Monte Carlo layer in between.
"""

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
        """Rebuild the panel's column layout from API snapshot objects.

        The forecaster was fitted on `panel.PanelBuilder` output, so inference
        input must present the same column names and meanings. This is the
        adapter between the two shapes — the API's nested `features` dict is
        flattened back out to the flat columns the model expects.

        `interval` is fixed at 2 because that is the step size the rollout
        advances by; multi-year horizons come from repeated steps, never from
        asking the model for a longer interval directly.
        """

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
        """Shift target-state log-odds for the rows an intervention applies to.

        Each intervention is scoped two ways, and both must match: by explicit
        `object_ids` (empty = all objects) and by `eligible_from_states`
        (empty = any state). An empty scope means "everything", so an
        under-specified intervention is broad rather than inert.

        Adjustments are applied in list order and compose multiplicatively in
        odds space, so two interventions targeting the same state at the same
        step stack. Working in log-odds rather than on probabilities directly is
        what keeps the row normalised and bounded — a large delta saturates
        toward certainty instead of pushing past 1.
        """

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
        """Crude out-of-distribution signal in [0, 1]; higher = trust it less.

        Deliberately a coverage heuristic, not a learned density estimate: it
        measures how much of the input the model is effectively guessing about
        (unknown states, absent geometry) rather than how far the input sits
        from the training manifold. The 0.6/0.4 split weights unknown status
        above missing geometry because status is the model's primary predictor.

        An empty input scores 1.0 — maximally untrustworthy — though `forecast`
        rejects that case before this is reached. A learned score is listed as
        future work in docs/forecasting-engine.md.
        """

        if not objects:
            return 1.0
        unknown = np.mean([item.state == CanonicalState.UNKNOWN for item in objects])
        missing_geometry = np.mean(
            [item.floors is None and item.height_m is None for item in objects]
        )
        return float(np.clip(0.6 * unknown + 0.4 * missing_geometry, 0.0, 1.0))

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Roll the transition model forward and summarise the KPI distribution.

        Returns both the audited first step (exact probabilities) and the
        sampled horizon (p05/p50/p95 bands per KPI per 2-year offset).
        """

        objects = request.snapshot.objects
        if not objects:
            raise ValueError("forecast snapshot contains no urban objects")
        initial_states = [item.state.value for item in objects]
        base = self._frame(objects, initial_states)
        object_ids = base["object_id"].astype(str).to_numpy()
        # Step 1 is computed once, unsampled, and returned verbatim. This is the
        # part of the output a reviewer can check against the model directly —
        # everything below is Monte Carlo on top of it.
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

        # Seeded from the request so a given (snapshot, scenario, seed) triple
        # reproduces byte-identical bands — required for the result to be
        # citable.
        rng = np.random.default_rng(request.seed)
        draws = request.draws
        object_count = len(objects)
        # (draws, objects): every draw is an independent parallel future of the
        # whole city, all starting from the same observed state.
        state_matrix = np.tile(np.asarray(initial_states, dtype=object), (draws, 1))
        requested_kpis = request.scenario.requested_kpis or list(DEFAULT_KPIS)
        unknown_kpis = sorted(set(requested_kpis) - set(DEFAULT_KPIS))
        if unknown_kpis:
            raise ValueError(f"unsupported KPIs: {unknown_kpis}")
        sampled_kpis: dict[tuple[int, str], list[float]] = {}
        tiled_ids = np.tile(object_ids, draws)
        # horizon_years // 2 steps: the model was fitted on 2-year transitions,
        # so a 6-year horizon is three applications rather than one long jump.
        for step in range(1, request.scenario.horizon_years // 2 + 1):
            flat_states = state_matrix.reshape(-1)
            # All draws are predicted in one batched call: the covariates are
            # static per object, so only `current_state` differs between draws.
            # This is the step that makes error compound — the model conditions
            # on the previous step's *sampled* state, not on the truth.
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
            # Collapse each row's distribution to one concrete state. Sampling
            # rather than taking the argmax is what preserves realistic
            # co-occurrence: a city where 3% of buildings were demolished is a
            # coherent scenario, whereas argmax would demolish either none or
            # implausibly many in lockstep.
            sampled = sample_categorical(probabilities, rng)
            state_matrix = np.asarray(LABELS, dtype=object)[sampled].reshape(
                draws, object_count
            )
            # KPIs are computed per draw, on that draw's fully-realised city, so
            # the spread across draws is the actual KPI distribution. Computing
            # them from mean probabilities instead would collapse the
            # uncertainty this whole loop exists to measure.
            for draw_states in state_matrix:
                values = compute_kpis(objects, draw_states)
                for kpi in requested_kpis:
                    sampled_kpis.setdefault((step * 2, kpi), []).append(values[kpi])

        # Empirical quantiles across draws — no distributional assumption is
        # made, so the bands can be asymmetric where the underlying KPI is.
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

        # Warnings travel with the result rather than being logged, so a caller
        # reading only the JSON still sees the caveats that apply to it.
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
            # The presence of any intervention downgrades the entire result's
            # evidence level. There is no partial credit: once a
            # caller-supplied effect size is in the rollout, the output is a
            # what-if, not an empirical projection.
            evidence_level=(
                EvidenceLevel.ASSUMPTION_BASED_SCENARIO
                if request.scenario.interventions
                else EvidenceLevel.EMPIRICAL_BAU
            ),
            first_step_transitions=transitions,
            kpis=estimates,
            out_of_distribution_score=ood,
            warnings=warnings,
            evidence_bundle_id=request.scenario.evidence_bundle_id,
        )

