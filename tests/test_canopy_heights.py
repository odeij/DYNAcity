import json

import numpy as np
import pytest

from dynacity.canopy_heights import (
    acquisition_dates, canopy_near, disc, find_peaks, height_summary, max_height_near, nearest_distance_m,
    quadkey, road_canopy_cover, sample_line, tiles_for_bounds,
)


def bump(shape, centre, height, radius):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    d = np.hypot(yy - centre[0], xx - centre[1])
    return np.where(d <= radius, height * (1 - 0.5 * d / radius), 0.0)


def test_find_peaks_keeps_crown_tops_above_threshold_with_their_heights():
    chm = np.zeros((60, 60))
    chm += bump(chm.shape, (15, 15), 12.0, 6)
    chm += bump(chm.shape, (40, 40), 8.0, 6)
    chm += bump(chm.shape, (15, 45), 2.0, 5)  # under the 3 m threshold
    peaks = find_peaks(chm, min_height_m=3.0, spacing_px=4)
    assert sorted(map(tuple, peaks)) == [(15, 15), (40, 40)]
    assert chm[15, 15] == pytest.approx(12.0)


def test_find_peaks_enforces_minimum_spacing_and_merges_flat_tops():
    chm = np.zeros((20, 40))
    chm[10, 10], chm[10, 13] = 9.0, 7.0  # 3 px apart: the lower one is dropped at 5 px spacing
    chm[5:8, 30:33] = 6.0  # flat 3 x 3 top: one candidate at its centre
    peaks = {tuple(p) for p in find_peaks(chm, spacing_px=5)}
    assert peaks == {(10, 10), (6, 31)}
    assert {tuple(p) for p in find_peaks(chm, spacing_px=2)} == {(10, 10), (10, 13), (6, 31)}


def test_find_peaks_ignores_nan_and_empty_grids():
    chm = np.full((10, 10), np.nan)
    assert find_peaks(chm, spacing_px=3).shape == (0, 2)
    assert find_peaks(np.zeros((10, 10)), spacing_px=3).shape == (0, 2)


def test_canopy_near_reaches_the_buffer_radius_only():
    chm = np.zeros((21, 21))
    chm[10, 10] = 5.0
    near = canopy_near(chm, 3.0, radius_px=4)
    assert near[10, 14] and near[14, 10] and not near[10, 15] and not near[0, 0]
    assert disc(4).sum() == 49


def test_max_height_near_reports_nan_off_grid_and_over_nodata():
    chm = np.zeros((10, 10))
    chm[5, 5] = 7.0
    chm[0:4, 0:4] = np.nan
    out = max_height_near(chm, np.array([5, 0, 20]), np.array([6, 0, 5]), radius_px=2)
    assert out[0] == 7.0 and np.isnan(out[1]) and np.isnan(out[2])


def test_road_cover_is_the_share_of_valid_samples_with_canopy():
    near = np.zeros((10, 100), bool)
    near[:, :50] = True
    valid = np.ones_like(near)
    valid[:, 90:] = False  # no data for the last 10 m
    # World = pixel: x is the column, y the row; the road runs along row 5 from x=0 to x=99 (+ one road off the grid).
    to_pixel = lambda xy: (np.floor(xy[:, 1]).astype(int), np.floor(xy[:, 0]).astype(int))
    result = road_canopy_cover([[(0, 5), (99, 5)], [(0, 50), (10, 50)]], near, valid, to_pixel, step_m=1.0)
    assert result[0]["canopy_cover"] == pytest.approx(50 / 90, abs=0.02)
    assert result[0]["valid_fraction"] == pytest.approx(0.9, abs=0.02)
    assert result[1]["canopy_cover"] is None and result[1]["valid_fraction"] == 0.0


def test_sample_line_includes_both_ends_and_spacing():
    pts = sample_line([(0, 0), (10, 0)], step_m=2.5)
    assert len(pts) == 5 and pts[0].tolist() == [0, 0] and pts[-1].tolist() == [10, 0]
    assert len(sample_line([(1, 1), (1, 1)])) == 1


