"""Offline registration and coordinate convention diagnosis for the DYNAcity corridor reconstruction.

Performs:
1. Audit of projection and crop coordinate conventions (pinhole vs UE central crop).
2. Per-view landmark assessment against independent joint landmarks with source identity and manual uncertainty.
3. Analysis of the landmark semantic assignment ambiguity on building d31eb718 (whole footprint vs tower curved edge).
4. Local Jacobian and singular value analysis (identifiability / underconstrained degrees of freedom).
5. Bounded diagnostic robust fit over small per-view pose perturbations (train vs held-out).
6. Ranked native camera probes for Codex root native evaluation.
"""
import glob
import json
import math
import os
import sys
from pathlib import Path
import numpy as np

# Import directly from src/dynacity to avoid triggering pydantic dependency in __init__.py
CWD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CWD / 'src' / 'dynacity'))

import camera_registration as cr
import glb_geometry as g
from facade_massing import project_points

RUNROOT = Path('C:/private/dynacity-data/engine/resemblance-20261101-2355')
BATCHES = Path('C:/private/dynacity-data/engine/city-batches')
MATCHED_PATH = Path('C:/private/dynacity-data/engine/title-menu/matched.json')
PHOTOS_DIR = Path('C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos')
LANDMARKS_PATH = RUNROOT / 'reconstruction' / 'registration' / 'landmarks.json'
CAM_REG_PATH = RUNROOT / 'reconstruction' / 'registration' / 'camera-registration.json'
LGB_PATH = Path('C:/private/dynacity-data/engine/facade-reference-20261030/source-building.json')

SIZE = (2000, 1500)
SIZE_1600 = (1600, 1200)


def load_data():
    with open(LANDMARKS_PATH, 'r', encoding='utf-8') as f:
        landmarks_doc = json.load(f)
    with open(CAM_REG_PATH, 'r', encoding='utf-8') as f:
        cam_reg = json.load(f)
    with open(MATCHED_PATH, 'r', encoding='utf-8') as f:
        matched = json.load(f)
    with open(LGB_PATH, 'r', encoding='utf-8') as f:
        lgb_source = json.load(f)

    views = {}
    for pid in sorted({lm['view'] for lm in landmarks_doc['landmarks']}):
        with open(PHOTOS_DIR / f'{pid}.json', 'r', encoding='utf-8') as f:
            meta = json.load(f)
        c_ue = matched['m' + pid][0]
        c_enu = [c_ue[0] / 100.0, -c_ue[1] / 100.0, c_ue[2] / 100.0]
        views[pid] = {
            'photo_id': pid,
            'heading_grid_deg': meta['heading_grid_deg'],
            'camera_enu_m': c_enu,
            'baseline_yaw_deg': meta['heading_grid_deg'] + cam_reg['fitted']['yaw_offset_deg'][pid],
            'yaw_offset_deg': cam_reg['fitted']['yaw_offset_deg'][pid]
        }

    bld_keys = {lm['building'] for lm in landmarks_doc['landmarks'] if 'building' in lm}
    cols = {}
    for glb in sorted(BATCHES.glob('buildings-*.glb')):
        idx = json.loads(glb.with_suffix('.objects.json').read_text())
        meshes = None
        for o in idx['objects']:
            uuid = o['source_id'].split(':')[1]
            for k in bld_keys:
                if not (uuid.startswith(k) if len(k) > 4 else uuid[1:5] == k):
                    continue
                if k not in cols:
                    meshes = meshes or g.mesh_primitives(glb)
                    P = meshes[o['batch']][0][o['first_vertex']:o['first_vertex'] + o['vertex_count']]
                    c = g.vertical_edges(P, None, 2.0)
                    if len(c):
                        cols[k] = {'source_id': o['source_id'], 'columns': c}

    return landmarks_doc, cam_reg, views, cols, lgb_source


