from types import SimpleNamespace

import numpy as np
from shapely.geometry import box

from dynacity.beirut001_pack import Beirut001Pack, bridge_decks
from dynacity.terrain import GroundModel


def pack_with(pieces, tops, ground_m=10.0):
    empty = np.zeros(0)
    ground = GroundModel(crs="EPSG:32636", x0=0.0, y0=1000.0, cell_m=10.0, ground=np.full((100, 100), ground_m))
    return Beirut001Pack(meta={}, building_ids=empty, buildings=[], base_z=empty, top_z=empty, ground_z=empty,
                         roof_levels=empty, bbed_objectid=empty, link_iou=empty, link_distance_m=empty,
                         bridges=pieces, bridge_z_min=np.zeros(len(pieces)), bridge_z_max=np.array(tops, float),
                         ground=ground)


def roads(*lines, levels):
    return SimpleNamespace(geometry=[list(line) for line in lines], level=np.array(levels))


def test_bridge_roads_take_the_deck_height_above_ground_of_the_pieces_they_run_along():
    deck = box(100, 495, 300, 505)
    model = roads([(100, 500), (300, 500)], [(100, 600), (300, 600)], [(100, 500), (300, 500)], levels=[1, 1, 0])
    # Deck top 22 m over ground at 10 m: 12 m. The second bridge has no piece; the third is at grade.
    assert bridge_decks(pack_with([deck], [22.0]), model) == {0: 12.0}


def test_implausible_heights_keep_the_assumed_deck():
    deck = box(100, 495, 300, 505)
    model = roads([(100, 500), (300, 500)], levels=[1])
    assert bridge_decks(pack_with([deck], [11.0]), model) == {}   # 1 m: a tunnel or missing ground
    assert bridge_decks(pack_with([deck], [90.0]), model) == {}   # 80 m: a mismatch
    assert bridge_decks(pack_with([deck], [22.0]), roads([(100, 500), (300, 500)], levels=[0])) == {}


def test_source_structure_mesh_keeps_above_ground_faces_and_clips_tunnel_faces():
    import base64
    from dataclasses import replace
    from dynacity.beirut001_pack import bridge_mesh_payload
    pack=replace(pack_with([], []), meta={'origin_xy':[0,0],'crs':'EPSG:32636'},
        bridge_triangles=np.array([[[100,500,12],[110,500,12],[100,510,12]],
                                   [[100,500,9],[110,500,9],[100,510,9]],
                                   [[100,500,9],[110,500,11],[100,510,11]]],dtype=float))
    payload=bridge_mesh_payload(pack)
    assert payload['triangles'] == 3 and payload['vertices'] == 9
    points=np.frombuffer(base64.b64decode(payload['positions']),dtype='<f4').reshape(-1,3)
    assert points[:,2].min() == 0 and points[:,2].max() == 2
    assert 'No columns or girders have been invented' in payload['provenance']
