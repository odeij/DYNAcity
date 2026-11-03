import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from dynacity.facade_massing import project_points
from dynacity.reference_streetscape import (MAX_PILOT_PLACEMENTS, SCHEMA, AssetDims, CameraPose, asset_dims, asset_fit, feasible_scale,
                                            native_recheck, pick_scale, validate_manifest, radius_from_vertices)

STREETSCAPE = Path(r'C:\private\dynacity-data\engine\resemblance-20261101-2355\reference-assets\streetscape')
PRIVATE = STREETSCAPE / 'streetscape-candidates.json'

BROADLEAF = {'key': 'tree_broadleaf', 'dimensions_m': [4.24876, 4.32561, 6.08], 'trunk_height_m': 2.35, 'vertex_radius_m': 2.162805} # synthetic regression fixture; not the real asset
LIBRARY = {'key': 'tree_small_02_library', 'dimensions_m': [2.92, 4.29, 4.56], 'vertex_radius_m': 2.145}          # no trunk height known
# the five crown records the review found failing (height lo/est/hi, radius hi)
FIVE = [((3.0, 5.0, 8.0), 1.6), ((4.21, 5.01, 6.21), 1.5), ((2.84, 3.64, 4.84), 1.5), ((4.15, 4.95, 6.15), 1.5), ((6.12, 6.92, 8.12), 1.5)]
OLD_SCALES = [0.82, 0.82, 0.60, 0.81, 1.14]


def crown(h, r):
    return {'height_m': {'lo': h[0], 'est': h[1], 'hi': h[2]}, 'radius_m': {'lo': 1.2, 'est': min(1.6, r), 'hi': r}, 'max_photo_supported_radius_m': r}


def cam(**kw):
    base = dict(photo_id='P', east_m=2400.0, north_m=1490.0, ground_up_m=10.0, height_above_ground_m=1.4, heading_deg=88.0,
                yaw_offset_deg=2.0, pitch_deg=11.2, hfov_deg=54.5)
    base.update(kw)
    return CameraPose(**base)


# ------------------------------------------------------------------------------------------- camera helpers
def test_projection_matches_the_reconstruction_pinhole_convention():
    c = cam()
    pts = np.array([[2420.0, 1494.0, 11.0], [2450.0, 1488.0, 14.0], [2399.0, 1490.0, 10.0]])
    mine = c.project(pts)
    theirs = project_points(pts, c.origin, c.heading_deg + c.yaw_offset_deg, c.pitch_deg, c.hfov_deg, c.image_size_px)
    assert np.allclose(mine[:2], theirs[:2], atol=1e-6)
    assert np.isnan(mine[2]).all() and np.isnan(theirs[2]).all()


def test_ground_point_round_trips_and_misses_above_the_horizon():
    c = cam()
    for u, v in ((248, 1050), (900, 1000), (40, 1180)):
        p, rng = c.ground_point(u, v)
        assert p is not None and p[2] == pytest.approx(c.ground_up_m) and rng > 0
        assert np.allclose(c.project(p[None])[0], [u, v], atol=1e-6)
    horizon = 600 + c.focal_px * math.tan(math.radians(c.pitch_deg))
    assert c.ground_point(800, horizon - 5)[0] is None
    assert c.ground_point(800, horizon + 5)[1] > 100


def test_range_grows_toward_the_horizon_and_height_is_recovered():
    c = cam()
    r = [c.ground_point(800, v)[1] for v in (1150, 1050, 950)]
    assert r[0] < r[1] < r[2]
    p, _ = c.ground_point(800, 1000)
    u, v = c.project(np.array([[p[0], p[1], c.ground_up_m + 6.0]]))[0]
    assert c.height_above((p[0], p[1]), u, v) == pytest.approx(6.0, abs=1e-6)


def test_crown_ellipse_bbox_is_finite_scales_with_distance_and_rejects_behind():
    c = cam()
    near = c.crown_ellipse_px((2412.0, 1491.0), 10.0, 6.0, 1.8)
    far = c.crown_ellipse_px((2440.0, 1493.0), 10.0, 6.0, 1.8)
    assert all(math.isfinite(x) for x in near + far)
    assert (near[2] - near[0]) > 1.8 * (far[2] - far[0]) > 0
    assert c.crown_ellipse_px((2300.0, 1490.0, ), 10.0, 6.0, 1.8) is None


