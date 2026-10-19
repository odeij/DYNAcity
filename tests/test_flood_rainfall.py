from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dynacity.flood_rainfall import (
    RainfallForcing, RainfallInterval, RainfallProvenance, hyetograph, import_rainfall_csv,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYNTHETIC = RainfallProvenance("hypothetical", "unit test fixture")


def event(values=(12, 24, 0), seconds=600, unit="mm/h"):
    return hyetograph(start=START, values=values, timestep_seconds=seconds, unit=unit, provenance=SYNTHETIC)


@pytest.mark.parametrize("unit,value,expected", [
    ("mm/h", 36, 3), ("m/s", 0.00001, 3), ("in/h", 1, 25.4 / 12),
    ("mm", 3, 3), ("m", 0.003, 3), ("in", 1, 25.4),
])
def test_unit_conversion(unit, value, expected):
    forcing = event([value], 300, unit)
    assert forcing.total_depth_mm == pytest.approx(expected)
    assert forcing.intervals[0].intensity_m_s * 300 * 1000 == pytest.approx(expected)


def test_duration_cumulative_and_immutable_intervals():
    forcing = event()
    assert forcing.duration_seconds == 1800
    assert forcing.timestep_seconds == 600
    assert forcing.total_depth_mm == 6
    assert forcing.cumulative_depth_mm(START) == 0
    assert forcing.cumulative_depth_mm(START + timedelta(seconds=900)) == 4
    assert forcing.cumulative_depth_mm(forcing.end) == forcing.total_depth_mm
    assert forcing.depth_between(START + timedelta(seconds=300), START + timedelta(seconds=1200)) == 5
    intervals = list(forcing.intervals)
    copied = RainfallForcing(intervals, SYNTHETIC)
    intervals.clear()
    assert len(copied.intervals) == 3


@pytest.mark.parametrize("seconds", [1, 37, 450, 600, 700, 1800, 3600])
def test_rebinning_preserves_precipitation_mass(seconds):
    forcing = event()
    steps = forcing.steps(seconds)
    assert sum(s.depth_mm for s in steps) == pytest.approx(6, abs=1e-12)
    assert steps[-1].cumulative_depth_mm == pytest.approx(6, abs=1e-12)
    assert steps[-1].end == forcing.end
    assert sum(s.duration_seconds for s in steps) == 1800
    assert all(a.end == b.start for a, b in zip(steps, steps[1:]))
    for step in steps:
        assert step.intensity_m_s * step.duration_seconds == pytest.approx(step.depth_m)
        assert step.cumulative_depth_mm == pytest.approx(forcing.cumulative_depth_mm(step.end))
    # Uniform rainfall volume over 2,000 m², before losses/drainage.
    assert sum(s.depth_m * 2000 for s in steps) == pytest.approx(12)


def test_overlap_weighted_rate_and_partial_final_step():
    steps = event().steps(900)
    assert [s.intensity_mm_h for s in steps] == [16, 8]
    assert [s.depth_mm for s in steps] == [4, 2]
    assert event().steps(700)[-1].duration_seconds == 400


def test_irregular_intervals_are_explicit_and_integrated():
    middle = START + timedelta(seconds=300)
    forcing = RainfallForcing((RainfallInterval(START, middle, 12),
                              RainfallInterval(middle, middle + timedelta(seconds=900), 24)), SYNTHETIC)
    assert forcing.timestep_seconds is None
    assert forcing.total_depth_mm == 7
    assert [s.depth_mm for s in forcing.steps(600)] == [3, 4]


def test_aware_timezones_are_normalized_and_dst_uses_elapsed_time():
    local = START.astimezone(ZoneInfo("Asia/Beirut"))
    forcing = hyetograph(start=local, values=[10], timestep_seconds=3600, unit="mm/h", provenance=SYNTHETIC)
    assert forcing.start == START
    assert forcing.start.tzinfo == timezone.utc
    # US fall-back crosses repeated wall-clock hour; elapsed duration is two hours.
    zone = ZoneInfo("America/New_York")
    interval = RainfallInterval(datetime(2025, 11, 2, 1, tzinfo=zone, fold=0),
                                datetime(2025, 11, 2, 2, tzinfo=zone), 10)
    assert interval.duration_seconds == 7200
    assert interval.depth_mm == 20


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf"), None, True, "5"])
def test_invalid_rainfall_values(value):
    with pytest.raises(ValueError):
        event([value])


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True, 1e-9, 1.5e-6, 1e308])
def test_invalid_timesteps(seconds):
    with pytest.raises(ValueError):
        event(seconds=seconds)
    with pytest.raises(ValueError):
        event().steps(seconds)


