"""Calibration and scenario adjustment primitives."""

from __future__ import annotations

import numpy as np


class TemperatureCalibrator:
    """Dependency-light probability temperature scaling using a fixed grid search."""

    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature

    @staticmethod
    def transform_values(probabilities: np.ndarray, temperature: float) -> np.ndarray:
        clipped = np.clip(probabilities, 1e-12, 1.0)
        logits = np.log(clipped) / temperature
        logits -= logits.max(axis=1, keepdims=True)
        values = np.exp(logits)
        return values / values.sum(axis=1, keepdims=True)

    def fit(self, probabilities: np.ndarray, truth_indexes: np.ndarray) -> "TemperatureCalibrator":
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
    cumulative = np.cumsum(probabilities, axis=1)
    cumulative[:, -1] = 1.0
    draws = rng.random(len(probabilities))
    return np.sum(draws[:, None] > cumulative, axis=1)

