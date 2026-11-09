import numpy as np
import pytest

from dynacity.corridor_reconstruction import _box_parts
from dynacity.geometry_checks import coplanar_overlaps, resolve_coplanar


def solids(*boxes):
    vs, fs, labels, base = [], [], [], 0
    for k, b in enumerate(boxes):
        v, f = _box_parts([b])
        vs.append(v)
        fs.append(f + base)
        labels += [f'box{k}'] * len(f)
        base += len(v)
    return np.concatenate(vs), np.concatenate(fs), labels


def area_facing(v, f, normal, offset, tol=1e-6):
    total = 0.
    for tri in f:
        p = v[tri]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            continue
        nn = n / ln
        if np.allclose(nn, normal, atol=1e-6) and abs(p[0] @ nn - offset) < tol:
            total += ln / 2
    return total


def test_covered_face_is_removed_and_the_visible_area_and_winding_are_unchanged():
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (0, 4, 1, 3, 0.5, 2.5))
    pri = [0 if lab_ == 'box0' else 1 for lab_ in lab]
    nv, nf, nl, st = resolve_coplanar(v, f, pri, lab)
    assert st['fully_covered_removed'] >= 2
    assert not [h for h in coplanar_overlaps(nv, nf, labels=nl) if h['same_orientation']]
    assert area_facing(nv, nf, [-1., 0., 0.], 0.) == pytest.approx(12.)  # the union on the plane x=0 is just A's face


def test_partial_overlap_keeps_only_the_part_outside_the_higher_priority_face():
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (0, 4, 3, 6, 0.5, 2.5))  # B's end face sticks out of A's by y 4..6
    pri = [0 if lab_ == 'box0' else 1 for lab_ in lab]
    nv, nf, nl, st = resolve_coplanar(v, f, pri, lab)
    assert st['clipped'] >= 1
    assert not [h for h in coplanar_overlaps(nv, nf, labels=nl) if h['same_orientation']]
    assert area_facing(nv, nf, [-1., 0., 0.], 0.) == pytest.approx(4 * 3 + 2 * 2)  # A's 12 plus B's remaining 2 x 2


def test_geometry_without_competitors_is_returned_untouched():
    v, f, lab = solids((0, 5, 0, 4, 0, 3), (5, 9, 4, 8, 0, 3))
    nv, nf, nl, st = resolve_coplanar(v, f, list(range(len(f))), lab)
    assert st['clipped'] == 0 and st['fully_covered_removed'] == 0 and (nf == f).all()


def test_priority_decides_which_face_survives():
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (0, 4, 1, 3, 0.5, 2.5))
    pri = [1 if lab_ == 'box0' else 0 for lab_ in lab]  # now the small box wins
    nv, nf, nl, st = resolve_coplanar(v, f, pri, lab)
    assert not [h for h in coplanar_overlaps(nv, nf, labels=nl) if h['same_orientation']]
    assert area_facing(nv, nf, [-1., 0., 0.], 0.) == pytest.approx(12.)  # union area is preserved either way
    assert any(label == 'box1' for label in nl)
