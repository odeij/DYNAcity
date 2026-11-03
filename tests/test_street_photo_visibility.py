import pytest
from shapely.geometry import box

from dynacity.street_photo_visibility import angle_delta, visible_wall_samples


def test_heading_wrap():
    assert angle_delta(1, 359) == 2
    assert angle_delta(359, 1) == -2


def test_forward_near_wall_survives_but_far_wall_is_occluded():
    target = box(-4, 20, 4, 30)
    result = visible_wall_samples((0, 0), 0, target, [target])
    assert result['status'] == 'geometry_candidate_needs_review'
    assert all(sample['wall_point_xy_m'][1] == 20 for sample in result['samples'])


def test_nearer_building_blocks_target():
    target = box(-4, 20, 4, 30)
    result = visible_wall_samples((0, 0), 0, target, [target, box(-8, 8, 8, 15)])
    assert result['samples'] == []


def test_behind_camera_is_rejected():
    target = box(-4, -30, 4, -20)
    assert visible_wall_samples((0, 0), 0, target, [target])['samples'] == []


def test_gps_inside_building_is_flagged_instead_of_assigned():
    target = box(-4, -4, 4, 4)
    assert visible_wall_samples((0, 0), 0, target, [target])['status'] == 'camera_inside_source_footprint'


def test_edge_of_view_and_distance_budget():
    target = box(18, 18, 22, 22)
    assert visible_wall_samples((0, 0), 0, target, [target], half_angle=50)['samples']
    assert not visible_wall_samples((0, 0), 0, target, [target], half_angle=30)['samples']
    assert not visible_wall_samples((0, 0), 0, target, [target], maximum_distance=10)['samples']
    with pytest.raises(ValueError):
        visible_wall_samples((0, 0), float('nan'), target, [target])
