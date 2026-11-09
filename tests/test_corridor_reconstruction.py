import numpy as np
import pytest

from dynacity.corridor_reconstruction import (backing_parts, building_ring, frontage_basis, place_parts, street_frontages,
                                              triangle_normals, wall_coverage, write_glb)
from dynacity.glb_geometry import mesh_primitives

MAT = {'m': {'base_color': [.5, .5, .5, 1.]}}


def box(x0, y0, x1, y1, z0, z1):
    """Extruded rectangle: wall triangles only, counter-clockwise footprint."""
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pos, tri = [], []
    for a, b in zip(ring, ring[1:] + ring[:1]):
        k = len(pos)
        pos += [(*a, z0), (*b, z0), (*b, z1), (*a, z1)]
        tri += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
    return np.array(pos, float), np.array(tri)


def outward_quad(w=4., h=3., y=0.):
    """Quad on the wall plane in the producer frame; right-hand-rule normal is -Y (street-facing)."""
    return {'name': 'q', 'material': 'm', 'vertices': np.array([[0, y, 0], [w, y, 0], [w, y, h], [0, y, h]], float),
            'triangles': np.array([[0, 1, 2], [0, 2, 3]])}


def south_front():
    pos, tri = box(0, 0, 20, 10, 5, 35)
    return street_frontages(building_ring(pos, tri), (10, -50), 5, 35)[0]


def test_ring_is_ccw_and_frontage_normals_point_outward():
    pos, tri = box(0, 0, 20, 10, 0, 30)
    ring = building_ring(pos, tri)
    assert len(ring) == 4
    area = .5 * np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1])
    assert area == pytest.approx(200.)
    fronts = street_frontages(ring, (10, -50), 0, 30)
    assert len(fronts) == 1 and fronts[0]['length_m'] == pytest.approx(20.)
    assert fronts[0]['normal'] == pytest.approx([0., -1.])
    assert not street_frontages(ring, (10, 5), 0, 30, min_facing=0.9)


def test_basis_is_a_proper_rotation_and_placement_keeps_faces_outward():
    front = south_front()
    assert np.linalg.det(frontage_basis(front)) == pytest.approx(1.)
    q = outward_quad()
    assert triangle_normals(q['vertices'], q['triangles']) == pytest.approx(np.array([[0., -1., 0.]] * 2))  # producer: street = -Y
    placed = place_parts([q], front, along_m=2.)[0]
    assert placed['vertices'][:, 2].min() == pytest.approx(5.) and placed['vertices'][:, 2].max() == pytest.approx(8.)
    assert (placed['triangles'] == q['triangles']).all()  # no winding change under a rotation
    # the quad lies on the south wall and its winding normal must point outward (south = -y), i.e. along front['normal']
    assert triangle_normals(placed['vertices'], placed['triangles']) == pytest.approx(np.array([[0., -1., 0.]] * 2))


def test_depth_goes_into_the_building_and_x_runs_to_the_viewers_right():
    front = south_front()
    part = {'name': 's', 'material': 'm', 'vertices': np.array([[0, -1.5, 0.], [4, -1.5, 0], [4, 0, 0], [0, 0, 0]]),
            'triangles': np.array([[0, 1, 2], [0, 2, 3]])}  # a slab projecting 1.5 m toward the street (-Y)
    v = place_parts([part], front)[0]['vertices']
    assert v[:, 1].min() == pytest.approx(-1.5) and v[:, 1].max() == pytest.approx(0.)  # street side = south
    inward = place_parts([{**part, 'vertices': part['vertices'] + [0, 3., 0]}], front)[0]['vertices']
    assert inward[:, 1].max() == pytest.approx(3.)  # +Y runs into the building (north)
    # a viewer south of the wall looking north has east on his right: X runs east from the frontage start
    assert front['tangent'] == pytest.approx([1., 0.]) and v[:, 0].min() == pytest.approx(0.) and v[:, 0].max() == pytest.approx(4.)


def test_basis_agrees_with_the_producers_placement_matrix_for_rotated_frontages():
    from dynacity.reference_facade_geometry import placement_matrix
    for deg in (0, 37, 90, 133, 215, 300):
        t = np.array([np.cos(np.radians(deg)), np.sin(np.radians(deg))])
        front = {'start': [3., 4.], 'tangent': t.tolist(), 'normal': [t[1], -t[0]], 'length_m': 10., 'base_z': 2.}
        m = placement_matrix((3., 4., 2.), tuple(t))
        assert frontage_basis(front) == pytest.approx(m[:3, :3])  # same rotation: no mirror, no double flip


