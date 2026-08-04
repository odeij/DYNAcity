"""FastAPI boundary for the forecasting engine."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .contracts import ForecastRequest, ForecastResult
from .engine import ForecastEngine


def create_app(engine: ForecastEngine) -> FastAPI:
    app = FastAPI(
        title="DynaCITY Urban-Form Forecasting",
        version="0.1.0",
        description=(
            "Probabilistic Beirut urban-state forecasts. Intervention scenarios are "
            "assumption-based and are not causal recommendations."
        ),
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model_version": engine.bundle.model_version}

    @app.get("/v1/models")
    def model_metadata() -> dict:
        return {
            "model_version": engine.bundle.model_version,
            "data_version": engine.bundle.data_version,
            "trained_at": engine.bundle.trained_at,
            "metrics": engine.bundle.metrics,
            "training_intervals": engine.bundle.training_intervals,
        }

    @app.post("/v1/forecast", response_model=ForecastResult)
    def forecast(request: ForecastRequest) -> ForecastResult:
        try:
            return engine.forecast(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app

