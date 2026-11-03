import hashlib
import json
import struct

import pytest

from dynacity.engine_street_linear_assets import build, line_mesh


def test_sloping_wall_respects_height_and_omits_missing_ground_gap():
    line = {'kind': 'wall', 'tags': {'height': '2.4 m', 'width': '.3'},
            'points_local_m': [[0, 0, 3], [3, 0, 4], [20, 0, 8], [23, 0, 9]]}
    verts, faces, meta = line_mesh(line)
    assert len(faces) == 20
    assert max(p[2] for p in verts) == pytest.approx(11.4)
    assert meta['height_source'] == 'OSM height tag'
    assert meta['skipped_segments'] == {'missing_ground_gap': 1}
    assert not any(min(verts[i][0] for i in tri) < 10 < max(verts[i][0] for i in tri)
                   for tri in faces)


def test_export_retains_source_hash_and_ids_excludes_bridge_and_railway(tmp_path):
    lines = [{'id': 'way/7', 'kind': 'fence', 'production_area': 'Ras Beirut',
              'render_geometry': True, 'tags': {'barrier': 'fence'},
              'points_local_m': [[0, 0, 1], [3, 0, 1.5]]}]
    lines += [{**lines[0], 'id': 'way/8', 'bridge_tagged': True},
              {**lines[0], 'id': 'way/9', 'kind': 'railway'}]
    source = tmp_path/'source.json'
    source.write_text(json.dumps({'schema': 'dynacity-engine-street-context/1',
                                 'origin_utm_m': [1, 2, 3], 'lines': lines}))
    result = json.loads(build(source, tmp_path).read_text())
    assert result['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['origin_utm_m'] == [1, 2, 3]
    assert result['skipped'] == {'provenance_only': 1}
    package, = result['packages']
    assert package['key'] == 'linear_ras_beirut_fence'
    assert [o['source_id'] for o in package['objects']] == ['way/7']
    assert package['objects'][0]['height_source'] == 'illustrative fallback'
    raw = __import__('pathlib').Path(package['asset']).read_bytes()
    magic, version, length = struct.unpack_from('<4sII', raw)
    assert (magic, version, length) == (b'glTF', 2, len(raw))
    doc_size = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20+doc_size])
    assert doc['materials'][0]['pbrMetallicRoughness']['baseColorFactor'] == [.24, .26, .25, 1]