def test_normals_survive_the_glb_round_trip(tmp_path):
    placed = place_parts([outward_quad()], south_front())
    write_glb(tmp_path / 'n.glb', placed, MAT)
    pos, tri = mesh_primitives(tmp_path / 'n.glb')['corridor_reconstruction']
    assert triangle_normals(pos, tri) == pytest.approx(np.array([[0., -1., 0.]] * 2))
    roof = {'name': 'r', 'material': 'm', 'vertices': np.array([[0, 0, 5.], [1, 0, 5], [1, 1, 5], [0, 1, 5]]),
            'triangles': np.array([[0, 1, 2], [0, 2, 3]])}
    write_glb(tmp_path / 'r.glb', [roof], MAT)
    pos, tri = mesh_primitives(tmp_path / 'r.glb')['corridor_reconstruction']
    assert triangle_normals(pos, tri)[:, 2] == pytest.approx([1., 1.])


def test_glb_keeps_positions_and_triangle_order(tmp_path):
    v = np.array([[1., 2., 3.], [4., 2., 3.], [4., 6., 3.], [1., 6., 3.]])
    part = {'name': 'p', 'material': 'm', 'vertices': v, 'triangles': np.array([[0, 1, 2], [0, 2, 3]])}
    assert write_glb(tmp_path / 'a.glb', [part], MAT)['triangles'] == 2
    pos, tri = mesh_primitives(tmp_path / 'a.glb')['corridor_reconstruction']
    assert pos == pytest.approx(v) and (tri == part['triangles']).all()


def test_glb_multi_material_single_mesh(tmp_path):
    v = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.]])
    parts = [{'name': 'a', 'material': 'm1', 'vertices': v, 'triangles': np.array([[0, 1, 2]])},
             {'name': 'b', 'material': 'm2', 'vertices': v + 5, 'triangles': np.array([[0, 1, 2]])}]
    info = write_glb(tmp_path / 'b.glb', parts, {'m1': {'base_color': [1, 0, 0, 1]}, 'm2': {'base_color': [0, 1, 0, 1]}})
    assert info['materials'] == ['m1', 'm2']
    pos, tri = mesh_primitives(tmp_path / 'b.glb')['corridor_reconstruction']
    assert len(pos) == 6 and len(tri) == 2


def test_coverage_detects_gaps_and_backing_closes_them():
    assert wall_coverage([outward_quad(10., 6.)], 10., 6.) > .99
    half = [{'name': 'a', 'material': 'm', 'vertices': np.array([[0, 0, 0], [5, 0, 0], [5, 0, 6], [0, 0, 6]], float),
             'triangles': np.array([[0, 1, 2], [0, 2, 3]])}]
    assert .45 < wall_coverage(half, 10., 6.) < .55
    back = backing_parts(10., 6., 1.4)
    assert wall_coverage(back[:1] + half, 10., 6.) > .99
    assert back[0]['vertices'][:, 1].min() > 1.4  # deeper into the building than any recess back wall
    assert triangle_normals(back[0]['vertices'], back[0]['triangles'])[:, 1] == pytest.approx([-1., -1.])
    assert triangle_normals(back[1]['vertices'], back[1]['triangles'])[:, 0] == pytest.approx([1., 1.])
    assert triangle_normals(back[2]['vertices'], back[2]['triangles'])[:, 0] == pytest.approx([-1., -1.])


def test_frontage_wall_triangles_select_only_the_street_wall():
    from dynacity.corridor_reconstruction import frontage_wall_triangles
    pos, tri = box(0, 0, 20, 10, 5, 35)
    front = street_frontages(building_ring(pos, tri), (10, -50), 5, 35)[0]
    chosen = frontage_wall_triangles(pos, tri, front)
    assert len(chosen) == 2 and set(chosen) == {0, 1}  # the south wall quad only; east/north/west walls stay
    roof = np.array([[0, 0, 35], [20, 0, 35], [20, 10, 35]], float)
    pos2 = np.vstack([pos, roof]); tri2 = np.vstack([tri, [[len(pos), len(pos) + 1, len(pos) + 2]]])
    assert len(frontage_wall_triangles(pos2, tri2, front)) == 2  # a roof triangle sharing the edge is not a wall


