"""Road mapping must fail closed if either model or city geometry differs."""
from types import SimpleNamespace

import pytest

from scripts.prepare_unreal_traffic_mapping import prepare
from dynacity.traffic_sumo import road_identity


def model(ways, geometry):
    return SimpleNamespace(way_id=ways, geometry=geometry)


def test_mapping_uses_unique_way_and_ordered_geometry():
    pilot = model([42], [[(100, 200), (110, 200)]])
    city = model([99, 42], [[(0, 0), (1, 1)], [(100, 200), (110, 200)]])
    index = {'origin_utm_m': [100, 200, 0],
             'roads': [{'id': 1, 'path': [[0, 0, 0], [10, 0, 0]]}]}
    mapping = prepare(pilot, city, index)
    assert mapping['roads'] == {'0': '1'}
    assert mapping['source_road_identity'] == road_identity(pilot)
    altered = model([42], [[(110, 200), (100, 200)]])
    assert road_identity(altered) != mapping['source_road_identity']
    with pytest.raises(ValueError, match='unique'):
        prepare(altered, city, index)


def test_mapping_rejects_ambiguous_or_misaligned_city_roads():
    geom = [(100, 200), (110, 200)]
    pilot = model([42], [geom])
    index = {'origin_utm_m': [100, 200, 0],
             'roads': [{'id': 0, 'path': [[0, 0, 0], [10, 0, 0]]}]}
    with pytest.raises(ValueError, match='unique'):
        prepare(pilot, model([42, 42], [geom, geom]), index)
    index['roads'][0]['path'] = [[50, 50, 0], [60, 50, 0]]
    with pytest.raises(ValueError, match='geometry'):
        prepare(pilot, model([42], [geom]), index)
