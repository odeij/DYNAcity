import numpy as np
import pytest

from dynacity.facade_massing import (estimate_massing, project_points, registration_error_px,
                                     registration_ok, Value)


def test_projection_centres_point_ahead_and_drops_behind():
    px = project_points([[0, 10, 0], [0, -10, 0]], [0, 0, 0], 0, 0, 90, (1600, 1200))
    assert np.allclose(px[0], [800, 600])
    assert np.isnan(px[1]).all()


def test_projection_right_is_clockwise_of_heading():
    px = project_points([[5, 10, 0]], [0, 0, 0], 0, 0, 90, (1600, 1200))
    assert px[0][0] > 800


def test_registration_gate():
    good = registration_error_px([[10, 10], [200, 50], [300, 300]], [[12, 11], [198, 52], [303, 298]])
    assert registration_ok(good, (1600, 1200))
    bad = registration_error_px([[10, 10], [200, 50], [300, 300]], [[90, 70], [100, 150], [403, 98]])
    assert not registration_ok(bad, (1600, 1200))
    with pytest.raises(ValueError):
        registration_error_px([[0, 0]], [[0, 0]])


def test_observed_requires_photo_and_estimates_are_labelled():
    with pytest.raises(ValueError):
        Value(5, 'observed')
    m = estimate_massing('b1', 0, 40., 18., observed={'balcony_rows': 12}, photo_ids=['1288770821'])
    assert m.features['balcony_rows'].basis == 'observed'
    assert m.features['floors'].basis == 'estimated'
    assert m.features['plinth_height_m'].basis == 'estimated'
    assert m.to_json()['features']['balcony_rows']['photo_ids'] == ['1288770821']
