"""Historical Tari'ak temporal-holdout climatology baselines.

Streams the Tari'ak probe-speed CSV, keeps calibration-year and held-out-year
readings apart, fits simple climatology baselines (medians of past readings by
hour / day type / road) from calibration readings only, and scores them against
the held-out years. A baseline is *not* a simulation and says nothing about
SUMO. The calibration p10-p90 band is a spread of individual probe readings,
not a confidence interval or a measurement uncertainty.

The year split, minimum cell count and speed cap come from `BaselineConfig` (defaults in this
module) and are hashed before the source file is read; nothing here tunes them after scores are
computed. That is a property of this process, not an external preregistration.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

SCHEMA = "dynacity.observation_baseline/1"
DEFAULT_CALIBRATION_YEARS = (2015, 2016, 2017)
DEFAULT_HOLDOUT_YEARS = (2018, 2019)
DEFAULT_MIN_COUNT = 20
# Plausibility cap in the file's own numeric speed units (valid under either
# m/s or km/h readings of the Velocity column); larger values are rejected and counted.
DEFAULT_SPEED_CAP = 300.0
BAND_QUANTILES = (0.1, 0.9)
MAX_ROAD_ID_DIGITS = 12  # keeps road * 48 + cell offsets inside int64
SPEED_HISTOGRAM_EDGES = (0.0, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 40.0, 55.6, 80.0, 120.0, 200.0, 300.0)

REJECTION_REASONS = (
    "malformed_row", "missing_date", "bad_date", "missing_time", "bad_time",
    "missing_way_id", "bad_way_id", "missing_speed", "invalid_speed",
    "nonfinite_speed", "negative_speed", "speed_above_cap",
)
BASELINES = {
    "global_hour": "median of all calibration readings at the same local hour",
    "global_daytype_hour": "median of calibration readings at the same day type (Mon-Fri / Sat-Sun) and local hour",
    "road_hour": "median of calibration readings on the same way at the same local hour",
    "road_daytype_hour": "median of calibration readings on the same way, day type and local hour",
    "road_overall": "median of all calibration readings on the same way",
}
LIMITATIONS = (
    "A climatology baseline is not a simulation, not an empirical SUMO validation, and not a target "
    "already achieved. It is a reference a later simulator would have to beat, nothing more.",
    "The calibration p10-p90 band is the spread of individual probe readings. It is not a confidence "
    "interval and not a measurement uncertainty.",
    "The Tari'ak data already informed the existing traffic model (free-flow limit, initial density, "
    "speed check). This split cannot make that model retroactively independent of the data.",
    "The holdout comes from the same probe system as the calibration years. Probe sampling bias "
    "(vehicle types, areas, hours, app users) and spatial dependence between neighbouring ways are unquantified.",
    "Numbers describe Tari'ak probe speeds on mapped OSM ways in the file's observed years, not current "
    "conditions; no season, current-conditions or multimodal (pedestrian, bus, parking) claim is made.",
    "Free flow and congestion are not separable in probe speeds; stationary (zero) readings would be kept here "
    "(this file has none; its maximum of 119.999 suggests upstream filtering at 120, which is not verified), unlike "
    "traffic.py which drops readings below its moving-speed floor, so values are not comparable to it.",
    "Readings are pooled individual probe points: points from one trip or device are not independent, cell medians "
    "are not time-weighted, and nothing here is a survey measurement uncertainty.",
    "Cells pool every calendar date in the configured years: months, seasons, public holidays, school terms and "
    "the weekday mix are not matched between calibration and holdout, and the day type is calendar-only.",
    "Scores are over the ways actually selected (the whole file, or only a supplied traffic-model way set), "
    "so a file-wide score is not a target for any one area and a way-set score covers only ways that have enough readings.",
    "Any tolerance shown is an illustrative setting supplied in the run configuration and not an accepted city accuracy target.",
)


class BaselineInputError(ValueError):
    """The source file or configuration cannot be evaluated."""


@dataclass(frozen=True)
class BaselineConfig:
    calibration_years: tuple = DEFAULT_CALIBRATION_YEARS
    holdout_years: tuple = DEFAULT_HOLDOUT_YEARS
    min_count: int = DEFAULT_MIN_COUNT
    speed_cap: float = DEFAULT_SPEED_CAP
    tolerance_absolute: float | None = None
    tolerance_relative: float | None = None

    def validated(self) -> "BaselineConfig":
        def years(values, name):
            try:
                out = tuple(sorted(set(values)))
            except TypeError as exc:
                raise BaselineInputError(f"{name} must be a list of years") from exc
            if not out:
                raise BaselineInputError(f"{name} must not be empty")
            for y in out:
                if isinstance(y, bool) or not isinstance(y, int) or not 1900 <= y <= 2200:
                    raise BaselineInputError(f"{name} contains an invalid year: {y!r}")
            return out

        cal, hold = years(self.calibration_years, "calibration_years"), years(self.holdout_years, "holdout_years")
        if set(cal) & set(hold):
            raise BaselineInputError(f"calibration and holdout years overlap: {sorted(set(cal) & set(hold))}")
        if min(hold) <= max(cal):
            raise BaselineInputError("holdout years must all be later than every calibration year "
                                     f"(calibration {list(cal)}, holdout {list(hold)}); this is a temporal holdout")
        if isinstance(self.min_count, bool) or not isinstance(self.min_count, int) or self.min_count < 1:
            raise BaselineInputError("min_count must be an integer >= 1")
        if not isinstance(self.speed_cap, (int, float)) or not math.isfinite(self.speed_cap) or self.speed_cap <= 0:
            raise BaselineInputError("speed_cap must be a positive finite number")
        for name in ("tolerance_absolute", "tolerance_relative"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
                raise BaselineInputError(f"{name} must be a finite number >= 0")
        return BaselineConfig(cal, hold, self.min_count, float(self.speed_cap),
                              self.tolerance_absolute, self.tolerance_relative)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["calibration_years"], d["holdout_years"] = list(self.calibration_years), list(self.holdout_years)
        return d

    def deviations_from_defaults(self) -> list[str]:
        defaults = BaselineConfig()
        return [k for k, v in self.as_dict().items() if v != defaults.as_dict()[k]]


def canonical_json(payload: Any) -> str:
    """Deterministic JSON text (insertion order kept, no NaN)."""
    return json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def sha256_file(path: str | Path, block_bytes: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(block_bytes):
            digest.update(block)
    return digest.hexdigest()


def _num(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


# --------------------------------------------------------------------------- road domain

@dataclass(frozen=True)
class RoadDomain:
    """Set of OSM way IDs taken from a traffic model file. Only the IDs are used."""
    ids: np.ndarray  # sorted unique int64
    model_file_name: str
    model_sha256: str
    model_size_bytes: int
    road_segments: int

    def contains(self, roads: np.ndarray) -> np.ndarray:
        return np.isin(roads, self.ids)

    @property
    def id_set_sha256(self) -> str:
        return hashlib.sha256("\n".join(map(str, self.ids.tolist())).encode()).hexdigest()

    def describe(self) -> dict:
        return {
            "mode": "traffic_model_road_id_set",
            "model_file_name": self.model_file_name, "model_sha256": self.model_sha256,
            "model_size_bytes": self.model_size_bytes,
            "road_segments_in_model": self.road_segments, "unique_way_ids": int(len(self.ids)),
            "way_id_set_sha256": self.id_set_sha256,
            "way_id_set_hash_method": "sha256 of ascending decimal way IDs joined by newline, no trailing newline",
            "fields_used_from_model": ["roads[*].way"],
            "note": "Only the way-ID set is used. The model's speeds, readings and density are not read, so they cannot "
                    "enter calibration. Several model segments can share one way ID; IDs are de-duplicated.",
        }


def load_road_domain(path: str | Path) -> RoadDomain:
    path = Path(path)
    if not path.is_file():
        raise BaselineInputError(f"Traffic model not found: {path}")
    digest = sha256_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BaselineInputError(f"Traffic model is not valid JSON: {exc}") from exc
    roads = data.get("roads") if isinstance(data, dict) else None
    if not isinstance(roads, list) or not roads:
        raise BaselineInputError("Traffic model has no non-empty 'roads' list")
    ids = []
    for i, road in enumerate(roads):
        way = road.get("way") if isinstance(road, dict) else None
        if isinstance(way, bool) or not isinstance(way, int) or not 0 < way < 10 ** MAX_ROAD_ID_DIGITS:
            raise BaselineInputError(f"Traffic model roads[{i}] has an invalid way ID: {way!r}")
        ids.append(way)
    return RoadDomain(np.unique(np.asarray(ids, dtype=np.int64)), path.name, digest, path.stat().st_size, len(roads))


# --------------------------------------------------------------------------- ingest

@dataclass
class Readings:
    road: np.ndarray
    hour: np.ndarray
    daytype: np.ndarray  # 0 Mon-Fri, 1 Sat-Sun (calendar only; holidays not modelled)
    speed: np.ndarray


class _Accumulator:
    def __init__(self):
        self.parts = {"calibration": [], "holdout": []}

    def add(self, split, road, hour, daytype, speed):
        if len(road):
            self.parts[split].append((road, hour, daytype, speed))

    def readings(self, split) -> Readings:
        parts = self.parts[split]
        if not parts:
            return Readings(np.empty(0, np.int64), np.empty(0, np.int8), np.empty(0, np.int8), np.empty(0))
        return Readings(*(np.concatenate([p[i] for p in parts]) for i in range(4)))


def _check_header(header: Sequence[str]) -> None:
    names = [h.strip().lower() for h in header]
    if len(names) != 6 or names[0] != "date" or names[1] != "time" or not names[2].startswith("coordinate") \
            or names[4] != "velocity" or names[5] != "osm id":
        raise BaselineInputError(f"Unexpected velocities.csv header: {list(header)!r}")


def _validate_chunk(rows: list[list[str]], speed_cap: float):
    """Return (reason per row as object array, parsed fields for rows without a reason)."""
    import pandas as pd

    frame = pd.DataFrame(rows, columns=["date", "time", "_coord", "_course", "speed", "way"], dtype=object)
    for col in ("date", "time", "speed", "way"):
        frame[col] = frame[col].str.strip()
    date, time_, speed, way = frame["date"], frame["time"], frame["speed"], frame["way"]
    date_ok = date.str.fullmatch(r"\d{4}-\d{2}-\d{2}")
    parsed_date = pd.to_datetime(date.where(date_ok, None), format="%Y-%m-%d", errors="coerce")
    time_ok = time_.str.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d")
    way_ok = way.str.fullmatch(rf"way/\d{{1,{MAX_ROAD_ID_DIGITS}}}")
    number = pd.to_numeric(speed, errors="coerce")
    values = number.to_numpy(dtype=float)
    conditions = [
        date == "", parsed_date.isna().to_numpy(), time_ == "", ~time_ok.to_numpy(dtype=bool),
        way == "", ~way_ok.to_numpy(dtype=bool), speed == "",
        np.isnan(values), np.isinf(values), values < 0, values > speed_cap,
    ]
    reasons = ["missing_date", "bad_date", "missing_time", "bad_time", "missing_way_id", "bad_way_id",
               "missing_speed", "invalid_speed", "nonfinite_speed", "negative_speed", "speed_above_cap"]
    conditions = [np.asarray(c, dtype=bool) for c in conditions]
    reason = np.select(conditions, reasons, default="")
    ok = reason == ""
    parsed = {
        "year": parsed_date.dt.year.to_numpy()[ok].astype(np.int64),
        "weekday": parsed_date.dt.dayofweek.to_numpy()[ok].astype(np.int8),
        "day": parsed_date[ok].values.astype("datetime64[D]").astype(np.int64),
        "hour": time_[ok].str.slice(0, 2).astype(np.int64).to_numpy().astype(np.int8),
        "road": way[ok].str.slice(4).astype(np.int64).to_numpy(),
        "speed": values[ok],
    }
    return reason, parsed


def _read_rows(path: Path, chunk_rows: int) -> Iterable[tuple[list[list[str]], int, int]]:
    """Yield (well-formed 6-field rows, malformed count, total rows) per chunk; header checked first."""
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle, skipinitialspace=True)
        try:
            header = next(reader)
        except StopIteration:
            raise BaselineInputError("velocities.csv is empty") from None
        _check_header(header)
        rows, malformed, total = [], 0, 0
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error:
                row = None
            total += 1
            if row is None or len(row) != 6:
                malformed += 1
            else:
                rows.append(row)
            if total % chunk_rows == 0:
                yield rows, malformed, total
                rows, malformed, total = [], 0, 0
        if total:
            yield rows, malformed, total


def ingest(path: str | Path, config: BaselineConfig, chunk_rows: int = 200_000,
           domain: "RoadDomain | None" = None) -> dict:
    """Stream the CSV once. Memory grows with retained valid split-year readings only.

    With a `domain`, valid rows on ways outside it are counted and dropped before any split,
    year bucketing or fitting; there is no fallback to the file-wide data.
    """
    config = config.validated()
    path = Path(path)
    if chunk_rows < 1:
        raise BaselineInputError("chunk_rows must be >= 1")
    split_of = {**{y: "calibration" for y in config.calibration_years},
                **{y: "holdout" for y in config.holdout_years}}
    acc = _Accumulator()
    rejected, rows_total, valid = Counter(), 0, 0
    by_year, by_hour = Counter(), np.zeros(24, dtype=np.int64)
    days = {"calibration": set(), "holdout": set(), "other": set()}
    in_domain, domain_by_year, domain_roads = 0, Counter(), set()
    hist_edges = np.asarray(SPEED_HISTOGRAM_EDGES)
    hist = np.zeros(len(hist_edges) - 1, dtype=np.int64)
    zero_speed, max_speed, min_day, max_day = 0, None, None, None
    for rows, malformed, total in _read_rows(path, chunk_rows):
        rows_total += total
        rejected["malformed_row"] += malformed
        if not rows:
            continue
        reason, p = _validate_chunk(rows, config.speed_cap)
        for name, count in zip(*np.unique(reason[reason != ""], return_counts=True)):
            rejected[str(name)] += int(count)
        n = len(p["road"])
        valid += n
        if not n:
            continue
        by_hour += np.bincount(p["hour"], minlength=24)
        hist += np.histogram(p["speed"], bins=hist_edges)[0]
        zero_speed += int((p["speed"] == 0).sum())
        max_speed = float(p["speed"].max()) if max_speed is None else max(max_speed, float(p["speed"].max()))
        lo, hi = int(p["day"].min()), int(p["day"].max())
        min_day, max_day = (lo if min_day is None else min(min_day, lo)), (hi if max_day is None else max(max_day, hi))
        for year, count in zip(*np.unique(p["year"], return_counts=True)):
            by_year[int(year)] += int(count)
        if domain is not None:  # file-wide statistics above stay file-wide; everything below is in-domain only
            keep = domain.contains(p["road"])
            p = {k: v[keep] for k, v in p.items()}
        in_domain += len(p["road"])
        for year, count in zip(*np.unique(p["year"], return_counts=True)):
            domain_by_year[int(year)] += int(count)
        domain_roads.update(np.unique(p["road"]).tolist())
        for split in ("calibration", "holdout", "other"):
            years = [y for y, s in split_of.items() if s == split] if split != "other" else None
            mask = np.isin(p["year"], years) if years else ~np.isin(p["year"], list(split_of))
            days[split].update(np.unique(p["day"][mask]).tolist())
            if split != "other":
                acc.add(split, p["road"][mask], p["hour"][mask], (p["weekday"][mask] >= 5).astype(np.int8),
                        p["speed"][mask])
    return {
        "readings": {s: acc.readings(s) for s in ("calibration", "holdout")},
        "rows_total": rows_total, "rows_valid": valid, "rejected": rejected, "by_year": by_year,
        "by_hour": by_hour, "days": days, "histogram": hist, "zero_speed": zero_speed,
        "max_speed": max_speed, "rows_in_domain": in_domain, "domain_by_year": domain_by_year,
        "domain_roads": domain_roads,
        "date_range": [None if d is None else str(np.datetime64(d, "D")) for d in (min_day, max_day)],
        "split_of": split_of,
    }


# --------------------------------------------------------------------------- statistics

def _group_stats(keys: np.ndarray, values: np.ndarray, quantiles: Sequence[float]):
    """Exact per-key counts and linear-interpolated quantiles (numpy 'linear' method)."""
    if not len(keys):
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty((0, len(quantiles)))
    order = np.lexsort((values, keys))
    k, v = keys[order], values[order]
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    counts = np.diff(np.r_[starts, len(k)])
    out = np.empty((len(starts), len(quantiles)))
    for j, q in enumerate(quantiles):
        pos = (counts - 1) * q
        lo, hi = np.floor(pos).astype(np.int64), np.ceil(pos).astype(np.int64)
        frac = pos - lo
        out[:, j] = v[starts + lo] + (v[starts + hi] - v[starts + lo]) * frac
    return k[starts], counts.astype(np.int64), out


def _lookup(sorted_keys: np.ndarray, query: np.ndarray):
    if not len(sorted_keys):
        return np.zeros(len(query), np.int64), np.zeros(len(query), bool)
    idx = np.minimum(np.searchsorted(sorted_keys, query), len(sorted_keys) - 1)
    return idx, sorted_keys[idx] == query


def _keys(name: str, road, daytype, hour):
    road, daytype, hour = (np.asarray(a, dtype=np.int64) for a in (road, daytype, hour))
    return {
        "global_hour": hour,
        "global_daytype_hour": daytype * 24 + hour,
        "road_hour": road * 24 + hour,
        "road_daytype_hour": road * 48 + daytype * 24 + hour,
        "road_overall": road,
    }[name]


def fit_baselines(calibration: Readings, min_count: int) -> dict:
    """Fit every baseline from calibration readings only (no other input exists)."""
    tables = {}
    for name in BASELINES:
        keys = _keys(name, calibration.road, calibration.daytype, calibration.hour)
        k, n, q = _group_stats(keys, calibration.speed, (BAND_QUANTILES[0], 0.5, BAND_QUANTILES[1]))
        tables[name] = {"keys": k, "n": n, "p10": q[:, 0], "median": q[:, 1], "p90": q[:, 2],
                        "supported": n >= min_count}
    return tables


def _metrics(pred, obs, weight, config: BaselineConfig) -> dict:
    n = len(obs)
    if not n:
        return {"cells": 0}
    err = pred - obs
    defined = obs > 0
    ratio = pred[defined] / obs[defined]
    out = {
        "cells": int(n),
        "mae": _num(np.abs(err).mean()),
        "median_abs_error": _num(np.median(np.abs(err))),
        "mean_signed_error_pred_minus_obs": _num(err.mean()),
        "reading_weighted_mae": _num((np.abs(err) * weight).sum() / weight.sum()),
        "median_ratio_pred_over_obs": _num(np.median(ratio)) if len(ratio) else None,
        "ratio_cells_defined": int(defined.sum()),
        "ratio_cells_excluded_observed_zero": int((~defined).sum()),
    }
    if config.tolerance_absolute is not None or config.tolerance_relative is not None:
        allowed = (config.tolerance_absolute or 0.0) + (config.tolerance_relative or 0.0) * np.abs(obs)
        out["illustrative_tolerance"] = {
            "absolute": config.tolerance_absolute, "relative": config.tolerance_relative,
            "status": "supplied_illustrative_setting_not_an_accepted_accuracy_target",
            "fraction_of_cells_within": _num((np.abs(err) <= allowed).mean()),
        }
    return out


def score(tables: dict, holdout: Readings, config: BaselineConfig) -> dict:
    """Score fitted baselines against held-out (road, day type, hour) cells."""
    cell_keys = _keys("road_daytype_hour", holdout.road, holdout.daytype, holdout.hour)
    k, n, q = _group_stats(cell_keys, holdout.speed, (0.5,))
    cell_median = q[:, 0]
    eligible = n >= config.min_count
    road, daytype, hour = k // 48, (k // 24) % 2, k % 24
    obs, weight = cell_median[eligible], n[eligible].astype(float)
    e_road, e_dt, e_hour = road[eligible], daytype[eligible], hour[eligible]
    reading_cell = np.searchsorted(k, cell_keys)
    reading_eligible = eligible[reading_cell]
    result = {
        "heldout_readings": int(len(holdout.speed)),
        "heldout_cells_total": int(len(k)),
        "heldout_cells_eligible": int(eligible.sum()),
        "heldout_cells_below_min_count": int((~eligible).sum()),
        "heldout_readings_in_eligible_cells": int(n[eligible].sum()),
        "heldout_readings_in_below_min_count_cells": int(n[~eligible].sum()),
    }
    support, preds = {}, {}
    for name, table in tables.items():
        cell_q = _keys(name, e_road, e_dt, e_hour)
        idx, found = _lookup(table["keys"], cell_q)
        ok = found & table["supported"][idx] if len(idx) else found
        support[name], preds[name] = ok, (idx, table)
    common = np.logical_and.reduce(list(support.values())) if support else np.zeros(len(obs), bool)
    road_present = np.isin(e_road, np.unique(tables["road_overall"]["keys"]))
    baselines = {}
    for name, ok in support.items():
        idx, table = preds[name]
        pred = table["median"][idx[ok]]
        entry = {
            "description": BASELINES[name],
            "scored_cells": int(ok.sum()),
            "coverage_of_eligible_cells": _num(ok.mean()) if len(ok) else None,
            "unscored_cells_no_calibration_support": int((~ok).sum()),
            "unscored_cells_road_absent_from_calibration": int((~ok & ~road_present).sum()) if name.startswith("road") else 0,
            "metrics": _metrics(pred, obs[ok], weight[ok], config),
        }
        # Optional p10-p90 band of calibration probe readings (not a confidence interval).
        lo, hi = table["p10"][idx[ok]], table["p90"][idx[ok]]
        band = {"band": "calibration_probe_reading_p10_p90",
                "note": "spread of individual calibration readings; not a confidence interval or measurement uncertainty"}
        band["heldout_cell_median_inside"] = _num(((obs[ok] >= lo) & (obs[ok] <= hi)).mean()) if ok.any() else None
        # Reading level: each held-out reading in a scored cell against its group's band.
        scored_cell_index = np.full(len(k), -1, dtype=np.int64)
        eligible_pos = np.flatnonzero(eligible)
        scored_cell_index[eligible_pos[ok]] = np.arange(int(ok.sum()))
        r_idx = scored_cell_index[reading_cell]
        in_scored = reading_eligible & (r_idx >= 0)
        if in_scored.any():
            rv, j = holdout.speed[in_scored], r_idx[in_scored]
            band["heldout_readings_inside"] = _num(((rv >= lo[j]) & (rv <= hi[j])).mean())
            band["heldout_readings_evaluated"] = int(in_scored.sum())
        else:
            band["heldout_readings_inside"], band["heldout_readings_evaluated"] = None, 0
        entry["calibration_band_p10_p90"] = band
        # Same-denominator comparison across every baseline.
        baselines[name] = entry
        entry["_ok"] = ok
    for name, entry in baselines.items():
        ok = entry.pop("_ok")
        idx, table = preds[name]
        pick = common[ok] if ok.any() else np.zeros(0, bool)
        pred_all = table["median"][idx[ok]]
        entry["metrics_on_common_cells"] = _metrics(pred_all[pick], obs[ok][pick], weight[ok][pick], config)
    result["common_scored_cells"] = int(common.sum())
    result["baselines"] = baselines
    return result


# --------------------------------------------------------------------------- source metadata

def read_source_metadata(velocities: Path) -> dict:
    """License / unit evidence from the neighbouring Tari'ak data package, if present."""
    folder = velocities.parent
    out: dict[str, Any] = {"datapackage": "not_found", "license_file": "not_found"}
    package = folder / "datapackage.json"
    if package.is_file():
        data = json.loads(package.read_text(encoding="utf-8"))
        fields = {}
        for resource in data.get("resources", []):
            if resource.get("name") == "velocities":
                fields = {f["name"]: f for f in resource.get("schema", {}).get("fields", [])}
        description = fields.get("Velocity", {}).get("description", "")
        low = description.lower()
        out["datapackage"] = {
            "sha256": sha256_file(package),
            "title": data.get("title"), "version": data.get("version"),
            "declared_period_whole_dataset": data.get("description"),
            "licenses": [lic.get("id") for lic in data.get("licenses", [])],
            "sources": [s.get("title") for s in data.get("sources", [])],
            "authors": [c.get("title") for c in data.get("contributors", [])],
            "declared_timezone_text": fields.get("Time", {}).get("description"),
            "velocity_field_description": description,
            "resource_description": next((r.get("description") for r in data.get("resources", []) if r.get("name") == "velocities"), None),
            "declared_velocity_unit": "m/s" if "meters/second" in low and "not kilometers" in low else "unknown",
        }
    licence = folder / "LICENSE.md"
    if licence.is_file():
        out["license_file"] = {"sha256": sha256_file(licence), "text_starts": licence.read_text(encoding="utf-8")[:40].strip()}
    return out


