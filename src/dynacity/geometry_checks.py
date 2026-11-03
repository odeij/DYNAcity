"""Mesh checks for visible-surface defects that no pixel inspection can attribute to a cause.

``coplanar_overlaps`` finds pairs of triangles that lie in the same plane (within ``tol`` metres), overlap in area, and so
compete for the same pixels (z-fighting). Faces with the SAME orientation are visible together and are a defect; faces with
OPPOSITE orientation are a back-to-back contact that is hidden when both solids are closed, reported separately.
Shared edges and touching corners have zero overlap area and are not reported.
"""
from __future__ import annotations

import numpy as np


def _clip(subject, clip):
    """Sutherland-Hodgman: convex ``subject`` polygon clipped by convex counter-clockwise ``clip`` polygon (both (K,2))."""
    out = [tuple(p) for p in subject]
    for i in range(len(clip)):
        a, b = np.asarray(clip[i]), np.asarray(clip[(i + 1) % len(clip)])
        edge = b - a
        inp, out = out, []
        if not inp:
            break
        side = [edge[0] * (p[1] - a[1]) - edge[1] * (p[0] - a[0]) for p in inp]
        for k in range(len(inp)):
            p, q, sp, sq = inp[k], inp[(k + 1) % len(inp)], side[k], side[(k + 1) % len(inp)]
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _area(poly):
    if len(poly) < 3:
        return 0.
    p = np.asarray(poly)
    return float(.5 * abs(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])))


def _ccw(tri):
    a, b, c = tri
    return tri if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) >= 0 else tri[::-1]


def coplanar_overlaps(vertices, triangles, tol=0.003, min_area=1e-3, labels=None, max_pairs=2000000):
    """Return a list of {'a','b','area_m2','same_orientation','normal','plane_offset','labels'} for overlapping coplanar triangles."""
    v = np.asarray(vertices, float)
    t = np.asarray(triangles, np.int64)
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    n[ok] /= ln[ok, None]
    sign = np.ones(len(t))
    for k in range(3):
        first = (np.abs(n[:, k]) > 1e-6) & ~((np.abs(n[:, :k]) > 1e-6).any(axis=1) if k else np.zeros(len(t), bool))
        sign[first & (n[:, k] < 0)] = -1
    nc = n * sign[:, None]
    off = np.einsum('ij,ij->i', nc, p0)
    buckets = {}
    for i in np.flatnonzero(ok):
        buckets.setdefault((*np.round(nc[i] * 1000).astype(np.int64),), []).append(i)
    results, pairs = [], 0
    for key, idx in buckets.items():
        if len(idx) < 2:
            continue
        idx = np.array(idx)
        order = idx[np.argsort(off[idx])]
        o = off[order]
        cuts = np.flatnonzero(np.diff(o) > tol) + 1
        for cl in np.split(order, cuts):
            if len(cl) < 2:
                continue
            nn = nc[cl[0]]
            u = np.cross(nn, [1., 0., 0.] if abs(nn[0]) < .9 else [0., 1., 0.])
            u /= np.linalg.norm(u)
            w = np.cross(nn, u)
            P = np.stack([p0[cl], p1[cl], p2[cl]], axis=1)  # (m,3,3)
            U, W = P @ u, P @ w
            umin, umax, wmin, wmax = U.min(1), U.max(1), W.min(1), W.max(1)
            srt = np.argsort(umin)
            for a_pos in range(len(srt)):
                a_ = srt[a_pos]
                for b_pos in range(a_pos + 1, len(srt)):
                    b_ = srt[b_pos]
                    if umin[b_] > umax[a_]:
                        break
                    if wmin[b_] > wmax[a_] or wmin[a_] > wmax[b_]:
                        continue
                    pairs += 1
                    if pairs > max_pairs:
                        raise RuntimeError('too many candidate pairs; tighten tol or split the mesh')
                    A = np.stack([U[a_], W[a_]], axis=1)
                    B = np.stack([U[b_], W[b_]], axis=1)
                    area = _area(_clip(_ccw(A), _ccw(B)))
                    if area >= min_area:
                        i, j = int(cl[a_]), int(cl[b_])
                        results.append({'a': i, 'b': j, 'area_m2': round(area, 5), 'same_orientation': bool(np.dot(n[i], n[j]) > 0),
                                        'normal': nc[i].round(3).tolist(), 'plane_offset': round(float(off[i]), 4),
                                        'labels': (labels[i], labels[j]) if labels is not None else None})
    return results


def summarise(overlaps):
    """Group by label pair; same-orientation overlaps are the visible defects."""
    out = {}
    for o in overlaps:
        key = (tuple(sorted(o['labels'])) if o['labels'] else ('?', '?'), 'same' if o['same_orientation'] else 'opposite')
        row = out.setdefault(key, {'pairs': 0, 'area_m2': 0.})
        row['pairs'] += 1
        row['area_m2'] = round(row['area_m2'] + o['area_m2'], 4)
    return [{'labels': list(k[0]), 'orientation': k[1], **v} for k, v in sorted(out.items(), key=lambda kv: -kv[1]['area_m2'])]


