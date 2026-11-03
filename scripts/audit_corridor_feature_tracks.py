"""Offline photographic correspondence feasibility; no camera calibration or scene changes."""
from __future__ import annotations
import argparse
import hashlib
from datetime import datetime, timezone
import itertools
import json
import sys
from pathlib import Path
import numpy as np

RUN = Path('C:/private/dynacity-data/engine/resemblance-20261101-2355')
ROOT = Path(__file__).resolve().parents[1]
PHOTO_IDS = ['1288770821', '1288770597', '1288771293', '1288770701']

def distances(F, a, b):
    """Sqrt Sampson and symmetric point-to-epipolar-line distances, in pixels."""
    x, y = np.column_stack([a, np.ones(len(a))]), np.column_stack([b, np.ones(len(b))])
    fx, fty = x @ F.T, y @ F
    e2 = np.sum(y * fx, axis=1) ** 2
    da, db = np.sum(fx[:, :2] ** 2, axis=1), np.sum(fty[:, :2] ** 2, axis=1)
    return np.sqrt(e2 / np.maximum(da + db, 1e-20)), np.sqrt(.5 * e2 * (1 / np.maximum(da, 1e-20) + 1 / np.maximum(db, 1e-20)))

def stats(values):
    f = np.asarray(values)[np.isfinite(values)]
    return {'n': len(values), 'finite_n': len(f), 'median_px': float(np.median(f)) if len(f) else None,
            'p90_px': float(np.percentile(f, 90)) if len(f) else None}