def _unit_evidence(ingested: dict, declared: str) -> dict:
    edges, hist = SPEED_HISTOGRAM_EDGES, ingested["histogram"]
    total = int(hist.sum())
    over_ms_plausible = int(hist[edges.index(55.6):].sum())  # >= 55.6 numeric (200 km/h if the unit were km/h)
    over_40 = int(hist[edges.index(40.0):].sum())
    return {
        "declared_by_data_package": declared,
        "traffic_py_default_assumption": "km/h (traffic.py velocity_unit default); the docs there cite forecasting-engine docs, not the data package",
        "conflict": "data package says m/s; repository code assumes km/h" if declared == "m/s" else "no declared unit found",
        "valid_readings": total,
        "histogram_edges_source_numeric": list(edges),
        "histogram_counts": [int(c) for c in hist],
        "readings_at_or_above_40": over_40, "share_at_or_above_40": _num(over_40 / total) if total else None,
        "readings_at_or_above_55_6": over_ms_plausible,
        "max_valid_value": _num(ingested["max_speed"]),
        "interpretation": "A large share of values above 40 would be implausible for m/s (144 km/h) but ordinary for km/h; "
                          "this is circumstantial evidence only and does not settle the unit.",
        "assumption_used": "none: MAE is reported in the file's own numeric speed units; ratios are unit-free",
    }


