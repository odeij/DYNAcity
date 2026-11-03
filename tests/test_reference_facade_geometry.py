import json
import math

import numpy as np
import pytest

from dynacity import reference_facade_geometry as G
from dynacity.glb_geometry import mesh_primitives, read_glb
from dynacity.reference_facade_geometry import FAMILIES, mesh_for_profile

SIZES = {'modern_stone_recessed_balcony': (25, 36), 'cream_yellow_older_balcony': (14, 20), 'older_brown_midrise': (10.5, 21),
         'construction_tower_frame': (12, 48), 'lgb_cream_redbrown_bands': (18, 48)}


def _signed_volume(v, f):
    t = v[f].astype(float)
    return float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)


@pytest.mark.parametrize('family', FAMILIES)
def test_geometry_is_finite_clipped_and_inside_the_frame(family):
    W, H = SIZES[family]
    m = mesh_for_profile(family, W, H)
    lo, hi = m.bounds
    assert np.isfinite(np.array([lo, hi])).all()
    assert lo[2] >= 0.0 and hi[2] == pytest.approx(H, abs=1e-4)      # roof height is exactly the supplied one
    assert lo[0] >= -1e-6 and hi[1] <= m.provenance['size_m']['depth'] + 1e-6
    overhang = G.DEFAULTS[family].get('net_offset_m')
    assert hi[0] <= W + (overhang.hi + 0.1 if overhang else 1e-6)    # only the construction net may leave the plan
    assert lo[1] < 0 or family == 'construction_tower_frame'          # relief projects toward the street (-Y)
    assert m.triangle_count > 1000


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('W,H', [(3.0, 3.0), (6.0, 6.5), (9.0, 12.0), (31.7, 80.3), (60.0, 120.0)])
def test_roof_clip_holds_at_every_size(family, W, H):
    m = mesh_for_profile(family, W, H)
    lo, hi = m.bounds
    assert hi[2] <= H + 1e-4 and lo[2] >= 0
    assert all(np.isfinite(p['verts']).all() for p in m.parts.values())
    assert hi[0] >= 0.9 * W                                           # still spans the supplied frontage


@pytest.mark.parametrize('family', FAMILIES)
def test_every_part_is_outward_wound_solid(family):
    m = mesh_for_profile(family, *SIZES[family])
    for name, p in m.parts.items():
        assert _signed_volume(p['verts'], p['faces']) > 0, name
        assert p['faces'].min() >= 0 and p['faces'].max() < len(p['verts'])
        areas = np.linalg.norm(np.cross(*(np.diff(p['verts'][p['faces']].astype(float), axis=1)[:, :2].transpose(1, 0, 2))), axis=1)
        assert (areas > 1e-9).all(), f'degenerate triangle in {name}'


def test_primitives_are_watertight_with_outward_normals():
    for v, f in (G._box(0, 2, 0, 3, 0, 4), G._prism([(0, 0), (4, 0), (4, 2), (1, 3), (0, 2)], 1, 5)):
        edges = {}
        for tri in f:
            for a, b in ((0, 1), (1, 2), (2, 0)):
                edges[(tri[a], tri[b])] = edges.get((tri[a], tri[b]), 0) + 1
        assert all(edges.get((b, a)) == 1 and c == 1 for (a, b), c in edges.items())   # each edge once per direction
        c0 = v.mean(0)
        t = v[f]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        assert (np.einsum('ij,ij->i', n, t.mean(1) - c0) > 0).all()
        assert _signed_volume(v, f) > 0


def test_provenance_and_bounds_flags():
    m = mesh_for_profile('lgb_cream_redbrown_bands', 18, 48)
    pv = m.provenance
    assert pv['family'] == 'lgb_cream_redbrown_bands' and pv['photo_ids'][0] == '1288771293'
    assert 'CC BY-SA 4.0' in pv['license'] and 'KartaView' in pv['license']
    assert pv['no_billboards'] and not pv['generated_imagery'] and pv['offline_component_not_native_proof']
    assert pv['identity']['proven_source_id'] == G.LGB_PROVEN_ID and 'NOT a reconstruction' in pv['identity']['statement']
    assert all(v['basis'] == 'estimated' and v['lo'] <= v['value'] <= v['hi'] for k, v in pv['parameters'].items() if v['within_bounds'])
    assert pv['dimensions_basis'].startswith('estimated')
    other = mesh_for_profile('cream_yellow_older_balcony', 14, 20).provenance
    assert 'proven_source_id' not in other['identity']
    for p in m.parts.values():
        assert p['meta']['evidence'] in ('observed', 'estimated', 'generic')
        if p['meta']['evidence'] == 'observed':
            assert p['meta']['photo_ids']
    out = mesh_for_profile('modern_stone_recessed_balcony', 25, 36, params={'gallery_depth_m': 2.5})
    assert any('gallery_depth_m' in w for w in out.provenance['warnings'])
    assert not out.provenance['parameters']['gallery_depth_m']['within_bounds']


