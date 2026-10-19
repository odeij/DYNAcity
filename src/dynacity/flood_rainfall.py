"""Validated, spatially uniform rainfall forcing; no runoff or hydraulic solver.

Rates are interval averages on [start, end), normalized to UTC and mm/hour.
Unknown rainfall is never converted to zero. All depths are precipitation depths,
not surface-water depths. No return-period inference is provided.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal

RainfallKind = Literal["hypothetical", "observed", "estimated"]
RainfallUnit = Literal["mm/h", "m/s", "in/h", "mm", "m", "in"]

__all__ = [
    "RainfallKind", "RainfallUnit", "RainfallProvenance", "RainfallInterval",
    "RainfallStep", "RainfallForcing", "hyetograph", "import_rainfall_csv",
]


def _number(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps require an explicit UTC offset/time zone")
    return value.astimezone(timezone.utc)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _timestep(value: float) -> timedelta:
    seconds = _number(value, "timestep_seconds", positive=True)
    try:
        delta = timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("timestep exceeds datetime duration range") from exc
    if delta <= timedelta(0) or delta.total_seconds() != seconds:
        raise ValueError("timestep must be representable as whole microseconds")
    return delta


@dataclass(frozen=True)
class RainfallProvenance:
    """Source supplied by caller, not an assertion of independent verification.

    Observed series require a station ID. Keep private locations/files outside Git.
    Notes should describe QC, original timestamp zone and spatial applicability.
    """

    kind: RainfallKind
    source: str
    station_id: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("hypothetical", "observed", "estimated"):
            raise ValueError("kind must be hypothetical, observed or estimated")
        _text(self.source, "source")
        if self.station_id is not None:
            _text(self.station_id, "station_id")
        if self.kind == "observed" and self.station_id is None:
            raise ValueError("observed rainfall requires a station_id")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string")


@dataclass(frozen=True)
class RainfallInterval:
    start: datetime
    end: datetime
    intensity_mm_h: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", _utc(self.start))
        object.__setattr__(self, "end", _utc(self.end))
        if self.end <= self.start:
            raise ValueError("rainfall interval end must follow start")
        object.__setattr__(self, "intensity_mm_h", _number(self.intensity_mm_h, "intensity"))
        if not math.isfinite(self.depth_mm):
            raise ValueError("interval depth overflows")

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def depth_mm(self) -> float:
        return self.intensity_mm_h * (self.duration_seconds / 3600.0)

    @property
    def intensity_m_s(self) -> float:
        return self.intensity_mm_h / 3_600_000.0


@dataclass(frozen=True)
class RainfallStep:
    """Solver-ready interval with event cumulative precipitation at its end."""

    start: datetime
    end: datetime
    duration_seconds: float
    intensity_mm_h: float
    depth_mm: float
    cumulative_depth_mm: float

    @property
    def depth_m(self) -> float:
        return self.depth_mm / 1000.0

    @property
    def intensity_m_s(self) -> float:
        return self.intensity_mm_h / 3_600_000.0


@dataclass(frozen=True)
class RainfallForcing:
    intervals: tuple[RainfallInterval, ...]
    provenance: RainfallProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "intervals", tuple(self.intervals))
        if not isinstance(self.provenance, RainfallProvenance):
            raise ValueError("provenance must be RainfallProvenance")
        if not self.intervals or not all(isinstance(i, RainfallInterval) for i in self.intervals):
            raise ValueError("at least one RainfallInterval is required")
        for previous, current in zip(self.intervals, self.intervals[1:]):
            if current.start != previous.end:
                raise ValueError("intervals must be ordered and contiguous; gaps/overlaps are invalid")
        try:
            total = self.total_depth_mm
        except OverflowError as exc:
            raise ValueError("total rainfall depth overflows") from exc
        if not math.isfinite(total):
            raise ValueError("total rainfall depth overflows")

    @property
    def start(self) -> datetime:
        return self.intervals[0].start

    @property
    def end(self) -> datetime:
        return self.intervals[-1].end

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def total_depth_mm(self) -> float:
        return math.fsum(i.depth_mm for i in self.intervals)

    @property
    def timestep_seconds(self) -> float | None:
        """Native timestep, or None for explicitly irregular intervals."""
        first = self.intervals[0].duration_seconds
        return first if all(i.duration_seconds == first for i in self.intervals) else None

    @property
    def warnings(self) -> tuple[str, ...]:
        return (
            f"Rainfall is {self.provenance.kind}; source and quality are caller-declared.",
            "Uniform areal forcing: station representativeness is not validated.",
            "No verified local IDF source or return period is attached; no Beirut calibration.",
        )

    def depth_between(self, start: datetime, end: datetime) -> float:
        """Integrated mm within coverage; out-of-coverage queries fail, even if dry."""
        start, end = _utc(start), _utc(end)
        if end < start or start < self.start or end > self.end:
            raise ValueError("query must be ordered and entirely inside rainfall coverage")
        return math.fsum(
            i.intensity_mm_h * ((min(end, i.end) - max(start, i.start)).total_seconds() / 3600)
            for i in self.intervals if i.start < end and i.end > start
        )

    def cumulative_depth_mm(self, at: datetime) -> float:
        return self.depth_between(self.start, at)

    def steps(self, timestep_seconds: float) -> tuple[RainfallStep, ...]:
        """Conservative overlap integration; the final step can be shorter.

        Subdivision assumes a constant rate inside each source interval and cannot
        recover subinterval peaks. Coarsening preserves depth, not peak intensity.
        """
        delta = _timestep(timestep_seconds)
        steps = []
        start = self.start
        index = 0
        cumulative = 0.0
        while start < self.end:
            end = start + min(delta, self.end - start)
            pieces = []
            while index < len(self.intervals):
                interval = self.intervals[index]
                if interval.start >= end:
                    break
                overlap = (min(end, interval.end) - max(start, interval.start)).total_seconds()
                pieces.append(interval.intensity_mm_h * (overlap / 3600))
                if interval.end > end:
                    break
                index += 1
            depth = math.fsum(pieces)
            cumulative = math.fsum((cumulative, depth))
            duration = (end - start).total_seconds()
            steps.append(RainfallStep(start, end, duration, depth / duration * 3600, depth, cumulative))
            start = end
        return tuple(steps)


def _rate(value: float, unit: RainfallUnit, seconds: float) -> float:
    value = _number(value, "rainfall value")
    if unit == "mm/h":
        return value
    if unit == "m/s":
        return value * 3_600_000
    if unit == "in/h":
        return value * 25.4
    if unit in ("mm", "m", "in"):
        depth = value * {"mm": 1, "m": 1000, "in": 25.4}[unit]
        return depth / seconds * 3600
    raise ValueError("unit must be mm/h, m/s, in/h, mm, m or in")


def hyetograph(
    *, start: datetime, values: Iterable[float], timestep_seconds: float,
    unit: RainfallUnit, provenance: RainfallProvenance,
) -> RainfallForcing:
    """Build a regular, start-of-interval series; depth units mean per-interval depth."""
    start = _utc(start)
    delta = _timestep(timestep_seconds)
    seconds = delta.total_seconds()
    intervals = []
    for value in values:
        end = start + delta
        intervals.append(RainfallInterval(start, end, _rate(value, unit, seconds)))
        start = end
    return RainfallForcing(tuple(intervals), provenance)


def import_rainfall_csv(
    path: str | Path, *, unit: RainfallUnit, provenance: RainfallProvenance,
) -> RainfallForcing:
    """Read UTF-8 CSV with exactly start,end,value; ISO 8601 aware timestamps.

    Each row declares its full measurement interval. Gaps, blank values, duplicate
    timestamps and invalid measurements fail without sorting or filling. Convert
    station-specific exports explicitly into this schema; cumulative gauges must
    be differenced with reset/QC handling upstream. No file is copied or written.
    """
    intervals = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or sorted(reader.fieldnames) != ["end", "start", "value"]:
            raise ValueError("CSV requires exactly start,end,value columns")
        for row_number, row in enumerate(reader, start=2):
            try:
                if None in row or any(value is None or not value.strip() for value in row.values()):
                    raise ValueError("missing or extra CSV fields")
                start = _utc(datetime.fromisoformat(row["start"]))
                end = _utc(datetime.fromisoformat(row["end"]))
                seconds = (end - start).total_seconds()
                if seconds <= 0:
                    raise ValueError("end must follow start")
                intervals.append(RainfallInterval(start, end, _rate(float(row["value"]), unit, seconds)))
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"invalid rainfall CSV row {row_number}: {exc}") from exc
    return RainfallForcing(tuple(intervals), provenance)