# ------------------------------------------------------------------------------------------- scale feasibility
def test_asset_dims_use_the_conservative_xy_radius():
    d = asset_dims(BROADLEAF)
    assert d == AssetDims(6.08, 2.162805, 2.35)                       # max(4.24876, 4.32561) / 2


def test_the_review_found_four_of_the_five_old_scales_break_the_radius_bound():
    d = asset_dims(BROADLEAF)
    radii = [round(s * d.radius_m, 2) for s in OLD_SCALES]
    assert radii == [1.77, 1.77, 1.30, 1.75, 2.47]
    over = [s * d.radius_m > rmax for s, (_, rmax) in zip(OLD_SCALES, FIVE)]
    assert over == [True, True, False, True, True]                     # the original failing values, as reported (first one 1.77 > 1.6)


def test_empty_feasible_interval_for_candidate_five_and_no_uniform_scale_exists():
    iv = feasible_scale(crown(*FIVE[4]), asset_dims(BROADLEAF))
    assert not iv['feasible']
    assert iv['lo'] == pytest.approx(6.12 / 6.08, abs=2e-6) and iv['hi'] == pytest.approx(1.5 / 2.162805, abs=2e-6) and iv['lo'] > 1.0 > iv['hi']
    assert iv['limiting'] == {'lo': 'height_lo', 'hi': 'radius_max'}
    fit = asset_fit(crown(*FIVE[4]), BROADLEAF)
    assert fit['status'] == 'infeasible' and 'proposed_scale' not in fit and 'no uniform scale' in fit['reason']


def test_intervals_for_the_other_four_and_near_bound_precision_for_candidate_two():
    d = asset_dims(BROADLEAF)
    got = [feasible_scale(crown(*c), d) for c in FIVE[:4]]
    assert [g['feasible'] for g in got] == [True, True, True, True]
    assert (got[1]['lo'], got[1]['hi']) == (0.692435, 0.693543)         # 4.21/6.08 .. 1.5/2.162805, outward-safe 6 decimals
    assert got[1]['lo'] <= 0.6935 <= got[1]['hi'] and not (got[1]['lo'] <= 0.69 <= got[1]['hi'])
    assert 0.69 * 6.08 < 4.21 and 0.6936 * 2.162805 > 1.5                # rounding either way breaks a bound
    assert got[0]['hi'] == pytest.approx(1.6 / 2.162805, abs=2e-6) and got[2]['lo'] == pytest.approx(2.84 / 6.08, abs=2e-6)
    assert got[3]['lo'] == pytest.approx(4.15 / 6.08, abs=2e-6)
    s = pick_scale(got[1], 5.01, d)
    assert s == 0.6935 and 4.21 <= s * 6.08 <= 6.21 and s * 2.162805 <= 1.5     # clipped to the upper bound, rounded inside
    fit = asset_fit(crown(*FIVE[1]), BROADLEAF)
    assert fit['proposed_scale'] == 0.6935 and fit['height_vs_estimate_m'] < -0.7 and 'shorter' in fit['note']   # underfit is recorded


def test_trunk_clearance_can_make_an_interval_empty():
    c = crown((2.84, 3.64, 4.84), 1.5)
    assert feasible_scale(c, asset_dims(BROADLEAF))['feasible']
    iv = feasible_scale(c, asset_dims(BROADLEAF), trunk_min_m=1.8)       # trunk 2.35 m * s >= 1.8 -> s >= 0.766 > radius cap 0.6935
    assert not iv['feasible'] and iv['limiting']['lo'] == 'trunk_clearance'