def test_modern_has_recessed_galleries_piers_and_projecting_fascia():
    m = mesh_for_profile('modern_stone_recessed_balcony', 25, 36, params={'gallery_depth_m': 1.2})
    s = m.part_summary()
    for need in ('piers', 'floor_bands', 'cantilever_fascia', 'gallery_back_walls', 'glass_parapets', 'podium', 'storefront_glass', 'roof_parapet'):
        assert need in s, need
    assert s['gallery_back_walls']['bounds_min'][1] == pytest.approx(1.2, abs=1e-4)       # real recess behind the pier line
    assert s['piers']['bounds_max'][1] >= 1.2 + 0.2                                      # piers run the whole gallery depth
    assert s['cantilever_fascia']['bounds_min'][1] < -0.4
    assert 3.0 <= m.provenance['parameters']['ground_floor_m']['value'] <= 5.0
    assert m.provenance['floors'] == 10 and m.provenance['bays'] == 6


def test_cream_has_parapet_balconies_loggias_shutters_and_windowless_podium():
    m = mesh_for_profile('cream_yellow_older_balcony', 18, 22, seed=1)
    s = m.part_summary()
    for need in ('parapet_balconies', 'loggia_back_walls', 'slab_edges', 'shutters', 'glazed_top', 'stone_podium', 'roof_railing'):
        assert need in s, need
    assert s['loggia_back_walls']['bounds_min'][1] == pytest.approx(2.0, abs=1e-4)
    assert s['shutters']['photo_ids'] == ['1288770821'] and s['shutters']['evidence'] == 'observed'
    podium_top = m.provenance['parameters']['ground_floor_m']['value']
    assert s['stone_podium']['bounds_max'][2] == pytest.approx(podium_top, abs=1e-4)
    assert 'podium_glass' not in s and 'ground_glass' not in s                           # evidenced as windowless


def test_ochre_has_bands_stepped_balconies_and_pergola_clipped_at_roof():
    m = mesh_for_profile('older_brown_midrise', 10.5, 21)
    s = m.part_summary()
    for need in ('slab_bands', 'balcony_slabs', 'balcony_rails', 'pergola_eave', 'front_wall', 'ground_base'):
        assert need in s, need
    assert s['pergola_eave']['bounds_max'][2] <= 21.0 and s['pergola_eave']['bounds_min'][1] < -1.0
    assert s['ground_base']['evidence'] == 'generic'
    assert m.part_summary()['balcony_slabs']['bounds_min'][1] == pytest.approx(-1.0, abs=1e-4)


def test_construction_is_an_open_frame_with_net_side_and_infilled_base():
    m = mesh_for_profile('construction_tower_frame', 12, 48, params={'open_floors': 6})
    s = m.part_summary()
    for need in ('columns', 'floor_slabs', 'infill_panels', 'net_side', 'scaffold'):
        assert need in s, need
    L = m.provenance['floors']
    fh = m.provenance['parameters']['floor_height_m']['value']
    g = m.provenance['parameters']['ground_floor_m']['value']
    infill_top = s['infill_panels']['bounds_max'][2]
    assert infill_top <= g + (L - 6) * fh + 1e-3                                         # the top 6 floors have no infill
    assert s['net_side']['bounds_min'][0] > 12.0                                         # net sits outside the plan on the +X side
    n_slabs = m.parts['floor_slabs']['meta']['primitives']
    assert n_slabs == L + 1
    assert 'body_core' not in s                                                          # no solid body: open frame


def test_lgb_has_frames_every_second_floor_and_a_curved_glazed_corner():
    m = mesh_for_profile('lgb_cream_redbrown_bands', 18, 48)
    s = m.part_summary()
    floors = m.provenance['floors']
    frames = math.ceil(floors / 2)
    assert m.parts['frame_slabs']['meta']['primitives'] == 2 * frames
    assert m.parts['frame_returns']['meta']['primitives'] == frames                       # open C frames: ONE return each (right end), not a closed O
    assert m.parts['curved_corner_glass']['meta']['primitives'] == 8                     # arc segments
    glass = m.parts['curved_corner_glass']['verts']
    r = m.provenance['parameters']['corner_radius_m']['value']
    assert glass[:, 0].max() <= r + 1e-4 and glass[:, 1].max() <= r + 1e-4               # the arc stays in the corner square
    xs = np.unique(np.round(glass[:, 0], 3))
    assert len(xs) > 4                                                                   # genuinely curved, not a box
    assert s['frame_slabs']['bounds_min'][1] < -1.0 and s['roof_cutouts']['triangles'] > 12
    depths = {round(float(d), 2) for d in m.parts['frame_slabs']['verts'][:, 1] if d < -0.5}
    assert len(depths) >= 2                                                              # alternate frames shifted outward


@pytest.mark.parametrize('family', [f for f in FAMILIES if f != 'construction_tower_frame'])
def test_rear_is_generic_and_street_relief_is_not_wrapped_around(family):
    m = mesh_for_profile(family, *SIZES[family])
    D = m.provenance['size_m']['depth']
    assert m.parts['rear_generic']['meta']['evidence'] == 'generic'
    relief = [n for n in m.parts if n not in ('rear_generic', 'body_core', 'roof_slab', 'roof_parapet', 'roof_railing', 'side_left', 'side_right',
                                              'side_left_bands', 'side_right_bands')]
    for n in relief:
        assert m.parts[n]['verts'][:, 1].max() <= D - 0.3 + 1e-4, f'{n} reaches the rear'


