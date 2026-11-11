"""Canonical geometry tests use synthetic projected streets, not Beirut surveys."""
from dataclasses import replace

import numpy as np
import pytest

from dynacity.street_twin import build_street_twin, content_hash
from dynacity.traffic import TrafficModel


def model():
    return TrafficModel(
        crs='EPSG:32636',
        geometry=[[(733000., 3750000.), (733100., 3750000.), (733300., 3750000.)],
                  [(733300., 3750000.), (733300., 3750200.)]],
        u=np.array([0, 1]), v=np.array([1, 2]), length_m=np.array([300., 200.]),
        oneway=np.array([0, -1]), highway=['primary', 'residential'], way_id=[42, 43],
        speed_kmh=np.ones((2, 24))*25, free_kmh=np.array([25., 25.]),
        readings=np.array([0, 0]), estimated=np.array([True, True]),
        lanes=np.array([2., 0.]), name=['Synthetic east street', 'Synthetic north street'])


def elevation_override(points):
    return {'path': points, 'evidence': {
        'geometry': {'status': 'mapped', 'source_ids': ['traffic_model']},
        'elevation': {'status': 'modelled', 'source_ids': ['ground']}}}


GROUND = {'id': 'ground', 'kind': 'model', 'title': 'Synthetic graded ground',
          'as_of': None, 'limitations': 'Test fixture; not surveyed'}


def by_index(twin, index):
    return next(r for r in twin['roads'] if r['source_index'] == index)


def test_preserves_geometry_and_unknowns_and_exact_sumo_bindings():
    twin = build_street_twin(model(), origin_utm_m=(733000, 3750000, 20))
    road = by_index(twin, 0)
    assert road['path'] == [[0, 0, None], [100, 0, None], [300, 0, None]]
    assert road['evidence']['elevation']['status'] == 'unknown'
    assert road['evidence']['lane_count']['status'] == 'mapped'
    assert road['evidence']['lane_width']['status'] == 'assumed'
    assert [edge['sumo_edge_id'] for edge in road['edges']] == ['0:1', '0:-1']
    assert road['edges'][1]['lanes'][1]['sumo_lane_id'] == '0:-1_1'
    reverse = by_index(twin, 1)
    assert [edge['direction'] for edge in reverse['edges']] == [-1]
    assert reverse['edges'][0]['from_node'] == '2'
    assert reverse['evidence']['lane_count']['status'] == 'assumed'


def test_preserves_explicit_grade_without_changing_identity():
    baseline = build_street_twin(model())
    twin = build_street_twin(model(), origin_utm_m=(733000, 3750000, 20), sources=[GROUND],
                            overrides={0: elevation_override([[733000,3750000,20],
                                                              [733100,3750000,25],
                                                              [733300,3750000,35]])})
    assert by_index(twin, 0)['id'] == by_index(baseline, 0)['id']
    assert by_index(twin, 0)['path'] == [[0,0,0], [100,0,5], [300,0,15]]
    assert by_index(twin, 0)['evidence']['elevation']['status'] == 'modelled'
    assert by_index(twin, 1)['path'][0][2] is None  # Do not fill other roads by adjacency.


def test_tile_hashes_survive_source_array_reordering_and_subsetting():
    original = model()
    reordered = replace(original, geometry=original.geometry[::-1],
                        u=original.u[::-1], v=original.v[::-1],
                        length_m=original.length_m[::-1], oneway=original.oneway[::-1],
                        highway=original.highway[::-1], way_id=original.way_id[::-1],
                        lanes=original.lanes[::-1], name=original.name[::-1])
    a, b = build_street_twin(original), build_street_twin(reordered)
    assert a['tiles'] == b['tiles']
    assert {r['id'] for r in a['roads']} == {r['id'] for r in b['roads']}
    selected = build_street_twin(original, road_indices=[1])
    assert selected['roads'][0]['id'] == by_index(a, 1)['id']
    assert selected['roads'][0]['edges'][0]['sumo_edge_id'] == '1:-1'


def test_incremental_change_only_invalidates_owning_tile():
    a = build_street_twin(model(), tile_size_m=100)
    changed = build_street_twin(model(), tile_size_m=100, sources=[GROUND], overrides={
        0: elevation_override([[733000,3750000,20], [733100,3750000,25], [733300,3750000,35]])})
    before = {t['id']: t['content_hash'] for t in a['tiles']}
    after = {t['id']: t['content_hash'] for t in changed['tiles']}
    changed_ids = [key for key in before if before[key] != after[key]]
    assert changed_ids == [by_index(a, 0)['owner_tile_id']]
    assert len(by_index(a, 0)['tile_ids']) >= 3
    assert sum(r['id'] in tile['road_ids'] for tile in a['tiles'] for r in [by_index(a, 0)]) == 1


def test_deterministic_hash_and_evidence_updates_invalidate_content():
    a, b = build_street_twin(model()), build_street_twin(model())
    assert a == b
    digest = a.pop('content_hash')
    assert digest == content_hash(a)
    changed = build_street_twin(model(), sources=[GROUND], overrides={0: {
        'evidence': {'geometry': {'status': 'modelled', 'source_ids': ['ground']}}}})
    assert changed['content_hash'] != digest
    assert changed['tiles'] != b['tiles']


@pytest.mark.parametrize('overrides,sources,match', [
    ({0: {'lane_width_m': 3.5}}, [], 'require evidence'),
    ({0: {'evidence': {'geometry': {'status': 'mapped', 'source_ids': ['missing']}}}}, [], 'undeclared'),
    ({0: {'evidence': {'geometry': {'status': 'surveyed', 'source_ids': ['traffic_model']}}}}, [], 'survey sources'),
    ({0: {'evidence': {'elevation': {'status': 'modelled', 'source_ids': ['ground']}}}}, [GROUND], 'Incomplete elevation'),
    ({99: {}}, [], 'outside'),
    ({0: {'lane_width_m': float('nan')}}, [], 'finite'),
])
def test_rejects_untraceable_or_invalid_overrides(overrides, sources, match):
    with pytest.raises(ValueError, match=match):
        build_street_twin(model(), overrides=overrides, sources=sources)


def test_rejects_geographic_crs_and_ambiguous_duplicate_road_identity():
    with pytest.raises(ValueError, match='projected metre'):
        build_street_twin(replace(model(), crs='EPSG:4326'))
    duplicate = model()
    duplicate.geometry[1] = duplicate.geometry[0]
    duplicate.way_id[1] = duplicate.way_id[0]
    with pytest.raises(ValueError, match='Ambiguous'):
        build_street_twin(duplicate)
