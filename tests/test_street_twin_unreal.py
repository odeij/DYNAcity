import copy
import json
import struct

import numpy as np
import pytest

from dynacity.street_twin_unreal import build_unreal_scenes, export_street_twin_unreal, lane_ribbon


def twin(unknown=False):
    return {'coordinates': {'crs': 'EPSG:32636', 'origin_utm_m': [500000., 3750000., 20.],
                            'units': 'metres', 'axes': 'X east, Y north, Z up'},
            'roads': [{'id': 'road:a', 'source_index': 4, 'path': [[0., 0., None if unknown else 1.],
                                                                   [20., 0., None if unknown else 3.]],
                       'oneway': 0, 'lane_count_per_direction': 1, 'lane_width_m': 3.2,
                       'evidence': {'elevation': {'status': 'modelled', 'source_ids': ['city']}},
                       'edges': [{'id': 'road:a:1', 'direction': 1, 'sumo_edge_id': '4:1',
                                  'lanes': [{'id': 'road:a:1:0', 'index': 0, 'width_m': 3.2}]},
                                 {'id': 'road:a:-1', 'direction': -1, 'sumo_edge_id': '4:-1',
                                  'lanes': [{'id': 'road:a:-1:0', 'index': 0, 'width_m': 3.2}]}]}],
            'tiles': [{'id': '2000:15000', 'road_ids': ['road:a'], 'content_hash': 'core'}]}


def test_two_way_lanes_share_boundary_and_retain_grade():
    scenes, omitted = build_unreal_scenes(twin())
    assert omitted == []
    meshes = scenes['2000:15000']['meshes']
    forward, reverse = [m['metadata']['lane_path_local_m'] for m in meshes]
    assert np.asarray(forward) == pytest.approx(np.array([[0, -1.6, 1], [20, -1.6, 3]]))
    assert np.asarray(reverse) == pytest.approx(np.array([[20, 1.6, 3], [0, 1.6, 1]]))
    assert meshes[0]['vertices'][0] == meshes[1]['vertices'][-2]
    for mesh in meshes:
        v = np.asarray(mesh['vertices'])
        for face in mesh['triangles']:
            a, b, c = v[face]
            assert np.cross(b-a, c-a)[2] > 0


def test_netconvert_shapes_override_schematic_offsets_and_apply_projection(tmp_path):
    network = tmp_path/'network.net.xml'
    network.write_text('''<net><location netOffset="-499900,-3749900"/>
      <edge id="4:1"><lane id="4:1_0" index="0" width="3.2" shape="102,99 110,98 118,99"/></edge>
      <edge id="4:-1"><lane id="4:-1_0" index="0" width="3.2" shape="118,102 102,102"/></edge>
      <edge id=":internal" function="internal"><lane id=":internal_0" index="0" shape="0,0 1,1"/></edge>
    </net>''')
    scenes, omitted = build_unreal_scenes(twin(), net_xml=network)
    assert omitted == []
    metadata = scenes['2000:15000']['meshes'][0]['metadata']
    assert metadata['sumo_lane_id'] == '4:1_0'
    samples = np.asarray(metadata['lane_path_local_m'])
    assert samples[[0, 2, 4]] == pytest.approx(np.array([[2, -1, 1.2], [10, -2, 2], [18, -1, 2.8]]))
    assert (np.linalg.norm(np.diff(samples[:, :2], axis=0), axis=1) <= 5).all()
    assert metadata['xy_source'] == 'SUMO netconvert'


def test_unknown_elevation_omitted_without_fabricating_flat_mesh(tmp_path):
    manifest = export_street_twin_unreal(twin(unknown=True), tmp_path)
    assert len(manifest['omitted']) == 2
    assert all(r['reason'] == 'unknown source elevation' for r in manifest['omitted'])
    assert manifest['tiles'][0]['asset'] is None
    assert not list(tmp_path.glob('*.glb'))


def test_content_addressed_assets_reuse_and_export_true_gltf_basis(tmp_path):
    source = twin()
    first = export_street_twin_unreal(source, tmp_path)
    asset = tmp_path/first['tiles'][0]['asset']
    stamp = asset.stat().st_mtime_ns
    assert export_street_twin_unreal(source, tmp_path) == first
    assert asset.stat().st_mtime_ns == stamp
    raw = asset.read_bytes()
    magic, version, total = struct.unpack_from('<4sII', raw)
    assert (magic, version, total) == (b'glTF', 2, len(raw))
    json_length, _ = struct.unpack_from('<I4s', raw, 12)
    document = json.loads(raw[20:20+json_length])
    # glTF maps source local (east,north,up) to (east,up,south).
    assert document['accessors'][0]['min'] == pytest.approx([0, 1, -3.2])
    bindings = document['nodes'][0]['extras']['lane_bindings']
    assert [binding['lane_id'] for binding in bindings] == ['road:a:1:0', 'road:a:-1:0']
    assert [binding['first_triangle'] for binding in bindings] == [0, 2]
    changed = copy.deepcopy(source)
    changed['roads'][0]['path'][1][2] += 1
    second = export_street_twin_unreal(changed, tmp_path)
    assert second['tiles'][0]['asset'] != first['tiles'][0]['asset']
    assert asset.exists()
    assert second['runtime_streaming_configured'] is False