def test_side_facades_are_explicit():
    none = mesh_for_profile('modern_stone_recessed_balcony', 25, 36, side_facades={'left': 'none', 'right': 'plain'})
    assert 'side_left' not in none.parts and 'side_right' in none.parts
    bands = mesh_for_profile('modern_stone_recessed_balcony', 25, 36, side_facades={'left': 'bands', 'right': 'plain'})
    assert bands.parts['side_left_bands']['verts'][:, 0].min() < 0 and 'side_right_bands' not in bands.parts
    assert bands.parts['side_left_bands']['meta']['evidence'] == 'estimated'
    assert bands.provenance['side_facades'] == {'left': 'bands', 'right': 'plain'}
    with pytest.raises(ValueError):
        mesh_for_profile('modern_stone_recessed_balcony', 25, 36, side_facades={'left': 'wrap'})
    with pytest.raises(ValueError):
        mesh_for_profile('modern_stone_recessed_balcony', 25, 36, side_facades={'top': 'plain'})


@pytest.mark.parametrize('bad', [dict(family_key='nope', width_m=10, height_m=10), dict(family_key='older_brown_midrise', width_m=2, height_m=10),
                                 dict(family_key='older_brown_midrise', width_m=10, height_m=float('nan')),
                                 dict(family_key='older_brown_midrise', width_m=10, height_m=400),
                                 dict(family_key='older_brown_midrise', width_m=10, height_m=10, params={'bogus': 1}),
                                 dict(family_key='older_brown_midrise', width_m=10, height_m=10, params={'balcony_depth_m': float('inf')}),
                                 dict(family_key='older_brown_midrise', width_m=10, height_m=10, depth_m=1.0)])
def test_invalid_input_is_rejected(bad):
    with pytest.raises(ValueError):
        mesh_for_profile(**bad)


def test_deterministic_cached_and_parametric():
    a = mesh_for_profile('cream_yellow_older_balcony', 14, 20, seed=5)
    b = mesh_for_profile('cream_yellow_older_balcony', 14.0, 20.0, seed=5)
    assert a is b and a.to_glb() == b.to_glb()
    assert not a.parts['stone_podium']['verts'].flags.writeable
    c = mesh_for_profile('cream_yellow_older_balcony', 14, 20, seed=6)
    assert c is not a
    G._build.cache_clear()
    d = mesh_for_profile('cream_yellow_older_balcony', 14, 20, seed=5)
    assert d is not a and d.to_glb() == a.to_glb()                                       # rebuilt bytes identical
    before = G.mesh_cache_info().misses
    sizes = [(w, h) for w in (8, 12, 17, 23) for h in (10, 19, 33)]
    tri = [mesh_for_profile('modern_stone_recessed_balcony', w, h).triangle_count for w, h in sizes]
    assert G.mesh_cache_info().misses - before == len(sizes)
    assert tri[-1] > tri[0]                                                              # geometry scales with the supplied size, no per-building model
    for w, h in sizes:
        mesh_for_profile('modern_stone_recessed_balcony', w, h)
    assert G.mesh_cache_info().misses - before == len(sizes)


def test_profile_overrides_defaults_and_is_hashed():
    doc = {'families': {'older_brown_midrise': {'parameters_m': {'floor_height_m': {'est': 3.3, 'lo': 3.0, 'hi': 3.4, 'basis': 'estimated'}}}}}
    m = mesh_for_profile('older_brown_midrise', 10, 20, profile=doc)
    assert m.provenance['profile_sha256'] and m.provenance['parameters']['floor_height_m']['lo'] == 3.0
    assert mesh_for_profile('older_brown_midrise', 10, 20).provenance['profile_sha256'] is None


def test_glb_roundtrip_matches_local_geometry_and_carries_provenance(tmp_path):
    m = mesh_for_profile('lgb_cream_redbrown_bands', 18, 48)
    path = tmp_path / 'lgb.glb'
    m.to_glb(path)
    doc, blob = read_glb(path)
    assert doc['asset']['extras']['family'] == 'lgb_cream_redbrown_bands' and doc['asset']['extras']['photo_ids']
    assert doc['scenes'][0]['extras']['summary']['triangles'] == m.triangle_count
    prims = mesh_primitives(path)
    assert sum(len(t) for _, t in prims.values()) == m.triangle_count
    for name, (pos, tri) in prims.items():
        part = m.parts[name.split('/', 1)[1]]
        want = part['verts'][part['faces']].reshape(-1, 3)
        np.testing.assert_allclose(np.sort(pos, axis=0), np.sort(want, axis=0), atol=1e-4)    # glTF (x,z,-y) maps back to local x,y,z
    assert {x['name'] for x in doc['materials']} >= {'cream_wall', 'redbrown_frame', 'glass'}
    nrm = doc['accessors'][1]
    assert nrm['type'] == 'VEC3' and doc['meshes'][0]['primitives'][0]['attributes']['NORMAL'] == 1