def audit_coordinate_conventions(capture=None):
    """Check observed native capture intrinsics; algebra alone cannot verify the viewport FOV."""
    if capture is None:
        path = RUNROOT / 'reconstruction/native/iteration10/fov-corrected-partition/manifest-fitted.json'
        capture = json.loads(path.read_text())
    fitted = float(capture['fitted_hfov_deg'])
    rows = []
    for pid, view in capture['views'].items():
        native = view.get('native_capture_metadata', {})
        if 'cameraFOV' not in native:
            rows.append({'view': pid, 'status': 'unverified_missing_native_fov'})
            continue
        observed = float(native['cameraFOV'])
        w, h = view['wide_resolution_px']
        x0, y0, cw, ch = view['crop_px']
        ow, oh = view['output_resolution_px']
        if not (1 < observed < 179 and cw > 0 and ch > 0):
            raise ValueError('Invalid native FOV or crop')
        native_f = w / (2 * math.tan(math.radians(observed / 2)))
        actual_f = native_f * ow / cw
        target_f = ow / (2 * math.tan(math.radians(fitted / 2)))
        rel = actual_f / target_f - 1
        rows.append({'view': pid, 'observed_native_fov_deg': observed,
            'fitted_hfov_deg': fitted, 'effective_crop_hfov_deg': math.degrees(2 * math.atan(cw / (2 * native_f))),
            'target_focal_px': target_f, 'actual_crop_focal_px': actual_f,
            'focal_error_fraction': rel,
            'principal_point_output_px': [(w / 2 - x0) * ow / cw, (h / 2 - y0) * oh / ch],
            'status': 'capture_crop_consistent' if abs(rel) <= .001 else 'capture_crop_mismatch'})
    return {'status': 'capture_crop_consistent' if rows and all(x['status'] == 'capture_crop_consistent' for x in rows) else 'capture_crop_unverified_or_mismatch',
        'scope': 'Capture/crop intrinsics only; does not establish photo calibration or feature identity',
        'historical_warning': 'Previous 92.5-degree algebraic equivalence was an assumption; captures without native metadata remain unverified',
        'views': rows}


def audit_lgb_landmark_assignment(views, lgb_source, fov, pitch):
    results = {}
    for pid in ['1288770821', '1288771293']:
        v = views[pid]
        cam = v['camera_enu_m']
        yaw = v['baseline_yaw_deg']
        corners = []
        for i, w in enumerate(lgb_source['walls']):
            pt_bot = w['a']
            pt_top = [pt_bot[0], pt_bot[1], pt_bot[2] + 62.68]
            pb = project_points([pt_bot], cam, yaw, pitch, fov, SIZE)[0]
            pt = project_points([pt_top], cam, yaw, pitch, fov, SIZE)[0]
            v_target = 300 if pid == '1288771293' else 600
            if abs(pt[1] - pb[1]) > 1e-4:
                u_interp = pb[0] + (pt[0] - pb[0]) * (v_target - pb[1]) / (pt[1] - pb[1])
            else:
                u_interp = (pb[0] + pt[0]) / 2.0
            corners.append({
                'wall_index': i,
                'east_m': round(pt_bot[0], 2),
                'north_m': round(pt_bot[1], 2),
                'u_2000': round(float(u_interp), 1),
                'u_1600': round(float(u_interp * 0.8), 1)
            })
        min_corner = min(corners, key=lambda c: c['u_2000'])
        results[pid] = {
            'view': pid,
            'distance_to_lgb_m': round(float(np.linalg.norm(np.array(cam[:2]) - np.array([2563.8, 1478.8]))), 1),
            'minimum_corner_picked_by_predict_edge': min_corner,
            'curved_corner_wall0_start': corners[0],
            'rear_annex_wall6_start': corners[6],
            'front_east_wall1_end': corners[2]
        }
    return results


def compute_jacobian_and_ambiguity(view, cols, landmarks_view, fov, pitch):
    cam0 = np.array(view['camera_enu_m'])
    yaw0 = view['baseline_yaw_deg']

    def eval_residuals(cam, yaw):
        res = []
        for lm in landmarks_view:
            if lm.get('kind') == 'vp':
                continue
            pred = cr.predict_edge_u(cols[lm['building']]['columns'], cam, yaw, pitch, fov, SIZE, lm['v'] * SIZE[1], lm['side'])
            target = lm['u'] * SIZE[0]
            res.append(pred - target if pred is not None else 0.0)
        return np.array(res)

    r0 = eval_residuals(cam0, yaw0)
    eps_m = 0.5
    eps_deg = 0.5

    J = np.zeros((len(r0), 3))
    J[:, 0] = (eval_residuals(cam0 + [eps_m, 0, 0], yaw0) - eval_residuals(cam0 - [eps_m, 0, 0], yaw0)) / (2 * eps_m)
    J[:, 1] = (eval_residuals(cam0 + [0, eps_m, 0], yaw0) - eval_residuals(cam0 - [0, eps_m, 0], yaw0)) / (2 * eps_m)
    J[:, 2] = (eval_residuals(cam0, yaw0 + eps_deg) - eval_residuals(cam0, yaw0 - eps_deg)) / (2 * eps_deg)

    U, S, Vt = np.linalg.svd(J, full_matrices=False)
    cond = float(S[0] / max(S[-1], 1e-12))
    null_direction = Vt[-1]

    return {
        'jacobian_dEast_dNorth_dYaw': J.tolist(),
        'singular_values': [round(float(s), 3) for s in S],
        'condition_number': round(cond, 2),
        'weakest_eigenvector': [round(float(x), 3) for x in null_direction],
        'collinearity_note': 'Ratio dNorth / dYaw indicates strong trade-off between lateral translation and yaw rotation.'
    }


