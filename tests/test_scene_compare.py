import copy

import pytest

from dynacity.scene_compare import compare


def snap(n=5):
    return {'actors': {f'/A{i}': {'label': f'buildings_x_{i}', 'visible': True, 'hidden_in_game': False, 'mesh': f'/m{i}',
                                  'materials': ['/mat'], 'collision': 'QUERY_AND_PHYSICS', 'transform': [0., 1., 2., 0., 0., 0., 1., 1., 1.]}
                       for i in range(n)}}


def test_identical_snapshots_pass_and_count_comes_from_the_data():
    r = compare(snap(), snap())
    assert r['ok'] and r['compared_actors'] == 5 and r['unexpected'] == []


def test_only_the_allowlisted_exact_change_is_expected():
    pre, post = snap(), snap()
    post['actors']['/A2']['visible'] = False
    allowed = {'/A2': {'visible': (True, False)}}
    r = compare(pre, post, allowed)
    assert r['ok'] and len(r['expected_changes']) == 1
    post['actors']['/A3']['visible'] = False  # not on the allowlist
    r = compare(pre, post, allowed)
    assert not r['ok'] and r['unexpected'][0]['actor'] == '/A3'


def test_every_tracked_field_is_checked():
    for field, value in (('mesh', '/other'), ('materials', ['/x']), ('collision', 'NO_COLLISION'), ('transform', [0., 1., 2.5, 0., 0., 0., 1., 1., 1.])):
        pre, post = snap(), snap()
        post['actors']['/A1'][field] = value
        r = compare(pre, post)
        assert not r['ok'] and r['unexpected'][0]['field'] == field


def test_allowed_change_must_match_exactly():
    pre, post = snap(), snap()
    post['actors']['/A0']['visible'] = False
    assert not compare(pre, post, {'/A0': {'visible': (False, True)}})['ok']


def test_missing_and_new_actors_and_empty_pre():
    pre, post = snap(), snap()
    del post['actors']['/A4']
    assert not compare(pre, post)['ok']
    post = snap()
    post['actors']['/new'] = {'label': 'Corridor visual - x'}
    assert not compare(pre, post)['ok']
    assert compare(pre, post, created_label_prefixes=('Corridor ',))['ok']
    with pytest.raises(ValueError):
        compare({'actors': {}}, post)


def test_small_float_noise_is_tolerated_but_real_moves_are_not():
    pre, post = snap(), copy.deepcopy(snap())
    post['actors']['/A0']['transform'][0] += 1e-5
    assert compare(pre, post)['ok']
    post['actors']['/A0']['transform'][0] += 1.
    assert not compare(pre, post)['ok']