def test_placement_matrix_is_right_handed_and_maps_street_side():
    m = G.placement_matrix((100, 200, 5), (0, 1))                       # frontage runs north
    assert np.isclose(np.linalg.det(m[:3, :3]), 1.0)
    street = m @ np.array([0, -1.0, 0, 0])                              # local -Y is the street side
    assert np.allclose(street[:2], [1, 0])                              # facing east: viewer on the right of the frontage line
    assert np.allclose(m @ np.array([2, 0, 3, 1]), [100, 202, 8, 1])
    with pytest.raises(ValueError):
        G.placement_matrix((0, 0, 0), (0, 1), outward_xy=(-1, 0))


def test_preview_is_a_real_render_not_blank():
    m = mesh_for_profile('older_brown_midrise', 10.5, 21)
    img = G.preview_image(m, 30, 15, (160, 200))
    a = np.asarray(img)
    assert a.shape == (200, 160, 3)
    assert (a != np.array([228, 232, 236])).any(axis=2).mean() > 0.15
    assert len({tuple(p) for p in a.reshape(-1, 3)}) > 8


def test_lgb_closed_envelope_and_nonoverlapping_frames():
    m = mesh_for_profile('lgb_cream_redbrown_bands', 18, 48)
    W, H, D = 18.0, 48.0, m.provenance['size_m']['depth']
    r = m.provenance['parameters']['corner_radius_m']['value']

    # 1. Connected curved glass and corner floor edges behind glass
    glass_verts = m.parts['curved_corner_glass']['verts']
    edge_verts = m.parts['corner_floor_edges']['verts']
    assert glass_verts[:, 0].min() >= 0.0 and glass_verts[:, 0].max() <= r + 1e-4
    assert glass_verts[:, 1].min() >= 0.0 and glass_verts[:, 1].max() <= r + 1e-4
    # Floor edge plates stay strictly behind outer glass arc
    dist_edge = np.hypot(edge_verts[:, 0] - r, edge_verts[:, 1] - r)
    assert (dist_edge <= r - 0.07 + 1e-4).all(), 'floor edges must sit behind outer glass pane'

    # 2. C-frame slabs and returns fit without penetrating front wall or each other
    slabs = m.parts['frame_slabs']['verts']
    returns = m.parts['frame_returns']['verts']
    assert slabs[:, 1].max() <= 1e-4, 'slabs must not penetrate into facade wall y > 0'
    assert returns[:, 1].max() <= 1e-4, 'returns must not penetrate into facade wall y > 0'

    # 3. Closed envelope: body_core reaches both flanks
    core = m.parts['body_core']['verts']
    assert core[:, 0].min() <= 1e-4, 'body core must reach west wall x = 0'
    assert core[:, 0].max() >= W - 1e-4, 'body core must reach east wall x = W'
    assert core[:, 1].max() >= D - 0.3 - 1e-4, 'body core must reach rear generic wall'

    # 4. Watertight roof slab caps body core
    roof = m.parts['roof_slab']['verts']
    assert roof[:, 2].max() <= H + 1e-4
    assert core[:, 2].max() <= roof[:, 2].min() + 1e-4, 'body core must not penetrate through roof slab'

    # 5. Roof parapet has aperture cutouts
    assert m.parts['roof_cutouts']['meta']['primitives'] >= 1
    assert m.parts['roof_parapet']['verts'][:, 2].max() >= H - 1e-4




# ---------------------------------------------------------------------------------------------------------------
# LGB open C frames (photo 1288771293: top/bottom ledges and a RIGHT vertical return; no left post)
def _lgb_frame_geometry(width=18.0, height=48.0):
    m = mesh_for_profile('lgb_cream_redbrown_bands', width, height)
    prm = m.provenance['parameters']
    r = min(prm['corner_radius_m']['value'], 0.4 * width, 0.4 * m.provenance['size_m']['depth'])
    xf0 = r
    xf1 = r + prm['frame_zone_frac']['value'] * (width - r)
    return m, r, xf0, xf1


