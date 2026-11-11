import numpy as np
import pytest

from dynacity.corridor_reconstruction import _box_parts
from dynacity.geometry_checks import coplanar_overlaps, summarise


def solids(*boxes):
    """One mesh from several axis-aligned boxes, with a label per triangle."""
    vs, fs, labels, base = [], [], [], 0
    for k, b in enumerate(boxes):
        v, f = _box_parts([b])
        vs.append(v); fs.append(f + base); labels += [f'box{k}'] * len(f); base += len(v)
    return np.concatenate(vs), np.concatenate(fs), labels


def test_two_boxes_with_coplanar_end_faces_are_reported_as_same_orientation_overlap():
    # box A spans x 0..10, box B spans x 0..4 and sits inside A's z-range: both have an outward -X face on the plane x=0
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (0, 4, 1, 3, 0.5, 2.5))
    hits = coplanar_overlaps(v, f, labels=lab)
    same = [h for h in hits if h['same_orientation']]
    assert same and sum(h['area_m2'] for h in same) == pytest.approx(2 * 2, rel=1e-6)  # B's end face (y 1..3, z 0.5..2.5) lies inside A's
    assert {tuple(sorted(h['labels'])) for h in same} == {('box0', 'box1')}


def test_touching_boxes_have_no_visible_overlap_only_a_hidden_back_to_back_contact():
    v, f, lab = solids((0, 5, 0, 4, 0, 3), (5, 9, 0, 4, 0, 3))  # B starts where A ends
    hits = coplanar_overlaps(v, f, labels=lab)
    assert hits and not any(h['same_orientation'] for h in hits)
    assert sum(h['area_m2'] for h in hits) == pytest.approx(4 * 3)


def test_edge_and_corner_contact_and_small_offsets():
    v, f, lab = solids((0, 5, 0, 4, 0, 3), (5, 9, 4, 8, 0, 3))  # only a shared edge
    assert coplanar_overlaps(v, f, labels=lab) == []
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (-0.02, 4, 1, 3, 0.5, 2.5))  # 2 cm proud of the plane: outside the 3 mm tolerance
    assert not [h for h in coplanar_overlaps(v, f, labels=lab, tol=0.003) if h['same_orientation']]
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (-0.001, 4, 1, 3, 0.5, 2.5))  # 1 mm: still fights for pixels
    assert [h for h in coplanar_overlaps(v, f, labels=lab, tol=0.003) if h['same_orientation']]


def test_rotated_planes_and_summary():
    th = np.radians(33.)
    R = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
    v, f, lab = solids((0, 10, 0, 4, 0, 3), (0, 4, 1, 3, 0.5, 2.5))
    hits = coplanar_overlaps(v @ R.T, f, labels=lab)
    assert sum(h['area_m2'] for h in hits if h['same_orientation']) == pytest.approx(4, rel=1e-4)
    s = summarise(hits)
    assert s[0]['orientation'] in ('same', 'opposite') and s[0]['area_m2'] > 0
