"""Descriptive summaries for observed BBED status transitions.

Reports what the surveys actually recorded over an interval. Nothing here fits,
scores, or predicts — it is the counterpart to `models.py`, covering the
question "what happened?" rather than "what will happen?".

This is what makes the descriptive (2018, 2024) interval useful without letting
it near a model: the full six-year span can be reported directly instead of
being inferred by chaining two two-year steps, while `temporal_benchmark`
continues to exclude it.

Both canonical and raw-label transitions are returned side by side. The gap
between them is the survey's own label churn — e.g. "Complete Residential" →
"Complete Building" is a raw change with no canonical change. Publishing both
rates makes that noise auditable rather than invisible.
"""

from __future__ import annotations

import pandas as pd


REQUIRED_COLUMNS = {
    "start_year",
    "target_year",
    "current_state",
    "target_state",
    "raw_current_state",
    "raw_target_state",
}


def select_interval(
    panel: pd.DataFrame,
    *,
    start_year: int,
    target_year: int,
) -> pd.DataFrame:
    """Select one exact interval without mixing rows that share an end year.

    Both endpoints are matched, for the same reason `temporal_benchmark` pins
    both: (2018, 2024) shares `target_year` with (2022, 2024) and `start_year`
    with (2018, 2022), so a single-endpoint filter would silently blend
    intervals of different lengths into one summary.

    Raises rather than returning empty — an interval the caller asked for that
    does not exist is a mistake worth surfacing, not a zero-row report.
    """

    missing = sorted(REQUIRED_COLUMNS - set(panel.columns))
    if missing:
        raise ValueError(f"transition panel is missing columns: {missing}")
    selected = panel[
        (panel["start_year"] == start_year)
        & (panel["target_year"] == target_year)
    ].copy()
    if selected.empty:
        raise ValueError(f"no {start_year}→{target_year} transition rows were found")
    return selected


def _counts(frame: pd.DataFrame, column: str) -> list[dict[str, int | str]]:
    counts = frame[column].astype(str).value_counts()
    return [
        {"state": str(state), "count": int(count)}
        for state, count in counts.items()
    ]


def _transition_counts(
    frame: pd.DataFrame,
    current_column: str,
    target_column: str,
) -> list[dict[str, float | int | str]]:
    counts = (
        frame.groupby([current_column, target_column], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(
            ["count", current_column, target_column],
            ascending=[False, True, True],
        )
    )
    total = len(frame)
    return [
        {
            "from_state": str(row[current_column]),
            "to_state": str(row[target_column]),
            "count": int(row["count"]),
            "share": float(row["count"] / total),
        }
        for _, row in counts.iterrows()
    ]


def summarize_interval(
    panel: pd.DataFrame,
    *,
    start_year: int,
    target_year: int,
) -> dict:
    """Return raw-label and canonical transition counts for one interval.

    The returned dict is written to disk verbatim by `summarize-transitions`, so
    it carries `schema_version` and an explicit `notes` list. The notes ship
    inside the payload rather than in documentation because the file outlives
    the context it was produced in — a downstream reader must not be able to
    mistake these counts for model output or causal effects.

    `share` is expressed as a fraction of all rows in the interval, not of the
    originating state, so shares sum to 1 across the whole transition table.
    """

    frame = select_interval(
        panel,
        start_year=start_year,
        target_year=target_year,
    )
    total = len(frame)
    canonical_changed = int((frame["current_state"] != frame["target_state"]).sum())
    raw_changed = int(
        (frame["raw_current_state"] != frame["raw_target_state"]).sum()
    )
    return {
        "schema_version": "1.0",
        "interval": {
            "start_year": start_year,
            "target_year": target_year,
            "years": target_year - start_year,
        },
        "total_rows": total,
        "canonical_changed": canonical_changed,
        "canonical_changed_rate": canonical_changed / total,
        "raw_label_changed": raw_changed,
        "raw_label_changed_rate": raw_changed / total,
        "canonical_start_state_counts": _counts(frame, "current_state"),
        "canonical_target_state_counts": _counts(frame, "target_state"),
        "canonical_transitions": _transition_counts(
            frame, "current_state", "target_state"
        ),
        "raw_transitions": _transition_counts(
            frame, "raw_current_state", "raw_target_state"
        ),
        "notes": [
            "Raw BBED labels changed between survey waves; canonical transitions "
            "remove label-only changes such as Complete Residential to Complete Building.",
            "Counts are descriptive observations, not causal effects.",
        ],
    }
