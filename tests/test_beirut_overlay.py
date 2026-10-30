import json

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import box, mapping

from dynacity.beirut_overlay import match_footprints, register_rigid


def test_only_strong_unique_overlap_links_and_missing_remains_missing():
    links = match_footprints([box(0, 0, 10, 10), box(50, 50, 60, 60), box(8, 0, 18, 10)], [box(0, 0, 10, 10)])
    assert links[0]['status'] == 'provisional' and links[0]['bbed_index'] == 0
    assert links[1]['status'] == 'unlinked' and 'bbed_index' not in links[1]
    assert links[2]['status'] == 'unlinked' and 'bbed_index' not in links[2]


def test_duplicate_solids_and_duplicate_records_do_not_get_silent_links():
    p = box(0, 0, 10, 10)
    assert all(x['status'] == 'unlinked' for x in match_footprints([p, p], [p]))
    link = match_footprints([p], [p, p])[0]
    assert link['status'] == 'unlinked' and 'bbed_index' not in link


# A small irregular "city": the model in local metres, BBED the same footprints
# turned 2° and moved into EPSG:32636, as the real model relates to BBED.
TURN_DEG, SHIFT = 2.0, (732000.0, 3752400.0)


def _city(n=40, seed=3):
    rng = np.random.default_rng(seed)
    corners = rng.uniform(0, 900, size=(n, 2))
    sizes = rng.uniform(8, 25, size=(n, 2))
    boxes = [box(x, y, x + w, y + d) for (x, y), (w, d) in zip(corners, sizes)]
    keep = []
    for b in boxes:  # no overlapping buildings, as in a real footprint layer
        if all(not b.buffer(3).intersects(k) for k in keep):
            keep.append(b)
    return keep


def _placed(geom):
    return affinity.translate(affinity.rotate(geom, TURN_DEG, origin=(0, 0)), *SHIFT)


def test_register_rigid_recovers_a_known_turn_and_shift():
    model = _city()
    a = [b.centroid.coords[0] for b in model]
    b = [_placed(g).centroid.coords[0] for g in model]
    rotation, shift, report = register_rigid(a, b)
    assert report['rotation_degrees'] == pytest.approx(TURN_DEG, abs=.01)
    assert shift == pytest.approx(SHIFT, abs=.05)
    assert report['bbed_to_model_percentiles_m'][-1] < .05


def _prism(rhino3dm, footprint, z0, z1):
    mesh = rhino3dm.Mesh()
    ring = list(footprint.exterior.coords)[:-1]
    for x, y in ring:
        mesh.Vertices.Add(x, y, z0)
    for x, y in ring:
        mesh.Vertices.Add(x, y, z1)
    n = len(ring)
    mesh.Faces.AddFace(0, 1, 2, 3)
    mesh.Faces.AddFace(n, n + 1, n + 2, n + 3)
    for i in range(n):
        j = (i + 1) % n
        mesh.Faces.AddFace(i, j, n + j, n + i)
    return mesh


@pytest.fixture
def pack_inputs(tmp_path):
    rhino3dm = pytest.importorskip("rhino3dm")
    model = rhino3dm.File3dm()
    layers = {name: model.Layers.AddLayer(name, (0, 0, 0, 255))
              for name in ("01_TOPOGRAPHY", "04_TUNNELS AND BRIDGES", "05_BUILDINGS")}

    def add(mesh, layer):
        attributes = rhino3dm.ObjectAttributes()
        attributes.LayerIndex = layers[layer]
        model.Objects.AddMesh(mesh, attributes)

    city = _city()
    ground = 10.0
    heights = []
    for i, footprint in enumerate(city):
        height = 12.0 + i
        heights.append(height)
        add(_prism(rhino3dm, footprint, ground - 5, ground + height), "05_BUILDINGS")  # sunk 5 m, like the source
    plane = rhino3dm.Mesh()
    for x, y in [(-50, -50), (1000, -50), (1000, 1000), (-50, 1000)]:
        plane.Vertices.Add(x, y, ground)
    plane.Faces.AddFace(0, 1, 2, 3)
    add(plane, "01_TOPOGRAPHY")
    add(_prism(rhino3dm, box(400, -40, 420, -10), 15, 16), "04_TUNNELS AND BRIDGES")
    path = tmp_path / "model.3dm"
    assert model.Write(str(path), 5)
    features = [{"type": "Feature", "geometry": mapping(_placed(f)),
                 "properties": {"OBJECTID": 100 + i, "NoofFloor": 4, "Building_Hight_m": 12}}
                for i, f in enumerate(city)]
    bbed = tmp_path / "bbed.geojson"
    bbed.write_text(json.dumps({"type": "FeatureCollection", "features": features,
                                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32636"}}}))
    return path, bbed, city, heights


def test_pack_keeps_outlines_heights_links_and_ground(pack_inputs, tmp_path):
    from dynacity.beirut001_pack import Beirut001Pack, build_pack
    from dynacity.terrain import GroundModel

    model, bbed, city, heights = pack_inputs
    pack = build_pack(model, bbed, cell_m=20)
    assert pack.meta["counts"]["buildings"] == len(city)
    assert pack.meta["registration"]["rotation_degrees"] == pytest.approx(TURN_DEG, abs=.01)
    # Every building finds its own BBED record, and is drawn from the ground up.
    assert sorted(pack.bbed_objectid.tolist()) == [100 + i for i in range(len(city))]
    order = np.argsort(pack.bbed_objectid)
    assert pack.heights()[order] == pytest.approx(heights, abs=.01)
    assert pack.buildings[order[0]].symmetric_difference(_placed(city[0])).area < .01
    assert pack.meta["counts"]["bridges"] == 1 and pack.bridge_z_max[0] == pytest.approx(16)
    assert pack.bridge_triangles.shape == (12, 3, 3)

    path = tmp_path / "pack.npz"
    pack.save(path)
    again = Beirut001Pack.load(path)
    np.testing.assert_allclose(again.bridge_triangles, pack.bridge_triangles, atol=.001)
    assert again.building_ids.tolist() == pack.building_ids.tolist()
    assert again.buildings[0].symmetric_difference(pack.buildings[0]).area < .01
    assert again.heights() == pytest.approx(pack.heights(), abs=.01)
    # The ground grid doubles as a --terrain file.
    terrain = GroundModel.load(path)
    centre = _placed(box(450, 450, 451, 451)).centroid
    assert terrain.sample([centre.x], [centre.y])[0] == pytest.approx(10.0, abs=.01)


def test_main_map_serves_pack_buildings_only_when_loaded(pack_inputs, tmp_path):
    from fastapi.testclient import TestClient
    from dynacity.beirut001_pack import build_pack, viewer_payload
    from dynacity.service import create_app
    from test_models_engine_api import engine_fixture

    model, bbed, city, _ = pack_inputs
    payload = viewer_payload(build_pack(model, bbed, cell_m=20), json.loads(bbed.read_text()))
    assert len(payload["bbed"]) == len(city) and payload["matched"] == len(city)
    lon, lat = payload["buildings"][0]["parts"][0][0][0]
    assert 35.4 < lon < 35.6 and 33.8 < lat < 33.95  # Beirut
    client = TestClient(create_app(engine_fixture(), beirut001=payload))
    assert client.get('/v1/beirut001').json() == {'available': True}
    response = client.get('/v1/beirut001/buildings')
    assert len(response.json()['buildings']) == len(city)
    assert response.headers['cache-control'] == 'private, no-store'
    client = TestClient(create_app(engine_fixture()))
    assert client.get('/v1/beirut001').json() == {'available': False}
    assert client.get('/v1/beirut001/buildings').status_code == 404
