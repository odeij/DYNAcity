"""Reproducible comparison of precomputed depths with independent observations.

No solver, reprojection, datum conversion, calibration or inferred observations
are performed here. All distances/depths are metres; all times are aware UTC.
See docs/flood/validation.md for the input contract and metric definitions.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Literal, Sequence

import numpy as np


def _time(value: datetime | str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def _positive(value: float, name: str, *, zero: bool = False) -> None:
    if not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")


@dataclass(frozen=True)
class ValidationGrid:
    """North-up regular grid; x0/y0 are west/north OUTER edges, row 0 north.

    CRS must be an explicitly declared projected CRS whose coordinates use m.
    CRS equivalence is strict string equality (no implicit axis/datum transforms).
    """
    crs: str
    vertical_datum: str
    x0: float
    y0: float
    cell_m: float
    rows: int
    cols: int
    coordinate_unit: str = "m"
    cell_y_m: float | None = None

    def __post_init__(self) -> None:
        if not self.crs.strip() or not self.vertical_datum.strip():
            raise ValueError("explicit CRS and vertical datum are required")
        if self.coordinate_unit != "m" or self.crs.upper() in {"EPSG:4326", "CRS:84"}:
            raise ValueError("grid requires a projected CRS with metre coordinates")
        _positive(self.cell_m, "cell_m")
        if self.cell_y_m is not None:
            _positive(self.cell_y_m, "cell_y_m")
        if not all(math.isfinite(v) for v in (self.x0, self.y0)):
            raise ValueError("grid origin must be finite")
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in (self.rows, self.cols)):
            raise ValueError("rows and cols must be positive integers")

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        col = math.floor((x - self.x0) / self.cell_m)
        row = math.floor((self.y0 - y) / self.dy_m)
        return (row, col) if 0 <= row < self.rows and 0 <= col < self.cols else None

    @property
    def dy_m(self) -> float:
        return self.cell_m if self.cell_y_m is None else self.cell_y_m


@dataclass(frozen=True)
class DepthCube:
    """Depth above local ground, shape (time, row, col); invalid != dry.

    nodata sentinel and nonfinite values are masked even when valid=True.
    Explicitly false validity masks negative/sentinel model cells too.
    """
    grid: ValidationGrid
    times: Sequence[datetime | str]
    depth_m: np.ndarray
    valid: np.ndarray
    event_id: str
    model_id: str
    provenance: str
    nodata: float | None = None
    calibration_event_ids: tuple[str, ...] = ()
    calibration_group_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        times = tuple(_time(t) for t in self.times)
        if not times or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("model times must be nonempty and strictly increasing")
        if not all(s.strip() for s in (self.event_id, self.model_id, self.provenance)):
            raise ValueError("event, model identity and provenance are required")
        depth = np.array(self.depth_m, dtype=float, copy=True)
        valid = np.array(self.valid, copy=True)
        if valid.dtype != bool or depth.shape != (len(times), self.grid.rows, self.grid.cols) or valid.shape != depth.shape:
            raise ValueError("depth and boolean valid arrays must have shape (time, row, col)")
        valid &= np.isfinite(depth)
        if self.nodata is not None:
            if not math.isfinite(self.nodata) or self.nodata >= 0:
                raise ValueError("nodata must be a finite negative sentinel or None")
            valid &= depth != self.nodata
        if np.any(depth[valid] < 0):
            raise ValueError("valid water depths cannot be negative")
        depth[~valid] = np.nan
        depth.setflags(write=False)
        valid.setflags(write=False)
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "depth_m", depth)
        object.__setattr__(self, "valid", valid)


@dataclass(frozen=True, kw_only=True)
class Observation:
    observation_id: str
    event_id: str
    group_id: str
    split: Literal["calibration", "holdout"]
    provenance: str
    evidence: Literal["measured", "estimated", "synthetic"]
    independent: bool
    crs: str
    vertical_datum: str

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.observation_id, self.event_id, self.group_id,
                                      self.provenance, self.crs, self.vertical_datum)):
            raise ValueError("observation identity, group, spatial references and provenance are required")
        if self.split not in {"calibration", "holdout"} or self.evidence not in {"measured", "estimated", "synthetic"}:
            raise ValueError("invalid split or evidence class")
        if not isinstance(self.independent, bool):
            raise ValueError("independent must be boolean")


@dataclass(frozen=True, kw_only=True)
class DepthObservation(Observation):
    x_m: float
    y_m: float
    time: datetime | str
    depth_m: float | None
    uncertainty_m: float
    # If supplied, compare sampled maximum over [time, window_end], not a snapshot.
    window_end: datetime | str | None = None


@dataclass(frozen=True, kw_only=True)
class ExtentObservation(Observation):
    grid: ValidationGrid
    time: datetime | str
    flooded: np.ndarray
    valid: np.ndarray
    uncertain: np.ndarray
    # Meaning of a wet label must match the model threshold, strictly depth > threshold.
    wet_threshold_m: float


@dataclass(frozen=True, kw_only=True)
class TimingObservation(Observation):
    x_m: float
    y_m: float
    earliest: datetime | str
    latest: datetime | str
    wet_threshold_m: float


def _metrics(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "bias": None, "mae": None, "rmse": None}
    a = np.asarray(errors)
    return {"n": len(errors), "bias": float(a.mean()), "mae": float(np.abs(a).mean()),
            "rmse": float(np.sqrt(np.mean(a * a)))}


def _ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, datetime):
        return _time(value).isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _snapshot(cube: DepthCube, when, tolerance_s: float) -> tuple[int | None, str | None]:
    when = _time(when)
    if not cube.times[0] <= when <= cube.times[-1]:
        return None, "outside_model_time"
    delta = np.asarray([abs((t - when).total_seconds()) for t in cube.times])
    nearest = int(delta.argmin())  # deterministic ties choose the earlier sample
    return (nearest, None) if delta[nearest] <= tolerance_s else (None, "time_misaligned")


def evaluate_flood(
    cube: DepthCube,
    observations: Sequence[DepthObservation | ExtentObservation | TimingObservation],
    *,
    time_tolerance_s: float = 0.0,
) -> dict:
    """Return a JSON-safe report, with per-record exclusions and per-split metrics.

    Reject duplicated IDs and split leakage before evaluation. Inputs are never
    mutated. No acceptance threshold or claim of calibration is inferred.
    """
    _positive(time_tolerance_s, "time_tolerance_s", zero=True)
    ids: set[str] = set()
    splits: dict[tuple[str, str], str] = {}
    for obs in observations:
        if not isinstance(obs, (DepthObservation, ExtentObservation, TimingObservation)):
            raise TypeError("unsupported observation type")
        if obs.observation_id in ids:
            raise ValueError("duplicate observation_id")
        ids.add(obs.observation_id)
        for key in (("event", obs.event_id), ("group", obs.group_id)):
            if key in splits and splits[key] != obs.split:
                raise ValueError("calibration/holdout leakage: event or group overlaps")
            splits[key] = obs.split
        if obs.split == "holdout" and (obs.event_id in cube.calibration_event_ids or
                                       obs.group_id in cube.calibration_group_ids):
            raise ValueError("holdout was used for model calibration")

    records = []
    for obs in observations:
        kind = "depth" if isinstance(obs, DepthObservation) else "extent" if isinstance(obs, ExtentObservation) else "timing"
        record = {"observation_id": obs.observation_id, "event_id": obs.event_id,
                  "group_id": obs.group_id, "split": obs.split, "kind": kind,
                  "evidence": obs.evidence, "provenance": obs.provenance, "status": "excluded",
                  "observation_crs": obs.crs, "observation_vertical_datum": obs.vertical_datum}
        if isinstance(obs, TimingObservation):
            record["observed_onset_interval_utc"] = [_time(obs.earliest).isoformat(), _time(obs.latest).isoformat()]
        else:
            record["observation_time_utc"] = _time(obs.time).isoformat()
        if isinstance(obs, DepthObservation) and obs.window_end is not None:
            record["observation_window_end_utc"] = _time(obs.window_end).isoformat()
        records.append(record)
        reason = ("not_independent" if not obs.independent else
                  "event_mismatch" if obs.event_id != cube.event_id else
                  "crs_mismatch" if obs.crs != cube.grid.crs else
                  "vertical_datum_unresolved" if any(v.strip().lower() in {"unknown", "unspecified", "missing"}
                                                      for v in (obs.vertical_datum, cube.grid.vertical_datum)) else
                  "vertical_datum_mismatch" if obs.vertical_datum != cube.grid.vertical_datum else None)
        if reason:
            record["reason"] = reason
            continue
        if isinstance(obs, ExtentObservation):
            _evaluate_extent(cube, obs, record, time_tolerance_s)
        else:
            cell = cube.grid.cell(obs.x_m, obs.y_m)
            if cell is None:
                record["reason"] = "outside_model_space"
                continue
            record["model_cell_row_col"] = list(cell)
            if isinstance(obs, DepthObservation):
                _evaluate_depth(cube, obs, cell, record, time_tolerance_s)
            else:
                _evaluate_timing(cube, obs, cell, record)

    summaries = {}
    for split in ("calibration", "holdout"):
        subset = [r for r in records if r["split"] == split]
        evaluated = [r for r in subset if r["status"] == "evaluated"]
        summary = {"observations_total": len(subset), "observations_evaluated": len(evaluated),
                   "observation_coverage": _ratio(len(evaluated), len(subset)),
                   "evaluated_evidence_counts": dict(Counter(r["evidence"] for r in evaluated)),
                   "exclusions": dict(Counter(r["reason"] for r in subset if r["status"] == "excluded"))}
        for kind, unit in (("depth", "m"), ("timing", "s")):
            samples = [r for r in evaluated if r["kind"] == kind]
            summary[kind] = {**_metrics([r["error"] for r in samples]), "unit": unit,
                             "within_observation_uncertainty": _ratio(sum(r["within_uncertainty"] for r in samples), len(samples))}
        extents = [r for r in subset if r["kind"] == "extent" and "cells" in r]
        counts = {key: sum(r["cells"][key] for r in extents) for key in
                  ("total", "observed_valid", "uncertain", "eligible", "paired", "model_missing", "tp", "fp", "fn", "tn")}
        tp, fp, fn, tn = (counts[k] for k in ("tp", "fp", "fn", "tn"))
        summary["extent"] = {"cells": counts, "paired_coverage": _ratio(counts["paired"], counts["eligible"]),
                             "csi_iou": _ratio(tp, tp + fp + fn), "precision": _ratio(tp, tp + fp),
                             "recall": _ratio(tp, tp + fn), "false_alarm_ratio": _ratio(fp, tp + fp),
                             "f1": _ratio(2 * tp, 2 * tp + fp + fn),
                             "accuracy": _ratio(tp + tn, tp + fp + fn + tn),
                             "paired_area_m2": counts["paired"] * cube.grid.cell_m * cube.grid.dy_m}
        summaries[split] = summary
    empirical = sum(r["status"] == "evaluated" and r["evidence"] == "measured" for r in records)
    return {"schema_version": "1.0", "workflow": "independent_flood_comparison",
            "model_id": cube.model_id, "model_provenance": cube.provenance, "event_id": cube.event_id,
            "grid": asdict(cube.grid), "model_times_utc": [t.isoformat() for t in cube.times],
            "calibration_event_ids": list(cube.calibration_event_ids),
            "calibration_group_ids": list(cube.calibration_group_ids),
            "input_sha256": _digest({"model": asdict(cube), "observations": [asdict(o) for o in observations]}),
            "time_tolerance_s": time_tolerance_s, "split_metrics": summaries, "records": records,
            "measured_observations_evaluated": empirical, "calibrated": False,
            "warnings": ["Metrics do not establish calibration or fitness for safety decisions.",
                         "Point sampling uses containing cells; peaks/onset are limited by model sampling.",
                         "Observation uncertainty is a supplied bound, not a confidence probability."] +
                        (["No independent measured observations were evaluated; absence is not zero flooding."] if not empirical else [])}


def _evaluate_depth(cube, obs, cell, record, tolerance):
    _positive(obs.uncertainty_m, "uncertainty_m", zero=True)
    if obs.depth_m is None or not math.isfinite(obs.depth_m):
        record["reason"] = "observation_nodata"
        return
    _positive(obs.depth_m, "observed depth_m", zero=True)
    start = _time(obs.time)
    if obs.window_end is None:
        index, reason = _snapshot(cube, start, tolerance)
        if reason:
            record["reason"] = reason
            return
        indices = [index]
    else:
        end = _time(obs.window_end)
        if end <= start:
            raise ValueError("peak window_end must be after time")
        # Exact endpoints ensure a peak cannot be scored against a partial event.
        if start not in cube.times or end not in cube.times:
            record["reason"] = "peak_window_not_fully_sampled"
            return
        indices = [i for i, t in enumerate(cube.times) if start <= t <= end]
    row, col = cell
    if not cube.valid[indices, row, col].all():
        record["reason"] = "model_nodata"
        return
    prediction = float(cube.depth_m[indices, row, col].max())
    error = prediction - obs.depth_m
    record.update(status="evaluated", observed_depth_m=obs.depth_m, predicted_depth_m=prediction,
                  error=error, uncertainty_m=obs.uncertainty_m,
                  within_uncertainty=abs(error) <= obs.uncertainty_m + 1e-12,
                  model_times_utc=[cube.times[i].isoformat() for i in indices])


def _evaluate_extent(cube, obs, record, tolerance):
    _positive(obs.wet_threshold_m, "wet_threshold_m", zero=True)
    if obs.grid != cube.grid or obs.crs != obs.grid.crs or obs.vertical_datum != obs.grid.vertical_datum:
        record["reason"] = "extent_grid_mismatch"
        return
    arrays = [np.asarray(a) for a in (obs.flooded, obs.valid, obs.uncertain)]
    if any(a.shape != (cube.grid.rows, cube.grid.cols) or a.dtype != bool for a in arrays):
        raise ValueError("extent flooded/valid/uncertain must be boolean arrays matching grid")
    wet, valid, uncertain = arrays
    eligible = valid & ~uncertain
    counts = {"total": wet.size, "observed_valid": int(valid.sum()),
              "uncertain": int((valid & uncertain).sum()), "eligible": int(eligible.sum()),
              "paired": 0, "model_missing": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0}
    record["cells"] = counts
    index, reason = _snapshot(cube, obs.time, tolerance)
    if reason:
        record["reason"] = reason
        return
    paired = eligible & cube.valid[index]
    counts["paired"] = int(paired.sum())
    counts["model_missing"] = int((eligible & ~cube.valid[index]).sum())
    predicted = cube.depth_m[index] > obs.wet_threshold_m
    for key, mask in (("tp", predicted & wet), ("fp", predicted & ~wet),
                      ("fn", ~predicted & wet), ("tn", ~predicted & ~wet)):
        counts[key] = int((paired & mask).sum())
    if not paired.any():
        record["reason"] = "no_paired_extent_cells"
        return
    record.update(status="evaluated", model_time_utc=cube.times[index].isoformat(),
                  wet_threshold_m=obs.wet_threshold_m)


def _evaluate_timing(cube, obs, cell, record):
    _positive(obs.wet_threshold_m, "wet_threshold_m", zero=True)
    earliest, latest = _time(obs.earliest), _time(obs.latest)
    if latest < earliest:
        raise ValueError("latest onset must be at or after earliest")
    if earliest < cube.times[0] or latest > cube.times[-1]:
        record["reason"] = "outside_model_time"
        return
    row, col = cell
    wet = cube.valid[:, row, col] & (cube.depth_m[:, row, col] > obs.wet_threshold_m)
    indices = np.flatnonzero(wet)
    stop = int(indices[0]) if indices.size else len(cube.times) - 1
    if not cube.valid[:stop + 1, row, col].all():
        record["reason"] = "model_nodata_before_onset"
        return
    if not indices.size:
        record["reason"] = "onset_not_reached_right_censored"
        return
    if stop == 0:
        record["reason"] = "onset_before_start_left_censored"
        return
    prediction = cube.times[stop]
    midpoint = earliest + (latest - earliest) / 2
    record.update(status="evaluated", predicted_onset_utc=prediction.isoformat(),
                  model_onset_bracket_utc=[cube.times[stop - 1].isoformat(), prediction.isoformat()],
                  observed_onset_interval_utc=[earliest.isoformat(), latest.isoformat()],
                  error=(prediction - midpoint).total_seconds(),
                  within_uncertainty=earliest <= prediction <= latest,
                  onset_intervals_overlap=cube.times[stop - 1] <= latest and prediction >= earliest,
                  wet_threshold_m=obs.wet_threshold_m)


def load_validation_inputs(path: str | Path) -> tuple[DepthCube, list[Observation]]:
    """Load explicit JSON arrays/metadata. Keep real input JSON outside Git.

    JSON null model depths become masked NaN; null observed depths stay missing.
    Unknown constructor fields fail, rather than silently dropping metadata.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    model = dict(payload["model"])
    model["grid"] = ValidationGrid(**model["grid"])
    model["depth_m"] = np.asarray(model["depth_m"], dtype=float)
    model["valid"] = np.asarray(model["valid"])
    observations = []
    classes = {"depth": DepthObservation, "extent": ExtentObservation, "timing": TimingObservation}
    for item in payload["observations"]:
        item = dict(item)
        kind = item.pop("kind")
        if kind not in classes:
            raise ValueError(f"unsupported observation kind: {kind}")
        if kind == "extent":
            item["grid"] = ValidationGrid(**item["grid"])
            for key in ("flooded", "valid", "uncertain"):
                item[key] = np.asarray(item[key])
        observations.append(classes[kind](**item))
    return DepthCube(**model), observations