@pytest.mark.parametrize('dims, cand4, cand5', [
    ([3.0, 3.0, 5.0], True, False),      # slim 1.5 m radius, 5 m tall: candidate 4 (4.15-6.15 m, r <= 1.5) fits at s ~0.83-1.0; candidate 5 needs 6.12 m -> 1.224 > 1.0 radius cap
    ([2.4, 2.4, 6.5], True, True),       # tall and narrow: even candidate 5 (6.12-8.12 m) fits; holding it would be wrong for this asset
    ([8.0, 8.0, 6.08], False, False),    # wide: nothing fits
])
def test_alternate_asset_dimensions(dims, cand4, cand5):
    rec = {'dimensions_m': dims, 'vertex_radius_m': max(dims[:2]) / 2}
    f4 = feasible_scale(crown(*FIVE[3]), asset_dims(rec))
    f5 = feasible_scale(crown(*FIVE[4]), asset_dims(rec))
    assert f4['feasible'] == cand4 and f5['feasible'] == cand5
    h, r = dims[2], max(dims[:2]) / 2
    assert f4['lo'] == pytest.approx(4.15 / h, abs=2e-6) and f4['hi'] == pytest.approx(min(6.15 / h, 1.5 / r), abs=2e-6)
    if cand5:
        assert f5['lo'] == pytest.approx(6.12 / 6.5, abs=2e-6) and f5['hi'] == pytest.approx(min(8.12 / 6.5, 1.5 / 1.2), abs=2e-6)
        fit = asset_fit(crown(*FIVE[4]), rec)
        assert fit['status'] == 'feasible' and 6.12 <= fit['resulting_height_m'] and fit['resulting_radius_m'] <= 1.5


def test_library_asset_has_no_trunk_constraint_and_is_feasible_only_for_some():
    d = asset_dims(LIBRARY)
    assert d.trunk_height_m is None and d.radius_m == pytest.approx(2.145)
    ok = [feasible_scale(crown(*c), d)['feasible'] for c in FIVE]
    assert ok == [True, False, True, False, False]                       # matches the manifest: only candidates 1 and 3


def test_native_recheck_rejects_assets_whose_current_bounds_differ():
    c2, c1 = crown(*FIVE[1]), crown(*FIVE[0])
    ok, iv, why = native_recheck(c2, 0.6935, [4.24876, 4.32561, 6.08], 2.35, vertex_radius_m=2.162805)
    assert ok and not why                                                    # the recorded dimensions reproduce the manifest
    ok, iv, why = native_recheck(c2, 0.6935, [4.6, 4.8, 6.08], 2.35, vertex_radius_m=2.4)         # re-exported wider: candidate 2's interval becomes empty
    assert not ok and 'empty' in why[0]
    ok, iv, why = native_recheck(c1, 0.7397, [4.4, 4.4, 6.08], 2.35, vertex_radius_m=2.2)         # candidate 1 stays feasible but its proposed scale now breaks the radius cap
    assert iv['feasible'] and not ok and 'outside' in why[0] and iv['hi'] < 0.7397
    ok, _, _ = native_recheck(c2, 0.6935, [4.24876, 4.32561, 6.2], 2.35, vertex_radius_m=2.162805)     # slightly taller: lower bound falls, scale still inside
    assert ok


# ------------------------------------------------------------------------------------------- manifest validator
def _asset(rec=BROADLEAF, key=None, cr=None):
    a = {**rec, 'native_path': '/Game/Beirut/Streets/Props/tree_broadleaf_01294468c0', 'license': 'CC0-1.0 textures; original mesh', 'provenance': 'project-authored generic broadleaf',
         'loaded_in_native': False, 'asset_capture_type': 'procedural_mesh_photographic_textures', 'simulation_assignment': 'estimated',
         'geographic_identity': 'generic stand-in; not a Beirut tree'}
    if key:
        a['key'] = key
    a['scale'] = asset_fit(cr, a)
    return a


def _placement(pid='m1', h=(4.21, 5.01, 6.21), r=1.5):
    cr = crown(h, r)
    a = _asset(cr=cr)
    return {'id': pid, 'family': 'broadleaf', 'status': 'candidate_pilot', 'native_ready': True, 'chosen_asset': 'tree_broadleaf',
            'observed_in': [{'photo_id': 'A', 'u': 248, 'v': 1050}],
            'position': {'estimate_en_m': [2440.0, 1496.0], 'proposed_en_m': [2440.0, 1495.8], 'sigma_m': 1.7, 'range_e_m': [2436.0, 2444.0], 'range_n_m': [1493.0, 1499.0], 'basis': 'estimated'},
            'ground': {'native_ground_trace_required': True, 'estimated_up_m': 11.9}, 'crown': cr, 'asset_candidates': [a],
            'clearance': {'building': 'ok', 'carriageway': 'ok', 'pedestrian_collision': 'pending_native'}, 'osm_corroboration': {'identity_claim': 'none'}}


def _held():
    cr = crown(*FIVE[4])
    p = _placement('m5', *FIVE[4])
    p.update(status='hold_not_native_ready', native_ready=False, chosen_asset=None, reason='no feasible uniform scale')
    p['asset_candidates'] = [_asset(cr=cr, key='tree_broadleaf')]
    return p


