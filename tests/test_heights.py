"""Height priority order and the storey-height fit, on synthetic data."""
import numpy as np
import pytest

from dynacity.heights import (FALLBACK, FALLBACK_HEIGHT_M, FLOORS, RECORDED, SIZE_BIN, STOREY_HEIGHT_M,
                              area_bin, bin_medians, fit_storey_height, mapped_height, resolve_height)

MEDIANS = [5, 6, 7, 8, 9, 10, 11]


def test_priority_recorded_then_floors_then_size_bin_then_fallback():
    a = resolve_height(recorded=31, floors=5, area_m2=60, medians=MEDIANS)
    assert (a.height_m, a.kind, a.estimated) == (31.0, RECORDED, False)
    b = resolve_height(recorded=None, floors=5, area_m2=60, medians=MEDIANS)
    assert (b.height_m, b.kind, b.estimated) == (5 * STOREY_HEIGHT_M, FLOORS, True)
    c = resolve_height(floors=None, area_m2=60, medians=MEDIANS)
    assert (c.height_m, c.kind, c.estimated) == (6, SIZE_BIN, True)
    d = resolve_height(area_m2=60, medians=None)
    assert (d.height_m, d.kind, d.estimated) == (FALLBACK_HEIGHT_M, FALLBACK, True)


def test_placeholder_values_do_not_count_as_recorded():
    # BBED stores -999 and 0 for missing values.
    for bad in (-999, 0, None, float("nan"), "n/a", 301):
        assert resolve_height(recorded=bad, floors=-999, area_m2=500, medians=MEDIANS).kind == SIZE_BIN
    assert resolve_height(recorded="12 m").height_m == 12.0
    assert mapped_height("1") is None and mapped_height("2") == 2.0 and mapped_height("nan") is None


def test_labels_name_the_source():
    assert "floors" in resolve_height(floors=4).source and "3 m" in resolve_height(floors=4).source
    assert resolve_height(recorded=9, recorded_label="Recorded height (BBED)").source == "Recorded height (BBED)"
    assert resolve_height(area_m2=60, medians=MEDIANS, median_label="X").source.endswith("X")


def test_area_bins_and_sparse_bin_medians():
    assert [area_bin(a) for a in (1, 50, 99, 1600, 1e6)] == [0, 1, 1, 6, 6]
    areas = [120] * 6 + [900]
    heights = [10, 12, 14, 16, 18, 20, 99]
    medians = bin_medians(areas, heights)
    assert medians[2] == 15 and medians[5] == 16  # bin with one building takes the overall median
    assert bin_medians([100, 200], [10, 20]) is None


def test_storey_fit_recovers_three_metres_and_wins_on_held_out_buildings():
    rng = np.random.default_rng(3)
    floors = rng.integers(1, 40, 600).astype(float)
    heights = floors * 3.0
    heights[:60] = floors[:60] * 3.2  # a tenth of buildings use 3.2 m
    report = fit_storey_height(floors, heights)
    assert report["n"] == 600
    assert report["best"] != "constant_3.2"
    assert report["models"]["median_ratio"]["slope"] == pytest.approx(3.0)
    assert report["models"]["median_ratio"]["mae"] < report["models"]["constant_3.2"]["mae"] / 4
    assert report["models"]["median_ratio"]["median_abs"] == pytest.approx(0.0, abs=1e-9)


def test_storey_fit_keeps_old_constant_when_it_is_right_and_drops_placeholders():
    floors = np.arange(1, 41).repeat(3).astype(float)
    heights = floors * 3.2
    floors = np.append(floors, [-999, 4]); heights = np.append(heights, [10, -999])
    report = fit_storey_height(floors, heights)
    assert report["n"] == 120 and report["best"] == "constant_3.2"
    with pytest.raises(ValueError):
        fit_storey_height([1, 2, 3], [3, 6, 9])
