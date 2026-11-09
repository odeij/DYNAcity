"""Parametric 3D facade components for the five reference families (offline; no photos, no billboards).

``mesh_for_profile(family_key, width_m, height_m, ...)`` returns a deterministic, cached ``FacadeMesh``
built from boxes and convex prisms: real recesses, openings, piers, slabs, parapets and glazing. It
reads no private data. Family defaults are estimates copied from ``reference-profiles.json``
(see ``reference_facade_profiles``) and each carries bounds; nothing is surveyed.

Local frame (right-handed, metres)
    X  along the frontage, 0..width_m, increasing to the right for a viewer facing the facade
    Y  depth: the facade plane is y = 0 and the building extends to y = depth_m; things that project
       toward the street (cantilever boxes, pergola eaves, scaffold) have y < 0, so the outward
       normal of the facade is -Y
    Z  up, 0 = ground level, the roof/parapet top is exactly height_m (all geometry is clipped to it)
glTF export maps (x, y, z) -> (x, z, -y), so the facade faces +Z in glTF, as is conventional.

Unseen faces: the rear is a plain generic wall (part ``rear_generic``), never a copy of the street
relief. Side faces are explicit: ``plain`` (closing wall), ``bands`` (floor bands only, an estimate) or
``none``.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

FAMILIES = ('modern_stone_recessed_balcony', 'cream_yellow_older_balcony', 'older_brown_midrise',
            'construction_tower_frame', 'lgb_cream_redbrown_bands')
SIDE_MODES = ('plain', 'bands', 'none')
LICENSE_NOTE = 'Derived from CC BY-SA 4.0 street photographs, (c) Grab and KartaView Contributors (2018-07-21).'
LGB_PROVEN_ID = 'beirut001:d31eb718-f947-49d5-b82d-14838045a9c7'
_EPS = 1e-3

# photos that evidence each family (from reference-profiles.json observations)
FAMILY_PHOTOS = {
    'modern_stone_recessed_balcony': ('1288770821', '1288770597', '1288770701'),
    'cream_yellow_older_balcony': ('1288770821', '1288770597', '1288770701'),
    'older_brown_midrise': ('1288770821', '1288770597', '1288770701'),
    'construction_tower_frame': ('1288770821', '1288770597', '1288771293', '1288770701'),
    'lgb_cream_redbrown_bands': ('1288771293', '1288770821', '1288770597', '1288770701'),
}


@dataclass(frozen=True)
class Range:
    est: float
    lo: float
    hi: float
    note: str = ''


R = Range
_COMMON = {
    'floor_height_m': R(3.1, 2.8, 3.5, 'repeated slab pitch vs typical storey'),
    'bay_width_m': R(4.2, 3.4, 5.2, 'window + pier spacing'),
    'ground_floor_m': R(4.2, 3.0, 5.0, 'storefront/podium base, estimated; not surveyed'),
    'depth_m': R(14.0, 8.0, 24.0, 'plan depth is UNSEEN; any value is an assumption'),
    'wall_t_m': R(0.3, 0.2, 0.45), 'parapet_h_m': R(0.9, 0.5, 1.3),
}
DEFAULTS = {
    'modern_stone_recessed_balcony': {**_COMMON, 'gallery_depth_m': R(1.1, 0.8, 1.4, 'recessed gallery behind the pier line'),
        'pier_w_m': R(0.5, 0.35, 0.8), 'band_h_m': R(0.55, 0.35, 0.8), 'stagger_proj_m': R(0.5, 0.0, 0.9, 'projecting dark fascia box on alternate bays'),
        'window_h_m': R(1.9, 1.5, 2.3)},
    'cream_yellow_older_balcony': {**_COMMON, 'bay_width_m': R(4.5, 3.5, 6.0), 'floor_height_m': R(3.0, 2.8, 3.4), 'ground_floor_m': R(3.2, 3.0, 5.0),
        'loggia_depth_m': R(2.0, 1.4, 2.8), 'pier_w_m': R(0.4, 0.3, 0.6), 'slab_t_m': R(0.3, 0.2, 0.45), 'rail_h_m': R(1.0, 0.9, 1.1),
        'shutter_fraction': R(0.3, 0.0, 0.6, 'share of closed bays with a roll shutter; placement is a seeded estimate')},
    'older_brown_midrise': {**_COMMON, 'bay_width_m': R(3.5, 2.8, 4.5), 'floor_height_m': R(3.0, 2.8, 3.4), 'ground_floor_m': R(3.6, 3.0, 5.0),
        'parapet_h_m': R(0.5, 0.3, 0.9), 'balcony_depth_m': R(1.0, 0.7, 1.5), 'band_h_m': R(0.28, 0.2, 0.4), 'eave_depth_m': R(1.4, 0.8, 2.0)},
    'construction_tower_frame': {**_COMMON, 'bay_width_m': R(4.5, 3.5, 6.0), 'ground_floor_m': R(3.5, 3.0, 5.0), 'parapet_h_m': R(1.0, 0.0, 1.3),
        'column_w_m': R(0.45, 0.3, 0.6), 'slab_t_m': R(0.25, 0.2, 0.35), 'open_floors': R(6, 0, 8, 'top floors left as bare frame'),
        'net_offset_m': R(0.8, 0.4, 1.4)},
    'lgb_cream_redbrown_bands': {**_COMMON, 'bay_width_m': R(4.2, 3.4, 5.2), 'ground_floor_m': R(4.2, 3.0, 5.0), 'parapet_h_m': R(1.2, 0.9, 1.6),
        'frame_depth_m': R(1.3, 0.8, 1.9), 'frame_pitch_floors': R(2, 2, 2), 'frame_height_floors': R(1.5, 1.0, 2.0),
        'corner_radius_m': R(3.0, 2.0, 5.0), 'frame_zone_frac': R(0.55, 0.4, 0.7, 'share of frontage (east of the curved corner) with balcony frames'),
        'frame_t_m': R(0.3, 0.2, 0.4),
        'glass_top_drop_m': R(0.0, 0.0, 9.3, 'ESTIMATED diagnostic: how far the rounded glass, mullions, floored facade and C frames end below the cream roof/parapet top. The photo (1288771293) shows the glazed volume ending lower than the cream upper mass; the number is not measured. 0 = single-tier envelope (unchanged). Snapped to a floor line'),
        'upper_setback_x_m': R(0.0, 0.0, 14.0, 'ESTIMATED diagnostic: distance from the rounded-corner end of the frontage to where the taller cream upper mass begins (0 = it spans the whole width). Snapped to a bay line. Only used when glass_top_drop_m > 0'),
        'upper_setback_y_m': R(0.0, 0.0, 3.0, 'ESTIMATED diagnostic: how far the upper cream mass is recessed behind the facade plane. Only used when glass_top_drop_m > 0'),
        'terrace_parapet_h_m': R(1.0, 0.0, 1.0, 'ESTIMATED diagnostic: height of the low white upstand along the exposed terrace edges (0 = none; the closed terrace cap stays). Only used when glass_top_drop_m > 0; not measured')},
}

# render colours: measured gray-world RGB of photo patches, lifted because patches sit in overcast shade
# (see reference-profiles.json `colour`). 'by_eye' = not measurable in the photos (cast / shade).
PALETTE = {
    'stone_light': ((118, 123, 134), 1.5, 'measured'), 'fascia_dark': ((77, 84, 93), 1.3, 'measured'),
    'podium_dark': ((51, 60, 70), 1.5, 'measured'), 'cream_wall': ((148, 142, 134), 1.5, 'measured'),
    'slab_cream': ((190, 181, 165), 1.2, 'measured'), 'loggia_back': ((96, 83, 76), 1.5, 'measured'),
    'stone_rough': ((138, 131, 121), 1.4, 'measured'), 'ochre': ((110, 105, 92), 1.6, 'measured; photo cast hides the warm hue'),
    'concrete_pale': ((106, 108, 114), 1.6, 'measured'), 'redbrown_frame': ((120, 66, 52), 1.0, 'by_eye: measured patch (126,118,117) is washed out'),
    'net_green': ((38, 58, 48), 1.0, 'by_eye'), 'glass': ((71, 84, 96), 1.2, 'measured'),
    'white_trim': ((205, 205, 200), 1.0, 'by_eye: painted rails/shutters'), 'generic_wall': ((170, 168, 160), 1.0, 'neutral generic for unseen faces'),
}
_GLASS = {'glass'}


def _rgb(name):
    rgb, lift, _ = PALETTE[name]
    return tuple(min(255, int(round(c * lift))) for c in rgb)


# ----------------------------------------------------------------------------- primitives
def _box(x0, x1, y0, y1, z0, z1):
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    f = np.array([[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]])
    return v, f


def _prism(poly, z0, z1):
    """Extrude a convex counter-clockwise (seen from +Z) polygon."""
    p = np.asarray(poly, float)
    n = len(p)
    v = np.vstack([np.c_[p, np.full(n, z0)], np.c_[p, np.full(n, z1)]])
    f = []
    for i in range(1, n - 1):
        f.append([0, i + 1, i])            # bottom, faces down
        f.append([n, n + i, n + i + 1])    # top, faces up
    for i in range(n):
        j = (i + 1) % n
        f.append([i, j, n + j])
        f.append([i, n + j, n + i])
    return v, np.array(f)


@dataclass
class Part:
    name: str
    material: str
    evidence: str                  # 'observed' (seen in the photos), 'estimated' (inferred), 'generic' (unseen/assumed)
    photo_ids: tuple = ()
    note: str = ''
    verts: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    primitives: int = 0

    def add(self, v, f):
        base = sum(len(x) for x in self.verts)
        self.verts.append(v)
        self.faces.append(f + base)
        self.primitives += 1


class _Ctx:
    """Collects parts; every box is clipped to the plan width (unless free_x) and to the roof height."""

    def __init__(self, W, H, D):
        self.W, self.H, self.D = W, H, D
        self.parts = {}

    def part(self, name, material, evidence='estimated', photos=(), note=''):
        if name not in self.parts:
            self.parts[name] = Part(name, material, evidence, tuple(photos), note)
        return self.parts[name]

    def box(self, part, x0, x1, y0, y1, z0, z1, free_x=False):
        if not free_x:
            x0, x1 = max(x0, 0.0), min(x1, self.W)
        z0, z1 = max(z0, 0.0), min(z1, self.H)
        if x1 - x0 < _EPS or y1 - y0 < _EPS or z1 - z0 < _EPS:
            return False
        self.parts[part].add(*_box(x0, x1, y0, y1, z0, z1))
        return True

    def prism(self, part, poly, z0, z1):
        z0, z1 = max(z0, 0.0), min(z1, self.H)
        if z1 - z0 < _EPS:
            return False
        self.parts[part].add(*_prism(poly, z0, z1))
        return True

    def wall(self, part, x0, x1, z0, z1, y0, y1, openings=(), glass=None):
        """Wall panel with rectangular through-openings (x0, x1, z0, z1); optional glass part set into each."""
        ops = sorted(o for o in openings if o[1] - o[0] > _EPS and o[3] - o[2] > _EPS)
        cur = x0
        for ox0, ox1, oz0, oz1 in ops:
            ox0, ox1, oz0, oz1 = max(ox0, x0), min(ox1, x1), max(oz0, z0), min(oz1, z1)
            if ox1 - ox0 < _EPS or oz1 - oz0 < _EPS or ox0 < cur - _EPS:
                continue
            self.box(part, cur, ox0, y0, y1, z0, z1)
            self.box(part, ox0, ox1, y0, y1, z0, oz0)
            self.box(part, ox0, ox1, y0, y1, oz1, z1)
            if glass:
                self.box(glass, ox0, ox1, y0 + (y1 - y0) * 0.45, y0 + (y1 - y0) * 0.45 + 0.05, oz0, oz1)
            cur = ox1
        self.box(part, cur, x1, y0, y1, z0, z1)


@dataclass
class FacadeMesh:
    family: str
    parts: dict          # name -> {'verts': (N,3) float32, 'faces': (M,3) int32, 'meta': Part-like dict}
    provenance: dict

    @property
    def triangle_count(self):
        return sum(len(p['faces']) for p in self.parts.values())

    @property
    def bounds(self):
        v = np.vstack([p['verts'] for p in self.parts.values()])
        return v.min(0).tolist(), v.max(0).tolist()

    def part_summary(self):
        out = {}
        for name, p in self.parts.items():
            lo, hi = p['verts'].min(0).tolist(), p['verts'].max(0).tolist()
            out[name] = {'triangles': int(len(p['faces'])), 'primitives': p['meta']['primitives'], 'material': p['meta']['material'],
                         'evidence': p['meta']['evidence'], 'photo_ids': list(p['meta']['photo_ids']), 'bounds_min': lo, 'bounds_max': hi}
        return out

    def summary(self):
        lo, hi = self.bounds
        return {'family': self.family, 'triangles': self.triangle_count, 'bounds_min': lo, 'bounds_max': hi, 'parts': self.part_summary()}

    def soup(self):
        """(triangles (T,3,3), normals (T,3), part name per triangle) with outward-wound flat normals."""
        tris, names = [], []
        for name, p in self.parts.items():
            tris.append(p['verts'][p['faces']])
            names += [name] * len(p['faces'])
        t = np.concatenate(tris).astype(float)
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        return t, n / np.where(ln == 0, 1, ln), names

    def to_glb(self, path=None):
        data = _glb_bytes(self)
        if path is not None:
            with open(path, 'wb') as fh:
                fh.write(data)
        return data

    def to_trimesh_scene(self):
        """Optional; needs ``trimesh`` (not a project dependency)."""
        import trimesh
        scene = trimesh.Scene()
        for name, p in self.parts.items():
            m = trimesh.Trimesh(p['verts'], p['faces'], process=False)
            m.visual.face_colors = list(_rgb(p['meta']['material'])) + [255]
            scene.add_geometry(m, node_name=name, geom_name=name)
        scene.metadata['provenance'] = self.provenance
        return scene


# ----------------------------------------------------------------------------- shared layout
class _Layout:
    def __init__(self, p, W, H, D):
        self.W, self.H, self.D = W, H, D
        self.par = min(p['parapet_h_m'], 0.12 * H)
        self.g = min(p['ground_floor_m'], 0.45 * H)
        self.Hf = H - self.par
        self.n = max(1, round((self.Hf - self.g) / p['floor_height_m']))
        self.fh = (self.Hf - self.g) / self.n
        self.nb = max(1, round(W / p['bay_width_m']))
        self.bw = W / self.nb
        self.t = min(p['wall_t_m'], 0.25 * self.fh)

    def fz(self, k):
        return self.g + k * self.fh, self.g + (k + 1) * self.fh

    def bx(self, b):
        return b * self.bw, (b + 1) * self.bw


def _window(L, z0, z1, x0, x1, p, wfrac=0.6, wmax=1.9, sill=0.3, hmax=None):
    """Opening rectangle centred in [x0,x1] for a floor spanning z0..z1."""
    cw = x1 - x0
    w = min(wfrac * cw, wmax)
    s = z0 + sill * (z1 - z0) * 0.8
    h = min(hmax or 1.9, 0.6 * (z1 - z0))
    return ((x0 + x1) / 2 - w / 2, (x0 + x1) / 2 + w / 2, s, s + h)


def _shell(ctx, L, core_y0, sides, side_photos=(), rear_t=0.3, core_poly=None, core_x0=0.0, core_z1=None):
    """Opaque body behind the facade, explicit side faces and a generic unseen rear."""
    z_top = L.Hf if core_z1 is None else core_z1
    ctx.part('body_core', 'generic_wall', 'estimated', note='solid volume behind the street relief; interior not modelled')
    if core_poly is None:
        ctx.box('body_core', core_x0, L.W, core_y0, L.D - rear_t, 0.0, z_top)
    else:
        ctx.prism('body_core', core_poly, 0.0, z_top)
    ctx.part('rear_generic', 'generic_wall', 'generic', note='rear is unseen: plain wall, no balcony relief copied from the street face')
    ctx.box('rear_generic', 0.0, L.W, L.D - rear_t, L.D, 0.0, z_top)
    for side, x0, x1 in (('left', 0.0, 0.3), ('right', L.W - 0.3, L.W)):
        mode = sides.get(side, 'plain')
        if mode == 'none':
            continue
        ctx.part(f'side_{side}', 'generic_wall', 'generic', note='side face unseen: closing wall' + ('; floor bands continued (estimate)' if mode == 'bands' else ''))
        ctx.box(f'side_{side}', x0, x1, 0.0, core_y0, 0.0, L.Hf)
        if mode == 'bands':
            ctx.part(f'side_{side}_bands', 'slab_cream', 'estimated', note='continuation of the street floor bands on the side; not observed')
            for k in range(L.n):
                _, z1 = L.fz(k)
                xa, xb = (-0.12, 0.0) if side == 'left' else (L.W, L.W + 0.12)
                ctx.box(f'side_{side}_bands', xa, xb, 0.0, L.D, z1 - 0.3, z1, free_x=True)


def _roof(ctx, L, front_y0=0.0, railing=False, photos=()):
    ctx.part('roof_slab', 'generic_wall', 'estimated')
    ctx.box('roof_slab', 0.0, L.W, front_y0, L.D, L.Hf - 0.25, L.Hf)
    if not railing:
        ctx.part('roof_parapet', 'stone_light', 'estimated', photos, 'parapet to the supplied roof height')
        w = 0.22
        ctx.box('roof_parapet', 0.0, L.W, front_y0, front_y0 + w, L.Hf, L.H)
        ctx.box('roof_parapet', 0.0, L.W, L.D - w, L.D, L.Hf, L.H)
        ctx.box('roof_parapet', 0.0, w, front_y0, L.D, L.Hf, L.H)
        ctx.box('roof_parapet', L.W - w, L.W, front_y0, L.D, L.Hf, L.H)
    else:
        ctx.part('roof_railing', 'white_trim', 'observed', photos, 'railing parapet seen on the rooftop terrace (photos); slim posts and rails')
        for x in np.linspace(0.05, L.W - 0.05, max(2, int(L.W / 1.5) + 1)):
            ctx.box('roof_railing', x - 0.03, x + 0.03, front_y0, front_y0 + 0.06, L.Hf, L.H)
        ctx.box('roof_railing', 0.0, L.W, front_y0, front_y0 + 0.08, L.H - 0.08, L.H)
        ctx.box('roof_railing', 0.0, L.W, L.D - 0.08, L.D, L.H - 0.08, L.H)
        ctx.box('roof_railing', 0.0, 0.08, front_y0, L.D, L.H - 0.08, L.H)
        ctx.box('roof_railing', L.W - 0.08, L.W, front_y0, L.D, L.H - 0.08, L.H)


# ----------------------------------------------------------------------------- family builders
def _modern(ctx, L, p, sides, rng):
    ph = FAMILY_PHOTOS['modern_stone_recessed_balcony']
    rd, pw, bh, st = p['gallery_depth_m'], p['pier_w_m'], p['band_h_m'], p['stagger_proj_m']
    t = L.t
    ctx.part('piers', 'stone_light', 'observed', ph, 'opaque stone piers between bays, run the full gallery depth')
    ctx.part('floor_bands', 'stone_light', 'observed', ph, 'flush stone floor band in the front plane')
    ctx.part('cantilever_fascia', 'fascia_dark', 'observed', ph, 'charcoal fascia box projecting on alternate bays (staggered), depth estimated')
    ctx.part('gallery_floors', 'stone_light', 'estimated')
    ctx.part('gallery_back_walls', 'stone_light', 'observed', ph, 'window wall set back by the estimated gallery depth')
    ctx.part('glass', 'glass', 'observed', ph)
    ctx.part('glass_parapets', 'glass', 'observed', ('1288770821',), 'glass parapet seen on the granite-mass balconies in photo 1')
    ctx.part('podium', 'podium_dark', 'observed', ('1288770821',), 'dark stone podium; shopfront bays estimated')
    ctx.part('storefront_glass', 'glass', 'estimated')
    ctx.part('podium_cap', 'fascia_dark', 'observed', ('1288770821',), 'projecting dark podium wrap seen in photo 1')
    core_y0 = rd + t
    for b in range(L.nb):  # podium shopfront
        x0, x1 = L.bx(b)
        ow = 0.72 * (x1 - x0)
        ctx.wall('podium', x0, x1, 0.0, L.g - 0.35, 0.0, 0.4, [((x0 + x1) / 2 - ow / 2, (x0 + x1) / 2 + ow / 2, 0.3, L.g - 0.7)], 'storefront_glass')
    ctx.box('podium_cap', 0.0, L.W, -0.6, 0.4, L.g - 0.35, L.g)
    for k in range(L.n):
        z0, z1 = L.fz(k)
        top = z1 - min(bh, 0.3 * L.fh)
        for b in range(L.nb):
            x0, x1 = L.bx(b)
            proj = st if (k + b) % 2 == 0 else 0.0
            ix0, ix1 = x0 + pw / 2, x1 - pw / 2
            if proj > _EPS:
                ctx.box('cantilever_fascia', ix0, ix1, -proj, 0.35, top, z1)
            else:
                ctx.box('floor_bands', x0, x1, 0.0, 0.35, top, z1)
            ctx.box('gallery_floors', ix0, ix1, -proj, rd + t, z0, z0 + 0.2)
            wz = _window(L, z0 + 0.2, top, ix0, ix1, p, 0.62, 1.8, 0.25, p['window_h_m'])
            ctx.wall('gallery_back_walls', ix0, ix1, z0 + 0.2, top, rd, rd + t, [wz], 'glass')
            ctx.box('glass_parapets', ix0, ix1, -proj, -proj + 0.04, z0 + 0.2, z0 + 0.2 + min(1.05, 0.38 * L.fh))
        for i in range(L.nb + 1):
            cx = i * L.bw
            ctx.box('piers', cx - pw / 2, cx + pw / 2, 0.0, core_y0, z0, top)
    _roof(ctx, L, photos=ph)
    return core_y0


def _cream(ctx, L, p, sides, rng):
    ph = FAMILY_PHOTOS['cream_yellow_older_balcony']
    ld, pw, rh, t = p['loggia_depth_m'], p['pier_w_m'], p['rail_h_m'], L.t
    st = min(p['slab_t_m'], 0.3 * L.fh)
    ctx.part('slab_edges', 'slab_cream', 'observed', ph, 'flush cream slab edge band; reads as an edge, not infill')
    ctx.part('piers', 'cream_wall', 'observed', ph)
    ctx.part('loggia_back_walls', 'loggia_back', 'observed', ph, 'warm ochre loggia back wall')
    ctx.part('loggia_floors', 'cream_wall', 'estimated')
    ctx.part('parapet_balconies', 'cream_wall', 'observed', ph, 'opaque parapet at the front of each loggia')
    ctx.part('glass', 'glass', 'observed', ph)
    ctx.part('glazed_top', 'white_trim', 'observed', ph, 'aluminium-framed glazing strips on the closed-in top floor')
    ctx.part('closed_bays', 'cream_wall', 'estimated', ph, 'closed window bays between loggias')
    ctx.part('shutters', 'white_trim', 'observed', ('1288770821',), 'roll shutters/curtains in lower openings (photo 1); placement is a seeded estimate')
    ctx.part('stone_podium', 'stone_rough', 'observed', ph, 'windowless rough limestone base wall (photos 1, 2, 4)')
    ctx.part('podium_courses', 'podium_dark', 'estimated', note='shallow course lines, spacing estimated')
    core_y0 = ld + t
    ctx.box('stone_podium', 0.0, L.W, 0.0, 0.45, 0.0, L.g)
    for z in np.arange(0.45, L.g, 0.45):
        ctx.box('podium_courses', 0.0, L.W, -0.03, 0.0, z, z + 0.04)
    plain = {b for b in range(L.nb) if L.nb >= 4 and b % 4 == 3}  # a closed bay now and then
    for k in range(L.n):
        z0, z1 = L.fz(k)
        top_floor = k == L.n - 1 and L.n > 1
        ctx.box('slab_edges', 0.0, L.W, 0.0, 0.3, z1 - st, z1)
        for b in range(L.nb):
            x0, x1 = L.bx(b)
            if top_floor:
                ow0, ow1 = x0 + 0.1, x1 - 0.1
                oz = (ow0, ow1, z0 + 0.8, z1 - st - 0.2)
                ctx.wall('glazed_top', x0, x1, z0, z1 - st, 0.0, 0.25, [oz], 'glass')
                for m in range(1, 3):  # mullions across the ribbon
                    mx = ow0 + (ow1 - ow0) * m / 3
                    ctx.box('glazed_top', mx - 0.03, mx + 0.03, -0.02, 0.1, z0 + 0.8, z1 - st - 0.2)
            elif b in plain:
                wz = _window(L, z0, z1 - st, x0 + 0.5, x1 - 0.5, p, 0.55, 1.6, 0.3)
                ctx.wall('closed_bays', x0, x1, z0, z1 - st, 0.0, 0.3, [wz], 'glass')
                if k <= 1 and rng.random() < max(0.5, p['shutter_fraction'] * 2):
                    ctx.box('shutters', wz[0] - 0.05, wz[1] + 0.05, -0.08, -0.02, wz[2], wz[3] + 0.05)
                    ctx.box('shutters', wz[0] - 0.08, wz[1] + 0.08, -0.12, -0.02, wz[3] + 0.05, wz[3] + 0.3)
            else:
                ix0, ix1 = x0 + pw / 2, x1 - pw / 2
                ctx.box('loggia_floors', ix0, ix1, 0.0, ld + t, z0, z0 + 0.2)
                ctx.box('parapet_balconies', ix0, ix1, 0.0, 0.18, z0 + 0.2, z0 + 0.2 + min(rh, 0.4 * L.fh))
                wz = _window(L, z0 + 0.2, z1 - st, ix0, ix1, p, 0.6, 1.7, 0.3)
                ctx.wall('loggia_back_walls', ix0, ix1, z0 + 0.2, z1 - st, ld, ld + t, [wz], 'glass')
                if k <= 1 and rng.random() < p['shutter_fraction']:
                    ctx.box('shutters', wz[0], wz[1], ld - 0.06, ld, wz[2], wz[3])
        for i in range(L.nb + 1):
            cx = i * L.bw
            ctx.box('piers', cx - pw / 2, cx + pw / 2, 0.0, core_y0, z0, z1 - st)
    _roof(ctx, L, railing=True, photos=ph)
    return core_y0


def _ochre(ctx, L, p, sides, rng):
    ph = FAMILY_PHOTOS['older_brown_midrise']
    bd, bh, ed, t = p['balcony_depth_m'], p['band_h_m'], p['eave_depth_m'], L.t
    ctx.part('front_wall', 'ochre', 'observed', ph, 'ochre rendered street wall with punched windows')
    ctx.part('slab_bands', 'white_trim', 'observed', ph, 'horizontal slab bands')
    ctx.part('balcony_slabs', 'white_trim', 'observed', ph, 'stepped balcony columns on alternate bays')
    ctx.part('balcony_rails', 'white_trim', 'observed', ph, 'white-painted rails')
    ctx.part('glass', 'glass', 'estimated')
    ctx.part('pergola_eave', 'ochre', 'observed', ph, 'slatted rooftop pergola projecting at the roofline; slat spacing estimated')
    ctx.part('ground_base', 'ochre', 'generic', note='ground floor is hidden in all four photos: generic shopfront')
    ctx.part('ground_glass', 'glass', 'generic')
    core_y0 = t + 0.1
    for b in range(L.nb):
        x0, x1 = L.bx(b)
        ow = 0.7 * (x1 - x0)
        ctx.wall('ground_base', x0, x1, 0.0, L.g, 0.0, t, [((x0 + x1) / 2 - ow / 2, (x0 + x1) / 2 + ow / 2, 0.4, L.g - 0.6)], 'ground_glass')
    for k in range(L.n):
        z0, z1 = L.fz(k)
        ctx.box('slab_bands', 0.0, L.W, -0.04, t, z1 - bh, z1)
        for b in range(L.nb):
            x0, x1 = L.bx(b)
            wz = _window(L, z0, z1 - bh, x0 + 0.3, x1 - 0.3, p, 0.55, 1.5, 0.3)
            ctx.wall('front_wall', x0, x1, z0, z1 - bh, 0.0, t, [wz], 'glass')
            if b % 2 == 0:
                ctx.box('balcony_slabs', x0 + 0.2, x1 - 0.2, -bd, t, z0, z0 + 0.18)
                rh = min(1.05, 0.38 * L.fh)
                ctx.box('balcony_rails', x0 + 0.2, x1 - 0.2, -bd, -bd + 0.06, z0 + 0.18, z0 + 0.18 + rh)
                ctx.box('balcony_rails', x0 + 0.2, x0 + 0.26, -bd, 0.0, z0 + 0.18, z0 + 0.18 + rh)
                ctx.box('balcony_rails', x1 - 0.26, x1 - 0.2, -bd, 0.0, z0 + 0.18, z0 + 0.18 + rh)
    for x in np.arange(0.1, L.W - 0.05, 0.45):  # pergola slats, clipped at the roof height
        ctx.box('pergola_eave', x, x + 0.12, -ed, t, L.H - 0.2, L.H - 0.08)
    ctx.box('pergola_eave', 0.0, L.W, -ed, -ed + 0.15, L.H - 0.28, L.H - 0.08)
    ctx.box('pergola_eave', 0.0, L.W, -0.05, 0.1, L.H - 0.28, L.H - 0.08)
    _roof(ctx, L, front_y0=0.0, photos=ph)
    return core_y0


def _construction(ctx, L, p, sides, rng):
    ph = FAMILY_PHOTOS['construction_tower_frame']
    cw, st, off = p['column_w_m'], min(p['slab_t_m'], 0.3 * L.fh), p['net_offset_m']
    open_f = int(min(p['open_floors'], L.n))
    ctx.part('columns', 'concrete_pale', 'observed', ph, 'concrete frame columns; spacing estimated')
    ctx.part('floor_slabs', 'concrete_pale', 'observed', ph, 'floor slabs with open edges on the upper floors')
    ctx.part('infill_panels', 'concrete_pale', 'observed', ('1288771293',), 'plastered pale infill on the lower floors, punched openings estimated')
    ctx.part('glass', 'glass', 'estimated')
    ctx.part('net_side', 'net_green', 'observed', ph, 'dark green net over scaffold on the right-hand side face')
    ctx.part('scaffold', 'white_trim', 'observed', ('1288771293',), 'scaffold standards and ledgers behind the net, spacing estimated')
    ctx.part('service_core', 'concrete_pale', 'generic', note='lift/stair core is hidden in the photos: generic block at the rear')
    ctx.part('rear_generic', 'generic_wall', 'generic', note='rear is unseen: infill on the lower floors only')
    nx = max(2, round(L.W / p['bay_width_m']) + 1)
    rows = [0.0, L.D - cw] if L.D < 12 else [0.0, (L.D - cw) / 2, L.D - cw]
    for x in np.linspace(0.0, L.W - cw, nx):
        for y in rows:
            ctx.box('columns', x, x + cw, y, y + cw, 0.0, L.Hf)
    for k in range(L.n + 1):
        z = L.g + k * L.fh
        ctx.box('floor_slabs', 0.0, L.W, 0.0, L.D, z - st, z) if k else ctx.box('floor_slabs', 0.0, L.W, 0.0, L.D, 0.0, 0.15)
    infilled = L.n - open_f
    for k in range(infilled):
        z0, z1 = L.fz(k)
        for b in range(L.nb):
            x0, x1 = L.bx(b)
            wz = _window(L, z0, z1 - st, x0 + 0.5, x1 - 0.5, p, 0.5, 1.4, 0.3)
            ctx.wall('infill_panels', x0, x1, z0, z1 - st, 0.0, 0.25, [wz], 'glass')
        ctx.box('rear_generic', 0.0, L.W, L.D - 0.25, L.D, z0, z1 - st)
    ctx.box('service_core', L.W * 0.4, L.W * 0.62, L.D - 4.0, L.D - 0.3, 0.0, L.Hf - st)
    x = L.W + off  # net plane + scaffold on the +X face (overhangs the plan by net_offset)
    ctx.box('net_side', x, x + 0.04, 0.0, L.D, 0.0, L.Hf + 0.0, free_x=True)
    for y in np.arange(0.0, L.D, 1.8):
        ctx.box('scaffold', x - 0.06, x, y, y + 0.05, 0.0, L.Hf, free_x=True)
    for k in range(L.n + 1):
        ctx.box('scaffold', x - 0.06, x, 0.0, L.D, min(L.H, L.g + k * L.fh) - 0.05, min(L.H, L.g + k * L.fh), free_x=True)
    if L.par > 0.2:
        ctx.part('roof_parapet', 'concrete_pale', 'estimated', ph)
        ctx.box('roof_parapet', 0.0, L.W, 0.0, 0.15, L.Hf, L.H)
    return 0.0


def _clip_convex(poly, x0, y0):
    """Convex polygon clipped to x >= x0 and y >= y0 (Sutherland-Hodgman against two half-planes)."""
    out = [tuple(q) for q in poly]
    for axis, lim in ((0, x0), (1, y0)):
        inp, out = out, []
        for i in range(len(inp)):
            p, q = inp[i], inp[(i + 1) % len(inp)]
            ip, iq = p[axis] >= lim - 1e-9, q[axis] >= lim - 1e-9
            if ip:
                out.append(p)
            if ip != iq:
                t = (lim - p[axis]) / (q[axis] - p[axis])
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
        if len(out) < 3:
            return []
    return out


def _lgb(ctx, L, p, sides, rng):
    ph = FAMILY_PHOTOS['lgb_cream_redbrown_bands']
    r = min(p['corner_radius_m'], 0.4 * L.W, 0.4 * L.D)
    fd, ft, pitch = p['frame_depth_m'], min(p['frame_t_m'], 0.25 * L.fh), max(1, int(p['frame_pitch_floors']))
    fhf, zone = p['frame_height_floors'], p['frame_zone_frac']
    t = L.t
    # ---- optional stepped top (ESTIMATED diagnostic; defaults 0 reproduce the single-tier envelope exactly) ----------------------
    # Observed (photo 1288771293): the rounded glazed volume and its top brown ledge end LOWER than a cream upper mass with sky-visible
    # parapet apertures that sits to the right. The numeric drop and setback footprint are NOT measured.
    nb = max(1, round((L.W - r) / p['bay_width_m']))
    bw = (L.W - r) / nb
    m_floors = L.n
    if p['glass_top_drop_m'] > 1e-9:
        m_floors = int(min(L.n, max(2, round((L.Hf - p['glass_top_drop_m'] - L.g) / L.fh))))
    stepped = m_floors < L.n
    Hg = L.g + m_floors * L.fh if stepped else L.Hf         # top of the floored / glazed facade (a floor line)
    if stepped:
        if p['upper_setback_x_m'] <= 1e-9:
            b0, ux0 = 0, 0.0
        else:
            b0 = int(min(nb - 1, max(0, round((p['upper_setback_x_m'] - r) / bw))))
            ux0 = r + b0 * bw
        uy0 = min(p['upper_setback_y_m'], 0.25 * L.D)
    else:
        b0, ux0, uy0 = 0, 0.0, 0.0
    L.eff_extra = {'glass_top_drop_m': float(L.Hf - Hg), 'upper_setback_x_m': float(ux0), 'upper_setback_y_m': float(uy0)}
    if stepped:
        L.eff_extra['terrace_parapet_h_m'] = float(p['terrace_parapet_h_m'])
    L.extra_prov = {'stepped_top': {'enabled': bool(stepped), 'floors_below_glass_top': int(m_floors), 'floors_total': int(L.n), 'glass_top_z_m': round(float(Hg), 3),
                                    'effective_drop_m': round(float(L.Hf - Hg), 3), 'upper_setback_x_effective_m': round(float(ux0), 3), 'upper_setback_y_m': round(float(uy0), 3), 'terrace_parapet_h_m': float(p['terrace_parapet_h_m']) if stepped else None,
                                    'basis': 'ESTIMATED diagnostic parameters. Observed only: in photo 1288771293 the glazed volume ends below a taller cream upper mass. '
                                             'Hidden roof plan, terrace extent and the numeric drop/setback are assumptions, not measurements.'}}
    ctx.part('front_wall', 'cream_wall', 'observed', ('1288771293',), 'cream panel wall with punched windows (east strip)')
    ctx.part('frame_slabs', 'redbrown_frame', 'observed', ('1288771293',), 'red-brown frame slabs, one frame per two floors, alternate frames shifted outward')
    ctx.part('frame_returns', 'redbrown_frame', 'observed', ('1288771293',), 'ONE vertical return at the RIGHT end of each open C frame (photo 1288771293, viewer facing the facade); the left end runs into the rounded corner with no post')
    ctx.part('window_glass', 'glass', 'observed', ('1288771293',))
    ctx.part('curved_corner_glass', 'glass', 'observed', ('1288771293',), 'rounded glazed west corner, full height of the floored facade; radius estimated')
    ctx.part('corner_mullions', 'white_trim', 'estimated', ('1288771293',))
    ctx.part('corner_floor_edges', 'cream_wall', 'estimated', ('1288771293',))
    ctx.part('ground_base', 'podium_dark', 'generic', note='ground storey hidden behind trees in the photos: generic glazed base')
    ctx.part('ground_glass', 'glass', 'generic')
    ctx.part('roof_cutouts', 'cream_wall', 'observed', ('1288771293',), 'roof band with rectangular cut-outs')
    ctx.part('roof_slab', 'generic_wall', 'estimated', note='closed cap of the highest tier' + ('; the upper cream mass only (stepped top)' if stepped else ''))
    ctx.part('roof_parapet', 'cream_wall', 'estimated', ph)
    if stepped:
        ctx.part('terrace_slab', 'generic_wall', 'estimated', note='closed cap of the lower (glazed) tier; exposed as a terrace where the upper mass is set back. Hidden roof plan is an estimate')
        if p['terrace_parapet_h_m'] > 1e-9:
            ctx.part('terrace_parapet', 'cream_wall', 'estimated', note='low guard along the exposed terrace edges; height is an estimate')
        ctx.part('upper_core', 'cream_wall', 'estimated', ph, 'closed solid of the set-back cream upper mass between the glazed top and the roof; footprint is an estimate')

    gt = 0.08  # glass thickness
    st = 0.35  # floor slab setback behind glass
    th = np.linspace(math.pi, 1.5 * math.pi, 9)
    outer = [(r + r * math.cos(a), r + r * math.sin(a)) for a in th]
    inner = [(r + (r - gt) * math.cos(a), r + (r - gt) * math.sin(a)) for a in th]
    inner_edge = [(r + (r - st) * math.cos(a), r + (r - st) * math.sin(a)) for a in th]

    # Curved corner glazing: 8 arc prism segments, ending at the top of the floored facade
    for j in range(8):
        poly = [outer[j], outer[j + 1], inner[j + 1], inner[j]]
        ctx.prism('curved_corner_glass', poly, 0.0, Hg - 0.25)

    # Corner mullions: vertical fins centered at the glass joints
    for j in range(9):
        cx = r + (r - gt / 2) * math.cos(th[j])
        cy = r + (r - gt / 2) * math.sin(th[j])
        ctx.box('corner_mullions', cx - 0.03, cx + 0.03, cy - 0.03, cy + 0.03, L.g, Hg - 0.25)

    # Floor edges: behind glass, floors 0..m_floors-1 (the top floor is capped by the lower cap)
    for k in range(m_floors):
        z = L.g + k * L.fh
        for j in range(8):
            poly = [inner[j], inner[j + 1], inner_edge[j + 1], inner_edge[j]]
            ctx.prism('corner_floor_edges', poly, max(0.0, z - 0.25), z)

    xf0, xf1 = r, r + zone * (L.W - r)
    for b in range(nb):
        x0, x1 = r + b * bw, r + (b + 1) * bw
        ow = 0.7 * bw
        ctx.wall('ground_base', x0, x1, 0.0, L.g, 0.0, t, [((x0 + x1) / 2 - ow / 2, (x0 + x1) / 2 + ow / 2, 0.3, L.g - 0.6)], 'ground_glass')
    for k in range(L.n):
        z0, z1 = L.fz(k)
        top = z1 if k < L.n - 1 else L.Hf - 0.25
        if k < m_floors:
            top = z1 if k < m_floors - 1 else Hg - 0.25
            bays, y0 = range(nb), 0.0
        else:  # floors of the set-back upper mass: plain cream punched windows, only on its own front
            bays, y0 = range(b0, nb), uy0
        for b in bays:
            x0, x1 = r + b * bw, r + (b + 1) * bw
            if k < m_floors and (x0 + x1) / 2 < xf1 and (k % pitch) <= 1:
                oz = (x0 + 0.5, x1 - 0.5, z0 + 0.2, top - 0.3)
            else:
                oz = _window(L, z0, top, x0 + 0.5, x1 - 0.5, p, 0.5, 1.4, 0.3)
            ctx.wall('front_wall', x0, x1, z0, top, y0, y0 + t, [oz], 'window_glass')

    for i in range((m_floors + pitch - 1) // pitch):  # frames
        zb = L.g + i * pitch * L.fh
        zt = zb + fhf * L.fh
        if stepped:
            zt = min(zt, Hg)  # a frame never reaches above the glazed top
            if zt - zb < 2 * ft + 0.3:
                continue
        d = fd + 0.25 * (i % 2)
        # Open C frame, as in photo 1288771293 (region x895-1120, y60-500 of the 1600x1200 frame): a top and a bottom ledge and a single
        # vertical return at the viewer's RIGHT end (larger local x). The left end of both ledges runs straight into the rounded glazed
        # corner (x = xf0 = r); the photograph shows no left vertical post, so none is built (an earlier version closed each frame into an O).
        ctx.box('frame_slabs', xf0, xf1, -d, 0.0, zb, zb + ft)
        ctx.box('frame_slabs', xf0, xf1, -d, 0.0, zt - ft, zt)
        ctx.box('frame_returns', xf1 - ft, xf1, -d, 0.0, zb + ft, zt - ft)

    arc_core = [(r + (r - st) * math.cos(a), r + (r - st) * math.sin(a)) for a in th]
    core_poly = arc_core + [(L.W, t), (L.W, L.D - 0.3), (0.0, L.D - 0.3), (0.0, r)]
    pw = 0.25
    if not stepped:
        # Roof slab: solid closed slab capping body_core and rear_generic
        ctx.box('roof_slab', 0.0, L.W, 0.0, L.D, L.Hf - 0.25, L.Hf)
        xa, y_front, ux, uy = r, 0.0, 0.0, 0.0
    else:
        # Lower tier: closed cap (terrace) over the whole plan at the glazed top; upper tier: closed solid + its own cap above it
        ctx.prism('terrace_slab', core_poly, Hg - 0.25, Hg)
        up = _clip_convex(core_poly, ux0, uy0 + t)
        if up:
            ctx.prism('upper_core', up, Hg, L.Hf - 0.25)
            ctx.prism('roof_slab', _clip_convex(core_poly, ux0, uy0), L.Hf - 0.25, L.Hf)
        gh, gw = float(p['terrace_parapet_h_m']), 0.15  # low terrace guard along the exposed edges only (gh = 0: no upstand, cap unchanged)
        if gh > 1e-9 and ux0 > r + 1e-6:      # front edge, west flank and rear of the terrace left of the upper mass
            ctx.box('terrace_parapet', r, ux0, 0.0, gw, Hg, Hg + gh)
            ctx.box('terrace_parapet', 0.0, gw, r, L.D - 0.3, Hg, Hg + gh)
            ctx.box('terrace_parapet', gw, ux0, L.D - 0.3 - gw, L.D - 0.3, Hg, Hg + gh)
        if gh > 1e-9 and uy0 > 0.3:          # front strip in front of the recessed upper mass
            ctx.box('terrace_parapet', max(ux0, r), L.W, 0.0, gw, Hg, Hg + gh)
            ctx.box('terrace_parapet', L.W - gw, L.W, gw, uy0, Hg, Hg + gh)
        xa, y_front, ux, uy = max(r, ux0), uy0, ux0, uy0
    # Roof parapet with rectangular sky apertures on the front of the highest tier
    span = L.W - xa
    ctx.wall('roof_cutouts', xa, L.W, L.Hf, L.H, y_front, y_front + 0.3,
             [(xa + 0.15 * span, xa + 0.35 * span, L.Hf + 0.2, L.H - 0.2),
              (xa + 0.55 * span, xa + 0.72 * span, L.Hf + 0.2, L.H - 0.2)])
    ctx.box('roof_parapet', ux, L.W, L.D - pw, L.D, L.Hf, L.H)
    ctx.box('roof_parapet', L.W - pw, L.W, y_front + 0.3, L.D - pw, L.Hf, L.H)
    ctx.box('roof_parapet', ux, ux + pw, max(r, uy) if ux < r else y_front + 0.3, L.D - pw, L.Hf, L.H)

    # Core polygon: counter-clockwise, closed west flank at x=0 and east flank at x=W; its top is the lower tier's cap level
    return 0.0, core_poly, (Hg - 0.25)


_BUILDERS = {'modern_stone_recessed_balcony': _modern, 'cream_yellow_older_balcony': _cream, 'older_brown_midrise': _ochre,
             'construction_tower_frame': _construction, 'lgb_cream_redbrown_bands': _lgb}


# ----------------------------------------------------------------------------- public API
_PROFILES = {}


def _profile_sha(doc):
    sha = hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()
    _PROFILES[sha] = doc
    return sha


def _resolve_params(family, overrides, profile_sha):
    base = dict(DEFAULTS[family])
    if profile_sha:
        pm = _PROFILES[profile_sha]['families'].get(family, {}).get('parameters_m', {})
        for k, v in pm.items():
            if k in base and isinstance(v, dict) and {'est', 'lo', 'hi'} <= v.keys():
                base[k] = Range(v['est'], v['lo'], v['hi'], 'from reference-profiles.json')
    values = {k: r.est for k, r in base.items()}
    for k, v in dict(overrides).items():
        if k not in base:
            raise ValueError(f'unknown parameter {k!r} for {family}; allowed: {sorted(base)}')
        values[k] = float(v)
    for k, v in values.items():
        if not math.isfinite(v) or v < 0:
            raise ValueError(f'parameter {k} must be finite and >= 0')
    return base, values


def mesh_for_profile(family_key, width_m, height_m, *, depth_m=None, floor_height_m=None, bay_width_m=None, ground_floor_m=None,
                     side_facades=None, seed=0, params=None, profile=None):
    """Deterministic, cached facade component. Returns a read-only :class:`FacadeMesh`.

    ``width_m`` is the frontage (X), ``height_m`` the roof/parapet top (Z, hard clip). Optional keywords
    override estimates; ``params`` overrides family-specific keys in :data:`DEFAULTS`. ``side_facades`` is
    ``{'left': mode, 'right': mode}`` with modes in :data:`SIDE_MODES` (default ``plain``). ``profile`` is an
    optional validated ``reference-profiles.json`` dict whose family parameters replace the defaults.
    Identical arguments return the identical (cached) object: do not mutate it.
    """
    if family_key not in FAMILIES:
        raise ValueError(f'unknown family {family_key!r}; expected one of {FAMILIES}')
    for name, v in (('width_m', width_m), ('height_m', height_m)):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f'{name} must be finite')
    if not 3.0 <= width_m <= 300.0 or not 3.0 <= height_m <= 300.0:
        raise ValueError('width_m and height_m must be within 3..300 m')
    sides = dict(side_facades or {})
    for k, v in sides.items():
        if k not in ('left', 'right') or v not in SIDE_MODES:
            raise ValueError(f'side_facades: {k}={v!r}; keys left/right, modes {SIDE_MODES}')
    ov = dict(params or {})
    for key, val in (('depth_m', depth_m), ('floor_height_m', floor_height_m), ('bay_width_m', bay_width_m), ('ground_floor_m', ground_floor_m)):
        if val is not None:
            ov[key] = val
    sha = _profile_sha(profile) if profile else ''
    key = (family_key, round(float(width_m), 2), round(float(height_m), 2), tuple(sorted((k, round(float(v), 3)) for k, v in ov.items())),
           tuple(sorted(sides.items())), int(seed), sha)
    return _build(*key)


@lru_cache(maxsize=512)
def _build(family, W, H, ov, sides, seed, profile_sha):
    base, values = _resolve_params(family, dict(ov), profile_sha)
    D = values['depth_m']
    if D < 3.0:
        raise ValueError('depth_m must be >= 3')
    ctx = _Ctx(W, H, D)
    L = _Layout(values, W, H, D)
    rng = np.random.default_rng(seed)
    out = _BUILDERS[family](ctx, L, values, dict(sides), rng)
    if isinstance(out, tuple):
        core_y0, core_poly = out[0], out[1]
        core_z1 = out[2] if len(out) > 2 else None
    else:
        core_y0, core_poly, core_z1 = out, None, None
    if family == 'construction_tower_frame':
        pass
    else:
        _shell(ctx, L, core_y0, dict(sides), core_poly=core_poly, core_x0=0.0 if core_poly else 0.0, core_z1=core_z1)
    parts = {}
    for name, part in ctx.parts.items():
        if not part.verts:
            continue
        v = np.vstack(part.verts).astype(np.float32)
        f = np.vstack(part.faces).astype(np.int32)
        v.setflags(write=False); f.setflags(write=False)
        parts[name] = {'verts': v, 'faces': f, 'meta': {'material': part.material, 'evidence': part.evidence, 'photo_ids': part.photo_ids,
                                                        'note': part.note, 'primitives': part.primitives}}
    warnings = []
    pinfo = {}
    eff = {'floor_height_m': L.fh, 'ground_floor_m': L.g, 'bay_width_m': L.bw}
    eff.update(getattr(L, 'eff_extra', {}))
    for k, r in base.items():
        v = eff.get(k, values[k])
        ok = r.lo - 1e-9 <= v <= r.hi + 1e-9
        pinfo[k] = {'value': round(float(v), 4), 'lo': r.lo, 'hi': r.hi, 'basis': 'estimated', 'within_bounds': bool(ok), **({'note': r.note} if r.note else {})}
        if not ok:
            warnings.append(f'{k}={v:.3g} outside estimated bounds [{r.lo}, {r.hi}]')
    prov = {
        'schema': 'dynacity-reference-facade-geometry/1', 'family': family, 'photo_ids': list(FAMILY_PHOTOS[family]),
        'source_profile': 'reference-profiles.json (dynacity-reference-facade-profiles/1)', 'profile_sha256': profile_sha or None,
        'license': LICENSE_NOTE, 'size_m': {'width': W, 'height': H, 'depth': D}, 'floors': L.n, 'bays': L.nb,
        'frame': 'local X frontage, Y depth (facade at y=0, street side is -Y), Z up, roof top = height_m; glTF export maps (x,y,z)->(x,z,-y)',
        'parameters': pinfo, 'side_facades': ({'left': 'open_frame', 'right': 'net_and_scaffold'} if family == 'construction_tower_frame' else
                         {'left': dict(sides).get('left', 'plain'), 'right': dict(sides).get('right', 'plain')}),
        'unseen': 'rear and side faces are unseen; rear is a generic plain wall, never a copy of the street relief',
        'seed': seed, 'warnings': warnings, 'dimensions_basis': 'estimated with bounds; not surveyed', **getattr(L, 'extra_prov', {}),
        'no_billboards': True, 'generated_imagery': False, 'offline_component_not_native_proof': True,
        'identity': ({'proven_source_id': LGB_PROVEN_ID, 'proven_for_photo': '1288771293',
                      'statement': 'family component only; it is NOT a reconstruction of that building and carries no source height'}
                     if family == 'lgb_cream_redbrown_bands' else {'statement': 'family component only; no building ID asserted'}),
    }
    return FacadeMesh(family, parts, prov)


def mesh_cache_info():
    return _build.cache_info()


def placement_matrix(origin_xyz, frontage_dir_xy, outward_xy=None):
    """4x4 local->world matrix. ``frontage_dir_xy`` is the world direction of local +X; the street-facing
    normal (local -Y) defaults to the frontage direction rotated clockwise (viewer's right = +X). Pass
    ``outward_xy`` only to assert it; a left-handed outward normal raises (mirror the footprint instead)."""
    f = np.array(frontage_dir_xy, float)
    f = f / np.linalg.norm(f)
    n = np.array([f[1], -f[0]])
    if outward_xy is not None:
        o = np.array(outward_xy, float)
        o = o / np.linalg.norm(o)
        if not np.allclose(o, n, atol=1e-6):
            raise ValueError('outward normal must be the frontage direction rotated clockwise (right-handed local frame)')
    m = np.eye(4)
    m[:3, 0] = [f[0], f[1], 0.0]
    m[:3, 1] = [-n[0], -n[1], 0.0]
    m[:3, 2] = [0.0, 0.0, 1.0]
    m[:3, 3] = origin_xyz
    return m


# ----------------------------------------------------------------------------- GLB export
def _glb_bytes(mesh: FacadeMesh):
    mats, mat_ix = [], {}
    bufs, views, accs, meshes, nodes = [], [], [], [], []
    offset = 0

    def push(arr, target):
        nonlocal offset
        raw = arr.tobytes()
        pad = (-len(raw)) % 4
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(raw), 'target': target})
        bufs.append(raw + b'\0' * pad)
        offset += len(raw) + pad
        return len(views) - 1

    for name, p in mesh.parts.items():
        m = p['meta']['material']
        if m not in mat_ix:
            c = [x / 255 for x in _rgb(m)]
            mat = {'name': m, 'pbrMetallicRoughness': {'baseColorFactor': c + [0.6 if m in _GLASS else 1.0], 'metallicFactor': 0.0, 'roughnessFactor': 0.15 if m in _GLASS else 0.85},
                   'doubleSided': False}
            if m in _GLASS:
                mat['alphaMode'] = 'BLEND'
            mat_ix[m] = len(mats)
            mats.append(mat)
        tri = p['verts'][p['faces']].astype(np.float64)
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = nrm / np.where(ln == 0, 1, ln)
        pos = tri.reshape(-1, 3)
        nn = np.repeat(nrm, 3, axis=0)
        g = lambda a: np.stack([a[:, 0], a[:, 2], -a[:, 1]], axis=1).astype(np.float32)  # local -> glTF Y-up
        pos, nn = g(pos), g(nn)
        idx = np.arange(len(pos), dtype=np.uint32)
        a_pos = push(pos, 34962); a_nrm = push(nn, 34962); a_idx = push(idx, 34963)
        accs.append({'bufferView': a_pos, 'componentType': 5126, 'count': len(pos), 'type': 'VEC3', 'min': pos.min(0).tolist(), 'max': pos.max(0).tolist()})
        accs.append({'bufferView': a_nrm, 'componentType': 5126, 'count': len(nn), 'type': 'VEC3'})
        accs.append({'bufferView': a_idx, 'componentType': 5125, 'count': len(idx), 'type': 'SCALAR'})
        base = len(accs) - 3
        meshes.append({'name': f'{mesh.family}/{name}', 'primitives': [{'attributes': {'POSITION': base, 'NORMAL': base + 1}, 'indices': base + 2, 'material': mat_ix[m]}],
                       'extras': {'part': name, **{k: (list(v) if isinstance(v, tuple) else v) for k, v in p['meta'].items()}, 'triangles': int(len(p['faces']))}})
        nodes.append({'name': f'{mesh.family}/{name}', 'mesh': len(meshes) - 1})
    root = {'name': mesh.family, 'children': list(range(len(nodes))), 'extras': mesh.provenance}
    nodes.append(root)
    doc = {'asset': {'version': '2.0', 'generator': 'dynacity.reference_facade_geometry', 'extras': mesh.provenance}, 'scene': 0,
           'scenes': [{'nodes': [len(nodes) - 1], 'extras': {'summary': mesh.summary()}}], 'nodes': nodes, 'meshes': meshes, 'materials': mats,
           'accessors': accs, 'bufferViews': views, 'buffers': [{'byteLength': offset}]}
    js = json.dumps(doc, separators=(',', ':'), default=float).encode()
    js += b' ' * ((-len(js)) % 4)
    blob = b''.join(bufs)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(js), b'JSON') + js + struct.pack('<I4s', len(blob), b'BIN\0') + blob


# ----------------------------------------------------------------------------- offline preview
def preview_image(mesh: FacadeMesh, azimuth_deg=35.0, elevation_deg=18.0, size=(640, 640), background=(228, 232, 236)):
    """Orthographic z-buffer render with Lambert shading. An offline sanity view of the
    component, not a photograph and not a native-engine capture. azimuth > 0 views from the front-right."""
    from PIL import Image
    tri, nrm, names = mesh.soup()
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    cam = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    f = -cam
    right = np.cross(f, [0, 0, 1.0]); right /= np.linalg.norm(right)
    up = np.cross(right, f)
    facing = nrm @ cam > 1e-6
    tri, nrm = tri[facing], nrm[facing]
    names = [n for n, k in zip(names, facing) if k]
    pts = tri.reshape(-1, 3)
    sx, sy = pts @ right, pts @ up
    lo, hi = np.array([sx.min(), sy.min()]), np.array([sx.max(), sy.max()])
    scale = 0.88 * min(size[0] / max(hi[0] - lo[0], 1e-6), size[1] / max(hi[1] - lo[1], 1e-6))
    ox = (size[0] - (hi[0] - lo[0]) * scale) / 2
    oy = (size[1] - (hi[1] - lo[1]) * scale) / 2
    xy = np.stack([(sx - lo[0]) * scale + ox, size[1] - ((sy - lo[1]) * scale + oy)], axis=1).reshape(-1, 3, 2)
    dep = (tri @ f)
    light = np.array([-0.4, -0.7, 0.6]); light /= np.linalg.norm(light)
    w, h = size
    zbuf = np.full((h, w), np.inf)
    rgb = np.empty((h, w, 3), np.uint8)
    rgb[:] = background
    mat = {n: p['meta']['material'] for n, p in mesh.parts.items()}
    for i in range(len(tri)):
        base = np.array(_rgb(mat[names[i]]), float)
        col = base * (0.45 + 0.55 * max(0.0, float(nrm[i] @ light)))
        if mat[names[i]] in _GLASS:
            col = 0.55 * col + 0.45 * np.array([150, 170, 185])
        a, b, c = xy[i]
        x0, x1 = int(max(0, math.floor(min(a[0], b[0], c[0])))), int(min(w - 1, math.ceil(max(a[0], b[0], c[0]))))
        y0, y1 = int(max(0, math.floor(min(a[1], b[1], c[1])))), int(min(h - 1, math.ceil(max(a[1], b[1], c[1]))))
        if x1 < x0 or y1 < y0:
            continue
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l1 = ((b[1] - c[1]) * (gx - c[0]) + (c[0] - b[0]) * (gy - c[1])) / den
        l2 = ((c[1] - a[1]) * (gx - c[0]) + (a[0] - c[0]) * (gy - c[1])) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        z = l1 * dep[i, 0] + l2 * dep[i, 1] + l3 * dep[i, 2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (z < sub - 1e-6)
        sub[win] = z[win]
        rgb[y0:y1 + 1, x0:x1 + 1][win] = col.astype(np.uint8)
    return Image.fromarray(rgb)
