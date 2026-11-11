"""Tari'ak temporal-holdout baseline: synthetic CSV fixtures only (never real results)."""
import csv
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from dynacity.observation_baseline import (
    BaselineConfig, BaselineInputError, canonical_json, evaluate, fit_baselines, ingest, load_road_domain,
)

HEADER = ["Date", "Time", "Coordinate\t (Lon, Lat)", "Course", "Velocity", "OSM ID"]


def write_csv(path, rows, header=HEADER):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        for date, time_, speed, way in rows:
            writer.writerow([date, time_, "35.5,33.9", "10", speed, way])
    return path


def rows_for(date, hour, way, speeds):
    return [(date, f"{hour:02d}:10:00", s, f"way/{way}") for s in speeds]


# 2015-03-02 and 2016-03-07 are Mondays.
def known_rows():
    cal = (rows_for("2015-03-02", 8, 1, [10, 20, 30]) + rows_for("2015-03-02", 9, 1, [40, 60])
           + rows_for("2015-03-02", 8, 2, [100, 100]))
    hold = (rows_for("2016-03-07", 8, 1, [24, 26]) + rows_for("2016-03-07", 9, 1, [55, 45])
            + rows_for("2016-03-07", 8, 2, [90, 110]) + rows_for("2016-03-07", 8, 3, [5, 7])
            + rows_for("2016-03-07", 10, 1, [33]))  # single reading: below min count
    return cal + hold


CONFIG = BaselineConfig((2015,), (2016,), 2, 300.0)


def test_known_numeric_results(tmp_path):
    report = evaluate(write_csv(tmp_path / "v.csv", known_rows()), CONFIG)
    assert report["status"] == "completed"
    res = report["results"]
    assert (res["heldout_cells_total"], res["heldout_cells_eligible"], res["heldout_cells_below_min_count"]) == (5, 4, 1)
    assert res["heldout_readings_in_below_min_count_cells"] == 1
    assert res["common_scored_cells"] == 3
    rh = res["baselines"]["road_hour"]
    assert rh["scored_cells"] == 3 and rh["unscored_cells_road_absent_from_calibration"] == 1
    assert rh["coverage_of_eligible_cells"] == 0.75
    assert rh["metrics"]["mae"] == pytest.approx(5 / 3, abs=1e-6)
    assert rh["metrics"]["median_ratio_pred_over_obs"] == 1.0
    assert rh["calibration_band_p10_p90"]["heldout_cell_median_inside"] == 1.0
    assert rh["calibration_band_p10_p90"]["heldout_readings_inside"] == pytest.approx(4 / 6, abs=1e-6)
    gh = res["baselines"]["global_hour"]
    assert gh["scored_cells"] == 4 and gh["metrics"]["mae"] == 24.75
    assert gh["metrics_on_common_cells"]["mae"] == 25.0
    assert res["baselines"]["road_overall"]["metrics"]["mae"] == pytest.approx(25 / 3, abs=1e-6)
    assert report["label"] == "historical_baseline"


def test_zero_observed_excluded_from_ratio_and_counted(tmp_path):
    rows = rows_for("2015-03-02", 8, 1, [0, 0]) + rows_for("2016-03-07", 8, 1, [0, 0]) \
        + rows_for("2015-03-02", 9, 1, [10, 10]) + rows_for("2016-03-07", 9, 1, [20, 20])
    report = evaluate(write_csv(tmp_path / "v.csv", rows), CONFIG)
    m = report["results"]["baselines"]["road_hour"]["metrics"]
    assert m["ratio_cells_excluded_observed_zero"] == 1 and m["ratio_cells_defined"] == 1
    assert m["median_ratio_pred_over_obs"] == 0.5
    assert report["rows"]["zero_speed_valid_rows"] == 4


def test_tolerance_is_labelled_illustrative(tmp_path):
    cfg = BaselineConfig((2015,), (2016,), 2, 300.0, tolerance_absolute=1.0)
    report = evaluate(write_csv(tmp_path / "v.csv", known_rows()), cfg)
    tol = report["results"]["baselines"]["road_hour"]["metrics"]["illustrative_tolerance"]
    assert tol["fraction_of_cells_within"] == pytest.approx(2 / 3, abs=1e-6)
    assert "not_an_accepted" in tol["status"]


