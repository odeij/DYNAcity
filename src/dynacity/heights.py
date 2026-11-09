"""One height priority order for every building the viewer draws.

Order, best first (the first source a building has wins):

1. ``recorded``  a stored height: the Beirut 001 model height (roof minus model
   ground) for model buildings, else the BBED ``Building_Hight_m``, else an
   OSM / Overture ``height`` tag.
2. ``floors``    a floor count times ``STOREY_HEIGHT_M`` (BBED ``NoofFloor``,
   Overture ``num_floors``, OSM ``building:levels``).
3. ``size_bin``  the median recorded height of buildings with a similar
   footprint area (BBED medians for BBED, Beirut 001 medians for context).
4. ``fallback``  an illustrative constant, only when nothing above exists.

Everything but ``recorded`` is an estimate and is flagged ``estimated``.

Why the Beirut 001 model beats BBED where both exist: on the 669 provisional
links with both heights, BBED heights are almost exactly floors x 3 m (median
ratio 3.00 over all 3,150 BBED buildings with both fields; 89 % are exactly
3.0 per floor), so they carry no more information than the floor count, while
the model height is measured from its own roof geometry. They differ by a
median 7.3 m (model taller by 5.4 m median), so the viewer shows both.

``STOREY_HEIGHT_M`` is fitted to BBED: heights recorded next to floor counts
(n = 3,150) give 3.0 m per floor with a held-out median error of 0.0 m and mean
absolute error 0.23 m, against 1.77 m for the earlier 3.2 m. An intercept for a
ground floor or roof does not help (OLS 0.16 m). ``fit_storey_height`` is the
code behind that number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

STOREY_HEIGHT_M = 3.0
FALLBACK_HEIGHT_M = 6.0
# Footprint-area bin edges (m2) shared by BBED and context buildings.
AREA_BINS = (0.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, math.inf)
MIN_BIN_COUNT = 5
MIN_MAPPED_HEIGHT_M, MAX_MAPPED_HEIGHT_M = 2.0, 300.0
MAX_FLOORS = 80

RECORDED, FLOORS, SIZE_BIN, FALLBACK = "recorded", "floors", "size_bin", "fallback"
KINDS = (RECORDED, FLOORS, SIZE_BIN, FALLBACK)


@dataclass(frozen=True)
class Height:
    height_m: float
    kind: str
    source: str  # label shown on hover

    @property
    def estimated(self) -> bool:
        return self.kind != RECORDED


def _positive(value, low: float = 0.0, high: float = math.inf) -> float | None:
    """A number in (low, high] or None; BBED's -999 / 0 placeholders become None."""
    try:
        number = float(str(value).split()[0]) if isinstance(value, str) else float(value)
    except (TypeError, ValueError, IndexError):
        return None
    return number if math.isfinite(number) and low < number <= high else None


def floors_height(floors: float) -> float:
    return floors * STOREY_HEIGHT_M


def area_bin(area_m2: float) -> int:
    return next(i for i, (low, high) in enumerate(zip(AREA_BINS, AREA_BINS[1:])) if low <= area_m2 < high)


def bin_medians(areas: Sequence[float], heights: Sequence[float]) -> list[float] | None:
    """Median height per footprint-area bin; sparse bins take the overall median.
    None when there are too few buildings to say anything."""
    pairs = [(a, h) for a, h in zip(areas, heights) if _positive(a) and _positive(h)]
    if len(pairs) < MIN_BIN_COUNT:
        return None
    a, h = np.array(pairs, float).T
    overall = float(np.median(h))
    out = []
    for low, high in zip(AREA_BINS, AREA_BINS[1:]):
        chosen = h[(a >= low) & (a < high)]
        out.append(float(np.median(chosen)) if len(chosen) >= MIN_BIN_COUNT else overall)
    return out


def resolve_height(*, recorded=None, recorded_label: str = "Recorded height",
                   floors=None, area_m2=None, medians: Sequence[float] | None = None,
                   median_label: str = "similar footprint area") -> Height:
    """Apply the priority order to one building. Inputs may be None or placeholder values."""
    height = _positive(recorded, 0.0, MAX_MAPPED_HEIGHT_M)
    if height is not None:
        return Height(height, RECORDED, recorded_label)
    n = _positive(floors, 0.99, MAX_FLOORS)
    if n is not None:
        return Height(floors_height(n), FLOORS, f"Estimated from {n:g} floors × {STOREY_HEIGHT_M:g} m")
    area = _positive(area_m2)
    if medians and area is not None:
        return Height(medians[area_bin(area)], SIZE_BIN, f"Estimated: median recorded height for {median_label}")
    return Height(FALLBACK_HEIGHT_M, FALLBACK, f"Illustrative fallback ({FALLBACK_HEIGHT_M:g} m)")


def mapped_height(value) -> float | None:
    """An OSM / Overture `height` tag in metres (2 to 300 m), else None."""
    return _positive(value, MIN_MAPPED_HEIGHT_M - 1e-9, MAX_MAPPED_HEIGHT_M)


def _errors(pred: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    e = pred - truth
    return {"n": int(len(e)), "median_abs": float(np.median(np.abs(e))), "mae": float(np.abs(e).mean()),
            "bias_mean": float(e.mean()), "p90_abs": float(np.percentile(np.abs(e), 90))}


def fit_storey_height(floors: Sequence[float], heights: Sequence[float], *, folds: int = 5, seed: int = 0) -> dict:
    """Metres per floor from buildings with both values, judged on held-out folds.

    Candidates: the old constant, a median ratio, and a least-squares line with
    an intercept. Returns each candidate's held-out error and the winner (the
    lowest held-out mean absolute error; the old constant wins ties).
    """
    f = np.asarray(floors, float)
    h = np.asarray(heights, float)
    keep = (f > 0) & (h > 0)
    f, h = f[keep], h[keep]
    if len(f) < folds * 2:
        raise ValueError("too few buildings with both floors and height")
    parts = np.array_split(np.random.default_rng(seed).permutation(len(f)), folds)

    def ratio(train):
        s = float(np.median(h[train] / f[train]))
        return lambda x: s * x, {"slope": s, "intercept": 0.0}

    def line(train):
        s, i = np.polyfit(f[train], h[train], 1)
        return lambda x: s * x + i, {"slope": float(s), "intercept": float(i)}

    models = {"constant_3.2": lambda train: ((lambda x: 3.2 * x), {"slope": 3.2, "intercept": 0.0}),
              "median_ratio": ratio, "ols_with_intercept": line}
    report = {}
    for name, fit in models.items():
        pred = np.empty(len(f))
        for k in range(folds):
            train = np.concatenate([parts[j] for j in range(folds) if j != k])
            pred[parts[k]] = fit(train)[0](f[parts[k]])
        _, params = fit(np.arange(len(f)))
        report[name] = {**_errors(pred, h), **params}
    best = min(report, key=lambda n: (report[n]["mae"], n != "constant_3.2"))
    return {"models": report, "best": best, "n": int(len(f))}
