"""Photo-backed streetscape helpers: an explicit pinhole camera and a validator for candidate tree manifests.

The camera is the *uncalibrated estimate* from the reconstruction registration (shared horizontal FOV and pitch,
per-view yaw offset, recorded camera position). Everything derived from it is an estimate with a stated
uncertainty, never a measurement. Local frame: east, north, up metres from the Beirut 001 city origin
(``corridor-manifest.json`` ``origin_utm_m``); headings are compass degrees, clockwise from north.

The validator keeps a candidate-placement manifest honest: every placement must cite photo anchors, carry
finite ranges, require a native ground trace, name licensed existing assets (or an explicit blocker), and the
manifest must stay bounded (no street-wide auto placement).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SCHEMA = 'dynacity-streetscape-candidates/1'
MAX_PILOT_PLACEMENTS = 4          # first native pilot: at most four, and only placements with a feasible asset fit
TRUNK_CLEAR_MIN_M = 1.0           # estimated minimum bare trunk below the crown (people / kerb furniture); head clearance stays pending native
SCALE_TOL = 2e-6
FAMILIES = ('broadleaf', 'palm')
CLEARANCE = ('ok', 'overlap', 'pending_native')
ASSET_CAPTURE_TYPES = ('procedural_mesh_photographic_textures', 'procedural_mesh', 'photogrammetry')


@dataclass(frozen=True)
class CameraPose:
    photo_id: str
    east_m: float
    north_m: float
    ground_up_m: float          # terrain height under the camera (native ground trace)
    height_above_ground_m: float
    heading_deg: float          # recorded grid heading
    yaw_offset_deg: float       # fitted per-view offset, added to the heading
    pitch_deg: float            # positive looks up
    hfov_deg: float
    image_size_px: tuple = (1600, 1200)

    @property
    def up_m(self):
        return self.ground_up_m + self.height_above_ground_m

    @property
    def origin(self):
        return np.array([self.east_m, self.north_m, self.up_m])

    @property
    def focal_px(self):
        return (self.image_size_px[0] / 2) / math.tan(math.radians(self.hfov_deg) / 2)

    def basis(self):
        yaw, pitch = math.radians(self.heading_deg + self.yaw_offset_deg), math.radians(self.pitch_deg)
        fwd = np.array([math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch)])
        right = np.array([math.cos(yaw), -math.sin(yaw), 0.0])
        return fwd, right, np.cross(right, fwd)

    def ray(self, u, v):
        fwd, right, up = self.basis()
        w, h = self.image_size_px
        f = self.focal_px
        d = fwd + ((u - w / 2) / f) * right - ((v - h / 2) / f) * up
        return self.origin, d / np.linalg.norm(d)

    def project(self, points):
        """(N,3) local ENU points -> (N,2) pixels, NaN behind the camera."""
        p = np.atleast_2d(np.asarray(points, float)) - self.origin
        fwd, right, up = self.basis()
        depth = p @ fwd
        w, h = self.image_size_px
        f = self.focal_px
        with np.errstate(divide='ignore', invalid='ignore'):
            out = np.stack([w / 2 + f * (p @ right) / depth, h / 2 - f * (p @ up) / depth], axis=1)
        out[depth <= 0] = np.nan
        return out

    def ground_point(self, u, v, plane_up_m=None):
        """Intersect the pixel ray with the horizontal plane through the camera's ground (or ``plane_up_m``).
        Returns (point ENU, horizontal range m) or (None, None) when the ray never meets the plane."""
        o, d = self.ray(u, v)
        plane = self.ground_up_m if plane_up_m is None else plane_up_m
        if d[2] >= -1e-6:
            return None, None
        t = (plane - o[2]) / d[2]
        p = o + d * t
        return p, float(math.hypot(p[0] - o[0], p[1] - o[1]))

    def height_above(self, ground_xy, u, v, plane_up_m=None):
        """Height above the plane of the point seen at pixel (u, v) over the horizontal position ``ground_xy``."""
        o, d = self.ray(u, v)
        dist = math.hypot(ground_xy[0] - o[0], ground_xy[1] - o[1])
        horiz = math.hypot(d[0], d[1])
        if horiz < 1e-9:
            return None
        z = o[2] + dist * d[2] / horiz
        return float(z - (self.ground_up_m if plane_up_m is None else plane_up_m))

    def metres_per_px(self, range_m):
        return range_m / self.focal_px

    def crown_ellipse_px(self, base_xy, ground_up_m, height_m, radius_m, crown_depth_frac=0.7):
        """Pixel bbox (x0, y0, x1, y1) of a crown spheroid (radius, crown_depth_frac*height tall) on a trunk at
        ``base_xy``. None when behind the camera."""
        z_top = ground_up_m + height_m
        z_bot = ground_up_m + height_m * (1 - crown_depth_frac)
        pts = []
        for z in np.linspace(z_bot, z_top, 7):
            r = radius_m * math.sqrt(max(0.0, 1 - ((z - (z_top + z_bot) / 2) / ((z_top - z_bot) / 2)) ** 2))
            for a in np.linspace(0, 2 * math.pi, 16, endpoint=False):
                pts.append([base_xy[0] + r * math.cos(a), base_xy[1] + r * math.sin(a), z])
        px = self.project(np.array(pts))
        px = px[~np.isnan(px).any(axis=1)]
        if len(px) == 0:
            return None
        return float(px[:, 0].min()), float(px[:, 1].min()), float(px[:, 0].max()), float(px[:, 1].max())


# ---------------------------------------------------------------------------------------- manifest validation
def _finite(*xs):
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def _range_ok(r):
    return isinstance(r, dict) and _finite(r.get('est'), r.get('lo'), r.get('hi')) and r['lo'] <= r['est'] <= r['hi']


@dataclass(frozen=True)
class AssetDims:
    height_m: float
    radius_m: float                 # conservative XY crown radius from the trunk pivot (origin); either the true max-hypot(x,y) measured
                                    # from source vertices or, when that is unavailable, a conservative origin-aware bound from the AABB
    trunk_height_m: float | None = None


def radius_from_vertices(vertices):
    """Maximum horizontal distance from the mesh pivot; input is pivot-relative ENU metres."""
    p = np.asarray(vertices, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not len(p) or not np.isfinite(p).all():
        raise ValueError('finite nonempty Nx3 pivot-relative vertices required')
    return float(np.linalg.norm(p[:, :2], axis=1).max())


def asset_dims(rec):
    """Require a measured pivot radius or explicit pivot-relative AABB endpoints.

    Extents alone do not establish the pivot or a radial bound. An AABB bound
    uses its farthest XY corner, including diagonal and off-centre geometry.
    """
    d = rec['dimensions_m']
    h = float(d[2])
    if rec.get('vertex_radius_m') is not None:
        r = float(rec['vertex_radius_m'])
    else:
        lo, hi = np.asarray(rec['bounds_min_m'], float), np.asarray(rec['bounds_max_m'], float)
        if lo.shape != (3,) or hi.shape != (3,) or not np.isfinite([lo, hi]).all() or np.any(lo > hi):
            raise ValueError('finite ordered pivot-relative AABB endpoints required')
        r = float(np.linalg.norm(np.maximum(np.abs(lo[:2]), np.abs(hi[:2]))))
    trunk = float(rec['trunk_height_m']) if rec.get('trunk_height_m') else None
    if not _finite(h, r) or h <= 0 or r <= 0 or (trunk is not None and (not math.isfinite(trunk) or trunk <= 0)):
        raise ValueError('positive finite asset dimensions required')
    return AssetDims(h, r, trunk)


def feasible_scale(crown, dims, trunk_min_m=TRUNK_CLEAR_MIN_M):
    """Uniform scale interval that keeps height inside the crown height range AND radius under the crown radius
    maximum AND the bare trunk above ``trunk_min_m``. Bounds are rounded outward-safe to 1e-6 (lower up, upper down).
    ``feasible`` is False when the interval is empty."""
    h_lo, h_hi = crown['height_m']['lo'] / dims.height_m, crown['height_m']['hi'] / dims.height_m
    r_hi = crown['radius_m']['hi'] / dims.radius_m
    t_lo = trunk_min_m / dims.trunk_height_m if dims.trunk_height_m else 0.0
    lo = math.ceil(max(h_lo, t_lo) * 1e6 - 1e-9) / 1e6
    hi = math.floor(min(h_hi, r_hi) * 1e6 + 1e-9) / 1e6
    limiting = {'lo': 'height_lo' if h_lo >= t_lo else 'trunk_clearance', 'hi': 'radius_max' if r_hi <= h_hi else 'height_hi'}
    return {'lo': lo, 'hi': hi, 'feasible': lo <= hi, 'height_scale': [h_lo, h_hi], 'radius_scale_max': r_hi, 'trunk_scale_min': t_lo, 'limiting': limiting}


def pick_scale(interval, target_height_m, dims):
    """Scale nearest the target height inside a feasible interval (4 decimals when that stays inside, else 6)."""
    lo, hi = interval['lo'], interval['hi']
    s = min(max(target_height_m / dims.height_m, lo), hi)
    if s >= hi:
        cand = math.floor(s * 1e4) / 1e4
    elif s <= lo:
        cand = math.ceil(s * 1e4) / 1e4
    else:
        cand = round(s, 4)
    return cand if lo <= cand <= hi else round(s, 6)


def asset_fit(crown, rec, trunk_min_m=TRUNK_CLEAR_MIN_M):
    """Per-candidate scale block written into the manifest: interval, status and (when feasible) the proposed scale."""
    dims = asset_dims(rec)
    iv = feasible_scale(crown, dims, trunk_min_m)
    out = {'asset_height_m': dims.height_m, 'asset_radius_m': dims.radius_m, 'asset_trunk_height_m': dims.trunk_height_m, 'trunk_clear_min_m': trunk_min_m,
           'feasible_interval': [iv['lo'], iv['hi']], 'limiting': iv['limiting'], 'status': 'feasible' if iv['feasible'] else 'infeasible'}
    if not iv['feasible']:
        out['reason'] = f"no uniform scale: height needs >= {iv['lo']:.6f} but the crown radius maximum allows <= {iv['hi']:.6f}"
        return out
    s = pick_scale(iv, crown['height_m']['est'], dims)
    out.update(proposed_scale=s, resulting_height_m=round(s * dims.height_m, 4), resulting_radius_m=round(s * dims.radius_m, 4),
               resulting_trunk_height_m=round(s * dims.trunk_height_m, 4) if dims.trunk_height_m else None,
               height_vs_estimate_m=round(s * dims.height_m - crown['height_m']['est'], 4),
               note=('height_vs_estimate_m < 0: the radius cap forces the tree shorter than the photo estimate (still inside the height range)'
                     if s * dims.height_m < crown['height_m']['est'] - 0.005 else ''))
    return out


def native_recheck(crown, proposed_scale, native_dims_m, trunk_height_m=None, trunk_min_m=TRUNK_CLEAR_MIN_M, vertex_radius_m=None, bounds_min_m=None, bounds_max_m=None):
    """Native owner: pass the asset's CURRENT measured [x, y, z] extents (metres).

    ``vertex_radius_m`` is the true max-hypot(x,y) from the pivot, measured from actual vertices.
    When omitted, explicit pivot-relative bounds_min_m/bounds_max_m are required (may reject a placement
    that the true geometry would allow).  Returns (ok, interval, reasons).
    """
    rec = {'dimensions_m': native_dims_m, 'trunk_height_m': trunk_height_m}
    if vertex_radius_m is not None:
        rec['vertex_radius_m'] = vertex_radius_m
    if bounds_min_m is not None and bounds_max_m is not None:
        rec.update(bounds_min_m=bounds_min_m, bounds_max_m=bounds_max_m)
    try:
        iv = feasible_scale(crown, asset_dims(rec), trunk_min_m)
    except (KeyError, ValueError, TypeError) as exc:
        return False, {}, [f'measured pivot-relative radius/bounds required: {exc}']
    reasons = []
    if not iv['feasible']:
        reasons.append('empty feasible interval for the measured asset')
    elif not (iv['lo'] - SCALE_TOL <= proposed_scale <= iv['hi'] + SCALE_TOL):
        reasons.append(f"proposed scale {proposed_scale} outside measured-asset interval [{iv['lo']}, {iv['hi']}]")
    return not reasons, iv, reasons


def _asset_errors(w, crown, a):
    errs = []
    k = a.get('key')
    if not a.get('native_path') or not a.get('license') or not a.get('provenance') or a.get('loaded_in_native') is not False:
        errs.append(f'{w}: asset {k} needs native_path, license, provenance and loaded_in_native=false (not verified offline)')
    if a.get('asset_capture_type') not in ASSET_CAPTURE_TYPES or a.get('simulation_assignment') != 'estimated' or 'stand-in' not in str(a.get('geographic_identity', '')):
        errs.append(f'{w}: asset {k} must state asset_capture_type, a stand-in geographic_identity, and simulation_assignment=estimated')
    sc = a.get('scale', {})
    try:
        dims = asset_dims(a)
        iv = feasible_scale(crown, dims, sc.get('trunk_clear_min_m', TRUNK_CLEAR_MIN_M))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return errs + [f'{w}: asset {k} lacks usable dimensions_m']
    stored = sc.get('feasible_interval')
    if not (isinstance(stored, list) and len(stored) == 2 and _finite(*stored) and abs(stored[0] - iv['lo']) <= SCALE_TOL and abs(stored[1] - iv['hi']) <= SCALE_TOL):
        errs.append(f'{w}: asset {k} stored feasible_interval {stored} != derived [{iv["lo"]}, {iv["hi"]}]')
    if sc.get('status') != ('feasible' if iv['feasible'] else 'infeasible'):
        errs.append(f'{w}: asset {k} status {sc.get("status")} contradicts the derived interval')
    if iv['feasible'] and sc.get('status') == 'feasible':
        s = sc.get('proposed_scale')
        if not _finite(s) or not (iv['lo'] - SCALE_TOL <= s <= iv['hi'] + SCALE_TOL):
            errs.append(f'{w}: asset {k} proposed_scale {s} outside the feasible interval [{iv["lo"]}, {iv["hi"]}]')
            return errs
        h, r = s * dims.height_m, s * dims.radius_m
        ch, cr = crown['height_m'], crown['radius_m']
        if h < ch['lo'] - 1e-6 or h > ch['hi'] + 1e-6:
            errs.append(f'{w}: asset {k} scaled height {h:.4f} m outside crown height [{ch["lo"]}, {ch["hi"]}]')
        if r > cr['hi'] + 1e-6:
            errs.append(f'{w}: asset {k} scaled radius {r:.4f} m exceeds crown radius maximum {cr["hi"]}')
        if dims.trunk_height_m and s * dims.trunk_height_m < sc.get('trunk_clear_min_m', TRUNK_CLEAR_MIN_M) - 1e-6:
            errs.append(f'{w}: asset {k} bare trunk {s * dims.trunk_height_m:.3f} m under the clearance minimum')
        for key, val in (('resulting_height_m', h), ('resulting_radius_m', r)):
            if not _finite(sc.get(key)) or abs(sc[key] - val) > 1e-3:
                errs.append(f'{w}: asset {k} {key} {sc.get(key)} != derived {val:.4f}')
        if not _finite(sc.get('height_vs_estimate_m')) or abs(sc['height_vs_estimate_m'] - (h - ch['est'])) > 1e-2:
            errs.append(f'{w}: asset {k} height_vs_estimate_m not recorded correctly')
    return errs


def _position_errors(w, p, photos):
    errors = []
    if p.get('family') not in FAMILIES:
        errors.append(f'{w}: family must be one of {FAMILIES} (palm = family only, never a species)')
    obs = p.get('observed_in', [])
    if not obs or any(o.get('photo_id') not in photos or not _finite(o.get('u'), o.get('v')) for o in obs):
        errors.append(f'{w}: needs finite anchors in known photos')
    pos = p.get('position', {})
    ok = (len(pos.get('estimate_en_m', [])) == 2 and len(pos.get('proposed_en_m', [])) == 2 and _finite(*pos.get('estimate_en_m', [math.nan]), *pos.get('proposed_en_m', [math.nan]))
          and _finite(pos.get('sigma_m')) and pos['sigma_m'] > 0 and len(pos.get('range_e_m', [])) == 2 and len(pos.get('range_n_m', [])) == 2)
    if ok:
        for key in ('estimate_en_m', 'proposed_en_m'):
            ok = ok and pos['range_e_m'][0] <= pos[key][0] <= pos['range_e_m'][1] and pos['range_n_m'][0] <= pos[key][1] <= pos['range_n_m'][1]
    if not ok:
        errors.append(f'{w}: position needs finite estimate and proposed positions inside finite ranges and sigma_m > 0')
    if pos.get('basis') == 'measured':
        errors.append(f'{w}: positions are estimates, never measured')
    g = p.get('ground', {})
    if g.get('native_ground_trace_required') is not True or not _finite(g.get('estimated_up_m')):
        errors.append(f'{w}: native ground trace must be required; estimated_up_m finite')
    c = p.get('crown', {})
    if not (_range_ok(c.get('height_m')) and _range_ok(c.get('radius_m')) and _finite(c.get('max_photo_supported_radius_m'))
            and c['radius_m']['hi'] <= c['max_photo_supported_radius_m'] + 1e-9):
        errors.append(f'{w}: crown height/radius ranges need lo <= est <= hi, radius capped by the photo-supported maximum')
    cl = p.get('clearance', {})
    if cl.get('building') not in CLEARANCE or cl.get('carriageway') not in CLEARANCE or cl.get('pedestrian_collision') != 'pending_native':
        errors.append(f'{w}: clearance must state building/carriageway and leave collision pending_native')
    if cl.get('building') == 'overlap' or cl.get('carriageway') == 'overlap':
        errors.append(f'{w}: trunk estimate overlaps a building or carriageway')
    if p.get('osm_corroboration', {}).get('identity_claim') not in (None, 'none'):
        errors.append(f'{w}: proximity to OSM objects is not identity')
    return errors


def validate_manifest(doc, *, max_placements=MAX_PILOT_PLACEMENTS):
    """Return a list of problems (empty when the manifest is internally consistent, bounded, and every native-ready
    placement has an asset whose DERIVED scale interval is non-empty and contains its proposed scale)."""
    errors = []
    if doc.get('schema') != SCHEMA:
        return [f'schema must be {SCHEMA}']
    photos = {p['photo_id']: p for p in doc.get('source_photos', [])}
    for pid, p in photos.items():
        if p.get('license') != 'CC BY-SA 4.0' or 'KartaView' not in str(p.get('attribution', '')) or not p.get('source_url') or not p.get('capture_local'):
            errors.append(f'photo {pid}: license/attribution/url/date must be preserved')
    if doc.get('camera_status') != 'uncalibrated_estimate':
        errors.append('camera_status must stay uncalibrated_estimate until a calibrated registration exists')
    placements = doc.get('placements', [])
    if len(placements) > max_placements:
        errors.append(f'{len(placements)} placements exceed the bounded pilot ({max_placements})')
    if doc.get('scope', {}).get('whole_street') is True:
        errors.append('whole-street placement is not allowed from photo evidence')
    ids = set()
    for p in placements:
        w = f'placement {p.get("id")}'
        if p.get('id') in ids:
            errors.append(f'{w}: duplicate id')
        ids.add(p.get('id'))
        errors += _position_errors(w, p, photos)
        if p.get('status') != 'candidate_pilot' or p.get('native_ready') is not True:
            errors.append(f'{w}: status must be candidate_pilot with native_ready true (infeasible placements belong in held_observations)')
        c = p.get('crown', {})
        a = p.get('asset_candidates', [])
        if not a and not p.get('asset_blocker'):
            errors.append(f'{w}: needs asset candidates or an explicit blocker')
        if a:
            if _range_ok(c.get('height_m')) and _range_ok(c.get('radius_m')):
                for x in a:
                    errors += _asset_errors(w, c, x)
                chosen = next((x for x in a if x.get('key') == p.get('chosen_asset')), None)
                if chosen is None or chosen.get('scale', {}).get('status') != 'feasible':
                    errors.append(f'{w}: chosen_asset must name a candidate whose scale status is feasible')
            else:
                errors.append(f'{w}: cannot verify asset fit without valid crown ranges')
    for p in doc.get('held_observations', []):
        w = f'held observation {p.get("id")}'
        if p.get('id') in ids:
            errors.append(f'{w}: duplicate id')
        ids.add(p.get('id'))
        errors += _position_errors(w, p, photos)
        if p.get('status') != 'hold_not_native_ready' or p.get('native_ready') is not False or not p.get('reason'):
            errors.append(f'{w}: must be hold_not_native_ready with a reason')
        if p.get('chosen_asset'):
            errors.append(f'{w}: a held observation must not name a chosen asset')
        c = p.get('crown', {})
        if not (_range_ok(c.get('height_m')) and _range_ok(c.get('radius_m'))):
            continue
        for x in p.get('asset_candidates', []):
            try:
                if feasible_scale(c, asset_dims(x), x.get('scale', {}).get('trunk_clear_min_m', TRUNK_CLEAR_MIN_M))['feasible']:
                    errors.append(f'{w}: asset {x.get("key")} is actually feasible; the hold is wrong')
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                errors.append(f'{w}: asset {x.get("key")} lacks usable dimensions_m')
    if doc.get('native_ready') != [p.get('id') for p in placements]:
        errors.append(f'native_ready {doc.get("native_ready")} must list exactly the feasible placements {[p.get("id") for p in placements]}')
    if not doc.get('with_without_vegetation_plan'):
        errors.append('with/without vegetation comparison plan missing')
    if not doc.get('uncertainty'):
        errors.append('uncertainty statements missing')
    return errors