L_SHAPE = np.array([[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]], float)  # concave, counter-clockwise, area 64


def test_triangulation_covers_a_concave_polygon_exactly():
    from dynacity.corridor_reconstruction import polygon_area, triangulate_polygon
    for ring in (L_SHAPE, L_SHAPE[::-1]):
        tris = triangulate_polygon(ring)
        assert len(tris) == len(ring) - 2
        area = sum(.5 * ((ring[b][0] - ring[a][0]) * (ring[c][1] - ring[a][1]) - (ring[b][1] - ring[a][1]) * (ring[c][0] - ring[a][0])) for a, b, c in tris)
        assert area == pytest.approx(abs(polygon_area(ring)))  # all counter-clockwise, summing to the polygon
        assert abs(polygon_area(ring)) == pytest.approx(64.)


def test_prism_is_closed_with_outward_normals():
    from dynacity.corridor_reconstruction import polygon_prism
    v, f = polygon_prism(L_SHAPE, 2., 5.)
    n = triangle_normals(v, f)
    centre = v.mean(0)
    mid = v[f].mean(1)
    # signed volume by the divergence theorem is positive for outward winding: 64 m2 x 3 m
    vol = np.sum(np.einsum('ij,ij->i', v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]]))) / 6.
    assert vol == pytest.approx(64. * 3.)
    assert (n[:, 2] > .99).sum() >= 4 and (n[:, 2] < -.99).sum() >= 4  # top and bottom caps are present and opposed


def test_raised_frame_is_perimeter_complete_and_inside_the_footprint():
    from dynacity.corridor_reconstruction import raised_frame
    parts = {p['name']: p for p in raised_frame(L_SHAPE, 20., 38., floor_h=3.1)}
    cols, plates = parts['raised_columns'], parts['raised_floor_plates']
    n_cols = len(cols['vertices']) // 8
    assert n_cols >= len(L_SHAPE)  # at least one per ring vertex, more on long edges
    cz = cols['vertices'][:, 2]
    assert cz.min() == pytest.approx(20.) and cz.max() == pytest.approx(38.)
    xy = cols['vertices'][:, :2]
    assert xy.min() > 0. and xy.max() < 10.  # strictly inside the footprint's bounding box
    # every ring edge has a column within max_span of each of its ends
    centres = xy.reshape(-1, 8, 2).mean(1)
    for a in L_SHAPE:
        assert np.min(np.linalg.norm(centres - a, axis=1)) < 1.0
    pz = plates['vertices'][:, 2]
    assert pz.max() == pytest.approx(38.) and pz.min() > 20.  # the first plate sits a storey above the existing roof
    assert len(plates['triangles']) % (len(L_SHAPE) * 2 + (len(L_SHAPE) - 2) * 2) == 0
    # no column box corner leaves the concave footprint (the notch quadrant x>4, y>4 is empty)
    assert not ((xy[:, 0] > 4.0) & (xy[:, 1] > 4.0)).any()


def test_envelope_prism_skips_the_facade_edge_and_faces_outward():
    from dynacity.corridor_reconstruction import envelope_prism
    ring = np.array([[0, 0], [20, 0], [20, 10], [0, 10]], float)
    v, f = envelope_prism(ring, 6., 20., skip_edges=(0,))  # edge 0 is the south wall: its facade is built separately
    n = triangle_normals(v, f)
    assert len(f) == 2 + 3 * 2  # roof cap (2 triangles) + three side walls (2 each); no south wall, no bottom
    assert (n[:, 2] > .99).sum() == 2  # roof cap faces up
    side = n[n[:, 2] < .5]
    assert not (side[:, 1] < -.99).any()  # nothing faces south: the facade edge is open
    for want in ([1., 0.], [0., 1.], [-1., 0.]):
        assert (np.abs(side[:, :2] - want).sum(1) < 1e-9).sum() == 2
    assert v[:, 2].min() == pytest.approx(6.) and v[:, 2].max() == pytest.approx(20.)
    with pytest.raises(ValueError):
        envelope_prism(ring[::-1], 6., 20.)