def test_nearest_distance_handles_empty_inputs():
    a = np.array([[0.0, 0.0], [10.0, 0.0]])
    assert nearest_distance_m(a, np.array([[3.0, 4.0]])).tolist() == [5.0, pytest.approx(8.06, abs=0.01)]
    assert np.isinf(nearest_distance_m(a, np.zeros((0, 2)))).all()
    assert nearest_distance_m(np.zeros((0, 2)), a).size == 0


def test_quadkey_names_the_tile_over_beirut():
    assert quadkey(35.5265, 33.8812, 9) == "122112210"
    assert tiles_for_bounds(35.52, 33.87, 35.54, 33.90) == ["122112210"]
    assert quadkey(0.0, 0.0, 1) == "3"


def test_acquisition_dates_by_footprint_and_share_of_area():
    from shapely.geometry import box
    metadata = {"features": [
        {"type": "Feature", "properties": {"acq_date": "2019-11-13"},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
        {"type": "Feature", "properties": {"acq_date": "2017-06-26"},
         "geometry": {"type": "Polygon", "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}},
    ]}
    dates, shares = acquisition_dates(metadata, np.array([[0.5, 0.5], [1.5, 0.5], [5.0, 5.0]]), box(0.5, 0, 2.5, 1))
    assert dates == ["2019-11-13", "2017-06-26", None]
    assert shares == {"2017-06-26": 0.5, "2019-11-13": 0.25, "no footprint": 0.25}


def test_height_summary_skips_missing_values():
    assert height_summary([None, float("nan")]) == {"count": 0}
    s = height_summary([3, 5, 7, None])
    assert s["count"] == 3 and s["median"] == 5 and s["max"] == 7
    json.dumps(s)


def test_window_maps_pixels_to_utm_and_back_on_a_web_mercator_tile(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    from dynacity.canopy_heights import read_window

    # A synthetic 1.19 m web-mercator tile around the Beirut river mouth with one 9 m crown.
    x0, y0 = transform("EPSG:32636", "EPSG:3857", [733000.0], [3752000.0])
    origin = (x0[0] - 300.0, y0[0] + 300.0)
    data = np.zeros((500, 500), dtype="uint8")
    data[250:253, 250:253] = [[7, 8, 7], [8, 9, 8], [7, 8, 7]]
    path = tmp_path / "tile.tif"
    with rasterio.open(path, "w", driver="GTiff", height=500, width=500, count=1, dtype="uint8", crs="EPSG:3857",
                       transform=from_origin(origin[0], origin[1], 1.19, 1.19)) as dst:
        dst.write(data, 1)
    window = read_window(path, (732900.0, 3751900.0, 733100.0, 3752100.0), "EPSG:32636", pad_m=10.0)
    assert window.ground_px_m == pytest.approx(1.19 * np.cos(np.radians(33.88)), abs=0.02)
    peaks = find_peaks(window.chm, spacing_px=4.0 / window.ground_px_m)
    assert len(peaks) == 1 and window.chm[tuple(peaks[0])] == 9
    xy = window.pixel_to_work(peaks[:, 0], peaks[:, 1])
    row, col = window.work_to_pixel(xy)
    assert (row[0], col[0]) == tuple(peaks[0])
    # The crown's centre is 251.5 px from the tile's origin; the UTM position agrees with the construction.
    ex, ey = transform("EPSG:3857", "EPSG:32636", [origin[0] + 251.5 * 1.19], [origin[1] - 251.5 * 1.19])
    assert xy[0] == pytest.approx([ex[0], ey[0]], abs=1.5)


def test_footprint_flags_mark_points_inside_building_polygons(tmp_path):
    from dynacity.canopy_heights import footprint_flags

    square = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[35.5, 33.9], [35.501, 33.9], [35.501, 33.901], [35.5, 33.901], [35.5, 33.9]]]}}
    path = tmp_path / "b.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [square]}), encoding="utf-8")
    flags = footprint_flags(path, np.array([[35.5005, 33.9005], [35.502, 33.9005]]))
    assert flags == [True, False]
