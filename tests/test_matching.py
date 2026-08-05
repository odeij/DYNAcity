import numpy as np
from shapely.geometry import box

from dynacity_bbed.matching import FootprintMatcher
from dynacity_bbed.models import Footprint


def test_matches_points_and_resolves_overlap_to_smallest_polygon():
    footprints = [
        Footprint(0, box(0, 0, 10, 10), {"OBJECTID": 100}),
        Footprint(1, box(2, 2, 4, 4), {"OBJECTID": 101}),
    ]
    result = FootprintMatcher(footprints).match(
        np.array([1.0, 3.0, 20.0, 0.0]),
        np.array([1.0, 3.0, 20.0, 5.0]),
    )

    assert result.match_ids.tolist() == [0, 1, -1, 0]
    assert result.overlap_counts.tolist() == [1, 2, 0, 1]