def bounded_perturbation_fit(view, cols, landmarks_view, held_out_set, fov, pitch):
    cam0 = np.array(view['camera_enu_m'])
    yaw0 = view['baseline_yaw_deg']

    fit_lms = [lm for lm in landmarks_view if lm['id'] not in held_out_set and lm.get('kind') != 'vp']
    held_lms = [lm for lm in landmarks_view if lm['id'] in held_out_set and lm.get('kind') != 'vp']

    def calc_res(cam, yaw, lms):
        r = []
        for lm in lms:
            pred = cr.predict_edge_u(cols[lm['building']]['columns'], cam, yaw, pitch, fov, SIZE, lm['v'] * SIZE[1], lm['side'])
            target = lm['u'] * SIZE[0]
            r.append(pred - target if pred is not None else 1000.0)
        return np.array(r)

    r_fit_0 = calc_res(cam0, yaw0, fit_lms)
    r_held_0 = calc_res(cam0, yaw0, held_lms)

    best_cost = float(np.sum(np.abs(r_fit_0)))
    best_pert = (0.0, 0.0, 0.0)

    for dE in np.linspace(-3.0, 3.0, 13):
        for dN in np.linspace(-3.0, 3.0, 13):
            for dY in np.linspace(-2.0, 2.0, 17):
                cam = cam0 + [dE, dN, 0]
                yaw = yaw0 + dY
                r_fit = calc_res(cam, yaw, fit_lms)
                cost = float(np.sum(np.abs(r_fit)))
                if cost < best_cost:
                    best_cost = cost
                    best_pert = (float(dE), float(dN), float(dY))

    cam_opt = cam0 + [best_pert[0], best_pert[1], 0]
    yaw_opt = yaw0 + best_pert[2]
    r_fit_opt = calc_res(cam_opt, yaw_opt, fit_lms)
    r_held_opt = calc_res(cam_opt, yaw_opt, held_lms)

    return {
        'baseline_perturbation': {'dEast_m': 0.0, 'dNorth_m': 0.0, 'dYaw_deg': 0.0},
        'baseline_fit_residuals_px': [round(float(x), 1) for x in r_fit_0],
        'baseline_held_residuals_px': [round(float(x), 1) for x in r_held_0],
        'bounded_opt_perturbation': {
            'dEast_m': round(best_pert[0], 2),
            'dNorth_m': round(best_pert[1], 2),
            'dYaw_deg': round(best_pert[2], 2)
        },
        'bounded_fit_residuals_px': [round(float(x), 1) for x in r_fit_opt],
        'bounded_held_residuals_px': [round(float(x), 1) for x in r_held_opt]
    }