def _doc():
    p = _placement()
    p['asset_candidates'][0]['key'] = 'tree_broadleaf'
    return {'schema': SCHEMA, 'camera_status': 'uncalibrated_estimate', 'scope': {'whole_street': False},
            'source_photos': [{'photo_id': 'A', 'license': 'CC BY-SA 4.0', 'attribution': '(c) Grab and KartaView Contributors', 'source_url': 'https://x', 'capture_local': '2018-07-21'}],
            'placements': [p], 'held_observations': [_held()], 'native_ready': ['m1'],
            'with_without_vegetation_plan': {'captures': ['A', 'B']}, 'uncertainty': ['estimates']}


def test_valid_manifest_passes_with_a_held_observation():
    assert validate_manifest(_doc()) == []


def test_validator_rejects_the_original_five_failing_scales():
    """Reproduces the review: stored scales .82/.82/.60/.81/1.14 as native-ready placements."""
    for (h, r), s in zip(FIVE, OLD_SCALES):
        d = _doc()
        p = _placement('x', h, r)
        p['asset_candidates'][0]['scale']['proposed_scale'] = s
        d['placements'], d['native_ready'], d['held_observations'] = [p], ['x'], []
        errs = validate_manifest(d)
        feasible = feasible_scale(crown(h, r), asset_dims(BROADLEAF))
        if feasible['lo'] <= s <= feasible['hi']:
            continue
        assert errs, (h, r, s)
        assert any('outside the feasible interval' in e or 'contradicts' in e or 'status' in e for e in errs), errs
    # candidate five cannot be native-ready at all, whatever scale it carries
    d = _doc()
    p = _placement('x', *FIVE[4])
    d['placements'], d['native_ready'], d['held_observations'] = [p], ['x'], []
    assert any('feasible' in e for e in validate_manifest(d))


def test_near_bound_scales_are_judged_exactly():
    d = _doc()
    d['placements'][0]['asset_candidates'][0]['scale']['proposed_scale'] = 0.69          # rounded down: height 4.19 < 4.21
    assert any('outside the feasible interval' in e for e in validate_manifest(d))
    d = _doc()
    d['placements'][0]['asset_candidates'][0]['scale']['proposed_scale'] = 0.7           # rounded up: radius 1.514 > 1.5
    assert any('outside the feasible interval' in e for e in validate_manifest(d))
    d = _doc()
    d['placements'][0]['asset_candidates'][0]['scale'].update(proposed_scale=0.6924, resulting_height_m=round(0.6924 * 6.08, 4))
    assert any('outside the feasible interval' in e for e in validate_manifest(d))       # 0.6924 < 0.692435
    d = _doc()
    d['placements'][0]['asset_candidates'][0]['scale']['proposed_scale'] = 0.693543
    d['placements'][0]['asset_candidates'][0]['scale'].update(resulting_height_m=round(0.693543 * 6.08, 4), resulting_radius_m=round(0.693543 * 2.162805, 4),
                                                              height_vs_estimate_m=round(0.693543 * 6.08 - 5.01, 4))
    assert validate_manifest(d) == []                                                    # the exact upper bound is accepted