def coverage(cv, points, shape):
    h, w = shape[:2]
    cells = {(min(3, max(0, int(x / w * 4))), min(2, max(0, int(y / h * 3)))) for x, y in points}
    area = cv.contourArea(cv.convexHull(np.float32(points))) if len(points) >= 3 else 0.
    return {'grid_4x3_occupied_cells': len(cells), 'grid_cells': sorted(cells), 'convex_hull_fraction_of_image': area / (w * h)}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=RUN / 'reference-assets/feature-tracks')
    p.add_argument('--images', type=Path, default=Path('C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos'))
    p.add_argument('--deps', type=Path, default=RUN / 'sfm-deps')
    p.add_argument('--seed', type=int, default=7)
    p.add_argument('--max-features',type=int,default=12000)
    p.add_argument('--source-manifest', type=Path, help='JSON source_photos list with photo_id, input_file and photo_source_sha256')
    p.add_argument('--adjacent', action='store_true', help='Fit consecutive manifest entries only')
    args = p.parse_args()
    sys.path.insert(0, str(args.deps))
    import cv2 as cv
    cv.setNumThreads(1); cv.setRNGSeed(args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((RUN / 'reference-assets/streetscape/streetscape-candidates.json').read_text())
    if args.source_manifest:
        catalog = json.loads(args.source_manifest.read_text(encoding='utf-8'))
    provenance = {str(x['photo_id']): x for x in catalog['source_photos']}
    photo_ids = list(provenance) if args.source_manifest else PHOTO_IDS
    if not 2 <= len(photo_ids) <= 16: raise ValueError('Expected 2..16 source photographs')
    sift = cv.SIFT_create(nfeatures=args.max_features)
    images, keys, descriptors, sources = [], [], [], []
    for i, pid in enumerate(photo_ids):
        path = Path(provenance[pid].get('input_file', args.images / f'{pid}.jpg'))
        if not path.exists():path = args.images / f'match-{i + 1}-real.jpg'
        im = cv.imread(str(path))
        if im is None: raise RuntimeError(f'Cannot read source photograph: {path}')
        hsv = cv.cvtColor(im, cv.COLOR_BGR2HSV)
        green = cv.inRange(hsv, (30, 35, 25), (95, 255, 255))
        mask = np.zeros(im.shape[:2], np.uint8); mask[:int(im.shape[0] * .65)] = 255; mask[green != 0] = 0
        kp, desc = sift.detectAndCompute(cv.cvtColor(im, cv.COLOR_BGR2GRAY), mask)
        cv.imwrite(str(args.output / f'mask-{pid}.png'), mask)
        images.append(im); keys.append(kp); descriptors.append(desc)
        source = dict(provenance[pid])
        source.update(input_file=str(path), actual_input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      dimensions_px=[im.shape[1], im.shape[0]], sift_keypoints=len(kp))
        source['matches_catalog_source_hash'] = source['actual_input_sha256'] == source.get('photo_source_sha256')
        source['analysis_input_role']='hash-verified catalog original' if source['matches_catalog_source_hash'] else 'derivative; original-file lineage unverified'
        sources.append(source)
    report = {'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status': 'unvalidated_until_visual_review', 'opencv_version': cv.__version__, 'sources': sources,
              'parameters': {'seed': args.seed, 'sift_max_features': args.max_features, 'error_reference_width_px':1600, 'ratio_threshold': .7, 'mutual_ratio_match': True,
                             'fit_fraction': .7, 'ransac_threshold_px': 1.5, 'confidence': .999, 'max_iterations': 10000,
                             'heldout_sampson_gate_px': 2.},
              'limits': ['Candidate mask is upper65% minus HSV green, NOT verified static segmentation.',
                         'Branches, repeated windows, shadows or moving objects can contaminate matches.',
                         'Fit/heldout descriptor matches are disjoint within a pair, but correlated features and overlap across pairs remain.',
                         'Fundamental matrices do not establish metric scale, geographic position, camera calibration or building dimensions.',
                         'Numerical feasibility gates are proposals requiring independent visual review; no scene or UI changes.'], 'pairs': []}
    matcher = cv.BFMatcher(cv.NORM_L2)
    pair_indices = zip(range(len(photo_ids)-1), range(1,len(photo_ids))) if args.adjacent else itertools.combinations(range(len(photo_ids)), 2)
    for pair_index, (i, j) in enumerate(pair_indices):
        stem = f'{photo_ids[i]}-{photo_ids[j]}'; row = {'pair': stem, 'status': 'insufficient_matches'}
        if descriptors[i] is None or descriptors[j] is None:
            report['pairs'].append(row); continue
        def ratio(a, b):
            return {m.queryIdx: m.trainIdx for group in matcher.knnMatch(a, b, k=2)
                    if len(group) == 2 for m, n in [group] if m.distance < .7 * n.distance}
        forward, backward = ratio(descriptors[i], descriptors[j]), ratio(descriptors[j], descriptors[i])
        pairs = sorted((a, b) for a, b in forward.items() if backward.get(b) == a)
        a = np.float64([keys[i][x].pt for x, y in pairs]).reshape(-1, 2)
        b = np.float64([keys[j][y].pt for x, y in pairs]).reshape(-1, 2)
        order = np.random.default_rng(args.seed + pair_index).permutation(len(pairs)); cut = int(len(order) * .7)
        fit, held = np.sort(order[:cut]), np.sort(order[cut:])
        row.update(mutual_candidates=len(pairs), fitting_n=len(fit), heldout_n=len(held))
        # Preserve raw evidence even when a pair cannot support a fundamental matrix.
        raw_path=args.output / f'{stem}.npz'
        np.savez_compressed(raw_path,points_a=a,points_b=b,descriptor_match_indices=np.asarray(pairs),fit_indices=fit,heldout_indices=held)
        row['npz']=str(raw_path)
        h=max(images[i].shape[0],images[j].shape[0]);w=images[i].shape[1]
        raw_sheet=np.full((h+90,w+images[j].shape[1],3),250,np.uint8)
        raw_sheet[90:90+images[i].shape[0],:w]=images[i];raw_sheet[90:90+images[j].shape[0],w:]=images[j]
        for group,color in [(fit,(0,220,0)),(held,(220,0,220))]:
            for k in (group[np.linspace(0,len(group)-1,min(30,len(group))).astype(int)] if len(group) else []):
                x=tuple(np.rint(a[k]+[0,90]).astype(int));y=tuple(np.rint(b[k]+[w,90]).astype(int))
                cv.line(raw_sheet,x,y,color,1,cv.LINE_AA);cv.circle(raw_sheet,x,5,color,2);cv.circle(raw_sheet,y,5,color,2)
        cv.putText(raw_sheet,stem+': RAW fit candidates GREEN / heldout MAGENTA; model not established',(20,35),cv.FONT_HERSHEY_SIMPLEX,1.,(20,20,20),2)
        raw_image=args.output / f'{stem}-matches.jpg';cv.imwrite(str(raw_image),raw_sheet,[cv.IMWRITE_JPEG_QUALITY,88]);row['visual_match_sheet']=str(raw_image)
        scale=min(images[i].shape[1],images[j].shape[1])/1600.
        row['metrics_unit']='pixels at 1600px reference width; NPZ coordinates/errors remain original input pixels'
        row['input_pixel_scale']=scale
        if len(fit) < 8:
            report['pairs'].append(row); continue
        cv.setRNGSeed(args.seed + pair_index)
        method = cv.USAC_MAGSAC if hasattr(cv, 'USAC_MAGSAC') else cv.FM_RANSAC
        F, inliers = cv.findFundamentalMat(a[fit], b[fit], method, 1.5*scale, .999, 10000)
        if F is None or F.shape != (3, 3) or inliers is None or not np.isfinite(F).all() or np.linalg.norm(F) < 1e-15:
            row['status'] = 'fundamental_estimation_failed'; report['pairs'].append(row); continue
        singular = np.linalg.svd(F, compute_uv=False)
        if singular[1] < singular[0] * 1e-10 or singular[2] > singular[0] * 1e-5:
            row['status'] = 'degenerate_fundamental'; report['pairs'].append(row); continue
        inlier_fit = fit[inliers.reshape(-1).astype(bool)]
        sampson, symmetric = distances(F, a, b)
        norm_sampson,norm_symmetric=sampson/scale,symmetric/scale
        ca, cb = coverage(cv, a[inlier_fit], images[i].shape), coverage(cv, b[inlier_fit], images[j].shape)
        passed = (len(inlier_fit) >= 50 and len(held) >= 20 and ca['grid_4x3_occupied_cells'] >= 4
                  and cb['grid_4x3_occupied_cells'] >= 4 and np.median(norm_sampson[held]) <= 2.)
        row.update(status='numerical_candidate_requires_visual_review' if passed else 'proposed_feasibility_gate_failed',
                   method='USAC_MAGSAC' if method == getattr(cv, 'USAC_MAGSAC', None) else 'FM_RANSAC',
                   fitting_inliers_n=len(inlier_fit), fitting_inliers_fraction=len(inlier_fit) / len(fit),
                   fitting_all_sampson=stats(norm_sampson[fit]), fitting_inlier_sampson=stats(norm_sampson[inlier_fit]),
                   heldout_all_sampson=stats(norm_sampson[held]), heldout_all_symmetric_epipolar=stats(norm_symmetric[held]),
                   heldout_fraction_at_2px=float(np.mean(norm_sampson[held] <= 2)) if len(held) else None,
                   coverage_a=ca, coverage_b=cb, fundamental_matrix=F.tolist())
        # A plane can fit F well without constraining 3D motion. Fit H on training only too.
        H,hmask=cv.findHomography(a[fit],b[fit],cv.RANSAC,3.*scale,maxIters=10000,confidence=.999)
        if H is not None and np.isfinite(H).all() and np.linalg.cond(H)<1e12:
            ab=cv.perspectiveTransform(a.reshape(-1,1,2),H).reshape(-1,2)
            ba=cv.perspectiveTransform(b.reshape(-1,1,2),np.linalg.inv(H)).reshape(-1,2)
            hd=np.sqrt(.5*(np.sum((ab-b)**2,axis=1)+np.sum((ba-a)**2,axis=1)))/scale
            frac=float(np.mean(hd[inlier_fit]<=2)) if len(inlier_fit) else 0.
            held_frac=float(np.mean(hd[held]<=2)) if len(held) else 0.
            dominant=frac>=.9 and held_frac>=.9
            row['homography_diagnostic']={'method':'RANSAC on fitting matches only','fitting_F_inlier_fraction_H_at_2px':frac,'heldout_all_symmetric_transfer':stats(hd[held]),'heldout_all_fraction_H_at_2px':held_frac,'homography_dominant_flag':dominant,'limits':'Plane dominance is a degeneracy warning, not proof of planar scene or verified motion.'}
            if dominant:row['status']='homography_dominant_requires_more_depth_evidence'
        npz = args.output / f'{stem}.npz' 
        np.savez_compressed(npz, points_a=a, points_b=b, descriptor_match_indices=np.asarray(pairs), fit_indices=fit,
                            heldout_indices=held, fitting_inlier_indices=inlier_fit, F=F, sampson_px=sampson, symmetric_px=symmetric)
        row['npz'] = str(npz)
        h = max(images[i].shape[0], images[j].shape[0]); w = images[i].shape[1]
        sheet = np.full((h + 90, w + images[j].shape[1], 3), 250, np.uint8)
        sheet[90:90 + images[i].shape[0], :w] = images[i]; sheet[90:90 + images[j].shape[0], w:] = images[j]
        for group, color in [(inlier_fit, (0, 220, 0)), (held, (220, 0, 220))]:
            shown = group[np.linspace(0, len(group) - 1, min(30, len(group))).astype(int)] if len(group) else []
            for k in shown:
                x = tuple(np.rint(a[k] + [0, 90]).astype(int)); y = tuple(np.rint(b[k] + [w, 90]).astype(int))
                cv.line(sheet, x, y, color, 1, cv.LINE_AA); cv.circle(sheet, x, 4, color, 1); cv.circle(sheet, y, 4, color, 1)
        cv.putText(sheet, f'{stem}: fit inliers GREEN; heldout raw matches MAGENTA; NOT calibrated', (20, 30), cv.FONT_HERSHEY_SIMPLEX, .8, (20, 20, 20), 2)
        cv.putText(sheet, 'Grab / KartaView, 2018-07-21, CC BY-SA 4.0. Candidate masks; visual verification required.', (20, 65), cv.FONT_HERSHEY_SIMPLEX, .7, (20, 20, 20), 1)
        image = args.output / f'{stem}-matches.jpg'; cv.imwrite(str(image), sheet, [cv.IMWRITE_JPEG_QUALITY, 88]); row['visual_match_sheet'] = str(image)
        report['pairs'].append(row)
        print(stem, row['status'], 'fit', len(inlier_fit), 'held median', row['heldout_all_sampson']['median_px'], flush=True)
    (args.output / 'feature-track-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(args.output / 'feature-track-report.json'), 'pairs': len(report['pairs'])}))

if __name__ == '__main__':
    main()