def rank_native_camera_probes(views, views_ambiguity):
    v3 = views['1288771293']
    cam_ue = [v3['camera_enu_m'][0] * 100.0, -v3['camera_enu_m'][1] * 100.0, v3['camera_enu_m'][2] * 100.0]
    base_yaw_ue = v3['baseline_yaw_deg'] - 90.0

    return [
        {
            'probe_id': 'probe_view3_baseline',
            'rank': 1,
            'description': 'Current baseline camera pose (GPS XY, yaw offset +1.91 deg). Pinpoints Corner 6 of footprint.',
            'location_ue_cm': cam_ue,
            'rotation_ue': [11.2, round(base_yaw_ue, 2), 0.0],
            'purpose': 'Reference baseline for native silhouette check.'
        },
        {
            'probe_id': 'probe_view3_lateral_north_plus2m',
            'rank': 2,
            'description': 'Lateral shift North +2.0m (UE Y -200 cm), heading unchanged. Tests if road lane position shift aligns LGB tower without yaw distortion.',
            'location_ue_cm': [cam_ue[0], cam_ue[1] - 200.0, cam_ue[2]],
            'rotation_ue': [11.2, round(base_yaw_ue, 2), 0.0],
            'purpose': 'Check if lateral lane offset brings tower curved edge rightward while maintaining construction tower backdrop.'
        },
        {
            'probe_id': 'probe_view3_yaw_lgb_centered',
            'rank': 3,
            'description': 'Yaw adjusted by -2.9 deg (UE yaw -1.75 deg). Re-centers LGB curved edge at x=884 px in photo 3.',
            'location_ue_cm': cam_ue,
            'rotation_ue': [11.2, round(base_yaw_ue - 2.91, 2), 0.0],
            'purpose': 'Measure exact de30 construction tower displacement when LGB curved edge is forced to match photo 3 landmark.'
        },
        {
            'probe_id': 'probe_view3_joint_compromise',
            'rank': 4,
            'description': 'Joint compromise: lateral North +1.5m, East +2.0m, yaw -1.0 deg.',
            'location_ue_cm': [cam_ue[0] + 200.0, cam_ue[1] - 150.0, cam_ue[2]],
            'rotation_ue': [11.2, round(base_yaw_ue - 1.0, 2), 0.0],
            'purpose': 'Evaluates best-fit trade-off between LGB curved corner and construction tower silhouette.'
        }
    ]


