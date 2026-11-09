"""Which source building/wall is the pale foreground volume in a native left view, and does the photo show a structure there?

python scripts/identify_occluder.py <camera-registration.json> <left-cameras.json> <corridor-manifest-v2.json> <out_dir>
1. Raster every source wall (depth-ordered) from the native 'street' camera (same position/rotation as the capture) and report, for the
   lower-central image region where the pale volume hides the block's podium, the visible pixels per source wall.
2. Project those same source walls through the fitted photo cameras and draw them on photos 2 and 4 so the overlay can be compared with
   what the photograph actually shows there. This only establishes correspondence evidence; it never changes geometry.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402


def raster_cam(walls, cam, yaw, pitch, f, size):
    W, H = size
    idmap = np.full((H, W), -1, np.int32); order = []
    for k, w in enumerate(walls):
        mid = (w['a'] + w['b']) / 2; t = w['b'] - w['a']; n = np.array([t[1], -t[0]]); n /= np.linalg.norm(n)
        if n @ (cam[:2] - mid) > 0:
            order.append((-float(np.linalg.norm(mid - cam[:2])), k))
    for _, k in sorted(order):
        w = walls[k]
        quad = np.array([[*w['a'], w['z0']], [*w['b'], w['z0']], [*w['b'], w['z1']], [*w['a'], w['z1']]])
        cs = vw.clip_near(vw.camera_space(quad, cam, yaw, pitch))
        if len(cs) < 3:
            continue
        px = np.stack([W / 2 + f * cs[:, 0] / cs[:, 2], H / 2 - f * cs[:, 1] / cs[:, 2]], axis=1)
        if np.abs(px).max() > 1e5:
            continue
        cv2.fillConvexPoly(idmap, px.round().astype(np.int32), int(k))
    return idmap


def main(reg_path, cams_path, man_path, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    views, walls, idmaps, f_photo, pitch_photo = vw.raster(reg_path)
    pose = json.loads(Path(cams_path).read_text())['poses']['street']
    cam = np.array(pose['cam_local_m']); look = np.array(pose['look_at_local_m']); d = look - cam
    yaw = math.degrees(math.atan2(d[0], d[1])); pitch = math.degrees(math.atan2(d[2], math.hypot(d[0], d[1])))
    size = (1600, 900); f = (size[0] / 2) / math.tan(math.radians(92.5 / 2))
    idm = raster_cam(walls, cam, yaw, pitch, f, size)
    region = np.zeros(size[::-1], bool); region[int(size[1] * 0.45):int(size[1] * 0.85), int(size[0] * 0.12):int(size[0] * 0.75)] = True
    ids, counts = np.unique(idm[region & (idm >= 0)], return_counts=True)
    ranked = sorted(zip(counts, ids), reverse=True)[:6]
    table = []
    for c, k in ranked:
        w = walls[k]; sid = w['sid']
        same = [x for x in walls if x['sid'] == sid]
        table.append({'source_id': sid, 'edge': w['edge'], 'visible_px_in_region': int(c), 'height_m': round(max(x['z1'] for x in same) - min(x['z0'] for x in same), 2),
                      'wall_length_m': round(float(np.linalg.norm(w['b'] - w['a'])), 2)})
        print(sid[10:18], 'edge', w['edge'], int(c), 'px', table[-1]['height_m'], 'm high', table[-1]['wall_length_m'], 'm wall')
    lead = table[0]['source_id']
    lead_ids = [k for k, w in enumerate(walls) if w['sid'] == lead]
    for pid in ('1288770597', '1288770701'):
        img = cv2.resize(cv2.imread(str(vw.PHOTOS / f'{pid}.jpg')), vw.S)
        lay = np.zeros_like(img); lay[np.isin(idmaps[pid], lead_ids)] = (0, 255, 255)
        bl = cv2.addWeighted(img, .65, lay, .35, 0)
        cv2.putText(bl, f'source {lead[10:18]} projected (yellow)', (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        cv2.imwrite(str(out / f'occluder-{lead[10:18]}-on-photo-{pid}.jpg'), bl[380:1000, 0:760])
    img = np.zeros((size[1], size[0], 3), np.uint8); img[:] = (60, 60, 60)
    img[np.isin(idm, lead_ids)] = (0, 255, 255)
    cv2.rectangle(img, (int(size[0] * .12), int(size[1] * .45)), (int(size[0] * .75), int(size[1] * .85)), (255, 255, 255), 2)
    cv2.imwrite(str(out / f'occluder-{lead[10:18]}-in-native-street-view.png'), img)
    Path(out / 'occluder-identification.json').write_text(json.dumps({'schema': 'dynacity-occluder-identification/1', 'camera_local_m': cam.round(2).tolist(), 'region_fraction': 'x 12-75%, y 45-85% of the frame',
        'ranked_visible_source_walls': table, 'lead': lead, 'limits': ['footprints carry ~5 m position error', 'identification only; nothing is hidden or moved']}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:5])
