import copy
import json
import struct

import numpy as np
import pytest

from dynacity.street_twin_finish import build_street_twin_finishes, export_street_twin_finish


def inputs(tmp_path):
    twin = {'content_hash': 'source-twin-hash',
            'coordinates': {'crs': 'EPSG:32636', 'origin_utm_m': [500000., 3750000., 20.],
                            'units': 'metres', 'axes': 'X east, Y north, Z up'},
            'roads': [{'id': 'road:a', 'source_index': 4, 'path': [[0., 0., 1.], [20., 0., 3.]],
                       'oneway': 1, 'lane_count_per_direction': 2, 'lane_width_m': 3.2,
                       'evidence': {}, 'edges': [{'id': 'road:a:1', 'direction': 1,
                       'sumo_edge_id': '4:1', 'lanes': [
                           {'id': 'road:a:1:0', 'index': 0, 'width_m': 3.2},
                           {'id': 'road:a:1:1', 'index': 1, 'width_m': 3.2}]}]}],
            'tiles': [{'id': 'pilot', 'road_ids': ['road:a']}]}
    network = tmp_path/'network.net.xml'
    network.write_text('''<net><location netOffset="-500000,-3750000"/>
      <edge id="4:1">
       <lane id="4:1_0" index="0" width="3.2" shape="0,-1.6 20,-1.6"/>
       <lane id="4:1_1" index="1" width="3.2" shape="0,1.6 20,1.6"/>
      </edge></net>''')
    return twin, network


def test_finishes_follow_actual_surface_grade_and_leave_junction_clearance(tmp_path):
    twin, network = inputs(tmp_path)
    scenes, omitted = build_street_twin_finishes(twin, net_xml=network)
    assert omitted == []
    scene = scenes['pilot']
    asphalt = [m for m in scene['meshes'] if m['kind'] == 'road']
    paint = [m for m in scene['meshes'] if m['kind'] == 'marking']
    assert len(asphalt) == 2
    assert len(paint) == 3
    assert [m['metadata']['finish_role'] for m in paint].count('edge_line') == 2
    for mesh in paint:
        v = np.asarray(mesh['vertices'])
        assert v[:, 0].min() >= 3
        assert v[:, 0].max() <= 17
        assert v[:, 2] == pytest.approx(1 + .1*v[:, 0] + .02)
        assert mesh['metadata']['evidence']['status'] == 'assumed'
        triangles = v[mesh['triangles']]
        assert (np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])[:, 2] > 0).all()
    divider = next(m for m in paint if m['metadata']['finish_role'] == 'lane_divider')
    # Dash 3..6 then 12..15, with a real six metre empty gap.
    x = np.asarray(divider['vertices'])[:, 0]
    assert not ((x > 6) & (x < 12)).any()


def test_export_keeps_asphalt_paint_materials_ids_and_cached_assets(tmp_path):
    twin, network = inputs(tmp_path)
    output = tmp_path/'finish'
    first = export_street_twin_finish(twin, output, net_xml=network)
    assert first['twin_content_hash'] == 'source-twin-hash'
    assert first['surveyed_markings'] is False
    asset = output/first['tiles'][0]['asset']
    stamp = asset.stat().st_mtime_ns
    second = export_street_twin_finish(twin, output, net_xml=network)
    assert first == second
    assert asset.stat().st_mtime_ns == stamp
    raw = asset.read_bytes()
    length, _ = struct.unpack_from('<I4s', raw, 12)
    doc = json.loads(raw[20:20+length])
    assert {doc['meshes'][n['mesh']]['primitives'][0]['material'] for n in doc['nodes']} == {1, 10}
    roles = [node['extras'].get('finish_role') for node in doc['nodes']]
    assert roles.count('edge_line') == 2
    assert roles.count('lane_divider') == 1
    assert all(node['extras']['sumo_lane_id'] in {'4:1_0', '4:1_1'} for node in doc['nodes'])


def test_filter_limits_processing_to_selected_tile(tmp_path):
    twin, network = inputs(tmp_path)
    other = copy.deepcopy(twin['roads'][0])
    other['id'] = 'road:b'
    other['path'] = [[0., 0., None], [20., 0., None]]
    twin['roads'].append(other)
    twin['tiles'].append({'id': 'other', 'road_ids': ['road:b']})
    scenes, omitted = build_street_twin_finishes(twin, net_xml=network, tile_ids=['pilot'])
    assert set(scenes) == {'pilot'}
    assert omitted == []
    with pytest.raises(ValueError, match='Unknown finish tile'):
        build_street_twin_finishes(twin, net_xml=network, tile_ids=['missing'])


def test_unknown_heights_never_receive_fabricated_paint(tmp_path):
    twin, network = inputs(tmp_path)
    twin['roads'][0]['path'][0][2] = None
    scenes, omitted = build_street_twin_finishes(twin, net_xml=network)
    assert scenes['pilot']['meshes'] == []
    assert len(omitted) == 2


def test_finish_requires_actual_network_and_valid_style(tmp_path):
    twin, network = inputs(tmp_path)
    with pytest.raises(ValueError, match='actual SUMO'):
        build_street_twin_finishes(twin, net_xml=None)
    with pytest.raises(ValueError, match='positive'):
        build_street_twin_finishes(twin, net_xml=network, style={'dash_m': 0})
    with pytest.raises(ValueError, match='Unknown'):
        build_street_twin_finishes(twin, net_xml=network, style={'kerb_height': .15})
