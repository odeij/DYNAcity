import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_appearance_citywide import street_groups, valid_tile, valid_street
from dynacity.building_appearance_evidence import digest


def test_street_group_ownership_handles_negative_tiles():
    records = [{'source_id': 'a', 'owner_tile': '-1:0'},
               {'source_id': 'b', 'owner_tile': '-2:1'},
               {'source_id': 'c', 'owner_tile': '0:0'}]
    groups = street_groups(records)
    assert [r['source_id'] for r in groups['-1:0']] == ['a', 'b']
    assert [r['source_id'] for r in groups['0:0']] == ['c']


def test_resume_rejects_modified_tile_output(tmp_path):
    evidence = tmp_path/'evidence.json'; evidence.write_text('{}')
    page = tmp_path/'review.html'; page.write_text('<p>ok</p>')
    (tmp_path/'result.json').write_text(json.dumps({'evidence_sha256': digest(evidence),
                                                    'review_sha256': digest(page)}))
    assert valid_tile(tmp_path)
    evidence.write_text('{"changed":true}')
    assert not valid_tile(tmp_path)


def test_resume_rejects_missing_or_changed_photo(tmp_path):
    query = tmp_path/'street-query.json'; query.write_text('{}')
    photo = tmp_path/'1.jpg'; photo.write_bytes(b'image')
    (tmp_path/'result.json').write_text(json.dumps({'query_sha256': digest(query),
                         'photos': [{'file': '1.jpg', 'sha256': digest(photo)}]}))
    assert valid_street(tmp_path)
    photo.write_bytes(b'other')
    assert not valid_street(tmp_path)
