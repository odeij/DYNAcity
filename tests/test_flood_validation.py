"""Synthetic arithmetic/contract tests, never Beirut ground truth."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pytest

from dynacity.flood_validation import (
    DepthCube, DepthObservation, ExtentObservation, TimingObservation,
    ValidationGrid, evaluate_flood, load_validation_inputs,
    from_hydraulic_output,
)


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
TIMES = [T0 + timedelta(seconds=i * 60) for i in range(3)]
GRID = ValidationGrid("EPSG:32636", "EGM2008", 0, 20, 10, 2, 3)


def cube(**kwargs):
    values = np.array([[[0, 0, 0], [0, 0, 0]],
                       [[0, 0.5, 1], [1.5, 2, 0]],
                       [[0, 1, 2], [3, 4, 0]]], dtype=float)
    return DepthCube(**(dict(grid=GRID, times=TIMES, depth_m=values,
                             valid=np.ones(values.shape, dtype=bool), event_id="synthetic-event",
                             model_id="synthetic-model-v1", provenance="analytic fixture; no field data") | kwargs))


def meta(oid="obs", **kwargs):
    return dict(observation_id=oid, event_id="synthetic-event", group_id="synthetic-group",
                split="holdout", provenance="synthetic fixture", evidence="synthetic",
                independent=True, crs=GRID.crs, vertical_datum=GRID.vertical_datum) | kwargs


def depth(oid="obs", **kwargs):
    return DepthObservation(**(meta(oid) | dict(x_m=15, y_m=15, time=TIMES[1], depth_m=1,
                                                uncertainty_m=0.1) | kwargs))


def extent(**kwargs):
    # Against t=60 model, threshold=0.75: TP=2, FP=1, FN=1, TN=2.
    return ExtentObservation(**(meta() | dict(grid=GRID, time=TIMES[1],
        flooded=np.array([[False, True, True], [False, True, False]]),
        valid=np.ones((2, 3), dtype=bool), uncertain=np.zeros((2, 3), dtype=bool),
        wet_threshold_m=0.75) | kwargs))


def timing(**kwargs):
    return TimingObservation(**(meta() | dict(x_m=15, y_m=15, earliest=TIMES[0] + timedelta(seconds=40),
        latest=TIMES[1], wet_threshold_m=0.2) | kwargs))


def record(report):
    return report["records"][0]


def test_known_depth_errors_bias_mae_rmse_and_uncertainty():
    report = evaluate_flood(cube(), [depth("one"), depth("two", x_m=25, depth_m=0.5,
                                                                       uncertainty_m=0.5)])
    result = report["split_metrics"]["holdout"]["depth"]
    assert result == dict(n=2, bias=0, mae=0.5, rmse=0.5, unit="m", within_observation_uncertainty=0.5)
    assert report["split_metrics"]["calibration"]["depth"]["mae"] is None
    assert not report["calibrated"]
    assert report["measured_observations_evaluated"] == 0
    assert any("absence is not zero" in w for w in report["warnings"])


def test_known_extent_confusion_and_area():
    report = evaluate_flood(cube(), [extent()])
    metrics = report["split_metrics"]["holdout"]["extent"]
    assert {k: metrics["cells"][k] for k in ("tp", "fp", "fn", "tn")} == dict(tp=2, fp=1, fn=1, tn=2)
    assert metrics["csi_iou"] == 0.5
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == pytest.approx(2 / 3)
    assert metrics["f1"] == pytest.approx(2 / 3)
    assert metrics["false_alarm_ratio"] == pytest.approx(1 / 3)
    assert metrics["paired_area_m2"] == 600


def test_extent_unknown_uncertain_and_model_nodata_are_excluded_not_dry():
    model = cube()
    valid = model.valid.copy()
    valid[1, 0, 2] = False  # formerly a TP
    observed_valid = np.ones((2, 3), dtype=bool)
    observed_valid[1, 0] = False  # formerly a FP
    uncertain = np.zeros((2, 3), dtype=bool)
    uncertain[0, 1] = True  # formerly a FN
    report = evaluate_flood(replace(model, valid=valid), [extent(valid=observed_valid, uncertain=uncertain)])
    result = report["split_metrics"]["holdout"]["extent"]
    assert result["cells"] == dict(total=6, observed_valid=5, uncertain=1, eligible=4,
                                    paired=3, model_missing=1, tp=1, fp=0, fn=0, tn=2)
    assert result["paired_coverage"] == 0.75


def test_all_dry_is_not_perfect_iou_and_empty_coverage_is_not_zero():
    model = cube(depth_m=np.zeros((3, 2, 3)))
    result = evaluate_flood(model, [extent(flooded=np.zeros((2, 3), dtype=bool))])
    metrics = result["split_metrics"]["holdout"]["extent"]
    assert metrics["csi_iou"] is None
    assert metrics["accuracy"] == 1
    empty = evaluate_flood(model, [])
    assert empty["split_metrics"]["holdout"]["observation_coverage"] is None
    assert empty["split_metrics"]["holdout"]["extent"]["paired_coverage"] is None


def test_timing_interval_midpoint_error_and_model_bracket():
    report = evaluate_flood(cube(), [timing()])
    row = record(report)
    assert row["error"] == 10
    assert row["within_uncertainty"]
    assert row["onset_intervals_overlap"]
    assert row["model_onset_bracket_utc"] == [t.isoformat() for t in TIMES[:2]]
    assert report["split_metrics"]["holdout"]["timing"]["mae"] == 10


def test_timing_reports_early_and_late_errors():
    early = timing(earliest=TIMES[1] + timedelta(seconds=20), latest=TIMES[2])
    assert record(evaluate_flood(cube(), [early]))["error"] == -40
    late = timing(earliest=TIMES[0], latest=TIMES[0] + timedelta(seconds=20))
    assert record(evaluate_flood(cube(), [late]))["error"] == 50


@pytest.mark.parametrize("changes,expected", [
    ({"x_m": 5}, "onset_not_reached_right_censored"),
    ({"earliest": T0 - timedelta(seconds=1)}, "outside_model_time"),
])
def test_timing_censoring(changes, expected):
    assert record(evaluate_flood(cube(), [timing(**changes)]))["reason"] == expected


def test_left_censored_and_missing_timing():
    model = cube()
    values = model.depth_m.copy()
    values[0, 0, 1] = 1
    assert record(evaluate_flood(replace(model, depth_m=values), [timing()]))["reason"] == "onset_before_start_left_censored"
    valid = model.valid.copy()
    valid[0, 0, 1] = False
    assert record(evaluate_flood(replace(model, valid=valid), [timing()]))["reason"] == "model_nodata_before_onset"


def test_peak_uses_full_window_and_missing_samples_exclude():
    obs = depth(time=TIMES[0], window_end=TIMES[2], depth_m=0.9)
    assert record(evaluate_flood(cube(), [obs]))["error"] == pytest.approx(0.1)
    partial = replace(obs, time=TIMES[0] + timedelta(seconds=1))
    assert record(evaluate_flood(cube(), [partial]))["reason"] == "peak_window_not_fully_sampled"
    valid = cube().valid.copy()
    valid[1, 0, 1] = False
    assert record(evaluate_flood(cube(valid=valid), [obs]))["reason"] == "model_nodata"


@pytest.mark.parametrize("changes,expected", [
    ({"crs": "EPSG:32637"}, "crs_mismatch"),
    ({"vertical_datum": "ellipsoidal"}, "vertical_datum_mismatch"),
    ({"event_id": "another-event"}, "event_mismatch"),
    ({"independent": False}, "not_independent"),
    ({"depth_m": None}, "observation_nodata"),
    ({"x_m": 30}, "outside_model_space"),
    ({"y_m": 0}, "outside_model_space"),
    ({"time": TIMES[0] - timedelta(seconds=1)}, "outside_model_time"),
    ({"time": TIMES[1] + timedelta(seconds=1)}, "time_misaligned"),
])
def test_explicit_exclusions(changes, expected):
    report = evaluate_flood(cube(), [depth(**changes)])
    assert record(report)["reason"] == expected
    assert report["split_metrics"]["holdout"]["exclusions"] == {expected: 1}
    assert report["split_metrics"]["holdout"]["observation_coverage"] == 0


def test_timezones_tolerance_and_no_extrapolation():
    obs = depth(time="2026-01-01T02:01:01+02:00")
    assert record(evaluate_flood(cube(), [obs], time_tolerance_s=1))["predicted_depth_m"] == 0.5
    assert record(evaluate_flood(cube(), [depth(time=TIMES[0] - timedelta(seconds=1))],
                                 time_tolerance_s=60))["reason"] == "outside_model_time"
    with pytest.raises(ValueError, match="timezone"):
        evaluate_flood(cube(), [depth(time="2026-01-01T00:01:00")])


def test_missing_sentinel_and_nan_are_never_zero():
    values = cube().depth_m.copy()
    values[1, 0, 1] = -9999
    assert record(evaluate_flood(cube(depth_m=values, nodata=-9999), [depth()]))["reason"] == "model_nodata"
    values[1, 0, 1] = np.nan
    assert record(evaluate_flood(cube(depth_m=values), [depth()]))["reason"] == "model_nodata"
    values[1, 0, 1] = -1
    with pytest.raises(ValueError, match="negative"):
        cube(depth_m=values)


def test_extent_rejects_spatial_regridding_and_reports_time_failure():
    shifted = replace(GRID, x0=1)
    assert record(evaluate_flood(cube(), [extent(grid=shifted)]))["reason"] == "extent_grid_mismatch"
    report = evaluate_flood(cube(), [extent(time=TIMES[1] + timedelta(seconds=1))])
    assert record(report)["reason"] == "time_misaligned"
    assert report["split_metrics"]["holdout"]["extent"]["paired_coverage"] == 0
    with pytest.raises(ValueError, match="boolean"):
        evaluate_flood(cube(), [extent(valid=np.ones((2, 3)))])


def test_split_leakage_and_duplicates_fail_before_scoring():
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_flood(cube(), [depth(), depth()])
    with pytest.raises(ValueError, match="leakage"):
        evaluate_flood(cube(), [depth(), depth("second", split="calibration", group_id="other")])
    with pytest.raises(ValueError, match="leakage"):
        evaluate_flood(cube(), [depth(), depth("second", split="calibration", event_id="other")])
    with pytest.raises(ValueError, match="used for model calibration"):
        evaluate_flood(cube(calibration_event_ids=("synthetic-event",)), [depth()])
    with pytest.raises(ValueError, match="used for model calibration"):
        evaluate_flood(cube(calibration_group_ids=("synthetic-group",)), [depth()])
    report = evaluate_flood(cube(calibration_event_ids=("synthetic-event",)), [depth(split="calibration")])
    assert report["split_metrics"]["calibration"]["depth"]["n"] == 1
    assert report["split_metrics"]["holdout"]["depth"]["n"] == 0


def test_reproducible_json_report_fingerprint_changes_with_inputs():
    first = evaluate_flood(cube(), [depth()])
    second = evaluate_flood(cube(), [depth()])
    assert first == second
    json.dumps(first, allow_nan=False)
    assert first["input_sha256"] != evaluate_flood(cube(), [depth(depth_m=2)])["input_sha256"]
    assert not cube().depth_m.flags.writeable


def test_json_ingestion_roundtrip_and_null_nodata(tmp_path):
    model = asdict(cube())
    model["times"] = [t.isoformat() for t in TIMES]
    model["depth_m"] = model["depth_m"].tolist()
    model["depth_m"][1][0][1] = None
    model["valid"] = model["valid"].tolist()
    obs = asdict(depth()) | {"kind": "depth", "time": TIMES[1].isoformat()}
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(dict(model=model, observations=[obs])))
    loaded, observations = load_validation_inputs(path)
    assert record(evaluate_flood(loaded, observations))["reason"] == "model_nodata"


def test_hydraulic_adapter_rectangular_grid_and_unresolved_datum():
    from dynacity.flood_surface import HydraulicOutput
    metadata = dict(schema="dynacity.hydraulic-grid.v1", output_kind="precomputed_hydraulic",
        horizontal_units="m", depth_units="m",
        grid=[0, 20, 10, 5], crs=GRID.crs, vertical_datum=GRID.vertical_datum,
        terrain_id="synthetic", terrain_provenance="Synthetic fixture",
        forcing={k: dict(status="none", description="Synthetic fixture")
                 for k in ("rainfall", "boundary", "drainage_exchange")},
        timestamps=TIMES, model=dict(name="external", version="1", run_id="fixture",
                                    source="synthetic fixture", evidence="synthetic"))
    # A validated producer normally supplies ISO timestamps.
    metadata["timestamps"] = [t.isoformat() for t in TIMES]
    output = HydraulicOutput(cube().depth_m, metadata)
    adapted = from_hydraulic_output(output, event_id="synthetic-event")
    assert adapted.model_id == "external:1:fixture"
    assert adapted.grid.cell(15, 14) == (1, 1)
    obs = extent(grid=adapted.grid)
    metrics = evaluate_flood(adapted, [obs])["split_metrics"]["holdout"]["extent"]
    assert metrics["paired_area_m2"] == 300
    metadata["vertical_datum"] = "unknown"
    output = HydraulicOutput(cube().depth_m, metadata)
    adapted = from_hydraulic_output(output, event_id="synthetic-event")
    assert record(evaluate_flood(adapted, [depth(vertical_datum="unknown")]))["reason"] == "vertical_datum_unresolved"


def test_unknown_datum_and_zero_nodata_cannot_silently_score():
    with pytest.raises(ValueError, match="nodata"):
        cube(nodata=0)
    model = cube(grid=replace(GRID, vertical_datum="unknown"))
    assert record(evaluate_flood(model, [depth(vertical_datum="unknown")]))["reason"] == "vertical_datum_unresolved"


@pytest.mark.parametrize("operation", [
    lambda: replace(GRID, crs=""), lambda: replace(GRID, coordinate_unit="degrees"),
    lambda: replace(GRID, cell_m=0), lambda: replace(GRID, rows=1.5),
    lambda: cube(times=list(reversed(TIMES))), lambda: cube(valid=np.ones((3, 2, 3))),
    lambda: evaluate_flood(cube(), [depth(uncertainty_m=-1)]),
    lambda: evaluate_flood(cube(), [depth(depth_m=-1)]),
    lambda: evaluate_flood(cube(), [timing(latest=TIMES[0])]),
    lambda: evaluate_flood(cube(), [], time_tolerance_s=float("nan")),
])
def test_invalid_contracts_fail(operation):
    with pytest.raises(ValueError):
        operation()