def _box_blocks(part):
    """Split a merged box part back into its boxes: (verts (8,3), faces (12,3) local indices)."""
    v, f = part['verts'].astype(float), part['faces']
    assert len(v) % 8 == 0 and len(f) % 12 == 0
    return [(v[8 * k:8 * k + 8], f[12 * k:12 * k + 12] - 8 * k) for k in range(len(v) // 8)]


def test_lgb_frames_are_open_c_shapes_with_only_a_right_hand_return():
    m, r, xf0, xf1 = _lgb_frame_geometry()
    frames = math.ceil(m.provenance['floors'] / 2)
    slabs, returns = _box_blocks(m.parts['frame_slabs']), _box_blocks(m.parts['frame_returns'])
    assert len(slabs) == 2 * frames and len(returns) == frames                      # two ledges and one return per frame
    for v, _ in slabs:
        assert v[:, 0].min() == pytest.approx(xf0) and v[:, 0].max() == pytest.approx(xf1)   # both ledges span corner -> right end
    for v, _ in returns:
        # the return lives at the viewer's right: it ends at xf1 and starts well to the right of the left (corner) end
        assert v[:, 0].max() == pytest.approx(xf1) and v[:, 0].min() > xf1 - 1.0
        assert v[:, 0].min() > xf0 + 1.0, 'no vertical post at the left end, where the ledges meet the rounded corner'


def test_lgb_each_return_joins_its_own_top_and_bottom_ledge_and_nothing_else():
    m, r, xf0, xf1 = _lgb_frame_geometry()
    slabs, returns = _box_blocks(m.parts['frame_slabs']), _box_blocks(m.parts['frame_returns'])
    for k, (rv, _) in enumerate(returns):
        bottom, top = slabs[2 * k][0], slabs[2 * k + 1][0]
        assert rv[:, 2].min() == pytest.approx(bottom[:, 2].max())                  # starts at the bottom ledge's upper face
        assert rv[:, 2].max() == pytest.approx(top[:, 2].min())                     # ends at the top ledge's lower face
        assert rv[:, 1].min() == pytest.approx(bottom[:, 1].min())                  # same projection as its ledges: a continuous C
        assert rv[:, 1].max() <= 1e-9


def test_lgb_frame_boxes_have_outward_winding_and_stay_inside_the_plan_and_height():
    m, r, xf0, xf1 = _lgb_frame_geometry()
    W, H = 18.0, 48.0
    for name in ('frame_slabs', 'frame_returns'):
        for v, f in _box_blocks(m.parts[name]):
            c = v.mean(0)
            n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
            n /= np.linalg.norm(n, axis=1, keepdims=True)
            assert (np.einsum('ij,ij->i', n, v[f].mean(1) - c) > 0).all(), name + ': a face winds inward'
            assert v[:, 0].min() >= -1e-9 and v[:, 0].max() <= W + 1e-9 and v[:, 2].min() >= -1e-9 and v[:, 2].max() <= H + 1e-9
            assert v[:, 1].max() <= 1e-9, 'frame projects toward the street (y <= 0) and never into the facade'


def test_lgb_frame_parts_have_no_visible_same_plane_overlap():
    from dynacity.geometry_checks import coplanar_overlaps
    m, *_ = _lgb_frame_geometry()
    v, f, lab, base = [], [], [], 0
    for name in ('frame_slabs', 'frame_returns'):
        pv = m.parts[name]['verts'].astype(float)
        v.append(pv)
        f.append(m.parts[name]['faces'] + base)
        lab += [name] * len(m.parts[name]['faces'])
        base += len(pv)
    hits = [h for h in coplanar_overlaps(np.concatenate(v), np.concatenate(f), labels=lab) if h['same_orientation']]
    assert hits == []


def test_lgb_right_return_lands_at_the_south_end_of_a_west_facing_frontage():
    # Orientation tied to the photograph: KartaView 1288771293 looks east at the LGB's west face, so the viewer's right is SOUTH and the
    # rounded corner (viewer's left) is the NORTH-west corner. The producer frame has X to the viewer's right; placed on that frontage
    # with the integration's own placement, the return must be south of the rounded corner glass.
    from dynacity.corridor_reconstruction import place_parts
    m, r, xf0, xf1 = _lgb_frame_geometry()
    front = {'start': [2554.0, 1489.3], 'tangent': [0.0, -1.0], 'normal': [-1.0, 0.0], 'length_m': 18.0, 'base_z': 14.8, 'top_z': 62.8}
    def north_south(name):
        part = {'name': name, 'material': 'm', 'vertices': m.parts[name]['verts'].astype(float), 'triangles': m.parts[name]['faces']}
        return place_parts([part], front)[0]['vertices'][:, 1]
    corner_n = north_south('curved_corner_glass')
    ret_n = north_south('frame_returns')
    assert ret_n.max() < corner_n.min() + r + 1e-6      # every return vertex is south of the corner's arc extent (north end of the frontage)
    assert ret_n.mean() < corner_n.mean() - 5.0


# ---------------------------------------------------------------------------------------------------------------
# LGB stepped top: ESTIMATED glass-top drop and upper cream-mass setback (photo 1288771293 shows only that the glazed volume ends
# below a taller cream upper mass). Defaults 0 must leave the single-tier envelope bit-identical.
W45, H45, D45 = 21.1116, 45.0, 19.4708
STEP = {'glass_top_drop_m': 6.2, 'upper_setback_x_m': 6.0, 'upper_setback_y_m': 1.8}


def _stepped(params=STEP):
    return mesh_for_profile('lgb_cream_redbrown_bands', W45, H45, depth_m=D45, params=params)


def _closed_outward(v, f):
    key = {}
    idx = [key.setdefault(tuple(np.round(p, 4)), len(key)) for p in v.astype(float)]
    e = {}
    for t in f:
        i = [idx[x] for x in t]
        for a, b in ((i[0], i[1]), (i[1], i[2]), (i[2], i[0])):
            e[(a, b)] = e.get((a, b), 0) + 1
    manifold = all(c == 1 and (b, a) in e for (a, b), c in e.items())
    vol = sum(np.dot(v[t[0]].astype(float), np.cross(v[t[1]].astype(float), v[t[2]].astype(float))) for t in f) / 6
    return manifold, vol


def _tier_geometry(m):
    st = m.provenance['stepped_top']
    r = m.provenance['parameters']['corner_radius_m']['value']
    return st, r


def test_lgb_stepped_top_defaults_are_bit_identical_and_explicit_zeros_match():
    base = mesh_for_profile('lgb_cream_redbrown_bands', W45, H45, depth_m=D45)
    zero = _stepped({'glass_top_drop_m': 0.0, 'upper_setback_x_m': 0.0, 'upper_setback_y_m': 0.0})
    assert set(base.parts) == set(zero.parts)
    assert all(np.array_equal(base.parts[k]['verts'], zero.parts[k]['verts']) and np.array_equal(base.parts[k]['faces'], zero.parts[k]['faces']) for k in base.parts)
    assert not {'terrace_slab', 'upper_core', 'terrace_parapet'} & set(base.parts)
    st = base.provenance['stepped_top']
    assert st['enabled'] is False and st['effective_drop_m'] == 0 and st['floors_below_glass_top'] == st['floors_total']
    assert 'ESTIMATED' in st['basis'] and 'not measurements' in st['basis']


def test_lgb_glass_ends_below_the_cream_roof_while_the_whole_height_stays_fixed():
    m = _stepped()
    st, r = _tier_geometry(m)
    fh = (H45 - m.provenance['parameters']['parapet_h_m']['value'] - m.provenance['parameters']['ground_floor_m']['value']) / st['floors_total']
    Hg, Hf = st['glass_top_z_m'], H45 - m.provenance['parameters']['parapet_h_m']['value']
    assert st['enabled'] and st['floors_below_glass_top'] == st['floors_total'] - 2
    assert st['effective_drop_m'] == pytest.approx(2 * fh, abs=1e-2) and Hg == pytest.approx(Hf - st['effective_drop_m'], abs=1e-2)
    z = lambda name: m.parts[name]['verts'][:, 2]
    assert z('roof_parapet').max() == pytest.approx(H45)                                   # the whole height did not change
    assert z('curved_corner_glass').max() == pytest.approx(Hg - 0.25, abs=2e-3)           # rounded glass ends at the lower tier's cap
    assert z('corner_mullions').max() <= Hg - 0.25 + 1e-6
    assert max(z('frame_slabs').max(), z('frame_returns').max()) <= Hg + 2e-3              # no C frame reaches the cream mass
    assert z('upper_core').min() == pytest.approx(Hg, abs=2e-3) and z('roof_slab').max() == pytest.approx(Hf, abs=2e-3)
    assert z('terrace_slab').max() == pytest.approx(Hg, abs=2e-3)
    assert z('roof_slab').min() > Hg + 1.0                                                # the highest cap stands clear above the glazed top


def test_lgb_upper_setback_snaps_to_a_bay_line_and_only_the_upper_floors_get_the_set_back_wall():
    m = _stepped()
    st, r = _tier_geometry(m)
    ux0, uy0, Hg = st['upper_setback_x_effective_m'], st['upper_setback_y_m'], st['glass_top_z_m']
    bw = (W45 - r) / round((W45 - r) / m.provenance['parameters']['bay_width_m']['value'])
    assert ux0 > r and (ux0 - r) / bw == pytest.approx(round((ux0 - r) / bw), abs=1e-2)     # on a bay line, beyond the rounded corner
    assert uy0 == pytest.approx(1.8)
    core = m.parts['upper_core']['verts']
    assert core[:, 0].min() == pytest.approx(ux0, abs=2e-3)
    assert core[:, 1].min() == pytest.approx(uy0 + m.provenance['size_m']['depth'] * 0 + 0.3, abs=0.06)   # behind its own front wall
    fw = m.parts['front_wall']['verts']
    above = fw[fw[:, 2] > Hg + 1e-3]
    assert len(above) and above[:, 0].min() >= ux0 - 1e-6 and above[:, 1].min() >= uy0 - 1e-6   # upper-floor wall only on the set-back front


def test_lgb_stepped_tiers_are_closed_outward_solids_and_the_plan_stays_capped():
    m = _stepped()
    for name in ('body_core', 'terrace_slab', 'upper_core', 'roof_slab'):
        ok, vol = _closed_outward(m.parts[name]['verts'], m.parts[name]['faces'])
        assert ok and vol > 0, f'{name}: closed={ok} volume={vol}'
    st, r = _tier_geometry(m)
    ux0, uy0 = st['upper_setback_x_effective_m'], st['upper_setback_y_m']
    # every point of the plan is under a cap: the terrace everywhere, and the higher roof slab exactly over the upper footprint
    def covered(part, pts):
        v, f = m.parts[part]['verts'].astype(float), m.parts[part]['faces']
        n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        up = f[n[:, 2] > 1e-6]
        hit = np.zeros(len(pts), bool)
        for t in up:
            a, b, c = v[t][:, :2]
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            l1 = ((b[1] - c[1]) * (pts[:, 0] - c[0]) + (c[0] - b[0]) * (pts[:, 1] - c[1])) / d
            l2 = ((c[1] - a[1]) * (pts[:, 0] - c[0]) + (a[0] - c[0]) * (pts[:, 1] - c[1])) / d
            hit |= (l1 >= -1e-9) & (l2 >= -1e-9) & (1 - l1 - l2 >= -1e-9)
        return hit
    xs, ys = np.meshgrid(np.linspace(r + 0.5, W45 - 0.5, 40), np.linspace(1.0, D45 - 1.0, 40))
    pts = np.column_stack([xs.ravel(), ys.ravel()])
    assert covered('terrace_slab', pts).all()
    in_upper = (pts[:, 0] > ux0 + 0.2) & (pts[:, 1] > uy0 + 0.5)
    roof = covered('roof_slab', pts)
    assert roof[in_upper].all() and not roof[(pts[:, 0] < ux0 - 0.2)].any()


def test_lgb_stepped_tier_parts_have_no_visible_same_plane_overlap():
    from dynacity.geometry_checks import coplanar_overlaps
    m = _stepped()
    names = ['body_core', 'terrace_slab', 'upper_core', 'roof_slab', 'terrace_parapet', 'roof_parapet', 'rear_generic']
    v, f, lab, base = [], [], [], 0
    for n in names:
        pv = m.parts[n]['verts'].astype(float)
        v.append(pv); f.append(m.parts[n]['faces'] + base); lab += [n] * len(m.parts[n]['faces']); base += len(pv)
    assert [h for h in coplanar_overlaps(np.concatenate(v), np.concatenate(f), labels=lab) if h['same_orientation']] == []


def test_lgb_stepped_c_frames_stay_open_and_below_the_glazed_top():
    m = _stepped()
    st, r = _tier_geometry(m)
    prm = m.provenance['parameters']
    xf1 = r + prm['frame_zone_frac']['value'] * (W45 - r)
    slabs, returns = _box_blocks(m.parts['frame_slabs']), _box_blocks(m.parts['frame_returns'])
    assert len(slabs) == 2 * len(returns) and len(returns) >= 1
    for k, (rv, _) in enumerate(returns):
        bottom, top = slabs[2 * k][0], slabs[2 * k + 1][0]
        assert rv[:, 0].min() > r + 1.0 and rv[:, 0].max() == pytest.approx(xf1, abs=2e-3)             # still ONE return, at the right end, no left post
        assert rv[:, 2].min() == pytest.approx(bottom[:, 2].max(), abs=1e-4) and rv[:, 2].max() == pytest.approx(top[:, 2].min(), abs=1e-4)
        assert top[:, 2].max() <= st['glass_top_z_m'] + 2e-3
    assert max(v[:, 1].max() for v, _ in slabs + returns) <= 1e-9                            # nothing projects into the facade


def test_lgb_terrace_guard_stays_outside_the_upper_footprint_and_everything_stays_in_bounds():
    m = _stepped()
    st, r = _tier_geometry(m)
    ux0, uy0 = st['upper_setback_x_effective_m'], st['upper_setback_y_m']
    for v, _ in _box_blocks(m.parts['terrace_parapet']):
        cx, cy = v[:, 0].mean(), v[:, 1].mean()
        assert not (cx > ux0 + 0.05 and cy > uy0 + 0.05), 'a terrace guard stands on the upper mass'
    for name, p in m.parts.items():
        v = p['verts'].astype(float)
        assert v[:, 2].min() >= -2e-3 and v[:, 2].max() <= H45 + 2e-3 and v[:, 0].min() >= -2e-3 and v[:, 0].max() <= W45 + 2e-3 and v[:, 1].max() <= D45 + 2e-3, name


def test_lgb_stepped_parameters_are_labelled_estimated_with_bounds_and_effective_values():
    m = _stepped()
    prm = m.provenance['parameters']
    for k in ('glass_top_drop_m', 'upper_setback_x_m', 'upper_setback_y_m'):
        assert prm[k]['basis'] == 'estimated' and prm[k]['lo'] == 0.0 and prm[k]['hi'] > 0
    assert prm['glass_top_drop_m']['value'] == pytest.approx(m.provenance['stepped_top']['effective_drop_m'], abs=1e-3)   # the effective (snapped) value is reported
    assert 'ESTIMATED' in m.provenance['stepped_top']['basis'] and m.parts['upper_core']['meta']['evidence'] == 'estimated'
    assert m.parts['terrace_slab']['meta']['evidence'] == 'estimated' and 'Hidden roof plan' in m.parts['terrace_slab']['meta']['note']
    far = _stepped({'glass_top_drop_m': 12.0, 'upper_setback_x_m': 6.0})
    assert any('glass_top_drop_m' in w for w in far.provenance['warnings'])              # outside the photo-motivated bound is flagged


def test_lgb_one_floor_drop_and_cli_variant_pass_their_own_verification(tmp_path):
    one = _stepped({'glass_top_drop_m': 3.1, 'upper_setback_x_m': 6.0, 'upper_setback_y_m': 1.8})
    assert one.provenance['stepped_top']['floors_below_glass_top'] == one.provenance['stepped_top']['floors_total'] - 1
    import importlib.util
    from pathlib import Path
    root = Path('C:/private/dynacity-data/engine/facade-reference-20261030')
    if not (root / 'source-batch.npz').exists():
        pytest.skip('private source batch not available')
    pytest.importorskip('shapely')
    spec = importlib.util.spec_from_file_location('lgb_pilot', Path(__file__).resolve().parents[1] / 'scripts' / 'build_lgb_native_pilot.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rp = mod.build(tmp_path, 45.0, 6.2, 6.0, 1.8)
    rep = json.loads(Path(rp).read_text())
    v = rep['verification']
    assert v['whole_height_unchanged'] and v['frames_and_glass_below_glass_top'] and v['same_orientation_overlaps_among_tier_parts'] == 0
    assert all(c['closed_manifold'] and c['outward'] for c in v['closed_parts'].values()) and {'terrace_slab', 'upper_core'} <= set(v['closed_parts'])
    assert all(a == 0 for a in v['plan_outside_footprint_m2'].values())                  # nothing leaves the source footprint
    assert rep['candidate_height_m'] == 45.0 and rep['source_unchanged'] and rep['height_status'].startswith('sensitivity')
    assert rep['diagnostic_parameters']['glass_top_drop_m'] == 6.2 and 'not observed or fitted' in rep['diagnostic_parameters']['status']
    base = json.loads(Path(mod.build(tmp_path, 45.0)).read_text())
    assert base['diagnostic_parameters']['effective']['enabled'] is False and 'terrace_slab' not in base['verification']['closed_parts']


def test_lgb_terrace_upstand_height_is_an_estimated_bounded_parameter_with_default_one():
    dflt = _stepped()
    low = _stepped({**STEP, 'terrace_parapet_h_m': 0.2})
    none = _stepped({**STEP, 'terrace_parapet_h_m': 0.0})
    Hg = dflt.provenance['stepped_top']['glass_top_z_m']
    prm = low.provenance['parameters']['terrace_parapet_h_m']
    assert prm['basis'] == 'estimated' and (prm['lo'], prm['hi']) == (0.0, 1.0) and prm['value'] == 0.2
    assert dflt.provenance['parameters']['terrace_parapet_h_m']['value'] == 1.0
    assert _stepped({**STEP, 'terrace_parapet_h_m': 1.0}).parts['terrace_parapet']['verts'].tolist() == dflt.parts['terrace_parapet']['verts'].tolist()
    assert dflt.parts['terrace_parapet']['verts'][:, 2].max() == pytest.approx(Hg + 1.0, abs=2e-3)
    assert low.parts['terrace_parapet']['verts'][:, 2].max() == pytest.approx(Hg + 0.2, abs=2e-3)
    assert low.provenance['stepped_top']['terrace_parapet_h_m'] == 0.2
    assert 'terrace_parapet' not in none.parts                                     # no upstand at 0 ...
    for m in (low, none):                                                         # ... but the closed connected terrace cap and upper tier are untouched
        for name in ('terrace_slab', 'upper_core', 'roof_slab', 'body_core'):
            assert np.array_equal(m.parts[name]['verts'], dflt.parts[name]['verts']) and np.array_equal(m.parts[name]['faces'], dflt.parts[name]['faces'])
            ok, vol = _closed_outward(m.parts[name]['verts'], m.parts[name]['faces'])
            assert ok and vol > 0
        assert m.parts['roof_parapet']['verts'][:, 2].max() == pytest.approx(H45)
    for bv, bf in _box_blocks(low.parts['terrace_parapet']):                       # each upstand box is its own closed outward solid
        ok, vol = _closed_outward(bv, bf)
        assert ok and vol > 0
    assert any('terrace_parapet_h_m' in w for w in _stepped({**STEP, 'terrace_parapet_h_m': 1.5}).provenance['warnings'])


def test_lgb_frame_zone_override_moves_only_the_frames_and_keeps_the_open_c_topology():
    a, b = _stepped(), _stepped({**STEP, 'frame_zone_frac': 0.4})
    r = a.provenance['parameters']['corner_radius_m']['value']
    for m, z in ((a, 0.55), (b, 0.4)):
        assert m.provenance['parameters']['frame_zone_frac']['value'] == z
        slabs, returns = _box_blocks(m.parts['frame_slabs']), _box_blocks(m.parts['frame_returns'])
        assert len(slabs) == 2 * len(returns) >= 2
        assert max(v[:, 0].max() for v, _ in returns) == pytest.approx(r + z * (W45 - r), abs=2e-3)
        assert min(v[:, 0].min() for v, _ in slabs) == pytest.approx(r, abs=2e-3)   # left end still runs into the rounded corner, no left post
    assert b.provenance['stepped_top'] == a.provenance['stepped_top']               # tiers, glass top and height unchanged
    for name in ('terrace_slab', 'upper_core', 'roof_slab', 'curved_corner_glass', 'terrace_parapet', 'roof_parapet'):
        assert np.array_equal(a.parts[name]['verts'], b.parts[name]['verts'])
    assert any('frame_zone_frac' in w for w in _stepped({**STEP, 'frame_zone_frac': 0.3}).provenance['warnings'])
