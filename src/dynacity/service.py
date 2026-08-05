"""FastAPI boundary for the forecasting engine.

A thin adapter, intentionally. All validation lives in `contracts.py` and all
behaviour in `engine.py`, so the HTTP surface and the CLI cannot drift apart —
the same request JSON produces the same result either way.

`create_app` takes an already-loaded engine rather than a model path so the
model is loaded once at startup, not per request, and so tests can inject an
engine without touching disk.
"""

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

    # Exposes the bundle's provenance — versions, training intervals, and the
    # benchmark metrics of the model that shipped. Published deliberately: a
    # consumer should be able to see how the model scored, including when the
    # learned candidate lost the gate and a baseline is serving.
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
        # The engine raises ValueError for inputs that parse but are not
        # forecastable (empty snapshot, unsupported KPI). That is a semantic
        # problem with the request, so 422 — matching how pydantic already
        # reports schema violations — rather than a 500.
        try:
            return engine.forecast(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app

