"""Forecasting baselines, production candidate, model selection, and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from .status import CanonicalState


LABELS = [state.value for state in CanonicalState]
CORE_NUMERIC_FEATURES = [
    "interval_years",
    "footprint_area_m2",
    "floors",
    "height_m",
    "years_since_permit",
    "years_since_completion",
    "permit_known",
    "completion_known",
    "lidar_available",
]
CORE_CATEGORICAL_FEATURES = ["current_state", "sector", "building_use"]


class TransitionForecaster(Protocol):
    model_version: str

    def fit(self, frame: pd.DataFrame) -> "TransitionForecaster": ...

    def predict_proba(self, frame: pd.DataFrame) -> pd.DataFrame: ...


def _align_probabilities(classes: list[str], values: np.ndarray) -> pd.DataFrame:
    aligned = np.zeros((len(values), len(LABELS)), dtype=np.float64)
    lookup = {label: index for index, label in enumerate(LABELS)}
    for source_index, label in enumerate(classes):
        if label in lookup:
            aligned[:, lookup[label]] = values[:, source_index]
    totals = aligned.sum(axis=1, keepdims=True)
    missing = totals[:, 0] == 0
    aligned[missing, lookup[CanonicalState.UNKNOWN.value]] = 1.0
    totals = aligned.sum(axis=1, keepdims=True)
    return pd.DataFrame(aligned / totals, columns=LABELS, index=frame_index(len(values)))


def frame_index(length: int) -> pd.RangeIndex:
    return pd.RangeIndex(start=0, stop=length, step=1)


class NoChangeForecaster:
    model_version = "no-change-v1"

    def fit(self, frame: pd.DataFrame) -> "NoChangeForecaster":
        return self

    def predict_proba(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = np.zeros((len(frame), len(LABELS)), dtype=np.float64)
        lookup = {label: index for index, label in enumerate(LABELS)}
        for row_index, state in enumerate(frame["current_state"].fillna("unknown")):
            result[row_index, lookup.get(str(state), lookup["unknown"])] = 1.0
        return pd.DataFrame(result, columns=LABELS)


class MarkovForecaster:
    model_version = "empirical-markov-v1"

    def __init__(self, smoothing: float = 0.5):
        self.smoothing = smoothing
        self._matrices: dict[int, dict[str, np.ndarray]] = {}

    def fit(self, frame: pd.DataFrame) -> "MarkovForecaster":
        if frame.empty:
            raise ValueError("cannot fit Markov model on an empty panel")
        self._matrices = {}
        for interval, group in frame.groupby("interval_years"):
            by_state: dict[str, np.ndarray] = {}
            for current in LABELS:
                counts = np.full(len(LABELS), self.smoothing, dtype=np.float64)
                targets = group.loc[group["current_state"] == current, "target_state"]
                for target, count in targets.value_counts().items():
                    if target in LABELS:
                        counts[LABELS.index(target)] += float(count)
                by_state[current] = counts / counts.sum()
            self._matrices[int(interval)] = by_state
        return self

    def predict_proba(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self._matrices:
            raise RuntimeError("Markov model has not been fitted")
        available = sorted(self._matrices)
        rows = []
        for _, record in frame.iterrows():
            requested = int(record.get("interval_years", available[0]))
            interval = min(available, key=lambda value: abs(value - requested))
            current = str(record.get("current_state", CanonicalState.UNKNOWN.value))
            rows.append(self._matrices[interval].get(current, self._matrices[interval]["unknown"]))
        return pd.DataFrame(np.asarray(rows), columns=LABELS)


class GradientBoostingForecaster:
    model_version = "hist-gradient-transition-v1"

    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.numeric_features: list[str] = []
        self.categorical_features: list[str] = []
        self.pipeline: Pipeline | None = None

    def _select_features(self, frame: pd.DataFrame) -> None:
        lidar = sorted(
            column
            for column in frame.columns
            if column.startswith("lidar_")
            and column not in {"lidar_acquisition_year", "lidar_available"}
        )
        self.numeric_features = [
            column
            for column in CORE_NUMERIC_FEATURES + lidar
            if column in frame and frame[column].notna().any()
        ]
        self.categorical_features = [
            column for column in CORE_CATEGORICAL_FEATURES if column in frame
        ]

    def fit(self, frame: pd.DataFrame) -> "GradientBoostingForecaster":
        if frame.empty:
            raise ValueError("cannot fit transition model on an empty panel")
        self._select_features(frame)
        numeric = Pipeline(
            [("impute", SimpleImputer(strategy="median", add_indicator=True))]
        )
        categorical = Pipeline(
            [
                ("impute", SimpleImputer(strategy="most_frequent")),
                (
                    "encode",
                    OrdinalEncoder(
                        handle_unknown="use_encoded_value",
                        unknown_value=-1,
                        encoded_missing_value=-1,
                    ),
                ),
            ]
        )
        preprocess = ColumnTransformer(
            [
                ("numeric", numeric, self.numeric_features),
                ("categorical", categorical, self.categorical_features),
            ],
            remainder="drop",
        )
        classifier = HistGradientBoostingClassifier(
            learning_rate=0.08,
            max_iter=250,
            max_leaf_nodes=15,
            min_samples_leaf=15,
            l2_regularization=1.0,
            random_state=self.random_state,
        )
        self.pipeline = Pipeline([("preprocess", preprocess), ("model", classifier)])
        counts = frame["target_state"].value_counts()
        weights = frame["target_state"].map(
            {label: len(frame) / (len(counts) * count) for label, count in counts.items()}
        )
        self.pipeline.fit(frame, frame["target_state"], model__sample_weight=weights)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.pipeline is None:
            raise RuntimeError("gradient model has not been fitted")
        values = self.pipeline.predict_proba(frame)
        classes = [str(label) for label in self.pipeline.named_steps["model"].classes_]
        aligned = np.zeros((len(frame), len(LABELS)), dtype=np.float64)
        for index, label in enumerate(classes):
            if label in LABELS:
                aligned[:, LABELS.index(label)] = values[:, index]
        totals = aligned.sum(axis=1, keepdims=True)
        missing = totals[:, 0] == 0
        aligned[missing, LABELS.index("unknown")] = 1.0
        return pd.DataFrame(aligned / aligned.sum(axis=1, keepdims=True), columns=LABELS)


def evaluate_forecaster(
    forecaster: TransitionForecaster,
    frame: pd.DataFrame,
) -> dict[str, float]:
    probabilities = forecaster.predict_proba(frame).reindex(columns=LABELS, fill_value=0.0)
    truth = frame["target_state"].astype(str).to_numpy()
    prediction = probabilities.columns[np.argmax(probabilities.to_numpy(), axis=1)]
    one_hot = np.zeros_like(probabilities.to_numpy())
    lookup = {label: index for index, label in enumerate(LABELS)}
    truth_indexes = np.empty(len(truth), dtype=np.int64)
    for row, label in enumerate(truth):
        truth_indexes[row] = lookup.get(label, lookup["unknown"])
        one_hot[row, truth_indexes[row]] = 1.0
    return {
        "accuracy": float(accuracy_score(truth, prediction)),
        "macro_f1": float(
            f1_score(truth, prediction, labels=LABELS, average="macro", zero_division=0)
        ),
        "multiclass_brier": float(
            np.mean(np.sum((probabilities.to_numpy() - one_hot) ** 2, axis=1))
        ),
        "log_loss": float(
            -np.mean(
                np.log(
                    np.clip(
                        probabilities.to_numpy()[np.arange(len(truth)), truth_indexes],
                        1e-12,
                        1.0,
                    )
                )
            )
        ),
    }


@dataclass
class ModelBundle:
    forecaster: TransitionForecaster
    model_version: str
    data_version: str
    trained_at: str
    metrics: dict[str, float]
    labels: list[str]
    training_intervals: list[str]

    @classmethod
    def create(
        cls,
        forecaster: TransitionForecaster,
        *,
        data_version: str,
        metrics: dict[str, float],
        training_intervals: list[str],
    ) -> "ModelBundle":
        return cls(
            forecaster=forecaster,
            model_version=forecaster.model_version,
            data_version=data_version,
            trained_at=datetime.now(UTC).isoformat(),
            metrics=metrics,
            labels=LABELS,
            training_intervals=training_intervals,
        )

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, destination)
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "ModelBundle":
        bundle = joblib.load(path)
        if not isinstance(bundle, cls):
            raise TypeError("artifact is not a DynaCITY ModelBundle")
        return bundle


def temporal_benchmark(panel: pd.DataFrame) -> tuple[ModelBundle, dict[str, dict[str, float]]]:
    train = panel[panel["target_year"] == 2022].copy()
    test = panel[panel["target_year"] == 2024].copy()
    if train.empty or test.empty:
        raise ValueError("temporal benchmark requires 2018→2022 train and 2022→2024 test rows")
    candidates: dict[str, TransitionForecaster] = {
        "no_change": NoChangeForecaster().fit(train),
        "markov": MarkovForecaster().fit(train),
        "gradient": GradientBoostingForecaster().fit(train),
    }
    metrics = {name: evaluate_forecaster(model, test) for name, model in candidates.items()}
    baseline_f1 = max(metrics["no_change"]["macro_f1"], metrics["markov"]["macro_f1"])
    baseline_brier = min(
        metrics["no_change"]["multiclass_brier"],
        metrics["markov"]["multiclass_brier"],
    )
    gradient_passes = (
        metrics["gradient"]["macro_f1"] > baseline_f1
        and metrics["gradient"]["multiclass_brier"] < baseline_brier
    )
    if gradient_passes:
        selected_name = "gradient"
    else:
        selected_name = min(
            ("no_change", "markov"),
            key=lambda name: (
                -metrics[name]["macro_f1"], metrics[name]["multiclass_brier"]
            ),
        )
    selected = candidates[selected_name]
    bundle = ModelBundle.create(
        selected,
        data_version="BBED-2018-2024",
        metrics=metrics[selected_name] | {"selected_model": selected_name},
        training_intervals=["2018-2022"],
    )
    return bundle, metrics
