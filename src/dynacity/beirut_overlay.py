"""Conservative geometry links for the private Beirut 001 / BBED pilot.

Spatial agreement is not building identity verification. Never replace missing
source attributes with values inferred from another record or from mesh height.
"""
from __future__ import annotations

import numpy as np
from shapely.strtree import STRtree


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