def test_overflowing_depth_is_invalid():
    with pytest.raises(ValueError, match="overflows"):
        event([1e308], seconds=7200)
    with pytest.raises(ValueError, match="overflows"):
        event([1e308, 1e308], seconds=3600)


@pytest.mark.parametrize("offset", [-600, -300, 1, 600])
def test_missing_overlapping_duplicate_and_unordered_times_fail(offset):
    first = RainfallInterval(START, START + timedelta(seconds=600), 1)
    second_start = first.end + timedelta(seconds=offset)
    with pytest.raises(ValueError, match="ordered and contiguous"):
        RainfallForcing((first, RainfallInterval(second_start, second_start + timedelta(seconds=600), 1)), SYNTHETIC)


def test_empty_bad_units_bad_times_and_outside_coverage():
    with pytest.raises(ValueError):
        event([])
    with pytest.raises(ValueError, match="unit"):
        event(unit="cm")
    with pytest.raises(ValueError, match="explicit"):
        RainfallInterval(START.replace(tzinfo=None), START, 1)
    with pytest.raises(ValueError, match="follow"):
        RainfallInterval(START, START, 1)
    for start, end in [(START - timedelta(seconds=1), START), (START, START + timedelta(days=1)),
                       (START + timedelta(seconds=1), START)]:
        with pytest.raises(ValueError, match="coverage"):
            event().depth_between(start, end)


def test_provenance_distinguishes_observations_and_hypotheticals():
    for kwargs in [dict(kind="observed", source="gauge"), dict(kind="invented", source="x"),
                   dict(kind="hypothetical", source=" "), dict(kind="observed", source="x", station_id="")]:
        with pytest.raises(ValueError):
            RainfallProvenance(**kwargs)
    forcing = event()
    assert forcing.provenance.kind == "hypothetical"
    assert any("return period" in w for w in forcing.warnings)
    assert not hasattr(forcing, "return_period")


def test_import_supplied_measurements_with_offsets_irregular_times_and_depth_units(tmp_path):
    path = tmp_path / "fixture.csv"
    path.write_text("start,end,value\n2026-01-01T02:00:00+02:00,2026-01-01T02:05:00+02:00,1\n"
                    "2026-01-01T00:05:00Z,2026-01-01T00:20:00Z,6\n")
    provenance = RainfallProvenance("observed", "supplied fixture; not a Beirut observation", "fixture-only")
    forcing = import_rainfall_csv(path, unit="mm", provenance=provenance)
    assert forcing.start == START
    assert forcing.provenance == provenance
    assert forcing.total_depth_mm == 7
    assert [i.intensity_mm_h for i in forcing.intervals] == [12, 24]


@pytest.mark.parametrize("rows", [
    "start,end,value\n", "start,end,mm\n", "start,start,value\n",
    "start,end,value\n2026-01-01T00:00:00Z,2026-01-01T01:00:00Z,\n",
    "start,end,value\n,2026-01-01T01:00:00Z,1\n",
    "start,end,value\n2026-01-01,2026-01-02,1\n",
    "start,end,value\n2026-01-01T00:00:00Z,2026-01-01T01:00:00Z,nan\n",
    "start,end,value\n2026-01-01T00:00:00Z,2026-01-01T01:00:00Z,1,extra\n",
    "start,end,value\n2026-01-01T00:00:00Z,2026-01-01T01:00:00Z,1\n"
    "2026-01-01T02:00:00Z,2026-01-01T03:00:00Z,1\n",
])
def test_import_rejects_missing_or_invalid_data(tmp_path, rows):
    path = tmp_path / "invalid.csv"
    path.write_text(rows)
    with pytest.raises(ValueError):
        import_rainfall_csv(path, unit="mm", provenance=SYNTHETIC)
