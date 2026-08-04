from datetime import date

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from dynacity.contracts import (
    ForecastRequest,
    ScenarioSpec,
    TransitionAdjustment,
    UrbanObjectState,
    UrbanStateSnapshot,
)
from dynacity.engine import ForecastEngine
from dynacity.models import MarkovForecaster, ModelBundle, evaluate_forecaster, temporal_benchmark
from dynacity.service import create_app
from dynacity.status import CanonicalState


def synthetic_panel() -> pd.DataFrame:
    rows = []
    states = [
        CanonicalState.STABLE_BUILT.value,
        CanonicalState.ACTIVE_CONSTRUCTION.value,
        CanonicalState.EMPTY_OR_PARKING.value,
    ]
    for target_year, start_year in [(2022, 2018), (2024, 2022)]:
        for index in range(120):
            current = states[index % len(states)]
            target = (
                CanonicalState.STABLE_BUILT.value
                if current == CanonicalState.ACTIVE_CONSTRUCTION.value and index % 2 == 0
                else current
            )
            rows.append(
                {
                    "object_id": str(index),
                    "current_state": current,
                    "target_state": target,
                    "sector": f"S{index % 4}",
                    "building_use": "Residential",
                    "start_year": start_year,
                    "target_year": target_year,
                    "interval_years": target_year - start_year,
                    "footprint_area_m2": 100 + index,
                    "floors": 2 + index % 8,
                    "height_m": 8 + index % 20,
                    "years_since_permit": index % 10,
                    "years_since_completion": np.nan,
                    "permit_known": 1,
                    "completion_known": 0,
                    "lidar_available": 0,
                    "lidar_acquisition_year": np.nan,
                }
            )
    return pd.DataFrame(rows)


def test_models_return_normalized_probabilities_and_temporal_report():
    panel = synthetic_panel()
    model = MarkovForecaster().fit(panel[panel.target_year == 2022])
    probabilities = model.predict_proba(panel.head(5))
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    metrics = evaluate_forecaster(model, panel[panel.target_year == 2024])
    assert 0 <= metrics["macro_f1"] <= 1
    bundle, report = temporal_benchmark(panel)
    assert bundle.metrics["selected_model"] in {"no_change", "markov", "gradient"}
    assert set(report) == {"no_change", "markov", "gradient"}


def engine_fixture() -> ForecastEngine:
    panel = synthetic_panel()
    markov = MarkovForecaster().fit(panel)
    bundle = ModelBundle.create(
        markov,
        data_version="synthetic",
        metrics={"macro_f1": 1.0},
        training_intervals=["synthetic"],
    )
    return ForecastEngine(bundle)


def request_fixture(with_intervention: bool = False) -> ForecastRequest:
    snapshot = UrbanStateSnapshot(
        snapshot_id="zone",
        as_of=date(2024, 4, 30),
        data_version="synthetic",
        objects=[
            UrbanObjectState(
                object_id="1",
                state=CanonicalState.ACTIVE_CONSTRUCTION,
                footprint_area_m2=100,
                floors=5,
                height_m=18,
                features={"permit_known": 1, "lidar_available": 0},
            ),
            UrbanObjectState(
                object_id="2",
                state=CanonicalState.EMPTY_OR_PARKING,
                footprint_area_m2=200,
                floors=0,
                height_m=0,
            ),
        ],
    )
    interventions = []
    if with_intervention:
        interventions = [
            TransitionAdjustment(
                intervention_id="complete-1",
                object_ids=["1"],
                eligible_from_states=[CanonicalState.ACTIVE_CONSTRUCTION],
                target_state=CanonicalState.STABLE_BUILT,
                log_odds_delta=2.0,
                effect_source="synthetic test assumption",
            )
        ]
    return ForecastRequest(
        snapshot=snapshot,
        scenario=ScenarioSpec(
            scenario_id="test",
            baseline_snapshot_id="zone",
            horizon_years=4,
            interventions=interventions,
        ),
        draws=50,
        seed=7,
    )


def test_forecast_engine_is_seeded_and_reports_assumptions():
    engine = engine_fixture()
    request = request_fixture(with_intervention=True)
    first = engine.forecast(request)
    second = engine.forecast(request)
    assert first == second
    assert len(first.kpis) == 20
    assert first.evidence_level == "assumption_based_scenario"
    assert any("not causal" in warning for warning in first.warnings)
    assert all(item.p05 <= item.p50 <= item.p95 for item in first.kpis)


def test_fastapi_uses_same_engine():
    client = TestClient(create_app(engine_fixture()))
    assert client.get("/health").status_code == 200
    response = client.post("/v1/forecast", json=request_fixture().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["scenario_id"] == "test"

