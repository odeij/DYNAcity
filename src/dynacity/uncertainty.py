"""Calibration and scenario adjustment primitives.

Array-level helpers the rollout depends on. All three operate in log space and
renormalise, so a probability row that goes in valid comes out valid — the
engine relies on that invariant since it feeds each step's output back in as the
next step's input.

Probabilities are clipped to 1e-12 before every log to keep a legitimate zero
from producing -inf and poisoning the row.
"""

from __future__ import annotations

import numpy as np


class TemperatureCalibrator:
    """Dependency-light probability temperature scaling using a fixed grid search.

    Sharpens (T < 1) or softens (T > 1) an over/under-confident distribution
    without changing which class ranks highest — so it can only improve
    calibration metrics like Brier and log-loss, never the argmax-based macro-F1.

    Fitted by scanning a fixed geometric grid rather than by gradient descent:
    the objective is one-dimensional and smooth, so 101 points over [0.25, 4.0]
    locates the optimum closely enough without adding an optimiser dependency.

    Not currently wired into `temporal_benchmark` — available for the case where
    a candidate discriminates well but fails the Brier half of the gate.
    """

    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature

    @staticmethod
    def transform_values(probabilities: np.ndarray, temperature: float) -> np.ndarray:
        """Apply temperature in log space, then softmax back to probabilities.

        The `logits -= logits.max(...)` line is numerical hygiene, not a
        modelling choice: subtracting the row max before exponentiating prevents
        overflow and cancels out in the normalisation.
        """

        clipped = np.clip(probabilities, 1e-12, 1.0)
        logits = np.log(clipped) / temperature
        logits -= logits.max(axis=1, keepdims=True)
        values = np.exp(logits)
        return values / values.sum(axis=1, keepdims=True)

    def fit(self, probabilities: np.ndarray, truth_indexes: np.ndarray) -> "TemperatureCalibrator":
        """Grid-search the temperature minimising negative log-likelihood.

        Must be fitted on held-out predictions, not on training-set
        predictions — calibrating against data the model has already memorised
        would drive the temperature toward 1 and accomplish nothing.
        """

        best_temperature = 1.0
        best_loss = float("inf")
        for temperature in np.geomspace(0.25, 4.0, 101):
            values = self.transform_values(probabilities, float(temperature))
            loss = -np.mean(np.log(np.clip(values[np.arange(len(values)), truth_indexes], 1e-12, 1.0)))
            if loss < best_loss:
                best_loss = float(loss)
                best_temperature = float(temperature)
        self.temperature = best_temperature
        return self

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        return self.transform_values(probabilities, self.temperature)


def apply_log_odds_adjustment(
    probabilities: np.ndarray,
    target_index: int,
    delta: float,
    mask: np.ndarray,
) -> np.ndarray:
    """Add `delta` to one class's log-odds for the masked rows, then renormalise.

    Log-odds is the right space for a scenario lever: `delta` is scale-free, so
    "+1.5" means the same strength of push whether the target state started at
    1% or 40%, and the result is bounded — a large delta saturates toward
    certainty rather than exceeding 1. Adding to the probability directly would
    do neither.

    Because the row is renormalised, boosting one state necessarily draws mass
    from the others in proportion to what they held. Unmasked rows are returned
    untouched; the input array is copied rather than mutated.
    """

    result = probabilities.copy()
    if not np.any(mask):
        return result
    logits = np.log(np.clip(result[mask], 1e-12, 1.0))
    logits[:, target_index] += delta
    logits -= logits.max(axis=1, keepdims=True)
    adjusted = np.exp(logits)
    result[mask] = adjusted / adjusted.sum(axis=1, keepdims=True)
    return result


def sample_categorical(probabilities: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Draw one class index per row via vectorised inverse-CDF sampling.

    Equivalent to looping `rng.choice` per row, but the rollout calls this with
    `draws × objects` rows at every step, so the loop-free form is what keeps
    forecasts tractable.

    Forcing the last cumulative entry to exactly 1.0 guards the edge case where
    floating-point summation leaves the CDF a hair below 1 and a draw in that
    gap would otherwise index past the final class.
    """

    cumulative = np.cumsum(probabilities, axis=1)
    cumulative[:, -1] = 1.0
    draws = rng.random(len(probabilities))
    return np.sum(draws[:, None] > cumulative, axis=1)

