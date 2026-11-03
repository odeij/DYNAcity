import copy
from pathlib import Path

import pytest

from dynacity.reference_facade_profiles import (family_estimates, load_profiles, observations_for,
                                                to_pixels, validate_profiles)

PRIVATE = Path(r'C:\private\dynacity-data\engine\resemblance-20261101-2355\reference-assets\reference-profiles.json')
LGB = 'beirut001:d31eb718-f947-49d5-b82d-14838045a9c7'
CAND = 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1'


def _doc():
    return {
        'schema': 'dynacity-reference-facade-profiles/1',
        'photos': [{'photo_id': 'P1', 'file_sha256': 'a' * 64, 'photo_source_sha256': 'b' * 64, 'license': 'CC BY-SA 4.0',
                    'attribution': '(c) Grab and KartaView Contributors', 'source_url': 'https://example.test/p1',
                    'capture_local': '2018-07-21 06:42:32', 'original_untouched': True},
                   {'photo_id': 'P2', 'file_sha256': 'c' * 64, 'photo_source_sha256': 'd' * 64, 'license': 'CC BY-SA 4.0',
                    'attribution': '(c) Grab and KartaView Contributors', 'source_url': 'https://example.test/p2',
                    'capture_local': '2018-07-21 06:42:38', 'original_untouched': True}],
        'families': {'f': {'parameters_m': {'floor_height_m': {'est': 3.1, 'lo': 2.9, 'hi': 3.5, 'basis': 'estimated'}}}},
        'observations': [{'photo_id': 'P2', 'family': 'f', 'identity_status': f'{LGB} (corroborated)',
                          'polygon_norm': [[0.1, 0.1], [0.4, 0.1], [0.4, 0.8]], 'bbox_norm': [0.1, 0.1, 0.4, 0.8],
                          'openings': [{'kind': 'balcony_slab_edge', 'bbox_norm': [0.1, 0.2, 0.3, 0.3]}],
                          'visible_floors': {'est': 13, 'lo': 11, 'hi': 16, 'basis': 'estimated_from_image'},
                          'visible_bays': {'est': 4, 'lo': 3, 'hi': 5, 'basis': 'estimated_from_image'}, 'occlusion': 'unknown'}],
        'identity': {'proven': [{'source_id': LGB, 'photo_id': 'P2'}], 'candidates_only_not_assigned': [{'source_id': CAND}]},
        'cross_view_ties': [{'id': 'T', 'family': 'f', 'photos': ['P1', 'P2']}],
        'unseen_and_unknown': {'unknown_items': ['rear faces']},
    }


def test_valid_document_and_helpers():
    d = _doc()
    assert validate_profiles(d) == []
    assert family_estimates(d, 'f') == {'floor_height_m': 3.1}
    assert len(observations_for(d, photo_id='P2')) == 1 and observations_for(d, family='nope') == []
    assert to_pixels([[0.5, 0.25]], (1600, 1200)) == [[800.0, 300.0]]
    assert to_pixels([0.25, 0.5, 0.5, 1.0], (1600, 1200)) == [400.0, 600.0, 800.0, 1200.0]


@pytest.mark.parametrize('mutate, needle', [
    (lambda d: d['observations'][0]['polygon_norm'].append([1.2, 0.5]), 'polygon'),
    (lambda d: d['observations'][0]['polygon_norm'].append([float('nan'), 0.5]), 'polygon'),
    (lambda d: d['observations'][0]['openings'][0].update(bbox_norm=[0.3, 0.2, 0.1, 0.3]), 'bad bbox'),
    (lambda d: d['families']['f']['parameters_m']['floor_height_m'].update(lo=4.0), 'lo <= est <= hi'),
    (lambda d: d['families']['f']['parameters_m']['floor_height_m'].update(hi=float('inf')), 'non-finite'),
    (lambda d: d['observations'][0]['visible_floors'].update(est=20), 'lo <= est <= hi'),
    (lambda d: d['photos'][0].update(license='CC0'), 'license'),
    (lambda d: d['photos'][0].update(file_sha256='xyz'), 'sha256'),
    (lambda d: d['photos'][0].update(original_untouched=False), 'untouched'),
    (lambda d: d['observations'][0].update(photo_id='P9'), 'unknown photo'),
    (lambda d: d['observations'][0].pop('occlusion'), 'occlusion'),
    (lambda d: d['observations'][0].update(identity_status=f'{CAND} (looks right)'), 'candidate'),
    (lambda d: d['observations'][0].update(photo_id='P1'), 'proven for P2 only'),
    (lambda d: d['cross_view_ties'][0]['photos'].append('P7'), 'tie'),
    (lambda d: d['unseen_and_unknown'].update(unknown_items=[]), 'unseen'),
])
def test_validator_rejects_broken_documents(mutate, needle):
    d = copy.deepcopy(_doc())
    mutate(d)
    errs = validate_profiles(d)
    assert errs and any(needle in e for e in errs), errs


def test_wrong_schema_short_circuits():
    d = _doc(); d['schema'] = 'other'
    assert validate_profiles(d) == ['schema must be dynacity-reference-facade-profiles/1']


@pytest.mark.skipif(not PRIVATE.exists(), reason='private reference-profiles.json not on this machine')
def test_private_profiles_are_valid_and_cover_all_four_views():
    d = load_profiles(PRIVATE)
    assert {o['photo_id'] for o in d['observations']} == {'1288770821', '1288770597', '1288771293', '1288770701'}
    assert {'modern_stone_recessed_balcony', 'cream_yellow_older_balcony', 'construction_tower_frame', 'lgb_cream_redbrown_bands'} <= set(d['families'])
    # the only building ID asserted anywhere is the reviewed LGB tower, and only in 1288771293
    assert [x['source_id'] for x in d['identity']['proven']] == [LGB]
    lgb = observations_for(d, family='lgb_cream_redbrown_bands', photo_id='1288771293')
    assert LGB in lgb[0]['identity_status'] and len(lgb[0]['frame_polygons_norm']) == 5
