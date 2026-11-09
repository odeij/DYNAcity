"""Photo-backed facade massing: registration check and a provenance-tagged massing spec.

A building's massing (floors, storefront plinth, balcony rows, recess depth) is
described per facade. Every value carries its basis: ``observed`` values name the
source photo and were read from it, ``estimated`` values were inferred from
footprint/height and say so. Nothing here generates imagery or edits meshes; it
decides what geometry a building should have and whether a camera pose is good
enough to compare against a photo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

BASES = ('observed', 'estimated')
FEATURES = ('floors', 'plinth_height_m', 'balcony_rows', 'recess_depth_m', 'bay_width_m')


def project_points(points_m, camera_m, yaw_deg, pitch_deg, fov_h_deg, image_size_px):
    """Pinhole projection of world points (x east, y north, z up, metres).

    ``yaw_deg`` is a compass heading (0 = +y north, clockwise). Returns an (N, 2)
    array of pixel coordinates, NaN for points behind the camera.
    """
    p = np.asarray(points_m, float) - np.asarray(camera_m, float)
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    fwd = np.array([math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch)])
    right = np.array([math.cos(yaw), -math.sin(yaw), 0.])
    up = np.cross(right, fwd)
    depth = p @ fwd
    w, h = image_size_px
    f = (w / 2) / math.tan(math.radians(fov_h_deg) / 2)
    with np.errstate(divide='ignore', invalid='ignore'):
        u = w / 2 + f * (p @ right) / depth
        v = h / 2 - f * (p @ up) / depth
    out = np.stack([u, v], axis=1)
    out[depth <= 0] = np.nan
    return out


def registration_error_px(model_points_px, photo_points_px):
    """Median/max pixel distance between projected model landmarks and the same
    landmarks clicked/measured in the photo. Geometry claims need this to be small."""
    a, b = np.asarray(model_points_px, float), np.asarray(photo_points_px, float)
    if a.shape != b.shape or a.ndim != 2 or len(a) < 3:
        raise ValueError('Need at least 3 matching landmark pairs')
    d = np.linalg.norm(a - b, axis=1)
    if np.isnan(d).any():
        raise ValueError('A landmark falls behind the camera')
    return {'median_px': float(np.median(d)), 'max_px': float(d.max()), 'n': int(len(d))}


def registration_ok(report, image_size_px, max_median_frac=0.01, max_max_frac=0.025):
    """Accept only when error is a small fraction of image width."""
    w = image_size_px[0]
    return report['median_px'] <= max_median_frac * w and report['max_px'] <= max_max_frac * w


@dataclass
class Value:
    value: float
    basis: str
    photo_ids: tuple = ()

    def __post_init__(self):
        if self.basis not in BASES:
            raise ValueError(f'basis must be one of {BASES}')
        if self.basis == 'observed' and not self.photo_ids:
            raise ValueError('observed values must name their source photo')


@dataclass
class FacadeMassing:
    source_id: str
    facade_index: int
    features: dict = field(default_factory=dict)

    def set(self, name, value, basis, photo_ids=()):
        if name not in FEATURES:
            raise ValueError(f'unknown feature {name}')
        self.features[name] = Value(float(value), basis, tuple(photo_ids))

    def to_json(self):
        return {'source_id': self.source_id, 'facade_index': self.facade_index,
                'features': {k: {'value': v.value, 'basis': v.basis, 'photo_ids': list(v.photo_ids)}
                             for k, v in sorted(self.features.items())}}


def estimate_massing(source_id, facade_index, height_m, wall_length_m, observed=None, photo_ids=()):
    """Fill a facade's massing: observed values win, the rest are labelled estimates.

    Defaults are generic Beirut mid-rise assumptions (3.1 m storey, 4.2 m
    commercial ground floor) and are always tagged ``estimated``.
    """
    m = FacadeMassing(source_id, facade_index)
    observed = dict(observed or {})
    plinth = observed.pop('plinth_height_m', None)
    for name, value in observed.items():
        m.set(name, value, 'observed', photo_ids)
    if plinth is not None:
        m.set('plinth_height_m', plinth, 'observed', photo_ids)
    else:
        m.set('plinth_height_m', 4.2 if height_m > 6 else 0., 'estimated')
    if 'floors' not in m.features:
        m.set('floors', max(1, round((height_m - m.features['plinth_height_m'].value) / 3.1) + (1 if height_m > 6 else 0)),
              'estimated')
    for name, default in (('balcony_rows', 0), ('recess_depth_m', 0.), ('bay_width_m', 3.)):
        if name not in m.features:
            m.set(name, default, 'estimated')
    return m
