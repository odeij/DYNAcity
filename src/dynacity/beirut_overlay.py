"""Alignment and conservative geometry links between Beirut 001 and BBED.

Spatial agreement is not building identity verification. Never replace missing
source attributes with values inferred from another record or from mesh height.
"""
from __future__ import annotations

import numpy as np
from shapely.strtree import STRtree


def register_rigid(model_xy, bbed_xy, *, step_m=10., pair_m=12.):
    """Infer the rotation and translation that lay model points on BBED points.

    The model has no geographic anchor, so its building centres are compared as
    a pattern with BBED's: both are binned on a `step_m` grid, the model grid is
    turned in 5° then 0.25° steps and cross-correlated (FFT) for the best shift,
    and the fit is refined on nearest pairs within `pair_m` (Kabsch). Scale is
    fixed at 1, since both are in metres. This is inferred, not surveyed.
    Returns `(rotation 2x2, translation xy, report)`; `xy @ rotation.T + translation`
    maps model coordinates into BBED's.
    """
    from scipy.ndimage import gaussian_filter
    from scipy.signal import fftconvolve
    from scipy.spatial import cKDTree

    a, b = np.asarray(model_xy, dtype=float), np.asarray(bbed_xy, dtype=float)

    def density(points):
        origin = points.min(0) - 20
        index = np.floor((points - origin) / step_m).astype(int)
        grid = np.zeros(index.max(0) + 3, dtype=np.float32)
        np.add.at(grid, (index[:, 0], index[:, 1]), 1)
        return gaussian_filter(np.minimum(grid, 1), .7), origin

    target, b_origin = density(b)

    def correlate(degrees):
        t = np.radians(degrees)
        rotation = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        source, a_origin = density(a @ rotation.T)
        c = fftconvolve(target, source[::-1, ::-1], mode='full')
        peak = np.array(np.unravel_index(c.argmax(), c.shape))
        return float(c.max()), rotation, (peak - (np.array(source.shape) - 1)) * step_m + b_origin - a_origin

    coarse = max(range(-180, 180, 5), key=lambda d: correlate(d)[0])
    _, rotation, shift = max((correlate(d) for d in np.arange(coarse - 4, coarse + 4.01, .25)), key=lambda x: x[0])
    for _ in range(30):
        distance, nearest = cKDTree(a @ rotation.T + shift).query(b)
        good = distance < pair_m
        aa, bb = a[nearest[good]], b[good]
        ca, cb = aa.mean(0), bb.mean(0)
        u, _, vt = np.linalg.svd((aa - ca).T @ (bb - cb))
        refined = vt.T @ u.T
        if np.linalg.det(refined) < 0:
            break
        moved = cb - ca @ refined.T
        done = np.linalg.norm(moved - shift) < .001 and np.linalg.norm(refined - rotation) < 1e-6
        rotation, shift = refined, moved
        if done:
            break
    distance, _ = cKDTree(a @ rotation.T + shift).query(b)
    report = {
        'method': 'rigid centroid-pattern registration, inferred not surveyed',
        'rotation_degrees': float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0]))),
        'rotation_matrix': rotation.tolist(), 'translation_xy_m': shift.tolist(),
        'source_objects': len(a), 'bbed_features': len(b),
        'bbed_to_model_percentiles_m': np.percentile(distance, [10, 25, 50, 75, 90]).tolist(),
        'bbed_within_5m': int((distance < 5).sum()), 'bbed_within_10m': int((distance < 10).sum()),
    }
    return rotation, shift, report


def match_footprints(model_shapes, bbed_shapes, *, min_iou=.65, max_centroid_m=8., margin=.15):
    """Return provisional one-to-one matches; ambiguity and collisions stay unlinked.

    Shapes must already share a projected metre CRS. The caller must disclose
    any inferred registration and source dates. Convex source silhouettes are
    only a conservative matching proxy, never a replacement for source geometry.
    """
    tree = STRtree(bbed_shapes)
    proposals = []
    for model in model_shapes:
        scored = []
        if not model.is_empty and model.area > 0:
            for j in tree.query(model):
                target = bbed_shapes[int(j)]
                if target.is_empty or target.area <= 0:
                    continue
                overlap = model.intersection(target).area
                union = model.area + target.area - overlap
                iou = overlap / union if union else 0.
                scored.append((iou, int(j), model.centroid.distance(target.centroid)))
        scored.sort(reverse=True)
        if not scored:
            proposals.append({'status': 'unlinked', 'reason': 'No footprint overlap'})
            continue
        best, target, distance = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.
        link = {'status': 'unlinked', 'candidate': target, 'iou': round(best, 4),
                'centroid_distance_m': round(distance, 2), 'runner_up_iou': round(runner, 4)}
        if best >= min_iou and distance <= max_centroid_m and best - runner >= margin:
            link.update(status='provisional', bbed_index=target, reason='Strong unique footprint overlap; identity not verified')
        else:
            link['reason'] = 'Footprint agreement is weak or ambiguous'
        proposals.append(link)
    # Separate source solids may describe the same physical building. Do not
    # silently attach a single BBED record to several separate model objects.
    owners = {}
    for i, link in enumerate(proposals):
        if link['status'] == 'provisional':
            owners.setdefault(link['bbed_index'], []).append(i)
    for indices in owners.values():
        if len(indices) > 1:
            for i in indices:
                proposals[i].pop('bbed_index')
                proposals[i].update(status='unlinked', reason='Multiple model objects compete for this BBED record')
    return proposals