@pytest.mark.parametrize('mutate, needle', [
    (lambda d: d['placements'][0]['asset_candidates'][0]['scale'].update(feasible_interval=[0.5, 0.9]), 'stored feasible_interval'),
    (lambda d: d['placements'][0]['asset_candidates'][0]['scale'].update(status='infeasible'), 'contradicts'),
    (lambda d: d['placements'][0]['asset_candidates'][0]['scale'].update(resulting_radius_m=1.2), 'resulting_radius_m'),
    (lambda d: d['placements'][0]['asset_candidates'][0]['scale'].update(height_vs_estimate_m=0.0), 'height_vs_estimate_m'),
    (lambda d: d['placements'][0]['asset_candidates'][0].update(vertex_radius_m=2.4), 'stored feasible_interval'),
    (lambda d: d['placements'][0]['asset_candidates'][0].pop('dimensions_m'), 'dimensions_m'),
    (lambda d: d['placements'][0].update(chosen_asset='nope'), 'chosen_asset'),
    (lambda d: d['placements'][0].update(native_ready=False), 'native_ready true'),
    (lambda d: d['placements'][0].update(status='hold_not_native_ready'), 'candidate_pilot'),
    (lambda d: d.update(native_ready=[]), 'native_ready'),
    (lambda d: d.update(native_ready=['m1', 'm5']), 'native_ready'),
    (lambda d: d['held_observations'][0].update(chosen_asset='tree_broadleaf'), 'must not name a chosen asset'),
    (lambda d: d['held_observations'][0].update(reason=''), 'hold_not_native_ready with a reason'),
    (lambda d: d['held_observations'][0]['asset_candidates'][0].update(dimensions_m=[2.4, 2.4, 6.5], vertex_radius_m=1.2), 'actually feasible'),
    (lambda d: d['placements'][0]['asset_candidates'][0].update(asset_capture_type='guess'), 'asset_capture_type'),
    (lambda d: d['placements'][0]['asset_candidates'][0].update(geographic_identity='the photographed tree'), 'stand-in'),
    (lambda d: d['placements'][0]['asset_candidates'][0].update(simulation_assignment='measured'), 'simulation_assignment'),
    (lambda d: d['placements'][0]['asset_candidates'][0].update(loaded_in_native=True), 'loaded_in_native'),
    (lambda d: d['placements'][0]['asset_candidates'][0].pop('license'), 'license'),
    (lambda d: d.update(schema='x'), 'schema'),
    (lambda d: d.update(camera_status='calibrated'), 'camera_status'),
    (lambda d: d['source_photos'][0].update(license='CC0'), 'license'),
    (lambda d: d['scope'].update(whole_street=True), 'whole-street'),
    (lambda d: d['placements'].extend([copy.deepcopy(d['placements'][0]) for _ in range(MAX_PILOT_PLACEMENTS)]), 'bounded pilot'),
    (lambda d: d['placements'][0].update(family='washingtonia'), 'family'),
    (lambda d: d['placements'][0].update(observed_in=[]), 'anchors'),
    (lambda d: d['placements'][0]['position'].update(estimate_en_m=[2440.0, float('inf')]), 'position'),
    (lambda d: d['placements'][0]['position'].update(range_e_m=[2441.0, 2444.0]), 'position'),
    (lambda d: d['placements'][0]['position'].update(sigma_m=0.0), 'position'),
    (lambda d: d['placements'][0]['position'].update(basis='measured'), 'never measured'),
    (lambda d: d['placements'][0]['ground'].update(native_ground_trace_required=False), 'ground trace'),
    (lambda d: d['placements'][0]['crown']['radius_m'].update(hi=3.0), 'crown'),
    (lambda d: d['placements'][0]['clearance'].update(carriageway='overlap'), 'overlaps'),
    (lambda d: d['placements'][0]['clearance'].update(pedestrian_collision='ok'), 'pending_native'),
    (lambda d: d['placements'][0]['osm_corroboration'].update(identity_claim='this is OSM node 1'), 'identity'),
    (lambda d: d.pop('with_without_vegetation_plan'), 'comparison'),
    (lambda d: d.update(uncertainty=[]), 'uncertainty'),
])
def test_validator_rejects_unsafe_manifests(mutate, needle):
    d = copy.deepcopy(_doc())
    mutate(d)
    errs = validate_manifest(d)
    assert errs and any(needle in e for e in errs), errs


def test_validator_checks_every_asset_candidate_not_only_the_chosen_one():
    d = _doc()
    cr = d['placements'][0]['crown']
    second = _asset(LIBRARY, key='tree_small_02_library', cr=cr)
    second['native_path'] = '/Game/Beirut/LibraryAssets/Meshes/tree_small_02_e381adfb18'
    d['placements'][0]['asset_candidates'].append(second)
    assert validate_manifest(d) == []
    second['scale']['status'] = 'feasible'                                 # lie: claim a feasible fit for an infeasible candidate
    second['scale']['proposed_scale'] = 0.7
    assert any('tree_small_02_library' in e for e in validate_manifest(d))


def test_asset_blocker_can_replace_candidates():
    d = _doc()
    d['placements'][0]['asset_candidates'] = []
    d['placements'][0]['asset_blocker'] = 'no palm mesh in the project: nothing to place'
    assert validate_manifest(d) == []


