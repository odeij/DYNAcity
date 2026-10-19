import json
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer

from conftest import write_las
from dynacity.point_cloud import PointCloudView, export_point_cloud
from dynacity.service import create_app


def test_point_export_preserves_every_position_rgb_and_explicit_z_offset(tmp_path):
    source = write_las(tmp_path / "measured.las", [
        (73297000, 375300000, 8000, 0, 65280, 32768, 0),
        (73297100, 375300100, 8300, 0, 0, 0, 65280),
    ])
    output = tmp_path / "view"
    meta = export_point_cloud(source, output, crs="EPSG:32636", crs_evidence="fixture")
    view = PointCloudView.load(output)
    xyz = np.frombuffer(view.positions, dtype="<f8").reshape(-1, 3)
    expected = Transformer.from_crs(32636, 4326, always_xy=True).transform([732970,732971], [3753000,3753001])
    assert meta["point_count"] == 2
    assert np.allclose(xyz[:,0], expected[0])
    assert np.allclose(xyz[:,1], expected[1])
    assert xyz[:,2].tolist() == [0, 3]
    assert np.frombuffer(view.colors, dtype="u1").tolist() == [255,128,0,0,0,255]
    assert meta["vertical_datum"] is None
    assert meta["display_z_offset"] == 80
    assert meta["sampling"] == "all source points; no decimation"
    client = TestClient(create_app(None, point_cloud=view))
    assert client.get('/v1/point-cloud').json()['metadata']['point_count'] == 2
    assert client.get('/v1/point-cloud/positions').content == view.positions
    assert client.get('/v1/point-cloud/colors').content == view.colors
    assert client.get('/v1/point-cloud/metadata.json').status_code == 404
    assert not TestClient(create_app(None)).get('/v1/point-cloud').json()['available']
    assert TestClient(create_app(None)).get('/v1/point-cloud/positions').status_code == 404
    (output / 'positions.bin').write_bytes(view.positions[:-1])
    with pytest.raises(ValueError, match='buffer size'):
        PointCloudView.load(output)


def test_point_export_keeps_unscaled_rgb_and_refuses_tampering(tmp_path):
    source = write_las(tmp_path / "measured.las", [(73297000,375300000,8000,0,12,34,56)])
    output = tmp_path / 'view'
    export_point_cloud(source, output, crs='EPSG:32636', crs_evidence='fixture')
    assert PointCloudView.load(output).colors == bytes([12,34,56])
    (output / 'colors.bin').write_bytes(bytes([13,34,56]))
    with pytest.raises(ValueError, match='checksum'):
        PointCloudView.load(output)


def test_visual_road_alignment_preserves_source_buffers_and_rejects_unknown_transform(tmp_path):
    source=write_las(tmp_path/'measured.las',[(73297000,375300000,8000,0,12,34,56)])
    output=tmp_path/'view'
    meta=export_point_cloud(source,output,crs='EPSG:32636',crs_evidence='fixture')
    original=PointCloudView.load(output)
    meta['display_alignment']={'kind':'visual_road_anchor','translation_z_m':-7.3,
                               'changes_source_coordinates':False}
    (output/'metadata.json').write_text(json.dumps(meta))
    aligned=PointCloudView.load(output)
    assert aligned.positions==original.positions
    assert aligned.colors==original.colors
    assert aligned.metadata['vertical_datum'] is None
    meta['display_alignment']['changes_source_coordinates']=True
    (output/'metadata.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='preserve source'):
        PointCloudView.load(output)
