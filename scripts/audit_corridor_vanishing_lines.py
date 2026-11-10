#!/usr/bin/env python3
import sys, json, numpy as np, math, hashlib, tempfile, os
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass

def to_native(val):
    """Convert numpy types to native Python types for JSON serialization."""
    if isinstance(val, (np.integer, np.floating, np.bool_)):
        return val.item()
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, (list, tuple)):
        return [to_native(v) for v in val]
    if isinstance(val, dict):
        return {k: to_native(v) for k, v in val.items()}
    return val

def atomic_write_json(path: Path, data: Dict):
    """Write JSON atomically via tempfile+replace to avoid truncated output on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as tf:
        json.dump(to_native(data), tf, indent=2, allow_nan=False)
        tmp_name = tf.name
    os.replace(tmp_name, path)

SFM_DEPS = Path("C:/private/dynacity-data/engine/resemblance-20261101-2355/sfm-deps")
sys.path.insert(0, str(SFM_DEPS))
import cv2

INPUT_DIR = Path("C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos")
PHOTO_IDS = ["1288770821", "1288770597", "1288771293", "1288770701"]
OUTPUT_DIR = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/vanishing-lines")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DETECT_WIDTH = 1600
HELDOUT_FRACTION = 0.25
LSD_SCALE = 0.8
LSD_SIGMA_SCALE = 0.6
LSD_QUANT = 2.0
LSD_ANG_TH = 22.5
LSD_LOG_EPS = 0
LSD_DENSITY_TH = 0.7
LSD_N_BINS = 1024
MIN_LINE_LENGTH_PX = 30
GREEN_HSV_LO = np.array([35, 40, 40])
GREEN_HSV_HI = np.array([85, 255, 255])
RANSAC_THRESH_DEG = 2.0
RANSAC_MAX_ITERS = 2000
MIN_INLIERS_VP = 8
VP_CLUSTER_ANGLE_DEG = 3.0
PRINCIPAL_POINT_TOL_PX = 50

@dataclass
class LineSegment:
    x1: float; y1: float; x2: float; y2: float
    length: float; angle_deg: float
    is_heldout: bool = False
    excluded: bool = False
    exclusion_reason: str = ""
    vp_cluster: int = -1
    vp_residual_deg: float = 0.0

@dataclass
class VanishingPoint:
    x: float; y: float; w: float
    is_infinite: bool
    direction_deg: float
    inlier_indices: List[int]
    inlier_count: int
    residual_median_deg: float
    residual_std_deg: float
    conditioning: float

@dataclass
class PhotoDiagnostic:
    photo_id: str; sha256: str; image_size: Tuple[int, int]
    raw_segments: List[LineSegment]; kept_segments: List[LineSegment]
    heldout_indices: List[int]; vps: List[VanishingPoint]
    vertical_vp: Optional[VanishingPoint]
    pitch_bounds_deg: Tuple[float, float]; roll_bounds_deg: Tuple[float, float]
    hfov_estimate: Optional[Dict[str, Any]]
    heldout_residuals_deg: List[float]
    current_hfov_pitch_plausible: bool; notes: List[str]

def line_angle(x1, y1, x2, y2):
    return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180

def line_length(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)

def line_direction(x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    if norm > 0:
        return dx / norm, dy / norm
    return 0.0, 0.0

def line_midpoint(x1, y1, x2, y2):
    return (x1 + x2) / 2, (y1 + y2) / 2

def vp_direction_from_segment(x1, y1, x2, y2, vp_x, vp_y, vp_w):
    dx, dy = line_direction(x1, y1, x2, y2)
    mx, my = line_midpoint(x1, y1, x2, y2)
    if abs(vp_w) < 1e-8:
        vpx, vpy = vp_x, vp_y
        vnorm = math.hypot(vpx, vpy)
        if vnorm > 0:
            vpx, vpy = vpx / vnorm, vpy / vnorm
    else:
        vx, vy = vp_x / vp_w, vp_y / vp_w
        vpx, vpy = vx - mx, vy - my
        vnorm = math.hypot(vpx, vpy)
        if vnorm > 0:
            vpx, vpy = vpx / vnorm, vpy / vnorm
        else:
            return 90.0
    dot = abs(dx * vpx + dy * vpy)
    dot = min(1.0, max(-1.0, dot))
    return math.degrees(math.acos(dot))

def vp_from_lines_homogeneous(line_homog, indices):
    if len(indices) < 2:
        return np.array([0., 0., 0.]), True
    A = np.array([line_homog[i] for i in indices])
    _, _, Vt = np.linalg.svd(A)
    vp = Vt[-1]
    is_inf = abs(vp[2]) < 1e-6
    return vp, is_inf

def vp_direction_deg(vp, img_center):
    cx, cy = img_center
    if abs(vp[2]) < 1e-8:
        return (math.degrees(math.atan2(vp[1], vp[0])) + 90) % 180
    vx, vy = vp[0]/vp[2], vp[1]/vp[2]
    return (math.degrees(math.atan2(vy - cy, vx - cx)) + 90) % 180

def is_green_region(img_hsv, x1, y1, x2, y2, sample_n=5):
    xs = np.linspace(x1, x2, sample_n).astype(int)
    ys = np.linspace(y1, y2, sample_n).astype(int)
    xs = np.clip(xs, 0, img_hsv.shape[1]-1)
    ys = np.clip(ys, 0, img_hsv.shape[0]-1)
    pixels = img_hsv[ys, xs]  # shape (N, 3)
    # Reshape for inRange: (N, 1, 3)
    pixels_reshaped = pixels.reshape(-1, 1, 3)
    green_mask = cv2.inRange(pixels_reshaped, GREEN_HSV_LO, GREEN_HSV_HI)
    return np.mean(green_mask > 0) > 0.4

def detect_curved_glazing_region(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = cv2.magnitude(gx, gy)
    sat = hsv[:,:,1].astype(np.float32) / 255.0
    kernel = np.ones((15,15), np.float32) / 225
    grad_mean = cv2.filter2D(grad_mag, -1, kernel)
    grad_var = cv2.filter2D(grad_mag**2, -1, kernel) - grad_mean**2
    mask = (sat > 0.3) & (grad_var > np.percentile(grad_var, 85))
    return mask.astype(np.uint8) * 255

def in_exclusion_mask(x, y, mask):
    h, w = mask.shape
    if 0 <= x < w and 0 <= y < h:
        return mask[int(y), int(x)] > 0
    return False

def normalize_lines(line_homog, img_center):
    cx, cy = img_center
    scale = 1.0 / math.hypot(img_center[0], img_center[1])
    T = np.array([[scale, 0, -cx*scale],
                  [0, scale, -cy*scale],
                  [0, 0, 1]])
    T_inv_T = np.linalg.inv(T).T
    normalized = []
    for l in line_homog:
        ln = T_inv_T @ l
        norm = np.linalg.norm(ln[:2])
        if norm > 0:
            ln = ln / norm
        normalized.append(ln)
    return normalized, T

def denormalize_vp(vp_norm, T):
    # Normalized points: p_norm = T @ p_orig
    # So VP in original coords: vp_orig = T^{-1} @ vp_norm
    T_inv = np.linalg.inv(T)
    vp = T_inv @ vp_norm
    return vp

def process_photo(photo_id):
    img_path = INPUT_DIR / f"{photo_id}.jpg"
    json_path = INPUT_DIR / f"{photo_id}.json"
    with open(json_path) as ff:
        meta = json.load(ff)
    sha256 = meta["sha256"]
    orig_w, orig_h = meta["image_size_px"]
    img_full = cv2.imread(str(img_path))
    if img_full is None:
        raise RuntimeError(f"Failed to load {img_path}")
    scale = DETECT_WIDTH / orig_w
    det_h = int(orig_h * scale)
    img_det = cv2.resize(img_full, (DETECT_WIDTH, det_h), interpolation=cv2.INTER_AREA)
    gray_det = cv2.cvtColor(img_det, cv2.COLOR_BGR2GRAY)
    hsv_det = cv2.cvtColor(img_det, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv_det, GREEN_HSV_LO, GREEN_HSV_HI)
    green_mask = cv2.dilate(green_mask, np.ones((5,5), np.uint8), iterations=1)
    glazing_mask = detect_curved_glazing_region(img_det)
    lsd = cv2.createLineSegmentDetector(
        cv2.LSD_REFINE_STD,
        LSD_SCALE,
        LSD_SIGMA_SCALE,
        LSD_QUANT,
        LSD_ANG_TH,
        LSD_LOG_EPS,
        LSD_DENSITY_TH,
        LSD_N_BINS
    )
    lines = lsd.detect(gray_det)[0]
    raw_segments = []
    if lines is not None:
        for l in lines[:, 0, :]:
            x1, y1, x2, y2 = l
            length = line_length(x1, y1, x2, y2)
            if length < MIN_LINE_LENGTH_PX:
                continue
            angle = line_angle(x1, y1, x2, y2)
            excluded = False
            reason = ""
            mid_x, mid_y = (x1+x2)/2, (y1+y2)/2
            if is_green_region(hsv_det, x1, y1, x2, y2):
                excluded = True
                reason = "green_foliage"
            elif in_exclusion_mask(mid_x, mid_y, glazing_mask):
                excluded = True
                reason = "curved_glazing_heuristic"
            raw_segments.append(LineSegment(
                x1=x1, y1=y1, x2=x2, y2=y2,
                length=length, angle_deg=angle,
                excluded=excluded, exclusion_reason=reason
            ))
    kept_indices = [i for i, s in enumerate(raw_segments) if not s.excluded]
    kept_indices.sort(key=lambda i: (raw_segments[i].angle_deg, -raw_segments[i].length))
    n_heldout = max(1, int(len(kept_indices) * HELDOUT_FRACTION))
    heldout_set = set(kept_indices[:n_heldout])
    for i in kept_indices:
        raw_segments[i].is_heldout = (i in heldout_set)
    fit_indices = [i for i in kept_indices if i not in heldout_set]
    fit_segments = [raw_segments[i] for i in fit_indices]
    line_homog = [np.array([s.y1 - s.y2, s.x2 - s.x1, s.x1*s.y2 - s.x2*s.y1]) for s in fit_segments]
    for i, l in enumerate(line_homog):
        norm = np.linalg.norm(l[:2])
        if norm > 0:
            line_homog[i] = l / norm
    img_center = (DETECT_WIDTH / 2, det_h / 2)
    line_homog_norm, T = normalize_lines(line_homog, img_center)
    vps = []
    used = set()
    # Limit families to 3 for speed
    max_families = 3
    families_found = 0
    for seed_idx in range(len(fit_segments)):
        if families_found >= max_families:
            break
        if seed_idx in used:
            continue
        best_inliers = []
        best_vp_norm = None
        # Bound iterations
        for _ in range(400):
            if len(fit_segments) < 2:
                break
            idxs = np.random.choice(len(fit_segments), 2, replace=False)
            vp_norm, is_inf = vp_from_lines_homogeneous(line_homog_norm, idxs.tolist())
            if np.allclose(vp_norm, 0):
                continue
            # Convert VP to original coords for residuals
            vp_orig = denormalize_vp(vp_norm, T)
            inliers = []
            for j, lh in enumerate(line_homog_norm):
                if j in used:
                    continue
                seg = fit_segments[j]
                res = vp_direction_from_segment(seg.x1, seg.y1, seg.x2, seg.y2,
                                                 vp_orig[0], vp_orig[1], vp_orig[2])
                if res < RANSAC_THRESH_DEG:
                    inliers.append(j)
            if len(inliers) > len(best_inliers):
                best_inliers = inliers
                best_vp_norm = vp_norm
        if len(best_inliers) >= MIN_INLIERS_VP and best_vp_norm is not None:
            families_found += 1
            vp_refined_norm, is_inf = vp_from_lines_homogeneous(line_homog_norm, best_inliers)
            vp_refined = denormalize_vp(vp_refined_norm, T)
            dir_deg = vp_direction_deg(vp_refined, img_center)
            residuals = [vp_direction_from_segment(fit_segments[i].x1, fit_segments[i].y1,
                                                     fit_segments[i].x2, fit_segments[i].y2,
                                                     vp_refined[0], vp_refined[1], vp_refined[2])
                         for i in best_inliers]
            A = np.array([line_homog_norm[i] for i in best_inliers])
            _, s_vals, _ = np.linalg.svd(A)
            cond = s_vals[-1] / s_vals[0] if s_vals[0] > 0 else 0
            vps.append(VanishingPoint(
                x=float(vp_refined[0]), y=float(vp_refined[1]), w=float(vp_refined[2]),
                is_infinite=is_inf,
                direction_deg=dir_deg,
                inlier_indices=best_inliers,
                inlier_count=len(best_inliers),
                residual_median_deg=float(np.median(residuals)),
                residual_std_deg=float(np.std(residuals)),
                conditioning=float(cond)
            ))
            used.update(best_inliers)
    merged_vps = []
    for vp in vps:
        merged = False
        for mvp in merged_vps:
            if abs(vp.direction_deg - mvp.direction_deg) < VP_CLUSTER_ANGLE_DEG or \
               abs(vp.direction_deg - mvp.direction_deg - 180) < VP_CLUSTER_ANGLE_DEG or \
               abs(vp.direction_deg - mvp.direction_deg + 180) < VP_CLUSTER_ANGLE_DEG:
                all_idx = list(set(mvp.inlier_indices) | set(vp.inlier_indices))
                vp_refined_norm, is_inf = vp_from_lines_homogeneous(line_homog_norm, all_idx)
                vp_refined = denormalize_vp(vp_refined_norm, T)
                dir_deg = vp_direction_deg(vp_refined, img_center)
                residuals = [vp_direction_from_segment(fit_segments[i].x1, fit_segments[i].y1,
                                                         fit_segments[i].x2, fit_segments[i].y2,
                                                         vp_refined[0], vp_refined[1], vp_refined[2])
                             for i in all_idx]
                A = np.array([line_homog_norm[i] for i in all_idx])
                _, s_vals, _ = np.linalg.svd(A)
                cond = s_vals[-1] / s_vals[0] if s_vals[0] > 0 else 0
                mvp.inlier_indices = all_idx
                mvp.inlier_count = len(all_idx)
                mvp.direction_deg = dir_deg
                mvp.residual_median_deg = float(np.median(residuals))
                mvp.residual_std_deg = float(np.std(residuals))
                mvp.conditioning = float(cond)
                mvp.x, mvp.y, mvp.w = float(vp_refined[0]), float(vp_refined[1]), float(vp_refined[2])
                mvp.is_infinite = is_inf
                merged = True
                break
        if not merged:
            merged_vps.append(vp)
    vps = merged_vps
    vertical_candidates = [vp for vp in vps if abs(vp.direction_deg) < 10 or abs(vp.direction_deg - 180) < 10]
    vertical_vp = None
    if vertical_candidates:
        vertical_vp = max(vertical_candidates, key=lambda v: v.inlier_count)
    # Pitch/roll bounds REMOVED - cannot compute without known focal length
    # Vertical VP direction only gives line orientation, not absolute pitch/roll
    pitch_bounds = (None, None)
    roll_bounds = (None, None)

    # HFOV estimation: world-orthogonal VP families and intrinsics not independently established
    # Pixel-angle ~90 deg is NOT world orthogonality; smallest SVD/largest is fit error not identifiability
    hfov_estimate = {
        "identifiable": False,
        "reason": "World-orthogonal VP families and intrinsics not independently established; pixel-angle orthogonality insufficient"
    }

    # Heldout residuals: ALL heldout segments assigned to nearest train VP
    heldout_residuals = []
    for i in heldout_set:
        if i < len(raw_segments):
            seg = raw_segments[i]
            min_res = 90.0
            for vp in vps:
                res = vp_direction_from_segment(seg.x1, seg.y1, seg.x2, seg.y2,
                                                 vp.x, vp.y, vp.w)
                min_res = min(min_res, res)
            heldout_residuals.append(min_res)

    # No plausibility claims for current 54.5/11.2 - calibration unaccepted
    current_plausible = False
    notes = []
    notes.append("HFOV not identifiable from line data: world-orthogonal families and intrinsics not independently established")
    notes.append("Lens distortion: no EXIF focal/camera model tags in source; straight-line assumption not validated at full resolution")
    notes.append("Exclusions: green_foliage (HSV), curved_glazing_heuristic (high sat + grad var) - not verified static")
    notes.append("VP candidates labeled by cluster direction; not proven static architectural")
    overlay = img_det.copy()
    for i, s in enumerate(raw_segments):
        color = (0, 255, 0) if not s.excluded and not s.is_heldout else \
                (0, 0, 255) if s.is_heldout else \
                (128, 128, 128) if s.excluded else (255, 255, 0)
        cv2.line(overlay, (int(s.x1), int(s.y1)), (int(s.x2), int(s.y2)), color, 1)
    for vp in vps:
        if not vp.is_infinite:
            vx, vy = vp.x / vp.w, vp.y / vp.w
            cv2.circle(overlay, (int(vx), int(vy)), 5, (255, 0, 255), -1)
    overlay_path = OUTPUT_DIR / f"overlay-{photo_id}.jpg"
    cv2.imwrite(str(overlay_path), overlay)
    return PhotoDiagnostic(
        photo_id=photo_id,
        sha256=sha256,
        image_size=(orig_w, orig_h),
        raw_segments=raw_segments,
        kept_segments=[s for s in raw_segments if not s.excluded],
        heldout_indices=list(heldout_set),
        vps=vps,
        vertical_vp=vertical_vp,
        pitch_bounds_deg=pitch_bounds,
        roll_bounds_deg=roll_bounds,
        hfov_estimate=hfov_estimate,
        heldout_residuals_deg=heldout_residuals,
        current_hfov_pitch_plausible=current_plausible,
        notes=notes
    )

def run_synthetic_test():
    print("\n=== SYNTHETIC TEST ===")
    np.random.seed(42)

    W, H = 1600, 1200
    f_true = 1200.0
    K_true = np.array([[f_true, 0, W/2], [0, f_true, H/2], [0, 0, 1]])
    pitch_true = 10.0
    roll_true = 0.0

    R_pitch = np.array([[1, 0, 0], [0, math.cos(math.radians(pitch_true)), -math.sin(math.radians(pitch_true))], [0, math.sin(math.radians(pitch_true)), math.cos(math.radians(pitch_true))]])
    R_roll = np.array([[math.cos(math.radians(roll_true)), -math.sin(math.radians(roll_true)), 0], [math.sin(math.radians(roll_true)), math.cos(math.radians(roll_true)), 0], [0, 0, 1]])
    R = R_pitch @ R_roll

    axes_world = np.array([[1,0,0], [0,1,0], [0,0,1]]).T
    axes_cam = R @ axes_world

    vps_true = []
    for i in range(3):
        d = axes_cam[:, i]
        if abs(d[2]) > 1e-6:
            v = K_true @ d
            v = v / v[2]
            vps_true.append((v[0], v[1], 1.0))
        else:
            v = K_true @ d
            vps_true.append((v[0], v[1], 0.0))

    n_per_vp = 30
    segments = []
    for vp_idx, vp in enumerate(vps_true):
        vx, vy, vw = vp
        for _ in range(n_per_vp):
            mx = np.random.uniform(100, W-100)
            my = np.random.uniform(100, H-100)
            if vw > 1e-6:
                dx = vx - mx
                dy = vy - my
            else:
                dx, dy = vx, vy
            norm = math.hypot(dx, dy)
            if norm < 1e-6:
                continue
            dx, dy = dx/norm, dy/norm
            length = np.random.uniform(50, 200)
            x1, y1 = mx - dx*length/2, my - dy*length/2
            x2, y2 = mx + dx*length/2, my + dy*length/2
            segments.append((x1, y1, x2, y2, vp_idx))

    n_outliers = 20
    for _ in range(n_outliers):
        x1, y1 = np.random.uniform(0, W), np.random.uniform(0, H)
        x2, y2 = np.random.uniform(0, W), np.random.uniform(0, H)
        if math.hypot(x2-x1, y2-y1) > 30:
            segments.append((x1, y1, x2, y2, -1))

    np.random.shuffle(segments)

    noisy_segments = []
    for x1, y1, x2, y2, vp_idx in segments:
        if vp_idx >= 0:
            ang_noise = np.random.normal(0, math.radians(1.0))
            dx, dy = x2-x1, y2-y1
            ang = math.atan2(dy, dx) + ang_noise
            length = math.hypot(dx, dy)
            mx, my = (x1+x2)/2, (y1+y2)/2
            x1n, y1n = mx - math.cos(ang)*length/2, my - math.sin(ang)*length/2
            x2n, y2n = mx + math.cos(ang)*length/2, my + math.sin(ang)*length/2
            noisy_segments.append((x1n, y1n, x2n, y2n, vp_idx))
        else:
            noisy_segments.append((x1, y1, x2, y2, vp_idx))

    n_train = int(len(noisy_segments) * 0.75)
    train_segs = noisy_segments[:n_train]
    heldout_segs = noisy_segments[n_train:]

    line_homog = []
    for x1, y1, x2, y2, _ in train_segs:
        l = np.array([y1 - y2, x2 - x1, x1*y2 - x2*y1])
        norm = np.linalg.norm(l[:2])
        if norm > 0:
            l = l / norm
        line_homog.append(l)

    img_center = (W/2, H/2)
    line_homog_norm, T = normalize_lines(line_homog, img_center)

    vps_found = []
    used = set()
    max_families = 3
    families_found = 0
    for seed_idx in range(len(train_segs)):
        if families_found >= max_families:
            break
        if seed_idx in used:
            continue
        best_inliers = []
        best_vp_norm = None
        for _ in range(400):
            if len(train_segs) < 2:
                break
            idxs = np.random.choice(len(train_segs), 2, replace=False)
            vp_norm, is_inf = vp_from_lines_homogeneous(line_homog_norm, idxs.tolist())
            if np.allclose(vp_norm, 0):
                continue
            # Convert VP to original coords for residuals
            vp_orig = denormalize_vp(vp_norm, T)
            inliers = []
            for j, (x1, y1, x2, y2, _) in enumerate(train_segs):
                if j in used:
                    continue
                res = vp_direction_from_segment(x1, y1, x2, y2, vp_orig[0], vp_orig[1], vp_orig[2])
                if res < 2.0:
                    inliers.append(j)
            if len(inliers) > len(best_inliers):
                best_inliers = inliers
                best_vp_norm = vp_norm
        if len(best_inliers) >= 8 and best_vp_norm is not None:
            families_found += 1
            vp_refined_norm, is_inf = vp_from_lines_homogeneous(line_homog_norm, best_inliers)
            vp_refined = denormalize_vp(vp_refined_norm, T)
            vps_found.append((vp_refined, best_inliers))
            used.update(best_inliers)

    print(f"True VPs: {[(v[0], v[1], v[2]) for v in vps_true]}")
    print(f"Found {len(vps_found)} VPs")
    for vp, inliers in vps_found:
        vx, vy, vw = vp
        print(f"  VP: ({vx:.1f}, {vy:.1f}, {vw:.3f}), inliers: {len(inliers)}")

    matched = []
    # Match recovered VPs to true VPs using inhomogeneous coordinates (x/w, y/w) with w sign check
    true_vps_inhomog = []
    for v in vps_true:
        w = v[2]
        if abs(w) < 1e-8:
            true_vps_inhomog.append(None)  # infinite
        else:
            true_vps_inhomog.append(np.array([v[0]/w, v[1]/w]))
    found_vps_inhomog = []
    for vp, _ in vps_found:
        w = vp[2]
        if abs(w) < 1e-8:
            found_vps_inhomog.append(None)
        else:
            found_vps_inhomog.append(np.array([vp[0]/w, vp[1]/w]))
    matched = []
    for i, tv in enumerate(true_vps_inhomog):
        if tv is None:
            continue
        best_idx = -1
        best_err = 999
        for j, fv in enumerate(found_vps_inhomog):
            if fv is None:
                continue
            err = np.linalg.norm(tv - fv)
            if err < best_err:
                best_err = err
                best_idx = j
        if best_idx >= 0:
            matched.append((vps_true[i], vps_found[best_idx][0], float(best_err)))
            print(f"  True VP {i} -> Found VP {best_idx}: error {best_err:.1f} px")
        else:
            print(f"  True VP {i}: NO MATCH")

    heldout_residuals = []
    for x1, y1, x2, y2, vp_idx in heldout_segs:
        min_res = 90.0
        for vp, _ in vps_found:
            res = vp_direction_from_segment(x1, y1, x2, y2, vp[0], vp[1], vp[2])
            min_res = min(min_res, res)
        heldout_residuals.append(min_res)

    print(f"Heldout median residual: {np.median(heldout_residuals):.2f} deg")
    print(f"Heldout max residual: {np.max(heldout_residuals):.2f} deg")

    finite_vps = [vp for vp, _ in vps_found if vp[2] > 1e-6]
    hfov_rec = None
    if len(finite_vps) >= 2:
        best_pair = None
        best_ortho = 0
        for i, vp1 in enumerate(finite_vps):
            for vp2 in finite_vps[i+1:]:
                cx, cy = W/2, H/2
                ang1 = math.degrees(math.atan2(vp1[1]/vp1[2] - cy, vp1[0]/vp1[2] - cx)) % 180
                ang2 = math.degrees(math.atan2(vp2[1]/vp2[2] - cy, vp2[0]/vp2[2] - cx)) % 180
                diff = abs(ang1 - ang2) % 180
                ortho = 1 - abs(diff - 90) / 90
                if ortho > best_ortho:
                    best_ortho = ortho
                    best_pair = (vp1, vp2)
        if best_pair and best_ortho > 0.7:
            v1, v2 = best_pair
            v1x, v1y, v1w = v1
            v2x, v2y, v2w = v2
            cx, cy = W/2, H/2
            num = -((v1x - cx*v1w)*(v2x - cx*v2w) + (v1y - cy*v1w)*(v2y - cy*v2w))
            den = v1w * v2w
            if den != 0:
                f_sq = num / den
                if f_sq > 0:
                    f_rec = math.sqrt(f_sq)
                    hfov_rec = 2 * math.degrees(math.atan(W / (2 * f_rec)))
                    print(f"Recovered f: {f_rec:.1f} (true: {f_true}), HFOV: {hfov_rec:.1f} deg (true: {2*math.degrees(math.atan(W/(2*f_true))):.1f})")

    return {
        "true_f": f_true,
        "true_hfov": 2 * math.degrees(math.atan(W / (2 * f_true))),
        "true_pitch": pitch_true,
        "true_roll": roll_true,
        "n_segments_total": len(noisy_segments),
        "n_train": len(train_segs),
        "n_heldout": len(heldout_segs),
        "n_vps_found": len(vps_found),
        "heldout_median_residual_deg": float(np.median(heldout_residuals)),
        "heldout_max_residual_deg": float(np.max(heldout_residuals)),
        "vp_matches": [(float(vt[0]), float(vt[1]), float(vt[2]), float(vf[0]), float(vf[1]), float(vf[2]), float(err)) for vt, vf, err in matched],
        "recovered_hfov_deg": hfov_rec
    }

def main():
    print("DYNAcity Reference Geometry: Vanishing Line Diagnostic")
    print(f"Processing {len(PHOTO_IDS)} photos...")

    all_diagnostics = []
    for pid in PHOTO_IDS:
        print(f"  {pid}...", end=" ", flush=True)
        diag = process_photo(pid)
        all_diagnostics.append(diag)
        print(f"OK: {len(diag.raw_segments)} raw, {len(diag.kept_segments)} kept, {len(diag.heldout_indices)} heldout, {len(diag.vps)} VPs")

    synthetic_metrics = run_synthetic_test()

    output = {
        "schema": "dynacity-vanishing-line-diagnostic/1",
        "timestamp": "2026-11-04",
        "photos": [],
        "synthetic_test": synthetic_metrics,
        "summary": {
            "current_hfov_deg": 54.5,
            "current_pitch_deg": 11.2,
            "overall_plausible": False,
            "hfov_identifiable_count": 0,
            "total_raw_segments": sum(len(d.raw_segments) for d in all_diagnostics),
            "total_kept_segments": sum(len(d.kept_segments) for d in all_diagnostics),
            "total_heldout": sum(len(d.heldout_indices) for d in all_diagnostics),
            "total_vps": sum(len(d.vps) for d in all_diagnostics)
        }
    }

    for d in all_diagnostics:
        def vp_to_dict(vp):
            return {
                "x": vp.x, "y": vp.y, "w": vp.w, "is_infinite": vp.is_infinite,
                "direction_deg": vp.direction_deg,
                "inlier_count": vp.inlier_count,
                "residual_median_deg": vp.residual_median_deg,
                "residual_std_deg": vp.residual_std_deg,
                "conditioning": vp.conditioning
            }

        photo_json = {
            "photo_id": d.photo_id,
            "sha256": d.sha256,
            "image_size": d.image_size,
            "raw_segments": [
                {
                    "x1": s.x1, "y1": s.y1, "x2": s.x2, "y2": s.y2,
                    "length": s.length, "angle_deg": s.angle_deg,
                    "is_heldout": s.is_heldout, "excluded": s.excluded,
                    "exclusion_reason": s.exclusion_reason
                } for s in d.raw_segments
            ],
            "kept_segments_count": len(d.kept_segments),
            "heldout_indices": d.heldout_indices,
            "vps": [vp_to_dict(vp) for vp in d.vps],
            "vertical_vp": vp_to_dict(d.vertical_vp) if d.vertical_vp else None,
            "pitch_bounds_deg": d.pitch_bounds_deg,
            "roll_bounds_deg": d.roll_bounds_deg,
            "hfov_estimate": d.hfov_estimate,
            "heldout_residuals_deg": d.heldout_residuals_deg,
            "heldout_residual_median_deg": float(np.median(d.heldout_residuals_deg)) if d.heldout_residuals_deg else None,
            "heldout_residual_max_deg": float(np.max(d.heldout_residuals_deg)) if d.heldout_residuals_deg else None,
            "current_hfov_pitch_plausible": d.current_hfov_pitch_plausible,
            "notes": d.notes,
            "overlay_path": str(OUTPUT_DIR / f"overlay-{d.photo_id}.jpg")
        }
        output["photos"].append(photo_json)

    json_path = OUTPUT_DIR / "vanishing-line-diagnostic.json"
    atomic_write_json(json_path, output)

    print(f"\nDone. Output: {json_path}")
    print(f"Overlays: {[str(OUTPUT_DIR / f'overlay-{pid}.jpg') for pid in PHOTO_IDS]}")

    print("\n=== COMPACT HANDOFF ===")
    print(f"Photos processed: {len(PHOTO_IDS)}")
    print(f"HFOV identifiable: {output['summary']['hfov_identifiable_count']}/{len(PHOTO_IDS)}")
    print(f"Current 54.5/11.2 plausible: {output['summary']['overall_plausible']}")
    for d in all_diagnostics:
        hfov = d.hfov_estimate
        if hfov.get("identifiable"):
            print(f"  {d.photo_id}: HFOV {hfov['nominal_deg']:.1f} deg [{hfov['min_deg']:.1f}, {hfov['max_deg']:.1f}], pitch bounds {d.pitch_bounds_deg}, VPs={len(d.vps)}")
        else:
            print(f"  {d.photo_id}: HFOV NOT IDENTIFIABLE ({hfov.get('reason')}), pitch bounds {d.pitch_bounds_deg}, VPs={len(d.vps)}")
    print(f"Output directory: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
