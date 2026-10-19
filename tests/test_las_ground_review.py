import json

import numpy as np
import pytest
from shapely.geometry import box, mapping

from conftest import write_las
from dynacity.las_ground_review import review_ground


def inputs(tmp_path):
    # Coordinates in fixture LAS use centimetre integer scaling. An elevated
    # vehicle lies in an explicitly excluded cell; a different cell is rough.
    points = [(20, 180, 1000, 0, 0, 0, 0), (30, 170, 1010, 0, 0, 0, 0),
              (40, 160, 1020, 0, 0, 0, 0), (120, 180, 1400, 0, 0, 0, 0),
              (20, 80, 1000, 0, 0, 0, 0), (30, 70, 1200, 0, 0, 0, 0),
              (40, 60, 1400, 0, 0, 0, 0), (200, 0, 1000, 0, 0, 0, 0)]
    source = write_las(tmp_path/'source.las', points)
    spec = {'type': 'FeatureCollection', 'horizontal_crs': 'EPSG:32636',
            'evidence': 'Mathematical test footprints, not Beirut data', 'features': [
                {'type': 'Feature', 'properties': {'role': 'include'}, 'geometry': mapping(box(0, 0, 2, 2))},
                {'type': 'Feature', 'properties': {'role': 'exclude'}, 'geometry': mapping(box(1, 1, 2, 2))}]}
    footprints = tmp_path/'footprints.json'
    footprints.write_text(json.dumps(spec))
    return source, footprints


def run(tmp_path, **overrides):
    source, footprints = inputs(tmp_path)
    options = dict(cell_m=1., horizontal_crs='EPSG:32636', crs_evidence='test only',
                   exclusion_buffer_m=0., min_points=3, max_spread_m=.3)
    options.update(overrides)
    return review_ground(source, footprints, tmp_path/'review.npz', **options)


def test_ground_review_preserves_source_observations_and_explicit_gaps(tmp_path):
    report = run(tmp_path)
    assert report['points_processed'] == 8
    assert report['selected_points'] == 7
    assert report['candidate_cells'] == 1
    assert report['vertical_reference'] is None
    assert report['verified_flood_terrain'] is False
    with np.load(tmp_path/'review.npz', allow_pickle=False) as data:
        assert data['candidate_z'][0, 0] == 10.1
        assert data['q10_observed_z'][0, 0] == 10
        assert data['q90_observed_z'][0, 0] == pytest.approx(10.2)
        assert np.isnan(data['candidate_z'][0, 1])  # excluded vehicle
        assert np.isnan(data['candidate_z'][1, 0])  # unsupported vertical spread
        assert np.isnan(data['candidate_z'][1, 1])  # missing, never interpolated
        assert json.loads(str(data['metadata'])) == report


def test_candidate_cell_must_not_bridge_excluded_geometry(tmp_path):
    source, footprints = inputs(tmp_path)
    spec = json.loads(footprints.read_text())
    spec['features'][1]['geometry'] = mapping(box(.8, 1.1, .9, 1.9))
    footprints.write_text(json.dumps(spec))
    report = review_ground(source, footprints, tmp_path/'review.npz', cell_m=1,
        horizontal_crs='EPSG:32636', crs_evidence='test', exclusion_buffer_m=0,
        min_points=3, max_spread_m=.3)
    assert report['candidate_cells'] == 0


@pytest.mark.parametrize('options', [{'min_points': 0}, {'cell_m': 0},
    {'max_spread_m': float('nan')}, {'horizontal_crs': 'EPSG:4326'},
    {'exclusion_buffer_m': -1}, {'max_points': 1}])
def test_ground_review_rejects_invalid_screening_and_unbounded_inputs(tmp_path, options):
    with pytest.raises(ValueError):
        run(tmp_path, **options)


def test_ground_review_refuses_silent_crs_conversion_or_geometry_repair(tmp_path):
    source, footprints = inputs(tmp_path)
    spec = json.loads(footprints.read_text())
    spec['horizontal_crs'] = 'EPSG:32637'
    footprints.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match='same asserted CRS'):
        review_ground(source, footprints, tmp_path/'review.npz', cell_m=1,
            horizontal_crs='EPSG:32636', crs_evidence='test', exclusion_buffer_m=0,
            min_points=3, max_spread_m=.3)