def _halfplane(poly, a, b, keep_left):
    """Clip a convex polygon to the left (or right) side of the directed line a->b."""
    out = []
    side = [((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) * (1 if keep_left else -1) for p in poly]
    for k in range(len(poly)):
        p, q, sp, sq = poly[k], poly[(k + 1) % len(poly)], side[k], side[(k + 1) % len(poly)]
        if sp >= 0:
            out.append(p)
        if (sp >= 0) != (sq >= 0):
            t = sp / (sp - sq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _subtract(piece, clip):
    """Convex ``piece`` minus convex counter-clockwise ``clip``: a list of convex pieces (the part inside ``clip`` is dropped)."""
    out, rest = [], list(piece)
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        outside = _halfplane(rest, a, b, keep_left=False)
        if len(outside) >= 3 and _area(outside) > 1e-9:
            out.append(outside)
        rest = _halfplane(rest, a, b, keep_left=True)
        if len(rest) < 3:
            break
    return out


def resolve_coplanar(vertices, triangles, priority, labels=None, tol=0.003, min_area=1e-6):
    """Remove same-orientation coplanar overlaps without moving anything.

    Triangles are processed in ``priority`` order (lower number = higher priority). Each triangle keeps only the part of its
    area that no earlier (higher-priority) coplanar, same-oriented triangle already covers; the remainder is re-triangulated
    with its original winding. Faces with no coplanar competitor are returned untouched (same vertices). Hidden back-to-back
    (opposite-orientation) contacts are left alone. Returns (vertices, triangles, labels, stats).
    """
    v = np.asarray(vertices, float)
    t = np.asarray(triangles, np.int64)
    pr = np.asarray(priority)
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    n[ok] /= ln[ok, None]
    sign = np.ones(len(t))
    for k in range(3):
        first = (np.abs(n[:, k]) > 1e-6) & ~((np.abs(n[:, :k]) > 1e-6).any(axis=1) if k else np.zeros(len(t), bool))
        sign[first & (n[:, k] < 0)] = -1
    nc = n * sign[:, None]
    off = np.einsum('ij,ij->i', nc, p0)
    groups = {}
    for i in np.flatnonzero(ok):
        groups.setdefault((*np.round(nc[i] * 1000).astype(int), int(sign[i])), []).append(i)
    new_v, new_t, new_l = [v.copy()], [], []
    nv = len(v)
    keep_mask = np.ones(len(t), bool)
    stats = {'triangles_in': int(len(t)), 'fully_covered_removed': 0, 'clipped': 0, 'pieces_added': 0, 'area_removed_m2': 0.}
    for key, idx in groups.items():
        if len(idx) < 2:
            continue
        o = off[idx]
        order = np.argsort(o)
        clusters, cur = [], [idx[order[0]]]
        for a_, b_ in zip(order[:-1], order[1:]):
            if o[b_] - o[a_] <= tol:
                cur.append(idx[b_])
            else:
                clusters.append(cur)
                cur = [idx[b_]]
        clusters.append(cur)
        for cl in clusters:
            if len(cl) < 2:
                continue
            cl = sorted(cl, key=lambda i: (pr[i], i))
            nn = nc[cl[0]]
            u = np.cross(nn, [1., 0., 0.] if abs(nn[0]) < .9 else [0., 1., 0.])
            u /= np.linalg.norm(u)
            w = np.cross(nn, u)
            kept = []
            for i in cl:
                P = [tuple(q) for q in _ccw(np.array([(x @ u, x @ w) for x in (p0[i], p1[i], p2[i])]))]
                area0 = _area(P)
                aabb = (min(q[0] for q in P), max(q[0] for q in P), min(q[1] for q in P), max(q[1] for q in P))
                pieces, touched = [P], False
                for K, kb in kept:
                    if kb[1] < aabb[0] or aabb[1] < kb[0] or kb[3] < aabb[2] or aabb[3] < kb[2]:
                        continue
                    nxt = []
                    for piece in pieces:
                        if _area(_clip(piece, K)) <= min_area:
                            nxt.append(piece)
                        else:
                            touched = True
                            nxt.extend(_subtract(piece, K))
                    pieces = nxt
                    if not pieces:
                        break
                pieces = [pc for pc in pieces if _area(pc) > min_area]
                if not touched:
                    kept.append((P, aabb))
                    continue
                keep_mask[i] = False
                stats['area_removed_m2'] += area0 - sum(_area(pc) for pc in pieces)
                if not pieces:
                    stats['fully_covered_removed'] += 1
                    continue
                stats['clipped'] += 1
                for pc in pieces:
                    kept.append((pc, (min(q[0] for q in pc), max(q[0] for q in pc), min(q[1] for q in pc), max(q[1] for q in pc))))
                    base = nv
                    new_v.append(np.array([u * q[0] + w * q[1] + nn * off[i] for q in pc]))
                    nv += len(pc)
                    for k in range(1, len(pc) - 1):
                        tri = [base, base + k, base + k + 1]
                        new_t.append(tri if sign[i] > 0 else tri[::-1])  # keep the original outward winding
                        new_l.append(labels[i] if labels is not None else None)
                        stats['pieces_added'] += 1
    out_t = np.concatenate([t[keep_mask], np.array(new_t, np.int64).reshape(-1, 3)]) if len(new_t) else t[keep_mask]
    out_l = ([labels[i] for i in np.flatnonzero(keep_mask)] + new_l) if labels is not None else None
    stats['triangles_out'] = int(len(out_t))
    return np.concatenate(new_v), out_t, out_l, stats
