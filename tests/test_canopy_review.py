import copy
import hashlib
import json

import pytest

from dynacity.canopy_review import load_canopy_review
from dynacity.cli import _attach_canopy_review, build_parser


def inputs():
    features = [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [35.52 + i / 1000, 33.89]},
                 'properties': {'id': str(i), 'canopy_height_at_peak_m': 6 + i,
                                'position_local_m': [1, 2, 3], 'private_path': 'C:/private/source.tif'}}
                for i in range(2)]
    collection = {'type': 'FeatureCollection', 'features': features}
    screen = {'inputs_sha256': {}, 'native_trace_queue': [{'id': '0', 'conflicts': []}],
              'held_offline': [{'id': '1', 'conflicts': [{'kind': 'pedestrian', 'source_id': 'way/1', 'private': 'secret'}]}],
              'limits': ['C:/private/internal.json']}
    return collection, screen


def write_inputs(tmp_path, collection, screen):
    raw = json.dumps(collection).encode()
    screen = copy.deepcopy(screen)
    screen['inputs_sha256']['corridor-canopy-candidates.geojson'] = hashlib.sha256(raw).hexdigest()
    a, b = tmp_path / 'candidates.geojson', tmp_path / 'screen.json'
    a.write_bytes(raw)
    b.write_text(json.dumps(screen), encoding='utf-8')
    return a, b


def test_review_contract_preserves_locations_statuses_and_private_field_boundary(tmp_path):
    collection, screen = inputs()
    original = copy.deepcopy((collection, screen))
    a, b = write_inputs(tmp_path, collection, screen)
    result = load_canopy_review(a, b)
    assert result['counts'] == {'raw': 2, 'pending': 1, 'held': 1}
    assert [c['position'] for c in result['candidates']] == [[35.52, 33.89], [35.521, 33.89]]
    assert result['candidates'][1]['review_status'] == 'held'
    assert result['candidates'][1]['conflicts'] == [{'kind': 'pedestrian', 'source_id': 'way/1'}]
    assert result['source']['capture_date'] is None
    assert result['source']['license'] == 'CC BY 4.0'
    assert 'C:/private' not in json.dumps(result)
    assert 'position_local_m' not in json.dumps(result)
    assert (collection, screen) == original


@pytest.mark.parametrize('value', [True, 0, -1, float('nan'), float('inf'), '6'])
def test_rejects_invalid_height_even_with_matching_input_hash(tmp_path, value):
    collection, screen = inputs()
    collection['features'][0]['properties']['canopy_height_at_peak_m'] = value
    with pytest.raises(ValueError):
        load_canopy_review(*write_inputs(tmp_path, collection, screen))


@pytest.mark.parametrize('position', [[181, 33], [35, 86], [float('nan'), 33], [True, 33], [35, 33, 2], None])
def test_rejects_invalid_map_positions(tmp_path, position):
    collection, screen = inputs()
    collection['features'][0]['geometry']['coordinates'] = position
    with pytest.raises(ValueError):
        load_canopy_review(*write_inputs(tmp_path, collection, screen))


@pytest.mark.parametrize('fault', ['raw_duplicate', 'queue_duplicate', 'overlap', 'missing', 'unknown', 'pending_conflict', 'held_empty', 'wrong_height'])
def test_screening_identity_and_conflict_invariants(tmp_path, fault):
    collection, screen = inputs()
    if fault == 'raw_duplicate': collection['features'][1]['properties']['id'] = '0'
    if fault == 'queue_duplicate': screen['native_trace_queue'] *= 2
    if fault == 'overlap': screen['native_trace_queue'].append({'id': '1', 'conflicts': []})
    if fault == 'missing': screen['held_offline'] = []
    if fault == 'unknown': screen['held_offline'][0]['id'] = 'unknown'
    if fault == 'pending_conflict': screen['native_trace_queue'][0]['conflicts'] = [{'kind': 'pedestrian', 'source_id': 'way/1'}]
    if fault == 'held_empty': screen['held_offline'][0]['conflicts'] = []
    if fault == 'wrong_height': screen['held_offline'][0]['canopy_peak_height_m'] = 17
    with pytest.raises(ValueError):
        load_canopy_review(*write_inputs(tmp_path, collection, screen))


def test_hash_guard_rejects_changed_candidate_bytes(tmp_path):
    a, b = write_inputs(tmp_path, *inputs())
    a.write_bytes(a.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA256'):
        load_canopy_review(a, b)


def test_opt_in_cli_requires_both_files_and_preserves_default(tmp_path):
    parser = build_parser()
    args = parser.parse_args(['export-viewer', '--bbed', 'a', '--snapshot', 'b', '--output', 'c'])
    payload = {'buildings': []}
    _attach_canopy_review(payload, args)
    assert payload == {'buildings': []}
    args.canopy_review = 'one'
    with pytest.raises(SystemExit, match='together'): _attach_canopy_review(payload, args)
    args.canopy_review, args.canopy_screening = write_inputs(tmp_path, *inputs())
    _attach_canopy_review(payload, args)
    assert payload['canopy_review']['counts']['held'] == 1