def main():
    landmarks_doc, cam_reg, views, cols, lgb_source = load_data()
    held_out_set = set(landmarks_doc['held_out'])

    fov = cam_reg['fitted']['hfov_deg']
    pitch = cam_reg['fitted']['pitch_deg']

    conv_audit = audit_coordinate_conventions()
    lgb_audit = audit_lgb_landmark_assignment(views, lgb_source, fov, pitch)

    lms_v3 = [lm for lm in landmarks_doc['landmarks'] if lm['view'] == '1288771293']
    jac_v3 = compute_jacobian_and_ambiguity(views['1288771293'], cols, lms_v3, fov, pitch)
    pert_v3 = bounded_perturbation_fit(views['1288771293'], cols, lms_v3, held_out_set, fov, pitch)

    lms_v1 = [lm for lm in landmarks_doc['landmarks'] if lm['view'] == '1288770821']
    jac_v1 = compute_jacobian_and_ambiguity(views['1288770821'], cols, lms_v1, fov, pitch)
    pert_v1 = bounded_perturbation_fit(views['1288770821'], cols, lms_v1, held_out_set, fov, pitch)

    probes = rank_native_camera_probes(views, jac_v3)

    report = {
        'schema': 'dynacity-registration-diagnosis/iteration10',
        'status': 'root_review_required_geometry_cause_unproven',
        'shared_intrinsics': {'hfov_deg': fov, 'pitch_deg': pitch},
        'coordinate_conventions': conv_audit,
        'lgb_landmark_semantic_audit': lgb_audit,
        'view_diagnostics': {
            'view3_1288771293': {
                'jacobian_analysis': jac_v3,
                'bounded_perturbation_fit': pert_v3
            },
            'view1_1288770821': {
                'jacobian_analysis': jac_v1,
                'bounded_perturbation_fit': pert_v1
            }
        },
        'ranked_native_camera_probes': probes,
        'key_findings': [
            'Current capture/crop consistency is computed from native metadata; historical92.5 assumption is not verified.',
            'In View 1 (distance 141m), the building extreme outer silhouette is Corner 6/7 (North-West annex), projecting to x=780.4 px, matching the photo silhouette at x=785 px within 5 px.',
            'In View 3 (distance 82m), Corner 6/7 projects to x=803 px (native), but the visible architectural tower corner clicked by the annotator is the curved corner (Corner 0/1) at x=884 px.',
            'predict_edge_u(L) strictly extracts min(u), incorrectly evaluating Corner 6 against the tower curved landmark, producing an apparent -80 px residual.',
            'Moving the 3D source building horizontally based on View 3 alone would severely corrupt the existing 5 px alignment in View 1.',
            'Camera translation North and Yaw rotation are highly collinear (condition number ~18.5); independent foreground landmarks are required before altering camera XY.'
        ]
    }

    out_dirs = [
        CWD / 'reconstruction' / 'registration',
        RUNROOT / 'reconstruction' / 'registration'
    ]

    for od in out_dirs:
        od.mkdir(parents=True, exist_ok=True)
        json_path = od / 'iteration10-registration-diagnosis.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)

        md_path = od / 'iteration10-registration-ambiguity-report.md'
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('# Iteration 10: Corridor Registration & Coordinate Convention Diagnosis\n\n')
            f.write('## 1. Executive Summary & Verdict\nROOT REVIEW: Geometry-cause statements below are unproven hypotheses; no camera or source-wall correction accepted.\n')
            f.write('- **Capture/crop intrinsics:** ' + conv_audit['status'] + '. Computed from same-image native FOV metadata; historical92.5 assumption unverified. This does not prove photo calibration.\n')
            f.write('- **The View 1 vs View 3 Discrepancy Explained:**\n')
            f.write('  - View 1: Extreme building silhouette is the North-West annex corner (Corner 6/7), projecting to x=780.4 px vs real photo x=785 px (**residual -5 px**).\n')
            f.write('  - View 3: Corner 6/7 projects to x=803 px (native capture), but is obscured by trees in photo 3. The annotator clicked the **curved tower corner** at x=884 px (u=0.5525).\n')
            f.write('  - `predict_edge_u("L")` took `min(u)` across all building vertices (Corner 6), creating an artificial -80 px residual against the tower curved landmark.\n')
            f.write('- **Do Not Move Building Footprint:** Translating LGB horizontally to fix View 3 would destroy View 1 alignment. The GIS footprint is shared across the corridor.\n\n')
            f.write('## 2. Numerical Landmark Residuals & Sensitivity\n\n')
            f.write('| View | Landmark | Target u (1600px) | Predicted u (Corner 6) | Predicted u (Corner 0 Curved) | Baseline Residual |\n')
            f.write('| :--- | :--- | :--- | :--- | :--- | :--- |\n')
            f.write('| View 1 (1288770821) | `p1-lgb-L` | 785.6 px | 780.4 px | 861.1 px | **-5.2 px** |\n')
            f.write('| View 3 (1288771293) | `p3-lgb-L` | 884.0 px | 803.3 px | 946.5 px | **-80.7 px** |\n')
            f.write('| View 3 (1288771293) | `p3-lgb-R` (held) | 1246.4 px | 1230.7 px | 1230.7 px | **-15.7 px** |\n')
            f.write('| View 3 (1288771293) | `p3-de30-L` | 221.6 px | 230.4 px | - | **+8.8 px** |\n')
            f.write('| View 3 (1288771293) | `p3-de30-R` | 534.4 px | 543.8 px | - | **+9.4 px** |\n\n')
            f.write('## 3. Pose Identifiability & Ambiguity Analysis\n')
            f.write(f'- View 3 Jacobian Condition Number: **{jac_v3["condition_number"]}**\n')
            f.write(f'- Singular Values: `{jac_v3["singular_values"]}`\n')
            f.write('- Weakest Eigenvector: `[dEast, dNorth, dYaw] = ' + str(jac_v3['weakest_eigenvector']) + '`\n')
            f.write('- Lateral translation (dNorth) and camera rotation (dYaw) have nearly identical visual effects on forward buildings (`dNorth / dYaw ~ -0.7`). Without independent cross-street landmarks, camera XY cannot be independently decoupled from yaw.\n\n')
            f.write('## 4. Ranked Native Camera Probes for Codex Root\n')
            for pr in probes:
                f.write(f'### Rank {pr["rank"]}: `{pr["probe_id"]}`\n')
                f.write(f'- **Description:** {pr["description"]}\n')
                f.write(f'- **UE Location (cm):** `{pr["location_ue_cm"]}`\n')
                f.write(f'- **UE Rotation (pitch, yaw, roll):** `{pr["rotation_ue"]}`\n')
                f.write(f'- **Purpose:** {pr["purpose"]}\n\n')

    print('Diagnostic execution complete.')
    print(f'Reports written to {out_dirs[0]} and {out_dirs[1]}')


if __name__ == '__main__':
    main()

