"""Forecasting baselines, production candidate, model selection, and persistence.

Every forecaster here answers the same one-step question: given a building's
state and covariates at the start of an interval, what is the probability
distribution over its state at the end? `engine.ForecastEngine` gets multi-year
horizons by applying that one step repeatedly — nothing in this module knows
about horizons.

All candidates therefore share a strict output contract: `predict_proba` returns
a DataFrame with exactly the `LABELS` columns, in that order, each row summing
to 1. Column order is load-bearing because the engine indexes into the array
positionally (e.g. to apply a scenario adjustment to a named target state).

Selection is adversarial by design. The learned candidate must beat the
baselines on both discrimination and calibration before it ships; see
`temporal_benchmark`.
"""

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


# Canonical column order for every probability matrix in the package. Derived
# from the enum so it cannot drift from the state taxonomy.
LABELS = [state.value for state in CanonicalState]

# Feature allowlist. Chosen rather than "everything in the panel" so that
# identifiers (object_id, parcel_id) and the answer itself (target_state,
# transitioned) cannot be fed to the model by accident. Point-cloud columns are
# not listed here — they are discovered by prefix at fit time, because which
# ones exist depends on whether a morphology table was joined.
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
    """Structural interface every candidate satisfies.

    A Protocol rather than a base class so candidates stay independent — the
    baselines share no implementation with the sklearn pipeline. `model_version`
    is recorded into the ModelBundle and surfaced over the API, so it must
    change whenever the model's behaviour does.
    """

    model_version: str

    def fit(self, frame: pd.DataFrame) -> "TransitionForecaster": ...

    def predict_proba(self, frame: pd.DataFrame) -> pd.DataFrame: ...