def test_existing_asset_corruption_is_detected(tmp_path):
    first = export_street_twin_unreal(twin(), tmp_path)
    (tmp_path/first['tiles'][0]['asset']).write_bytes(b'broken')
    with pytest.raises(ValueError, match='checksum'):
        export_street_twin_unreal(twin(), tmp_path)


def test_degenerate_lane_rejected():
    with pytest.raises(ValueError, match='Degenerate'):
        lane_ribbon([[0, 0, 0], [0, 0, 1]], 3.2)
    with pytest.raises(ValueError, match='doubles back'):
        lane_ribbon([[0, 0, 0], [10, 0, 0], [0, 0, 0]], 3.2)


def test_only_changed_tile_gets_a_new_asset(tmp_path):
    source = twin()
    other = copy.deepcopy(source['roads'][0])
    other['id'] = 'road:b'
    for edge in other['edges']:
        edge['id'] = edge['id'].replace('road:a', 'road:b')
        for lane in edge['lanes']:
            lane['id'] = lane['id'].replace('road:a', 'road:b')
    other['path'] = [[100., 0., 1.], [120., 0., 3.]]
    source['roads'].append(other)
    source['tiles'].append({'id': 'other-tile', 'road_ids': ['road:b']})
    first = export_street_twin_unreal(source, tmp_path)
    stable = tmp_path/first['tiles'][1]['asset']
    stamp = stable.stat().st_mtime_ns
    source['roads'][0]['path'][1][2] += 2
    second = export_street_twin_unreal(source, tmp_path)
    assert first['tiles'][0]['asset'] != second['tiles'][0]['asset']
    assert first['tiles'][1] == second['tiles'][1]
    assert stable.stat().st_mtime_ns == stamp


def test_net_lane_gaps_and_width_disagreement_are_not_silent(tmp_path):
    network = tmp_path/'network.net.xml'
    network.write_text('''<net><location netOffset="0,0"/>
      <edge id="4:1"><lane id="4:1_0" index="0" width="4" shape="500000,3750000 500020,3750000"/></edge>
    </net>''')
    scenes, omitted = build_unreal_scenes(twin(), net_xml=network)
    assert scenes['2000:15000']['meshes'] == []
    assert {item['reason'] for item in omitted} == {
        'SUMO lane width differs from canonical width', 'lane missing from SUMO network'}


def test_sparse_sumo_lane_keeps_known_source_midpoint_hill(tmp_path):
    source = twin()
    source['content_hash'] = 'canonical-provenance-hash'
    source['roads'][0]['path'] = [[0., 0., 1.], [7., 0., 8.], [20., 0., 3.]]
    network = tmp_path/'network.net.xml'
    network.write_text('''<net><location netOffset="-500000,-3750000"/>
      <edge id="4:1"><lane id="4:1_0" index="0" width="3.2" shape="2,-1.6 18,-1.6"/></edge>
      <edge id="4:-1"><lane id="4:-1_0" index="0" width="3.2" shape="18,1.6 2,1.6"/></edge>
    </net>''')
    scenes, omitted = build_unreal_scenes(source, net_xml=network)
    assert omitted == []
    for mesh in scenes['2000:15000']['meshes']:
        samples = np.asarray(mesh['metadata']['lane_path_local_m'])
        hill = samples[np.isclose(samples[:, 0], 7)]
        assert len(hill) == 1
        assert hill[0, 2] == pytest.approx(8.)
        assert np.abs(samples[:, 1]) == pytest.approx(np.full(len(samples), 1.6))
        assert sorted([samples[0, 0], samples[-1, 0]]) == pytest.approx([2, 18])
        assert (np.linalg.norm(np.diff(samples[:, :2], axis=0), axis=1) <= 5).all()
    manifest = export_street_twin_unreal(source, tmp_path/'assets', net_xml=network)
    assert manifest['twin_content_hash'] == 'canonical-provenance-hash'


def test_source_break_immediately_before_bend_does_not_fold_lane():
    from dynacity.street_twin_unreal import _elevate
    reference = [[0., 0.], [10., 0.], [10., 10.]]
    source = [[0., 0., 0.], [9.999, 0., 2.], [10., 0., 2.1], [10., 10., 3.]]
    samples = _elevate(reference, source)
    assert any(abs(p[0] - 9.999) < 1e-8 and abs(p[2] - 2) < 1e-8 for p in samples)
    vertices, faces = lane_ribbon(samples, 3.2, reference_xy=reference)
    assert len(faces) == 2 * (len(samples) - 1)
    assert np.isfinite(vertices).all()
    # Subdivision preserves the original mitered boundary polygon's area.
    original_vertices, original_faces = lane_ribbon([[*p, 0.] for p in reference], 3.2)
    def area(v, f):
        triangles = np.asarray(v)[f]
        return np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])[:, 2].sum()/2
    assert area(vertices, faces) == pytest.approx(area(original_vertices, original_faces))

