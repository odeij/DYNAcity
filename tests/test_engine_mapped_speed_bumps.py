import pytest
from dynacity.engine_mapped_speed_bumps import speed_bump_placements


def point(**overrides):
    return {'id': 'node/42', 'kind': 'traffic_calming', 'tags': {'traffic_calming': 'bump'},
            'position_local_m': [1., 2., 3.], 'road_width_m': 4., 'road_heading_degrees': 30.,
            'road_level': 0, **overrides}


def test_bump_keeps_mapped_location_and_crosses_road():
    result = speed_bump_placements({'schema': 'dynacity-engine-street-context/1', 'points': [point()]}, 8)
    row = result['placements'][0]
    assert row['position_local_m'] == [1., 2., 3.]
    assert row['heading_degrees'] == 120 and row['scale_x'] == .5
    assert 'inferred' in row['placement_source']


def test_unrelated_and_unusable_nodes_are_not_placed():
    context = {'schema': 'dynacity-engine-street-context/1', 'points': [point(road_level=1),
               point(id='node/43', tags={'traffic_calming': 'island'})]}
    result = speed_bump_placements(context, 8)
    assert not result['placements'] and len(result['skipped']) == 1


def test_duplicate_sources_and_nonfinite_coordinates_fail():
    with pytest.raises(ValueError): speed_bump_placements({'schema':'dynacity-engine-street-context/1', 'points':[point(), point()]}, 8)
    with pytest.raises(ValueError): speed_bump_placements({'schema':'dynacity-engine-street-context/1', 'points':[point(position_local_m=[1, 2, float('nan')])]}, 8)
