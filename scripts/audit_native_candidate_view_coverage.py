"""Project candidate bounds into saved native views; no occlusion/clearance claim."""
import argparse
import itertools
import json
import math
from pathlib import Path

import numpy as np


def project_bounds(bounds_ue_cm, view):
    """Conservative AABB projection using Unreal X-forward/Y-right/Z-up basis."""
    lo, hi = np.asarray(bounds_ue_cm, dtype=float)
    if lo.shape != (3,) or not np.isfinite([lo, hi]).all() or np.any(lo > hi):
        raise ValueError('Finite ordered UE centimetre bounds required')
    pitch, yaw, roll = np.radians(view['rotation_pitch_yaw_roll'])
    if abs(roll) > 1e-8:
        raise ValueError('Roll is not supported by this diagnostic')
    forward = np.array([math.cos(pitch)*math.cos(yaw), math.cos(pitch)*math.sin(yaw), math.sin(pitch)])
    right = np.array([-math.sin(yaw), math.cos(yaw), 0.])
    up = np.cross(forward, right)
    corners = np.array(list(itertools.product(*zip(lo, hi))))
    relative = corners - np.asarray(view['location_ue_cm'])
    depth = relative @ forward
    out = {'depth_cm': [float(depth.min()), float(depth.max())]}
    if depth.max() <= 0:
        return dict(out, status='BEHIND_CAMERA', fitted_overlap=False)
    if depth.min() <= 0:
        return dict(out, status='NEAR_PLANE_INTERSECTION', fitted_overlap=None)
    w, h = view['wide_resolution_px']
    focal = w / (2 * math.tan(math.radians(view['render_fov_deg'])/2))
    pixels = np.column_stack([w/2 + focal*(relative @ right)/depth,
                              h/2 - focal*(relative @ up)/depth])
    x0, y0, cw, ch = view['crop_px']
    pmin, pmax = pixels.min(axis=0), pixels.max(axis=0)
    overlaps = bool(pmax[0] >= x0 and pmin[0] <= x0+cw and pmax[1] >= y0 and pmin[1] <= y0+ch)
    return dict(out, status='CROP_OVERLAP' if overlaps else 'OUTSIDE_FITTED_CROP',
                fitted_overlap=overlaps, wide_bounds_px=[pmin.tolist(),pmax.tolist()],
                fitted_bounds_normalized=[((pmin-[x0,y0])/[cw,ch]).tolist(),
                                          ((pmax-[x0,y0])/[cw,ch]).tolist()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True,
                        help='JSON list with id and bounds_ue_cm:[min,max]')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    views = json.loads(args.manifest.read_text())['views']
    candidates = json.loads(args.candidates.read_text())
    result = {'limits': 'Bounds/frustum broadphase only; does not establish native visibility, occlusion, source registration or clearance.',
              'candidates': [{'id': c['id'], 'views': {key: project_bounds(c['bounds_ue_cm'], v)
                             for key,v in views.items()}} for c in candidates]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