def _align_probabilities(classes: list[str], values: np.ndarray) -> pd.DataFrame:
    """Reindex a classifier's native class order onto `LABELS`.

    NOTE: currently unused — `GradientBoostingForecaster.predict_proba` performs
    the same alignment inline. Kept as the reference implementation of the
    contract: absent classes get probability 0, and a row that ends up with no
    mass at all is assigned entirely to UNKNOWN rather than producing a
    divide-by-zero.
    """

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
    """Persistence baseline: predict the building stays exactly as it is.

    This is the bar any real model must clear. Urban form is dominated by
    stability — most buildings do not change state in two years — so a
    do-nothing predictor scores deceptively well on accuracy. It is included
    precisely to make that inflation visible, and it is why macro-F1 (which
    weights the rare, interesting classes equally) is the headline metric
    instead.

    Predictions are one-hot, so it is maximally confident and always wrong on
    every transition that does occur — which its Brier score reflects.
    """

    model_version = "no-change-v1"

    def fit(self, frame: pd.DataFrame) -> "NoChangeForecaster":
        """No parameters to learn; accepts the panel only to satisfy the protocol."""

        return self

    def predict_proba(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = np.zeros((len(frame), len(LABELS)), dtype=np.float64)
        lookup = {label: index for index, label in enumerate(LABELS)}
        for row_index, state in enumerate(frame["current_state"].fillna("unknown")):
            result[row_index, lookup.get(str(state), lookup["unknown"])] = 1.0
        return pd.DataFrame(result, columns=LABELS)


class MarkovForecaster:
    """Empirical transition matrix, estimated per interval length.

    The classical urban-change model: P(next state | current state), counted
    straight off the training panel. It uses no covariates at all — two
    buildings in the same state get identical predictions regardless of size,
    age, or sector. That is the gap a learned model has to justify closing.

    Separate matrices are kept per `interval_years` because transition rates are
    not linear in time: a 4-year window is not two 2-year windows applied twice.

    `smoothing` is additive (Laplace) smoothing over the counts. Without it, any
    from→to pair never observed in training would get probability 0 and make the
    log-loss infinite the first time it appears in test. 0.5 is the Jeffreys
    prior — weak enough not to distort well-observed rows, strong enough to keep
    rare states from being declared impossible.
    """

    model_version = "empirical-markov-v1"

    def __init__(self, smoothing: float = 0.5):
        self.smoothing = smoothing
        self._matrices: dict[int, dict[str, np.ndarray]] = {}

    def fit(self, frame: pd.DataFrame) -> "MarkovForecaster":
        """Count transitions into one smoothed row-stochastic matrix per interval length."""

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
        """Look up each row's stored transition row.

        Grouped by (interval length, current state) rather than iterated per row:
        the engine calls this once per rollout step with `draws × objects` rows,
        so a Python-level loop over rows dominates forecast latency.

        Rows whose `interval_years` was never fitted fall back to the nearest
        observed length instead of raising — the engine steps in fixed 2-year
        increments and may ask for a length the panel does not contain. Unknown
        or unseen `current_state` values fall back to the "unknown" row, which
        smoothing guarantees exists and is non-degenerate.
        """

        if not self._matrices:
            raise RuntimeError("Markov model has not been fitted")
        available = sorted(self._matrices)
        requested = (
            frame["interval_years"].fillna(available[0]).astype(int).to_numpy()
            if "interval_years" in frame
            else np.full(len(frame), available[0], dtype=int)
        )
        current_states = (
            frame["current_state"]
            .fillna(CanonicalState.UNKNOWN.value)
            .astype(str)
            .to_numpy()
        )
        result = np.zeros((len(frame), len(LABELS)), dtype=np.float64)
        for requested_interval in np.unique(requested):
            interval = min(
                available,
                key=lambda value: abs(value - int(requested_interval)),
            )
            interval_mask = requested == requested_interval
            matrix = self._matrices[interval]
            for state in np.unique(current_states[interval_mask]):
                state_mask = interval_mask & (current_states == state)
                result[state_mask] = matrix.get(str(state), matrix["unknown"])
        return pd.DataFrame(result, columns=LABELS)


class GradientBoostingForecaster:
    """The learned candidate: histogram gradient boosting over panel covariates.

    Unlike the Markov baseline this conditions on building attributes, so two
    buildings in the same state can get different forecasts. Histogram boosting
    was chosen because it handles the panel's realities natively — mixed
    numeric/categorical columns, heavy missingness in the morphology block, and
    a few thousand rows rather than millions.

    `random_state` is fixed so that a rebuild on identical data reproduces the
    same model; the benchmark's pass/fail verdict must not depend on the seed.
    """

    model_version = "hist-gradient-transition-v1"

    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.numeric_features: list[str] = []
        self.categorical_features: list[str] = []
        self.pipeline: Pipeline | None = None

    def _select_features(self, frame: pd.DataFrame) -> None:
        """Resolve the feature list against the columns this panel actually has.

        Point-cloud columns are discovered by their `lidar_` prefix, since which
        morphology fields exist depends on whether a table was joined at all.
        `lidar_acquisition_year` and `lidar_available` are excluded from that
        sweep: the first is provenance metadata, and the second is already in
        the core numeric list as the missing-modality indicator.

        Columns that are entirely NaN are dropped — with the leakage rule in
        place, a panel of only pre-acquisition intervals has empty morphology
        columns, and passing those to the imputer yields a constant feature.
        """

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
        # add_indicator matters more than the imputed value itself: "no
        # morphology for this building" is signal (it means the point cloud did
        # not cover it), so the model gets an explicit missingness flag rather
        # than only a median that looks like a real measurement.
        numeric = Pipeline(
            [("impute", SimpleImputer(strategy="median", add_indicator=True))]
        )
        categorical = Pipeline(
            [
                ("impute", SimpleImputer(strategy="most_frequent")),
                # Ordinal (not one-hot) because tree splits do not need
                # orthogonal columns, and sectors/uses have enough distinct
                # values that one-hot would explode the feature space. Unseen
                # categories at inference map to -1 instead of raising: a
                # forecast request may legitimately contain a sector absent from
                # the training panel.
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
        # Balanced sample weights, inverse to class frequency. Without them the
        # model optimises toward the dominant stable_built class and predicts
        # near-persistence — i.e. it converges on the no-change baseline and
        # fails the macro-F1 half of the gate. Weighting buys recall on the rare
        # transitions at some cost to raw accuracy, which is the intended trade.
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
    """Score one forecaster on a held-out frame.

    Four metrics, deliberately measuring two different things:

    - accuracy — reported for context only. Inflated by class imbalance; the
      no-change baseline scores well here while being useless.
    - macro_f1 — the discrimination metric that gates selection. Averaged
      unweighted over classes, so getting rare transitions right counts as much
      as getting the dominant stable class right.
    - multiclass_brier — the calibration metric that gates selection. Squared
      error against the one-hot truth, so a confident wrong answer is punished
      far more than a hedged one. This matters because the engine *samples* from
      these probabilities; miscalibration corrupts the KPI bands even when the
      argmax label is right.
    - log_loss — a sharper calibration view, reported but not gated. Computed
      against clipped probabilities so a zero on the true class cannot produce
      an infinite score.

    Labels absent from `LABELS` are folded into UNKNOWN so a stray value in the
    truth column cannot silently shift the one-hot matrix.
    """

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
    """A trained forecaster packaged with the provenance needed to trust it.

    The bundle — not the bare forecaster — is what gets persisted and served,
    because a probability is uninterpretable without knowing which model
    produced it, on which data version, and how it scored. `service.py` exposes
    these fields over `/v1/models` so a consumer can audit a forecast after the
    fact.

    Persisted with joblib, which pickles: loading a bundle executes code, so
    only load artifacts you produced. `load` type-checks the result to catch the
    accidental case, not a hostile one.
    """

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
    """Fit every candidate time-forward, then apply the selection gate.

    Train on 2018→2022, test on 2022→2024 — strictly forward in time. Shuffled
    cross-validation is *not* usable here: it would let a building's 2024 state
    inform the model predicting its 2022 state.

    Both endpoints are pinned on each filter. This is not redundant. The panel
    also carries a descriptive (2018, 2024) interval which shares `target_year`
    with the test set and `start_year` with the training set, so filtering on a
    single year would silently pull six-year transitions into a two-year
    benchmark. `test_temporal_benchmark_excludes_direct_2018_to_2024_rows`
    locks this behaviour.

    Returns the selected bundle plus the full per-candidate metrics dict — the
    losers' scores are kept and written alongside the model, so a negative
    result for the learned candidate stays visible instead of being discarded.
    """

    train = panel[
        (panel["start_year"] == 2018) & (panel["target_year"] == 2022)
    ].copy()
    test = panel[
        (panel["start_year"] == 2022) & (panel["target_year"] == 2024)
    ].copy()
    if train.empty or test.empty:
        raise ValueError("temporal benchmark requires 2018→2022 train and 2022→2024 test rows")
    candidates: dict[str, TransitionForecaster] = {
        "no_change": NoChangeForecaster().fit(train),
        "markov": MarkovForecaster().fit(train),
        "gradient": GradientBoostingForecaster().fit(train),
    }
    metrics = {name: evaluate_forecaster(model, test) for name, model in candidates.items()}
    # The gate. The learned model is compared against the *best* baseline on
    # each metric independently (highest F1, lowest Brier) — not against a
    # single overall winner — so it cannot pass by beating whichever baseline
    # happens to be weaker on that axis.
    baseline_f1 = max(metrics["no_change"]["macro_f1"], metrics["markov"]["macro_f1"])
    baseline_brier = min(
        metrics["no_change"]["multiclass_brier"],
        metrics["markov"]["multiclass_brier"],
    )
    # Both conditions required, strictly. Discrimination without calibration
    # gives confident-but-wrong rollouts; calibration without discrimination
    # gives a well-hedged model that predicts nothing useful. Ties go to the
    # baseline: added complexity has to earn its place.
    gradient_passes = (
        metrics["gradient"]["macro_f1"] > baseline_f1
        and metrics["gradient"]["multiclass_brier"] < baseline_brier
    )
    if gradient_passes:
        selected_name = "gradient"
    else:
        # Fall back to the stronger baseline: macro-F1 descending first (hence
        # the negation), Brier ascending as the tiebreak.
        selected_name = min(
            ("no_change", "markov"),
            key=lambda name: (
                -metrics[name]["macro_f1"], metrics[name]["multiclass_brier"]
            ),
        )
    selected = candidates[selected_name]
    production_rows = pd.concat([train, test], ignore_index=True)
    selected.fit(production_rows)
    bundle = ModelBundle.create(
        selected,
        data_version="BBED-2018-2024",
        metrics=metrics[selected_name] | {"selected_model": selected_name},
        training_intervals=["2018-2022", "2022-2024"],
    )
    return bundle, metrics