def from_hydraulic_output(
    output,
    *,
    event_id: str,
    calibration_event_ids: tuple[str, ...] = (),
    calibration_group_ids: tuple[str, ...] = (),
) -> DepthCube:
    """Adapt flood_surface.HydraulicOutput with no resampling or invented times.

    Requires the surface module's validated metre/north-up metadata contract.
    Unknown vertical datum is retained but comparisons will be excluded.
    Event and calibration lineage are caller-supplied; never infer from run ID.
    """
    metadata = output.metadata
    if (metadata.get("schema") != "dynacity.hydraulic-grid.v1" or
            metadata.get("output_kind") != "precomputed_hydraulic" or
            metadata.get("horizontal_units") != "m" or metadata.get("depth_units") != "m"):
        raise ValueError("expected validated HydraulicOutput metre grid contract")
    x0, y0, dx, dy = metadata["grid"]
    _, rows, cols = output.depth_m.shape
    grid = ValidationGrid(metadata["crs"], metadata["vertical_datum"], x0, y0, dx, rows, cols,
                          cell_y_m=dy if dy != dx else None)
    model = metadata["model"]
    return DepthCube(grid, metadata["timestamps"], output.depth_m, output.valid, event_id,
                     f"{model['name']}:{model['version']}:{model['run_id']}",
                     json.dumps(metadata, sort_keys=True, allow_nan=False),
                     calibration_event_ids=calibration_event_ids,
                     calibration_group_ids=calibration_group_ids)