def test_holdout_perturbation_cannot_change_calibration_fit(tmp_path):
    base = known_rows()
    changed = [r if not r[0].startswith("2016") else (r[0], r[1], str(float(r[2]) * 3 + 1), r[3]) for r in base]
    a, b = write_csv(tmp_path / "a.csv", base), write_csv(tmp_path / "b.csv", changed)
    fit = {}
    for name, path in (("a", a), ("b", b)):
        cal = ingest(path, CONFIG)["readings"]["calibration"]
        fit[name] = fit_baselines(cal, 2)
    for table in fit["a"]:
        for key in ("keys", "n", "p10", "median", "p90"):
            assert np.array_equal(fit["a"][table][key], fit["b"][table][key])
    ra, rb = evaluate(a, CONFIG), evaluate(b, CONFIG)
    assert ra["results"]["baselines"]["road_hour"]["metrics"] != rb["results"]["baselines"]["road_hour"]["metrics"]
    assert ra["rows"]["valid_rows_in_calibration_years"] == rb["rows"]["valid_rows_in_calibration_years"]


def test_backwards_or_interleaved_split_is_refused(tmp_path):
    path = write_csv(tmp_path / "v.csv", known_rows())
    assert evaluate(path, CONFIG)["config"]["holdout_strictly_after_calibration"] is True
    for cal, hold in (((2016,), (2015,)), ((2015, 2017), (2016,)), ((2016,), (2016, 2017))):
        with pytest.raises(BaselineInputError):
            evaluate(path, BaselineConfig(cal, hold, 2, 300.0))


def test_years_must_be_disjoint_valid_and_nonempty():
    for bad in (BaselineConfig((2015, 2016), (2016, 2017)), BaselineConfig((), (2018,)),
                BaselineConfig((2015,), (1500,)), BaselineConfig((2015,), (2018,), 0),
                BaselineConfig((2015,), (2018,), 5, float("nan")), BaselineConfig((2015,), (2018,), 5, 300.0, -1.0)):
        with pytest.raises(BaselineInputError):
            bad.validated()


def test_missing_holdout_reports_blocked_without_scores(tmp_path):
    rows = rows_for("2015-03-02", 8, 1, [10, 20])
    report = evaluate(write_csv(tmp_path / "v.csv", rows), CONFIG)
    assert report["status"] == "blocked" and report["blocked_reason"] == "no_held_out_observations"
    assert report["results"] is None
    no_cal = evaluate(write_csv(tmp_path / "w.csv", rows_for("2016-03-07", 8, 1, [10, 20])), CONFIG)
    assert no_cal["blocked_reason"] == "no_calibration_observations"


def test_holdout_without_eligible_cells_is_blocked(tmp_path):
    rows = rows_for("2015-03-02", 8, 1, [10, 20]) + rows_for("2016-03-07", 8, 1, [30])
    report = evaluate(write_csv(tmp_path / "v.csv", rows), CONFIG)
    assert report["status"] == "blocked" and report["blocked_reason"] == "no_eligible_held_out_cells"
    assert report["results"]["heldout_cells_below_min_count"] == 1 and "baselines" not in report["results"]


def test_bad_rows_are_rejected_and_counted_once(tmp_path):
    good = rows_for("2015-03-02", 8, 1, [10, 20])
    bad = [
        ("", "08:00:00", "5", "way/1"), ("2015-02-30", "08:00:00", "5", "way/1"), ("15-03-02", "08:00:00", "5", "way/1"),
        ("2015-03-02", "", "5", "way/1"), ("2015-03-02", "24:00:00", "5", "way/1"), ("2015-03-02", "08:60:00", "5", "way/1"),
        ("2015-03-02", "08:00:00", "5", ""), ("2015-03-02", "08:00:00", "5", "node/5"),
        ("2015-03-02", "08:00:00", "", "way/1"), ("2015-03-02", "08:00:00", "abc", "way/1"),
        ("2015-03-02", "08:00:00", "nan", "way/1"), ("2015-03-02", "08:00:00", "inf", "way/1"),
        ("2015-03-02", "08:00:00", "-1", "way/1"), ("2015-03-02", "08:00:00", "1000", "way/1"),
        ("", "", "", ""),  # first failing check wins: missing_date
    ]
    path = write_csv(tmp_path / "v.csv", good + bad + rows_for("2014-05-01", 8, 1, [9]))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('"2015-03-02","08:00:00","1"\n\n')  # short row and blank line
    report = evaluate(path, CONFIG)
    rej = report["rows"]["rejected_by_first_failing_check"]
    assert rej["malformed_row"] == 2
    assert rej["missing_date"] == 2 and rej["bad_date"] == 2 and rej["missing_time"] == 1 and rej["bad_time"] == 2
    assert rej["missing_way_id"] == 1 and rej["bad_way_id"] == 1 and rej["missing_speed"] == 1
    assert rej["invalid_speed"] == 2 and rej["nonfinite_speed"] == 1 and rej["negative_speed"] == 1
    assert rej["speed_above_cap"] == 1
    rows = report["rows"]
    assert rows["rejected_rows"] == sum(rej.values()) == 17
    assert rows["total_data_rows"] == rows["valid_rows"] + rows["rejected_rows"] == 20
    assert rows["valid_rows_in_domain_outside_both_splits_excluded"] == 1  # the 2014 row is valid but in neither split
    assert report["source"]["date_range_observed_in_this_file"]["valid_rows_in_2014"] == 1


