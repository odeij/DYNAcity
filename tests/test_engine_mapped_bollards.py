import pytest

from dynacity.engine_mapped_bollards import bollard_placements


def test_mapped_nodes_keep_exact_position_and_source():
    point = {'id': 'node/12', 'kind': 'bollard', 'position_local_m': [1, 2, 3],
             'source_position_lonlat': [35.5, 33.9], 'elevation_source': 'terrain'}
    out = bollard_placements({'schema': 'dynacity-engine-street-context/1',
                              'points': [point, {'id': 'way/7', 'kind': 'bollard', 'position_local_m': [4, 5, 6]}]})
    assert len(out['placements']) == 1
    assert out['placements'][0]['source_id'] == 'node/12'
    assert out['placements'][0]['position_local_m'] == [1, 2, 3]
    assert 'not observed' in out['placements'][0]['appearance_source']


def test_rejects_nonfinite_and_duplicate_nodes():
    base = {'schema': 'dynacity-engine-street-context/1'}
    p = {'id': 'node/1', 'kind': 'bollard', 'position_local_m': [1, 2, float('nan')]}
    with pytest.raises(ValueError):
        bollard_placements({**base, 'points': [p]})
    p['position_local_m'] = [1, 2, 3]
    with pytest.raises(ValueError):
        bollard_placements({**base, 'points': [p, p]})
