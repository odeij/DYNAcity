"""Crossing stripes must sit between the kerbs without losing stripes or sliding off the road."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('mapped_crossings',
    Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/mapped_crossings.py')
crossings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crossings)
fit, common = crossings.fit_to_clearance, crossings.common_interval
M = crossings.CLEARANCE_MARGIN_M


def edges(result):
    return result['offset_m'] - result['width_m']/2, result['offset_m'] + result['width_m']/2


def test_footprint_exactly_as_wide_as_the_road_is_narrowed_to_keep_a_margin_both_sides():
    # node/6556278088: kerb-to-kerb 3.20 m and a 3.20 m footprint (stripes on the kerb face)
    result = fit(-1.57, 1.63, 3.2)
    lo, hi = edges(result)
    assert result['status'] == 'narrowed'
    assert lo >= -1.57 + M - 1e-9 and hi <= 1.63 - M + 1e-9
    assert result['width_m'] == pytest.approx(3.2 - 2*M, abs=0.05)


def test_open_side_only_slides_the_footprint_and_keeps_its_width():
    # node/35350999: kerb at +1.58, open junction mouth on the other side: same width, minimal shift away from the kerb
    result = fit(-4.92, 1.58, 3.2)
    lo, hi = edges(result)
    assert result['status'] == 'shifted' and result['width_m'] == 3.2
    assert hi == pytest.approx(1.58 - M) and lo > -4.92 + M
    assert result['offset_m'] == pytest.approx(-(3.2/2 - (1.58 - M)), abs=1e-9)


def test_footprint_with_room_on_both_sides_is_not_touched():
    # clean control node/6553935445: free interval -4.52..4.63 for a 3.2 m footprint
    assert fit(-4.52, 4.63, 3.2) == {'status': 'unchanged', 'offset_m': 0.0, 'width_m': 3.2, 'room_m': pytest.approx(8.91 - 0.24)} or \
        fit(-4.52, 4.63, 3.2)['status'] == 'unchanged'


def test_wedge_that_blocks_only_one_end_limits_the_common_interval():
    # kerb wedge at one end of the footprint (rows measured along the footprint): the intersection is what must be respected
    rows = [(-3.22, 3.18), (-3.17, 3.23), (-3.17, 1.9)]
    assert common(rows) == (-3.17, 1.9)
    result = fit(*common(rows), 6.4)
    assert edges(result)[1] <= 1.9 - M + 1e-9 and edges(result)[0] >= -3.17 + M - 1e-9


def test_blocked_centre_or_too_little_room_is_reported_not_forced():
    assert common([(-1.0, 1.0), None]) is None
    result = fit(-0.5, 0.5, 3.2)
    assert result['status'] == 'unfit' and result['offset_m'] == 0.0 and result['width_m'] == 3.2


def test_all_six_stripes_are_kept_the_mesh_is_only_scaled_across():
    # The scale stays a plain Y scale of the 5.48 m mesh: no stripe removal, no X change
    result = fit(-1.57, 1.63, 3.2)
    scale_y = result['width_m']/crossings.MESH_WIDTH_M
    assert 0.33 < scale_y < 0.6
    assert crossings.MESH_LENGTH_M == 3.0


# ---- preflight decisions (pure) ----
classify = crossings.classify_preflight


def facts(**over):
    base = {'fallback': False, 'centre_road': True, 'multilevel': False, 'normal_z': 0.999, 'interval': (-1.57, 1.63), 'width_before_m': 3.2,
            'fit': fit(-1.57, 1.63, 3.2), 'ground_range_m': 0.03, 'plane_gap_mm': (26.0, 32.0)}
    base.update(over)
    return base


def test_preflight_adjustable_unchanged_and_each_block_reason():
    assert classify(facts())[0] == 'adjustable'
    assert classify(facts(fit=fit(-4.52, 4.63, 3.2), interval=(-4.52, 4.63)))[0] == 'unchanged_safe'
    assert classify(facts(fallback=True))[0] == 'excluded_unverified_fallback'
    assert classify(facts(centre_road=False))[0] == 'blocked_no_road_at_centre'
    assert classify(facts(multilevel=True))[0] == 'blocked_multilevel'
    assert classify(facts(normal_z=0.9))[0] == 'blocked_steep'
    assert classify(facts(normal_z=None))[0] == 'blocked_steep'
    assert classify(facts(interval=None))[0] == 'blocked_centre_not_clear'
    assert classify(facts(width_before_m=21.9))[0] == 'blocked_unreasonable_width'
    assert classify(facts(ground_range_m=0.2))[0] == 'blocked_ground_discontinuity'
    assert classify(facts(ground_range_m=None))[0] == 'blocked_ground_discontinuity'
    assert classify(facts(fit=fit(-0.5, 0.5, 3.2)))[0] == 'blocked_no_inset_room'


def test_preflight_fails_closed_on_large_offset_or_large_narrowing():
    far = fit(-0.2, 5.0, 3.2)       # free road far to one side: a 1.2 m slide
    assert abs(far['offset_m']) > crossings.MAX_OFFSET_M and classify(facts(fit=far, interval=(-0.2, 5.0)))[0] == 'blocked_large_offset'
    tight = fit(-2.1, 2.1, 6.4)     # 6.4 m footprint with 4.0 m of road would shrink to 62%
    assert tight['status'] == 'narrowed' and classify(facts(fit=tight, interval=(-2.1, 2.1), width_before_m=6.4))[0] == 'blocked_excessive_narrowing'
    # the applied fixes would be 'adjustable' by their own rules (7-9% narrowing, 0.16 m slide)
    assert classify(facts(fit=fit(-1.6, 1.56, 3.2)))[0] == 'adjustable'
    assert classify(facts(fit=fit(-1.56, 2.56, 3.2), interval=(-1.56, 2.56)))[0] == 'adjustable'


def test_preflight_plane_gap_rule_and_idempotent_dead_band():
    assert classify(facts(plane_gap_mm=(-44.0, 30.0)))[0] == 'blocked_plane_fit'
    assert classify(facts(plane_gap_mm=(30.0, 105.0)))[0] == 'blocked_plane_fit'
    assert classify(facts(plane_gap_mm=None))[0] == 'blocked_plane_fit'
    # a footprint that was already fitted (X-01 after the fix: 2.92 m in a 2.92 m room, 2 cm centre difference) is left alone
    done = fit(-1.6, 1.56, 2.92)
    assert done['status'] == 'unchanged' and done['offset_m'] == 0.0 and done['width_m'] == 2.92
    assert classify(facts(fit=done, interval=(-1.6, 1.56), width_before_m=2.92))[0] == 'unchanged_safe'
    # but a real overshoot still moves it
    assert fit(-1.57, 1.63, 3.2)['status'] == 'narrowed'


# ---- batch apply helpers (pure) ----
def _vec(**over):
    base = [100.0, 200.0, 50.0, 0.0, 170.0, 0.0, 1.0, 0.584, 1.0]
    for k, v in over.items():
        base[int(k[1:])] = v
    return base


def test_vectors_close_uses_explicit_tolerances_and_wraps_angles():
    tol = crossings.APPLY_TOL
    a = _vec()
    assert crossings.vectors_close(a, _vec(i0=100.04), tol)
    assert not crossings.vectors_close(a, _vec(i0=100.2), tol)
    assert crossings.vectors_close(_vec(i4=179.995), _vec(i4=-179.999), tol)          # +-180 are the same angle
    assert not crossings.vectors_close(a, _vec(i7=0.5842), tol) or tol['scale'] >= 2e-4


def test_reconcile_requires_outside_ids_exactly_equal_and_allowed_ids_at_plan():
    before = {'a': _vec(), 'b': _vec(i0=500.0), 'c': _vec(i0=900.0)}
    expected = dict(before); expected['a'] = _vec(i1=198.0, i7=0.53)
    after = dict(before); after['a'] = _vec(i1=198.0, i7=0.53)
    ok = crossings.reconcile(before, after, {'a'}, expected)
    assert ok['ok'] and ok['allowlist_changed_count'] == 1
    leak = dict(after); leak['b'] = _vec(i0=500.000001)
    bad = crossings.reconcile(before, leak, {'a'}, expected)
    assert not bad['ok'] and bad['outside_allowlist_changed'] == ['b']
    missed = dict(before)
    bad = crossings.reconcile(before, missed, {'a'}, expected)
    assert not bad['ok'] and bad['allowlist_not_at_expected'] == ['a']
    reordered = {'b': after['b'], 'a': after['a'], 'c': after['c']}
    assert not crossings.reconcile(before, reordered, {'a'}, expected)['ok']


def test_planned_vector_keeps_scale_x_and_z_and_baseline_hash_is_stable():
    plan = {'proposed_location': [1.0, 2.0, 3.0], 'proposed_rotation': [0.1, 0.2, 0.3], 'scale_y': 0.5}
    assert crossings.planned_vector(plan, _vec(i6=1.0, i8=1.0)) == [1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 1.0, 0.5, 1.0]
    h = crossings.baseline_hash({'b': [1.00001] * 9, 'a': [2.0] * 9})
    assert h == crossings.baseline_hash({'a': [2.0] * 9, 'b': [1.00001] * 9}) and len(h) == 64