def test_bad_header_or_missing_file_fails_loudly(tmp_path):
    with pytest.raises(BaselineInputError):
        evaluate(write_csv(tmp_path / "v.csv", [], header=["a", "b"]), CONFIG)
    with pytest.raises(BaselineInputError):
        evaluate(tmp_path / "missing.csv", CONFIG)


def test_chunk_size_and_rerun_do_not_change_bytes(tmp_path):
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(600):
        year = int(rng.choice([2014, 2015, 2016, 2017]))
        rows.append((f"{year}-{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 28)):02d}",
                     f"{int(rng.integers(0, 24)):02d}:{int(rng.integers(0, 60)):02d}:00",
                     f"{rng.uniform(0, 90):.3f}", f"way/{int(rng.integers(1, 6))}"))
    rows += [("2015-01-01", "99:00:00", "5", "way/1"), ("2015-01-01", "01:00:00", "x", "way/1")]
    path = write_csv(tmp_path / "v.csv", rows)
    cfg = BaselineConfig((2015, 2016), (2017,), 3, 300.0)
    texts = {canonical_json(evaluate(path, cfg, chunk_rows=n)) for n in (1, 7, 128, 10_000)}
    assert len(texts) == 1
    assert json.loads(texts.pop())["status"] == "completed"


def test_cli_script_writes_only_to_requested_folder(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "evaluate_tariak_baseline", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_tariak_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = write_csv(tmp_path / "v.csv", known_rows())
    out = tmp_path / "out"
    args = ["--velocities", str(source), "--output", str(out), "--calibration-years", "2015",
            "--holdout-years", "2016", "--min-count", "2"]
    assert module.main(args) == 0
    first = (out / "observation-baseline.json").read_bytes()
    assert module.main(args) == 0
    assert (out / "observation-baseline.json").read_bytes() == first
    meta = json.loads((out / "run-metadata.json").read_text())
    assert len(meta["runs"]) == 2 and "peak_memory" in meta["runs"][0]
    assert {p.name for p in out.iterdir()} == {"observation-baseline.json", "results.md", "run-metadata.json", "manifest.json"}


def load_script():
    spec = importlib.util.spec_from_file_location(
        "evaluate_tariak_baseline", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_tariak_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir()}


def test_completed_output_survives_missing_blocked_changed_and_unreadable_reruns(tmp_path, capsys):
    module = load_script()
    source = write_csv(tmp_path / "v.csv", known_rows())
    out = tmp_path / "out"
    base = ["--velocities", str(source), "--output", str(out), "--calibration-years", "2015",
            "--holdout-years", "2016", "--min-count", "2"]
    assert module.main(base) == 0
    before = snapshot(out)
    # Missing source: clean message, no traceback, nothing written.
    assert module.main(["--velocities", str(tmp_path / "nope.csv"), *base[2:]]) == 1
    assert "nothing was written" in capsys.readouterr().err
    # Source that is a directory (unreadable): same.
    assert module.main(["--velocities", str(tmp_path), *base[2:]]) == 1
    # Missing traffic model.
    assert module.main([*base, "--traffic-model", str(tmp_path / "bad.json")]) == 1
    # Blocked run with a different configuration: refused, completed files intact.
    capsys.readouterr()
    assert module.main(base[:4] + ["--calibration-years", "2020", "--holdout-years", "2021", "--min-count", "2"]) == 3
    assert "new --output folder" in capsys.readouterr().err
    # Changed configuration, changed source, and a way-set domain are all refused.
    assert module.main(base[:-2] + ["--min-count", "3"]) == 3
    other = write_csv(tmp_path / "w.csv", known_rows() + rows_for("2016-03-07", 8, 1, [40]))
    assert module.main(["--velocities", str(other), *base[2:]]) == 3
    assert module.main([*base, "--traffic-model", str(write_model(tmp_path / "m.json", [1, 2]))]) == 3
    assert snapshot(out) == before
    # Same identity: deterministic rerun is allowed and keeps the JSON bytes.
    assert module.main(base) == 0
    assert (out / "observation-baseline.json").read_bytes() == before["observation-baseline.json"]
    assert len(json.loads((out / "run-metadata.json").read_text())["runs"]) == 2


def test_unreadable_existing_result_is_not_overwritten(tmp_path):
    module = load_script()
    out = tmp_path / "out"
    out.mkdir()
    (out / "observation-baseline.json").write_text("not json")
    source = write_csv(tmp_path / "v.csv", known_rows())
    assert module.main(["--velocities", str(source), "--output", str(out), "--calibration-years", "2015",
                        "--holdout-years", "2016", "--min-count", "2"]) == 3
    assert (out / "observation-baseline.json").read_text() == "not json"


def test_blocked_output_may_be_replaced_by_a_different_run(tmp_path):
    module = load_script()
    out = tmp_path / "out"
    source = write_csv(tmp_path / "v.csv", known_rows())
    args = ["--velocities", str(source), "--output", str(out), "--min-count", "2"]
    assert module.main(args + ["--calibration-years", "2020", "--holdout-years", "2021"]) == 2
    assert module.main(args + ["--calibration-years", "2015", "--holdout-years", "2016"]) == 0
    assert json.loads((out / "observation-baseline.json").read_text())["status"] == "completed"


def write_model(path, ways, speed=30.0, **extra):
    roads = [{"way": w, "speed": [[speed] * 24], "readings": [999], "name": "x"} for w in ways]
    path.write_text(json.dumps({"schema": "t", "roads": roads, **extra}), encoding="utf-8")
    return path


def test_domain_dedups_way_ids_and_records_lineage(tmp_path):
    model = write_model(tmp_path / "m.json", [1, 1, 1, 2, 2, 3])
    domain = load_road_domain(model)
    assert domain.ids.tolist() == [1, 2, 3] and domain.road_segments == 6
    info = domain.describe()
    assert info["unique_way_ids"] == 3 and info["road_segments_in_model"] == 6
    assert len(info["model_sha256"]) == 64 and len(info["way_id_set_sha256"]) == 64
    # The ID-set hash depends on the IDs only, not on the model's speed values.
    other = load_road_domain(write_model(tmp_path / "n.json", [3, 2, 1], speed=99.0))
    assert other.id_set_sha256 == domain.id_set_sha256 and other.model_sha256 != domain.model_sha256


@pytest.mark.parametrize("payload", ["not json", "[]", "{}", '{"roads": []}', '{"roads": [{"way": "1"}]}',
                                     '{"roads": [{"way": true}]}', '{"roads": [{"way": -5}]}', '{"roads": [{}]}',
                                     '{"roads": [{"way": 1}, 7]}', '{"roads": [{"way": 1000000000000}]}'])
def test_malformed_or_empty_domain_model_is_rejected(tmp_path, payload):
    path = tmp_path / "m.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(BaselineInputError):
        load_road_domain(path)
    with pytest.raises(BaselineInputError):
        load_road_domain(tmp_path / "missing.json")


def test_out_of_domain_extremes_cannot_affect_fit_or_scores(tmp_path):
    extremes = (rows_for("2015-03-02", 8, 99, [250, 250, 250]) + rows_for("2016-03-07", 8, 99, [1, 1, 1])
                + rows_for("2015-03-02", 9, 98, [250, 250]) + rows_for("2016-03-07", 9, 98, [0, 0])
                + rows_for("2014-01-01", 8, 97, [5]))
    base_path = write_csv(tmp_path / "base.csv", known_rows())
    mixed_path = write_csv(tmp_path / "mixed.csv", known_rows() + extremes)
    domain = load_road_domain(write_model(tmp_path / "m.json", [1, 1, 2, 3]))
    reference = evaluate(base_path, CONFIG)
    scoped = evaluate(mixed_path, CONFIG, domain=domain)
    assert scoped["results"] == reference["results"]
    unscoped = evaluate(mixed_path, CONFIG)
    assert unscoped["results"] != reference["results"]  # the extremes do matter without a domain
    d, rows = scoped["domain"], scoped["rows"]
    assert d["valid_rows_outside_domain"] == len(extremes) == rows["valid_rows_outside_domain_excluded"]
    assert d["selected_valid_rows"] == len(known_rows()) and d["selected_unique_ways_with_valid_rows"] == 3
    assert rows["valid_rows_in_calibration_years"] == 7 and rows["valid_rows_in_holdout_years"] == 9
    assert rows["valid_rows_in_domain_outside_both_splits_excluded"] == 0
    assert rows["rejected_rows"] == 0 and d["domain_ways_without_valid_rows"] == 0
    assert scoped["config"]["config_sha256"] != unscoped["config"]["config_sha256"]


def test_domain_with_year_exclusions_and_bad_rows_keeps_counts_separate(tmp_path):
    rows = known_rows() + rows_for("2014-01-01", 8, 1, [5]) + rows_for("2015-03-02", 8, 99, [5]) \
        + [("bad", "08:00:00", "5", "way/1")]
    domain = load_road_domain(write_model(tmp_path / "m.json", [1, 2, 3, 4]))
    report = evaluate(write_csv(tmp_path / "v.csv", rows), CONFIG, domain=domain)
    r, d = report["rows"], report["domain"]
    assert r["rejected_rows"] == 1 and d["valid_rows_outside_domain"] == 1
    assert r["valid_rows_in_domain_outside_both_splits_excluded"] == 1
    assert d["domain_ways_without_valid_rows"] == 1  # way 4 has no readings
    assert r["total_data_rows"] == r["valid_rows"] + r["rejected_rows"]
    assert d["selected_valid_rows"] == r["valid_rows"] - d["valid_rows_outside_domain"]


def test_domain_with_no_rows_is_blocked_without_fallback(tmp_path):
    domain = load_road_domain(write_model(tmp_path / "m.json", [500]))
    report = evaluate(write_csv(tmp_path / "v.csv", known_rows()), CONFIG, domain=domain)
    assert report["status"] == "blocked" and report["blocked_reason"] == "no_calibration_observations"
    assert report["results"] is None


def test_file_wide_default_is_labelled_unverified_geography(tmp_path):
    report = evaluate(write_csv(tmp_path / "v.csv", known_rows()), CONFIG)
    assert report["domain"]["mode"] == "source_file_wide"
    assert "not verified" in report["domain"]["note"] and "not Beirut targets" in report["domain"]["note"]
    assert report["domain"]["valid_rows_outside_domain"] == 0
    assert "Beirut" not in json.dumps(report["source"]["date_range_observed_in_this_file"])


def test_provenance_wording_claims_no_external_preregistration(tmp_path):
    cfg = evaluate(write_csv(tmp_path / "v.csv", known_rows()), CONFIG)["config"]
    assert cfg["fixed_in_configuration_before_computation"] is True
    assert "not an external preregistration" in cfg["preregistration"]
    text = json.dumps(cfg).lower()
    assert "frozen" not in text and "predeclared" not in text and "pre-declared" not in text


def test_domain_run_is_reproducible_and_chunk_invariant(tmp_path):
    rng = np.random.default_rng(3)
    rows = [(f"{int(rng.choice([2015, 2016, 2017]))}-{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 28)):02d}",
             f"{int(rng.integers(0, 24)):02d}:00:00", f"{rng.uniform(0, 90):.2f}", f"way/{int(rng.integers(1, 9))}")
            for _ in range(500)]
    path = write_csv(tmp_path / "v.csv", rows)
    domain = load_road_domain(write_model(tmp_path / "m.json", [2, 4, 4, 6]))
    cfg = BaselineConfig((2015, 2016), (2017,), 2, 300.0)
    texts = {canonical_json(evaluate(path, cfg, chunk_rows=n, domain=domain)) for n in (1, 50, 10_000)}
    assert len(texts) == 1


def test_cli_traffic_model_option_records_session_and_domain(tmp_path):
    module = load_script()
    source = write_csv(tmp_path / "v.csv", known_rows() + rows_for("2015-03-02", 8, 99, [250]))
    model = write_model(tmp_path / "m.json", [1, 2, 3, 3])
    out = tmp_path / "out"
    args = ["--velocities", str(source), "--output", str(out), "--calibration-years", "2015", "--holdout-years", "2016",
            "--min-count", "2", "--traffic-model", str(model), "--session-id", "sess-1"]
    assert module.main(args) == 0
    report = json.loads((out / "observation-baseline.json").read_text())
    assert report["domain"]["unique_way_ids"] == 3 and report["domain"]["valid_rows_outside_domain"] == 1
    assert json.loads((out / "run-metadata.json").read_text())["runs"][0]["session_id"] == "sess-1"
    assert json.loads((out / "manifest.json").read_text())["session_id"] == "sess-1"
    assert "unique way IDs" in (out / "results.md").read_text()