# ------------------------------------------------------------------------------------------- the generated manifest
@pytest.mark.skipif(not PRIVATE.exists(), reason='private streetscape manifest not on this machine')
def test_private_manifest_is_bounded_feasible_and_keeps_the_hold():
    d = json.loads(PRIVATE.read_text(encoding='utf-8'))
    assert validate_manifest(d) == []
    ids = [p['id'] for p in d['placements']]
    assert ids == d['native_ready'] == ['corridor-median-1', 'corridor-median-3']
    assert len(ids) <= MAX_PILOT_PLACEMENTS == 4 and d['scope']['whole_street'] is False
    held = d['held_observations']
    assert [h['id'] for h in held] == ['corridor-median-2', 'corridor-median-4', 'corridor-median-5'] and held[0]['native_ready'] is False and held[0]['status'] == 'hold_not_native_ready'
    assert held[-1]['crown']['height_m']['lo'] == pytest.approx(6.12) and held[-1]['crown']['radius_m']['hi'] == pytest.approx(1.5)   # bounds were not widened
    # recompute everything from the ASSET FILE inventory, not from the manifest's own scale fields
    inv = json.loads((STREETSCAPE / 'existing-tree-assets.json').read_text(encoding='utf-8'))['assets']
    for p in d['placements'] + held:
        for a in p['asset_candidates']:
            rec = inv[a['key']]
            iv = feasible_scale(p['crown'], asset_dims(rec))
            assert a['scale']['feasible_interval'] == [iv['lo'], iv['hi']]
    expected = {p['id']: tuple(feasible_scale(p['crown'], asset_dims(inv[p['chosen_asset']]))[k] for k in ('lo', 'hi')) for p in d['placements']}
    for p in d['placements']:
        a = next(x for x in p['asset_candidates'] if x['key'] == p['chosen_asset'])
        assert tuple(a['scale']['feasible_interval']) == expected[p['id']]
        s = a['scale']['proposed_scale']
        assert expected[p['id']][0] <= s <= expected[p['id']][1]
        assert s * 6.08 >= p['crown']['height_m']['lo'] and s * inv[p['chosen_asset']]['vertex_radius_m'] <= p['crown']['radius_m']['hi']
        assert p['crown']['radius_m']['hi'] <= p['crown']['max_photo_supported_radius_m']
        assert all(x['asset_capture_type'] and 'stand-in' in x['geographic_identity'] and x['simulation_assignment'] == 'estimated' and x['loaded_in_native'] is False for x in p['asset_candidates'])
        assert p['ground']['native_ground_trace_required'] and p['osm_corroboration']['identity_claim'] == 'none'
        assert p['existing_native_trees']['live_scene_queried'] is False
    assert d['placements'][0]['asset_candidates'][0]['scale']['height_vs_estimate_m'] < -0.7            # recorded underfit
    ctx = d['existing_native_context']
    assert 'No live native query' in ctx['basis'] and 'mandatory' in ctx['does_not_establish']
    assert 'uniform scale only' in d['uniform_scale_contract']


def test_true_vertex_radius_includes_offcentre_diagonal():
    p = [[3., 4., 1.], [2., 0., 0.], [0., 1., 6.]]
    assert radius_from_vertices(p) == 5.
    assert asset_dims({'dimensions_m': [3, 4, 6], 'bounds_min_m': [0, 0, 0], 'bounds_max_m': [3, 4, 6]}).radius_m == 5.
    with pytest.raises(KeyError):
        asset_dims({'dimensions_m': [3, 4, 6]})
    assert not native_recheck(crown(*FIVE[0]), .6, [3, 4, 6])[0]


@pytest.mark.parametrize('bad', [[], [[0, float('nan'), 1]], [[1, 2]]])
def test_vertex_radius_rejects_invalid_geometry(bad):
    with pytest.raises(ValueError):
        radius_from_vertices(bad)


def test_measured_broadleaf_holds_two_four_five():
    actual = {**BROADLEAF, 'vertex_radius_m': 2.369761}
    fits = [asset_fit(crown(*c), actual) for c in FIVE]
    assert [f['status'] for f in fits] == ['feasible', 'infeasible', 'feasible', 'infeasible', 'infeasible']
    for c, f in zip(FIVE, fits):
        if f['status'] == 'feasible':
            scale = f['proposed_scale']
            assert c[0][0] <= scale * 6.08 <= c[0][2]
            assert scale * 2.369761 <= c[1]