# --------------------------------------------------------------------------- driver

def evaluate(velocities: str | Path, config: BaselineConfig | None = None, chunk_rows: int = 200_000,
             source_sha256: str | None = None, processing_notes: Sequence[str] = (),
             domain: RoadDomain | None = None) -> dict:
    """Run the whole evaluation and return a deterministic report (no timestamps)."""
    config = (config or BaselineConfig()).validated()
    velocities = Path(velocities)
    if not velocities.is_file():
        raise BaselineInputError(f"Source file not found: {velocities}")
    config_text = canonical_json({**config.as_dict(),
                                  "domain_way_id_set_sha256": domain.id_set_sha256 if domain else None})
    config_hash = hashlib.sha256(config_text.encode()).hexdigest()  # computed before any row is read
    digest = source_sha256 or sha256_file(velocities)
    meta = read_source_metadata(velocities)
    ing = ingest(velocities, config, chunk_rows, domain)
    cal, hold = ing["readings"]["calibration"], ing["readings"]["holdout"]
    status, blocked_reason, scores = "completed", None, None
    if not len(cal.speed):
        status, blocked_reason = "blocked", "no_calibration_observations"
    elif not len(hold.speed):
        status, blocked_reason = "blocked", "no_held_out_observations"
    else:
        tables = fit_baselines(cal, config.min_count)
        scores = score(tables, hold, config)
        if not scores["heldout_cells_eligible"]:
            status, blocked_reason, scores = "blocked", "no_eligible_held_out_cells", {
                k: v for k, v in scores.items() if k != "baselines"}
    declared = meta["datapackage"]["declared_velocity_unit"] if isinstance(meta["datapackage"], dict) else "unknown"
    rejected = {r: int(ing["rejected"].get(r, 0)) for r in REJECTION_REASONS}
    years = sorted(ing["by_year"])
    in_domain = ing["rows_in_domain"]
    domain_info = domain.describe() if domain else {
        "mode": "source_file_wide",
        "note": "All valid rows of the source file are used. The data package says the resource is confined to the country "
                "of Lebanon; the geographic footprint of this file is not verified here, and the date range says nothing "
                "about geography. Scores are file-wide and are not Beirut targets."}
    domain_info.update(
        selected_valid_rows=int(in_domain), valid_rows_outside_domain=int(ing["rows_valid"] - in_domain),
        selected_unique_ways_with_valid_rows=len(ing["domain_roads"]),
        domain_ways_without_valid_rows=(int(len(domain.ids)) - len(ing["domain_roads"])) if domain else None,
        counts_meaning="selected_valid_rows are valid rows on selected ways in any year; split-year counts below refer to them; "
                       "malformed rows and valid rows outside the domain are separate from split-year exclusions",
        selected_valid_rows_by_year={str(y): int(ing["domain_by_year"][y]) for y in sorted(ing["domain_by_year"])})
    first, last = ing["date_range"]
    return {
        "schema": SCHEMA,
        "status": status,
        "blocked_reason": blocked_reason,
        "label": "historical_baseline",
        "interpretation": "Historical climatology baseline on Tari'ak probe speeds "
                          + ("restricted to the ways of a supplied traffic model" if domain else "over the whole source file (Lebanon dataset, footprint unverified)")
                          + ". Not a simulation, not SUMO validation, not an accepted accuracy target. See limitations.",
        "domain": domain_info,
        "limitations": list(LIMITATIONS),
        "config": {
            **config.as_dict(),
            "config_sha256": config_hash,
            "fixed_in_configuration_before_computation": True,
            "policy": "Years, minimum cell count and speed cap are fixed in this run's configuration (code defaults unless "
                      "overridden on the command line) and hashed before the source is read; nothing is tuned after scores "
                      "are computed in this process.",
            "preregistration": "none. This is not an external preregistration and it gives no evidence either way about "
                               "earlier human inspection of the data; the existing traffic model already used this dataset.",
            "deviations_from_code_defaults": config.deviations_from_defaults(),
            "code_defaults": BaselineConfig().as_dict(),
            "holdout_strictly_after_calibration": True,
            "cell_definition": "(OSM way, day type, local hour); day type is calendar Mon-Fri vs Sat-Sun, holidays not modelled",
            "min_count_rule": "a held-out cell is scored only if it has >= min_count readings, and a calibration group "
                              "supports a prediction only if it has >= min_count calibration readings; otherwise unscored and counted",
        },
        "source": {
            "file_name": velocities.name,
            "size_bytes": velocities.stat().st_size,
            "sha256": digest,
            "dataset": "Lebanon Traffic Dataset (Tari'ak)",
            "license": "ODbL-1.0 (Open Data Commons Open Database License)",
            "attribution": "Traffic speeds: Lebanon Traffic Dataset (Tari'ak), ODbL. Roads (c) OpenStreetMap contributors, ODbL.",
            "package_metadata": meta,
            "time_basis": "Date and Time columns are Asia/Beirut local clock time per the data package; the local hour is used "
                          "as recorded and no UTC conversion or daylight-saving disambiguation is done.",
            "dataset_period_declared_whole_dataset": "2014-2019 (data package description, all of Lebanon)",
            "geographic_coverage_declared": meta["datapackage"].get("resource_description") if isinstance(meta["datapackage"], dict) else None,
            "geographic_footprint_of_this_file": "not verified here; the date range does not establish geography",
            "date_range_observed_in_this_file": {
                "first_valid_date": first, "last_valid_date": last,
                "years_with_valid_rows": years, "valid_rows_in_2014": int(ing["by_year"].get(2014, 0)),
                "note": "The declared 2014 start is not present as rows in this file." if 2014 not in ing["by_year"] else
                        "Rows from 2014 are present.",
            },
            "velocity_unit_evidence": _unit_evidence(ing, declared),
        },
        "rows": {
            "total_data_rows": ing["rows_total"], "valid_rows": ing["rows_valid"],
            "valid_rows_scope_note": "valid_rows, by-year and by-hour counts and distinct-date counts for 'other' are file-wide; see domain for selected rows",
            "rejected_rows": int(sum(rejected.values())),
            "rejected_by_first_failing_check": rejected,
            "rejection_note": "Each rejected row is counted once, under the first failing check in the listed order.",
            "valid_rows_by_year": {str(y): int(ing["by_year"][y]) for y in years},
            "valid_rows_by_local_hour": [int(c) for c in ing["by_hour"]],
            "zero_speed_valid_rows": ing["zero_speed"],
            "valid_rows_in_calibration_years": int(len(cal.speed)),
            "valid_rows_in_holdout_years": int(len(hold.speed)),
            "calibration_and_holdout_rows_are": "in the selected domain only",
            "valid_rows_outside_domain_excluded": int(ing["rows_valid"] - in_domain),
            "valid_rows_in_domain_outside_both_splits_excluded": int(in_domain - len(cal.speed) - len(hold.speed)),
            "distinct_dates": {k: len(v) for k, v in ing["days"].items()},
        },
        "baselines_defined": BASELINES,
        "results": scores,
        "lineage": {
            "module": "dynacity.observation_baseline",
            "module_sha256": sha256_file(Path(__file__)),
            "steps": [
                "hash source in 1 MiB blocks", "stream rows with csv.reader in fixed-size chunks",
                "validate every row (shape, date, time, way id, speed) and count rejections",
                *(["restrict valid rows to the traffic model's unique way IDs before splitting or fitting"] if domain else []),
                "keep valid readings of configured calibration and holdout years only",
                "fit baselines from calibration readings only", "score held-out cells", *processing_notes,
            ],
            "numpy": np.__version__, "pandas": __import__("pandas").__version__,
            "chunk_rows_affects_result": False,
        },
    }
