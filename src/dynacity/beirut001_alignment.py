"""Measure and refine the Beirut 001 placement against independent footprints.

`beirut_overlay.register_rigid` lays the Beirut 001 model onto EPSG:32636 by
matching building-centre patterns against BBED, so BBED cannot also judge it.
Here the placed outlines are compared with a reference that shares nothing with
either source: Overture building footprints (almost all OpenStreetMap). The
steps are

1. match placed outlines to reference footprints one-to-one (overlap gate, then
   the largest overlap first) and take the centroid offset in metres,
2. fit a similarity (rotation, translation, scale) to those pairs with Huber
   IRLS, so wrongly paired or since-rebuilt buildings do not pull the fit,
3. score it on spatial block cross-validation, in which whole 500 m tiles are
   held out together (pairing and fitting are redone without them), against the current placement and against richer
   corrections (affine, quadratic); a richer correction is adopted only if it
   clearly wins on held-out blocks,
4. apply the winner to an existing pack (outlines, bridges, bridge triangles,
   ground grid) without the Rhino source, and re-run the BBED link matching.

The reference is itself traced from imagery, so a residual here is agreement
with OSM footprints, not distance from the true building; the reference's own
error is not separable from the model's. Nothing here is surveyed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.strtree import STRtree

from .beirut001_pack import Beirut001Pack
from .beirut_overlay import match_footprints
from .terrain import GroundModel
from .viewer import lonlat_to_utm

# Gates for pairing a placed outline with a reference footprint.
PAIR_MIN_IOU = 0.25
PAIR_MAX_CENTROID_M = 15.0
PAIR_AREA_RATIO = (0.4, 2.5)
TILE_M = 500.0
FOLDS = 5
# A richer-than-similarity correction must cut the held-out median by this much
# (absolute and relative) and win in all but one fold to be adopted.
ADOPT_MIN_GAIN_M = 0.3
ADOPT_MIN_GAIN_REL = 0.08
# A rotation or scale must lower the held-out median by this much to be kept, however significant.
MIN_STEP_GAIN_M = 0.05
POLY_SCALE_M = 1000.0

MODEL_ORDER = {"shift": 0, "similarity": 1, "affine": 1, "quadratic": 2}


# --------------------------------------------------------------------------- data


def load_reference(path: str | Path, *, zone: int = 36, north: bool = True) -> tuple[np.ndarray, list[str]]:
    """Reference footprints in EPSG:32636 (valid polygons) and the dataset each came from."""

    collection = json.loads(Path(path).read_text(encoding="utf-8"))
    geoms, datasets = [], []
    for feature in collection["features"]:
        if not feature.get("geometry"):
            continue
        geoms.append(shape(feature["geometry"]))
        sources = (feature.get("properties") or {}).get("sources") or [{}]
        datasets.append(str(sources[0].get("dataset", "unknown")))
    coords = shapely.get_coordinates(geoms)
    xy = np.array([lonlat_to_utm(lon, lat, zone, north) for lon, lat in coords]) if len(coords) else coords
    projected = shapely.make_valid(shapely.set_coordinates(np.array(geoms, dtype=object), xy))
    keep = np.array([g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty for g in projected])
    return projected[keep], [d for d, k in zip(datasets, keep) if k]


def load_bbed_footprints(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """BBED footprints (EPSG:32636) and their OBJECTIDs; geometry and OBJECTID only."""

    collection = json.loads(Path(path).read_text(encoding="utf-8"))
    crs = collection.get("crs", {}).get("properties", {}).get("name", "")
    if not crs.endswith("32636"):
        raise ValueError(f"BBED must be in EPSG:32636 (got {crs!r})")
    shapes, ids = [], []
    for feature in collection["features"]:
        if not feature.get("geometry"):
            continue
        geom = shape(feature["geometry"])
        if geom.is_valid and not geom.is_empty:
            shapes.append(geom)
            ids.append(int(feature["properties"]["OBJECTID"]))
    return np.array(shapes, dtype=object), np.array(ids, dtype=np.int64)


# ------------------------------------------------------------------- corrections


@dataclasses.dataclass(frozen=True)
class Correction:
    """A small displacement field laid over the placed model.

    `xy' = xy + basis(xy) @ coeffs`, with the basis `[1, u, v]` (order 1) or
    `[1, u, v, u², uv, v²]` (order 2), `u, v` the offsets from `center` in km.
    Order 0 is a shift; a similarity is an order-1 field with the similarity's
    structure. Everything is metres in EPSG:32636.
    """

    kind: str
    center: tuple[float, float]
    coeffs: np.ndarray
    order: int

    def displacement(self, xy: np.ndarray) -> np.ndarray:
        return _basis(np.asarray(xy, dtype=float), self.center, self.order) @ self.coeffs

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, dtype=float)
        return xy + self.displacement(xy)

    def inverse(self, xy: np.ndarray) -> np.ndarray:
        """Points that this correction moves onto `xy` (fixed-point iteration; the field is tiny)."""

        xy = np.asarray(xy, dtype=float)
        p = xy.copy()
        for _ in range(8):
            p = xy - self.displacement(p)
        return p

    def transform_geometry(self, geom):
        return shapely.transform(geom, self.__call__)

    def affine_parts(self) -> tuple[np.ndarray, np.ndarray] | None:
        """`(matrix, offset)` with `xy' = xy @ matrix.T + offset`, or None for a quadratic field."""

        if self.order > 1:
            return None
        o = np.array(self(np.zeros((1, 2))))[0]
        e = self(np.array([[1., 0.]]))[0] - o
        n = self(np.array([[0., 1.]]))[0] - o
        return np.column_stack([e, n]), o

    def to_json(self) -> dict:
        return {"kind": self.kind, "order": self.order, "center_xy": list(self.center),
                "coeffs": self.coeffs.tolist(), "basis_scale_m": POLY_SCALE_M}

    @classmethod
    def from_json(cls, data: dict) -> Correction:
        return cls(data["kind"], tuple(data["center_xy"]), np.asarray(data["coeffs"], dtype=float), int(data["order"]))

    def similarity_summary(self) -> dict:
        """Turn (degrees), scale and mean shift (m) of this correction about the data centre."""

        parts = self.affine_parts()
        if parts is None:
            return {}
        matrix, _ = parts
        c = np.array(self.center)
        shift = self(c[None])[0] - c
        return {"rotation_degrees": float(np.degrees(np.arctan2(matrix[1, 0] - matrix[0, 1], matrix[0, 0] + matrix[1, 1]))),
                "scale": float(np.sqrt(abs(np.linalg.det(matrix)))), "shift_at_center_m": shift.tolist(),
                "center_xy": list(self.center)}


def _basis(xy: np.ndarray, center, order: int) -> np.ndarray:
    u = (xy[:, 0] - center[0]) / POLY_SCALE_M
    v = (xy[:, 1] - center[1]) / POLY_SCALE_M
    cols = [np.ones_like(u)]
    if order >= 1:
        cols += [u, v]
    if order >= 2:
        cols += [u * u, u * v, v * v]
    return np.column_stack(cols)


def identity_correction(center=(0., 0.)) -> Correction:
    return Correction("identity", tuple(center), np.zeros((1, 2)), 0)


def _huber_weights(r: np.ndarray, floor_m: float = 0.5) -> np.ndarray:
    scale = 1.4826 * np.median(np.abs(r - np.median(r))) if len(r) else 0.
    k = max(1.345 * scale, floor_m)
    return np.where(r <= k, 1.0, k / np.maximum(r, 1e-12))


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, scale: bool = True, rotate: bool = True,
                   center=None, iterations: int = 25) -> Correction:
    """Huber-IRLS similarity taking `src` onto `dst` (weighted Umeyama about `center`)."""

    src, dst = np.asarray(src, float), np.asarray(dst, float)
    c = np.asarray(center if center is not None else np.median(src, axis=0), float)
    a, b = src - c, dst - c
    w = np.ones(len(a))
    s, rot, t = 1.0, np.eye(2), np.zeros(2)
    for _ in range(iterations):
        wa, wb = (w[:, None] * a).sum(0) / w.sum(), (w[:, None] * b).sum(0) / w.sum()
        if rotate:
            h = ((a - wa) * w[:, None]).T @ (b - wb)
            u, sv, vt = np.linalg.svd(h)
            d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
            rot = vt.T @ np.diag([1.0, d]) @ u.T
            if scale:
                s = (sv[0] + d * sv[1]) / (w[:, None] * (a - wa) ** 2).sum()
        t = wb - s * (rot @ wa)
        residual = np.linalg.norm(b - (s * a @ rot.T + t), axis=1)
        w_new = _huber_weights(residual)
        if np.abs(w_new - w).max() < 1e-4:
            w = w_new
            break
        w = w_new
    matrix = s * rot  # xy' - c = matrix (xy - c) + t
    coeffs = np.zeros((3, 2))
    coeffs[0] = t
    coeffs[1:] = (matrix - np.eye(2)).T  # rows: effect of u, v (km) on (x, y)
    coeffs[1:] *= POLY_SCALE_M
    kind = "similarity" if scale and rotate else ("rigid" if rotate else "shift")
    return Correction(kind, (float(c[0]), float(c[1])), coeffs, 1)


def fit_polynomial(src: np.ndarray, dst: np.ndarray, order: int, *, center=None, iterations: int = 25) -> Correction:
    """Huber-IRLS displacement field of the given order (0 shift, 1 affine, 2 quadratic)."""

    src, dst = np.asarray(src, float), np.asarray(dst, float)
    c = tuple(np.asarray(center if center is not None else np.median(src, axis=0), float))
    design = _basis(src, c, order)
    target = dst - src
    w = np.ones(len(src))
    coeffs = np.zeros((design.shape[1], 2))
    for _ in range(iterations):
        sw = np.sqrt(w)[:, None]
        coeffs = np.linalg.lstsq(design * sw, target * sw, rcond=None)[0]
        w_new = _huber_weights(np.linalg.norm(target - design @ coeffs, axis=1))
        if np.abs(w_new - w).max() < 1e-4:
            break
        w = w_new
    kind = {0: "shift", 1: "affine", 2: "quadratic"}[order]
    return Correction(kind, (float(c[0]), float(c[1])), coeffs, order)


def fit_model(name: str, src: np.ndarray, dst: np.ndarray, center=None) -> Correction:
    if name == "identity":
        return identity_correction(center if center is not None else np.median(src, axis=0))
    if name == "shift":
        return fit_polynomial(src, dst, 0, center=center)
    if name == "rigid":
        return fit_similarity(src, dst, scale=False, center=center)
    if name == "similarity":
        return fit_similarity(src, dst, scale=True, center=center)
    if name == "affine":
        return fit_polynomial(src, dst, 1, center=center)
    if name == "quadratic":
        return fit_polynomial(src, dst, 2, center=center)
    raise ValueError(f"unknown model {name!r}")


# ---------------------------------------------------------------------- matching


@dataclasses.dataclass(frozen=True)
class Pairs:
    """One-to-one pairs of placed outlines and reference footprints."""

    building: np.ndarray
    reference: np.ndarray
    iou: np.ndarray
    src: np.ndarray  # placed centroid
    dst: np.ndarray  # reference centroid

    def __len__(self) -> int:
        return len(self.building)

    def residual_m(self) -> np.ndarray:
        return np.linalg.norm(self.dst - self.src, axis=1)

    def subset(self, mask) -> Pairs:
        return Pairs(self.building[mask], self.reference[mask], self.iou[mask], self.src[mask], self.dst[mask])


def match_pairs(placed, reference, *, tree: STRtree | None = None, min_iou: float = PAIR_MIN_IOU,
                max_centroid_m: float = PAIR_MAX_CENTROID_M, area_ratio=PAIR_AREA_RATIO) -> Pairs:
    """One-to-one overlap matching, best IoU first, gated by IoU, centroid distance and area ratio."""

    placed = shapely.make_valid(np.asarray(placed, dtype=object))
    tree = tree or STRtree(reference)
    bi, ri = tree.query(placed, predicate="intersects")
    if not len(bi):
        z = np.zeros(0, dtype=np.int64)
        return Pairs(z, z, np.zeros(0), np.zeros((0, 2)), np.zeros((0, 2)))
    pb, pr = placed[bi], reference[ri]
    inter = shapely.area(shapely.intersection(pb, pr))
    area_b, area_r = shapely.area(pb), shapely.area(pr)
    iou = inter / np.maximum(area_b + area_r - inter, 1e-9)
    cb, cr = shapely.get_coordinates(shapely.centroid(pb)), shapely.get_coordinates(shapely.centroid(pr))
    dist = np.linalg.norm(cb - cr, axis=1)
    ratio = area_r / np.maximum(area_b, 1e-9)
    ok = (iou >= min_iou) & (dist <= max_centroid_m) & (ratio >= area_ratio[0]) & (ratio <= area_ratio[1])
    cand = np.flatnonzero(ok)
    cand = cand[np.argsort(-iou[cand], kind="stable")]
    used_b, used_r, chosen = set(), set(), []
    for k in cand:
        if bi[k] in used_b or ri[k] in used_r:
            continue
        used_b.add(bi[k])
        used_r.add(ri[k])
        chosen.append(k)
    chosen = np.array(chosen, dtype=np.int64)
    return Pairs(bi[chosen], ri[chosen], iou[chosen], cb[chosen], cr[chosen])


def percentiles(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"n": 0}
    return {"n": int(len(values)), "median_m": round(float(np.median(values)), 3),
            "p90_m": round(float(np.percentile(values, 90)), 3), "mean_m": round(float(values.mean()), 3)}


# -------------------------------------------------------------- fitting and CV


def _tile_key(xy: np.ndarray, tile_m: float) -> np.ndarray:
    tile = np.floor(np.asarray(xy, dtype=float) / tile_m).astype(np.int64)
    return tile[:, 0] * 1_000_000 + tile[:, 1]


def spatial_folds(xy: np.ndarray, *, tile_m: float = TILE_M, folds: int = FOLDS, seed: int = 0) -> np.ndarray:
    """Fold index per point; every `tile_m` tile lies wholly in one fold (balanced by point count)."""

    tiles, inverse, counts = np.unique(_tile_key(xy, tile_m), return_inverse=True, return_counts=True)
    order = np.random.default_rng(seed).permutation(len(tiles))
    order = order[np.argsort(-counts[order], kind="stable")]
    load = np.zeros(folds)
    assignment = np.zeros(len(tiles), dtype=np.int64)
    for t in order:  # largest tiles first, each to the lightest fold
        f = int(np.argmin(load))
        assignment[t] = f
        load[f] += counts[t]
    return assignment[inverse]


def centroids(shapes) -> np.ndarray:
    return shapely.get_coordinates(shapely.centroid(np.asarray(shapes, dtype=object)))


def fit_icp(placed, reference, tree, model: str, *, center=None, rounds: int = 5, min_iou: float = PAIR_MIN_IOU,
            max_centroid_m: float = PAIR_MAX_CENTROID_M) -> tuple[Correction, Pairs]:
    """Fit `model` by alternating pairing and fitting.

    Pairs found under the placement as it stands are fitted (Huber) from the
    *original* centroids to the reference's, the placement is updated, and the
    outlines are paired again. Pairing against the unmoved placement alone would
    favour buildings that already overlap and shrink the estimate toward no change.
    """

    placed = np.asarray(placed, dtype=object)
    own = centroids(placed)
    center = np.median(own, axis=0) if center is None else center
    correction = identity_correction(center)
    pairs = None
    for _ in range(rounds):
        moved = np.array([correction.transform_geometry(g) for g in placed], dtype=object) if pairs is not None else placed
        found = match_pairs(moved, reference, tree=tree, min_iou=min_iou, max_centroid_m=max_centroid_m)
        pairs = Pairs(found.building, found.reference, found.iou, own[found.building], found.dst)
        correction = fit_model(model, pairs.src, pairs.dst, center=center)
    return correction, pairs


def cross_validate(placed, reference, tree, models=("shift", "rigid", "similarity", "affine", "quadratic"),
                   *, tile_m: float = TILE_M, folds: int = FOLDS, seed: int = 0) -> dict:
    """Spatial block cross-validation of the whole procedure (pair, fit, apply).

    Buildings are split by `tile_m` tile into `folds` folds. For each fold the
    correction is fitted on the other folds' buildings only, then the held-out
    buildings are placed with it, paired with the reference with the same gates
    as the 'current' row, and scored. Medians and 90th percentiles are of the
    held-out centroid distances pooled over folds; `mean_best_iou_all_buildings`
    needs no pairing or gates at all."""

    placed = np.asarray(placed, dtype=object)
    fold = spatial_folds(centroids(placed), tile_m=tile_m, folds=folds, seed=seed)
    result = {"folds": {"count": folds, "tile_m": tile_m, "buildings_per_fold": np.bincount(fold, minlength=folds).tolist()}}
    for name in ("current", *models):
        distance, iou, best, per_fold = [], [], 0.0, []
        for f in range(folds):
            test = np.flatnonzero(fold == f)
            if name == "current":
                moved = placed[test]
            else:
                c, _ = fit_icp(placed[fold != f], reference, tree, name)
                moved = np.array([c.transform_geometry(g) for g in placed[test]], dtype=object)
            found = match_pairs(moved, reference, tree=tree)
            distance.append(found.residual_m())
            iou.append(found.iou)
            best += _mean_best_iou(moved, reference, tree) * len(test)
            per_fold.append(float(np.median(found.residual_m())))
        result[name] = {**percentiles(np.concatenate(distance)), "mean_iou_of_pairs": round(float(np.concatenate(iou).mean()), 3),
                        "mean_best_iou_all_buildings": round(best / len(placed), 4),
                        "fold_medians_m": [round(v, 3) for v in per_fold]}
    return result


def choose_model(cv: dict, boot: dict) -> tuple[str, str]:
    """The simplest model the evidence supports.

    Start from a shift. Add a rotation if its bootstrap interval excludes 0 and
    it lowers the held-out median by at least MIN_STEP_GAIN_M; then add a scale
    on the same two conditions. A parameter that is statistically significant
    but moves nothing held-out is not worth carrying. Finally, affine or
    quadratic replace the result only if one clearly beats it on held-out
    blocks (ADOPT_MIN_GAIN_M and ADOPT_MIN_GAIN_REL, and a better median in all
    but one fold)."""

    base, notes = "shift", []
    for name, significant, label in (("rigid", not boot["rotation_degrees"]["zero_inside_ci"], "rotation"),
                                     ("similarity", not boot["scale"]["one_inside_ci"], "scale")):
        gain = cv[base]["median_m"] - cv[name]["median_m"]
        if significant and gain >= MIN_STEP_GAIN_M:
            base = name
            notes.append(f"{label} significant and lowers the held-out median by {gain:.2f} m")
        else:
            notes.append(f"{label} {'significant but' if significant else 'not significant;'} held-out gain {gain:.3f} m, kept out")
    best, why = base, f"{base}: " + "; ".join(notes) + "; no richer correction clearly beat it on held-out blocks"
    ref = cv[base]
    for name in ("affine", "quadratic"):
        gain = ref["median_m"] - cv[name]["median_m"]
        wins = sum(a < b for a, b in zip(cv[name]["fold_medians_m"], ref["fold_medians_m"]))
        if gain >= ADOPT_MIN_GAIN_M and gain / ref["median_m"] >= ADOPT_MIN_GAIN_REL and wins >= cv["folds"]["count"] - 1:
            if best == base or cv[name]["median_m"] < cv[best]["median_m"]:
                best, why = name, (f"{name}: held-out median {cv[name]['median_m']} m vs {ref['median_m']} m for {base}, "
                                   f"wins {wins} of {cv['folds']['count']} folds")
    return best, why


def similarity_bootstrap(pairs: Pairs, *, tile_m: float = TILE_M, draws: int = 300, seed: int = 0) -> dict:
    """Rotation and scale of the similarity fit with tile-block bootstrap intervals."""

    tiles, inverse = np.unique(_tile_key(pairs.src, tile_m), return_inverse=True)
    members = [np.flatnonzero(inverse == t) for t in range(len(tiles))]
    rng = np.random.default_rng(seed)
    centre = np.median(pairs.src, axis=0)
    drawn = []
    for _ in range(draws):
        idx = np.concatenate([members[t] for t in rng.integers(0, len(tiles), len(tiles))])
        s = fit_similarity(pairs.src[idx], pairs.dst[idx], center=centre).similarity_summary()
        drawn.append((s["rotation_degrees"], s["scale"]))
    rot, sc = np.array(drawn).T
    point = fit_similarity(pairs.src, pairs.dst, center=centre).similarity_summary()
    r_lo, r_hi = np.percentile(rot, [2.5, 97.5])
    s_lo, s_hi = np.percentile(sc, [2.5, 97.5])
    return {"bootstrap": f"{draws} tile-block resamples ({int(tile_m)} m tiles)",
            "rotation_degrees": {"value": point["rotation_degrees"], "ci95": [float(r_lo), float(r_hi)],
                                 "zero_inside_ci": bool(r_lo <= 0 <= r_hi),
                                 "metres_per_km": float(np.radians(point["rotation_degrees"]) * 1000)},
            "scale": {"value": point["scale"], "ci95": [float(s_lo), float(s_hi)], "one_inside_ci": bool(s_lo <= 1 <= s_hi),
                      "metres_per_km": float((point["scale"] - 1) * 1000)}}


def gate_sensitivity(placed, reference, tree, model: str, center) -> list[dict]:
    """The fit under looser and tighter pairing gates: how much the answer depends on them."""

    out = []
    for iou, dist in ((0.25, 15.0), (0.4, 10.0), (0.5, 8.0)):
        c, p = fit_icp(placed, reference, tree, model, center=center, min_iou=iou, max_centroid_m=dist)
        out.append({"min_iou": iou, "max_centroid_m": dist, "pairs": int(len(p)),
                    "shift_at_center_m": [round(float(v), 3) for v in (c(np.array([center]))[0] - np.asarray(center))]})
    return out


def overlap_shift_check(placed, reference, tree, *, radius_m: float = 5.0, step_m: float = 0.5, sample: int = 6000,
                        seed: int = 0) -> dict:
    """Translation that maximises footprint overlap area, with no pairing or gates.

    A cross-check on the fitted shift: a random `sample` of model buildings is
    translated over a grid and the area it shares with the reference footprints
    is summed. Rotation and scale are not tested; this is the shift alone.
    """

    placed = shapely.make_valid(np.asarray(placed, dtype=object))
    rng = np.random.default_rng(seed)
    chosen = placed[rng.choice(len(placed), min(sample, len(placed)), replace=False)]
    bi, ri = tree.query(shapely.buffer(chosen, radius_m + 1))
    pb, pr = chosen[bi], reference[ri]
    steps = np.arange(-radius_m, radius_m + step_m / 2, step_m)
    grid = np.zeros((len(steps), len(steps)))
    for i, dy in enumerate(steps):
        for j, dx in enumerate(steps):
            moved = shapely.transform(pb, lambda c, dx=dx, dy=dy: c + (dx, dy))
            grid[i, j] = shapely.area(shapely.intersection(moved, pr)).sum()
    i, j = np.unravel_index(grid.argmax(), grid.shape)
    # Sub-step peak: centre of mass of the cells within 0.5% of the maximum.
    w = np.clip(grid - grid.max() * .995, 0, None)
    east, north = float((w.sum(0) * steps).sum() / w.sum()), float((w.sum(1) * steps).sum() / w.sum())
    zero = grid[len(steps) // 2, len(steps) // 2]
    return {"buildings_sampled": int(len(chosen)), "step_m": step_m, "radius_m": radius_m,
            "best_shift_east_north_m": [round(east, 2), round(north, 2)],
            "grid_peak_east_north_m": [float(steps[j]), float(steps[i])],
            "overlap_area_gain_vs_no_shift": round(float(grid.max() / zero - 1), 4)}


def residual_structure(pairs: Pairs, correction: Correction, *, tile_m: float = TILE_M) -> dict:
    """Mean residual vector per tile after the correction: is what is left spatially structured?"""

    r = pairs.dst - correction(pairs.src)
    _, inverse, counts = np.unique(_tile_key(pairs.src, tile_m), return_inverse=True, return_counts=True)
    cells, noise = [], []
    for t in np.flatnonzero(counts >= 20):
        m = inverse == t
        mean = r[m].mean(0)
        cells.append({"centre_xy": pairs.src[m].mean(0).round(0).tolist(), "n": int(m.sum()),
                      "mean_dx_m": round(float(mean[0]), 2), "mean_dy_m": round(float(mean[1]), 2),
                      "median_residual_m": round(float(np.median(np.linalg.norm(r[m], axis=1))), 2)})
        # Offset of a tile mean expected from noise alone.
        noise.append(np.linalg.norm(r[m].std(0)) / np.sqrt(m.sum()))
    mags = np.array([np.hypot(c["mean_dx_m"], c["mean_dy_m"]) for c in cells])
    return {"tile_m": tile_m, "tiles_with_20_pairs": len(cells),
            "median_tile_mean_offset_m": round(float(np.median(mags)), 3) if len(mags) else None,
            "p90_tile_mean_offset_m": round(float(np.percentile(mags, 90)), 3) if len(mags) else None,
            "max_tile_mean_offset_m": round(float(mags.max()), 3) if len(mags) else None,
            "median_noise_floor_m": round(float(np.median(noise)), 3) if len(noise) else None,
            "largest_tiles": sorted(cells, key=lambda c: -np.hypot(c["mean_dx_m"], c["mean_dy_m"]))[:10]}


def local_field_check(pairs: Pairs, *, bandwidth_m: float = 300.0, reach_m: float = 900.0, k: int = 40,
                      tile_m: float = TILE_M, folds: int = FOLDS, seed: int = 0) -> dict:
    """Would a piecewise (local) correction help on blocks it has not seen?

    A shift is fitted on the training folds, then each held-out pair is also moved
    by the Gaussian-weighted mean residual of the nearest training pairs
    (`bandwidth_m`, only from pairs within `reach_m`). If the structure seen in
    `residual_structure` were smooth enough to predict across a 500 m block, this
    would beat the plain shift on held-out tiles."""

    from scipy.spatial import cKDTree

    fold = spatial_folds(pairs.src, tile_m=tile_m, folds=folds, seed=seed)
    plain, local = np.zeros(len(pairs)), np.zeros(len(pairs))
    for f in range(folds):
        train, test = np.flatnonzero(fold != f), np.flatnonzero(fold == f)
        shift = fit_model("shift", pairs.src[train], pairs.dst[train])
        residual = pairs.dst[train] - shift(pairs.src[train])
        distance, index = cKDTree(pairs.src[train]).query(pairs.src[test], k=k, distance_upper_bound=reach_m)
        valid = np.isfinite(distance)
        weight = np.where(valid, np.exp(-0.5 * (np.where(valid, distance, 0) / bandwidth_m) ** 2), 0.0)
        field = (weight[..., None] * residual[np.where(valid, index, 0)]).sum(1) / np.maximum(weight.sum(1), 1e-9)[:, None]
        # Shrink toward no extra correction where the neighbours are few or far.
        field *= (weight.sum(1) / (weight.sum(1) + 1.0))[:, None]
        base = shift(pairs.src[test])
        plain[test] = np.linalg.norm(pairs.dst[test] - base, axis=1)
        local[test] = np.linalg.norm(pairs.dst[test] - (base + field), axis=1)
    return {"held_out_median_shift_only_m": round(float(np.median(plain)), 3),
            "held_out_median_shift_plus_local_field_m": round(float(np.median(local)), 3),
            "bandwidth_m": bandwidth_m, "reach_m": reach_m}


# ------------------------------------------------------------------ measurement


def measure_alignment(pack: Beirut001Pack, reference, bbed_shapes=None, *, seed: int = 0) -> dict:
    """The whole evidence chain for one pack: the report dict and the chosen correction."""

    placed = np.array(pack.buildings, dtype=object)
    tree = STRtree(reference)
    center = np.median(centroids(placed), axis=0)
    before = match_pairs(placed, reference, tree=tree)
    _, sim_pairs = fit_icp(placed, reference, tree, "similarity", center=center)
    boot = similarity_bootstrap(sim_pairs, seed=seed)
    cv = cross_validate(placed, reference, tree, seed=seed)
    choice, why = choose_model(cv, boot)
    chosen, final_pairs = fit_icp(placed, reference, tree, choice, center=center)
    moved = np.array([chosen.transform_geometry(g) for g in placed], dtype=object)
    after = match_pairs(moved, reference, tree=tree)
    report = {
        "reference": {"features": int(len(reference))},
        "pairing": {"gates": {"min_iou": PAIR_MIN_IOU, "max_centroid_m": PAIR_MAX_CENTROID_M, "area_ratio": list(PAIR_AREA_RATIO),
                              "rule": "intersecting, best IoU first, one-to-one"},
                    "model_buildings": int(len(placed))},
        "current_all_buildings": {"pairs": percentiles(before.residual_m()), "mean_iou_of_pairs": round(float(before.iou.mean()), 3),
                                  "mean_best_iou_all_buildings": round(_mean_best_iou(placed, reference, tree), 4),
                                  "median_offset_east_north_m": np.median(before.dst - before.src, axis=0).round(3).tolist()},
        "cross_validation": cv,
        "selected_model": choice, "selected_reason": why,
        "similarity_bootstrap": boot,
        "gate_sensitivity": gate_sensitivity(placed, reference, tree, choice, center),
        "overlap_shift_check": overlap_shift_check(placed, reference, tree, seed=seed),
        "refined_all_buildings": {"correction": chosen.to_json(), "summary": chosen.similarity_summary(),
                                  "pairs": percentiles(after.residual_m()), "mean_iou_of_pairs": round(float(after.iou.mean()), 3),
                                  "mean_best_iou_all_buildings": round(_mean_best_iou(moved, reference, tree), 4),
                                  "note": "in-sample (fitted on these buildings); held-out figures are in cross_validation"},
        "residual_structure": {**residual_structure(final_pairs, chosen), "local_field_check": local_field_check(final_pairs)},
    }
    if bbed_shapes is not None:
        report["bbed"] = bbed_residuals(placed, moved, bbed_shapes)
    return {"report": report, "correction": chosen}


def _mean_best_iou(shapes, reference, tree) -> float:
    shapes = shapely.make_valid(np.asarray(shapes, dtype=object))
    bi, ri = tree.query(shapes, predicate="intersects")
    inter = shapely.area(shapely.intersection(shapes[bi], reference[ri]))
    iou = inter / np.maximum(shapely.area(shapes[bi]) + shapely.area(reference[ri]) - inter, 1e-9)
    best = np.zeros(len(shapes))
    np.maximum.at(best, bi, iou)
    return float(best.mean())


def bbed_residuals(placed, moved, bbed_shapes) -> dict:
    """Centroid residuals of the model against BBED footprints before and after.

    BBED was used to infer the original placement, so the 'before' figure is a
    fit residual, not an independent measure."""

    tree = STRtree(bbed_shapes)
    b = match_pairs(placed, bbed_shapes, tree=tree, min_iou=0.4, max_centroid_m=10.0)
    a = match_pairs(moved, bbed_shapes, tree=tree, min_iou=0.4, max_centroid_m=10.0)
    return {"note": "BBED fitted the original placement; not independent",
            "before": {**percentiles(b.residual_m()), "mean_iou": round(float(b.iou.mean()), 3)},
            "after": {**percentiles(a.residual_m()), "mean_iou": round(float(a.iou.mean()), 3)},
            "gates": {"min_iou": 0.4, "max_centroid_m": 10.0}}


# ------------------------------------------------------------------ vertical


def vertical_check(ground: GroundModel, other: GroundModel) -> dict:
    """Pack ground minus another ground model over the cells where both are known (signed metres)."""

    rows, cols = ground.ground.shape
    cc, rr = np.meshgrid(np.arange(cols), np.arange(rows))
    x, y = ground.x0 + (cc.ravel() + .5) * ground.cell_m, ground.y0 - (rr.ravel() + .5) * ground.cell_m
    mine, theirs = ground.ground.ravel(), other.sample(x, y)
    ok = np.isfinite(mine) & np.isfinite(theirs)
    d = (mine - theirs)[ok]
    if not len(d):
        return {"cells": 0}
    corr = float(np.corrcoef(mine[ok], theirs[ok])[0, 1])
    # Tilt: does the offset change across the city?
    design = np.column_stack([np.ones(ok.sum()), (x[ok] - x[ok].mean()) / 1000, (y[ok] - y[ok].mean()) / 1000])
    tilt = np.linalg.lstsq(design, d, rcond=None)[0]
    return {"cells": int(ok.sum()), "median_diff_m": round(float(np.median(d)), 3),
            "median_abs_diff_m": round(float(np.median(np.abs(d))), 3),
            "p90_abs_diff_m": round(float(np.percentile(np.abs(d), 90)), 3),
            "iqr_diff_m": [round(float(v), 3) for v in np.percentile(d, [25, 75])],
            "correlation": round(corr, 4),
            "tilt_m_per_km": {"east": round(float(tilt[1]), 3), "north": round(float(tilt[2]), 3)},
            "note": "GLO-30 here is an approximate bare-earth filter of a surface model, not ground truth; "
                    "this checks the vertical frame of the model, not its accuracy."}


# ---------------------------------------------------------------------- apply


def resample_ground(ground: GroundModel, correction: Correction) -> GroundModel:
    """Re-grid the ground north-up after `correction` moved it (NaN-aware bilinear; coast stays coast)."""

    rows, cols = ground.ground.shape
    edge = np.array([[ground.x0, ground.y0], [ground.x0 + cols * ground.cell_m, ground.y0],
                     [ground.x0, ground.y0 - rows * ground.cell_m],
                     [ground.x0 + cols * ground.cell_m, ground.y0 - rows * ground.cell_m]])
    moved = correction(edge)
    cell = ground.cell_m
    x0, y0 = math.floor(moved[:, 0].min() / cell) * cell, math.ceil(moved[:, 1].max() / cell) * cell
    n_cols, n_rows = int(math.ceil((moved[:, 0].max() - x0) / cell)), int(math.ceil((y0 - moved[:, 1].min()) / cell))
    cc, rr = np.meshgrid(np.arange(n_cols), np.arange(n_rows))
    target = np.column_stack([x0 + (cc.ravel() + .5) * cell, y0 - (rr.ravel() + .5) * cell])
    source = correction.inverse(target)
    known = np.isfinite(ground.ground)
    filled = GroundModel(ground.crs, ground.x0, ground.y0, cell, np.where(known, ground.ground, 0.0))
    weight = GroundModel(ground.crs, ground.x0, ground.y0, cell, known.astype(float))
    value = filled.sample(source[:, 0], source[:, 1])
    w = weight.sample(source[:, 0], source[:, 1])
    out = np.where((w >= .5) & np.isfinite(value), value / np.maximum(w, 1e-9), np.nan).reshape(n_rows, n_cols)
    return GroundModel(ground.crs, float(x0), float(y0), float(cell), out,
                       ground.source.replace("inferred alignment", "alignment refined on OSM footprints"))


def apply_correction(pack: Beirut001Pack, correction: Correction, bbed_path: str | Path | None = None,
                     *, report: dict | None = None, reference_name: str = "") -> Beirut001Pack:
    """A new pack with the correction applied to outlines, bridges, bridge triangles and ground grid,
    BBED links re-matched, and registration metadata updated. Heights and levels are untouched."""

    buildings = [correction.transform_geometry(g) for g in pack.buildings]
    bridges = [correction.transform_geometry(g) for g in pack.bridges]
    triangles = pack.bridge_triangles.copy()
    if len(triangles):
        flat = correction(triangles[..., :2].reshape(-1, 2))
        triangles[..., :2] = flat.reshape(-1, 3, 2)
    ground = resample_ground(pack.ground, correction) if pack.ground is not None else None
    meta = json.loads(json.dumps(pack.meta))
    bounds = shapely.total_bounds(buildings)
    meta["origin_xy"] = [float(bounds[0]), float(bounds[1])]
    objectids, link_iou, link_distance = pack.bbed_objectid, pack.link_iou, pack.link_distance_m
    if bbed_path is not None:
        bbed_shapes, bbed_ids = load_bbed_footprints(bbed_path)
        links = match_footprints(buildings, list(bbed_shapes))
        objectids = np.array([int(bbed_ids[link["bbed_index"]]) if "bbed_index" in link else -1 for link in links], dtype=np.int64)
        link_iou = np.array([link.get("iou", 0.) for link in links])
        link_distance = np.array([link.get("centroid_distance_m", np.nan) for link in links])
        before_linked = int(meta["counts"]["linked"])
        meta["counts"]["linked"] = int((objectids >= 0).sum())
        meta["counts"]["unlinked"] = len(buildings) - meta["counts"]["linked"]
        meta["counts"]["linked_before_refinement"] = before_linked
    registration = meta["registration"]
    registration["initial"] = {k: registration[k] for k in ("method", "rotation_degrees", "rotation_matrix", "translation_xy_m") if k in registration}
    parts = correction.affine_parts()
    if parts is not None:
        old = np.array(registration["rotation_matrix"]), np.array(registration["translation_xy_m"])
        # model -> initial placement -> corrected placement
        probe = np.array([[0., 0.], [1., 0.], [0., 1.]]) @ old[0].T + old[1]
        out = correction(probe)
        matrix = np.column_stack([out[1] - out[0], out[2] - out[0]])
        registration.update(method=f"{correction.kind} refinement on Overture footprints over rigid centroid-pattern registration; inferred, not surveyed",
                            rotation_matrix=matrix.tolist(), translation_xy_m=out[0].tolist(),
                            rotation_degrees=float(np.degrees(np.arctan2(matrix[1, 0] - matrix[0, 1], matrix[0, 0] + matrix[1, 1]))),
                            scale=float(np.sqrt(abs(np.linalg.det(matrix)))))
    else:
        registration["method"] = f"{correction.kind} refinement on Overture footprints over rigid registration; inferred, not surveyed"
    meta["alignment"] = _alignment_meta(correction, report, reference_name, meta["counts"])
    meta["matching"] += " Re-run after the alignment refinement." if bbed_path is not None else ""
    return dataclasses.replace(
        pack, meta=meta, buildings=buildings, bridges=bridges, bridge_triangles=triangles, ground=ground,
        bbed_objectid=objectids, link_iou=link_iou, link_distance_m=link_distance)


def _alignment_meta(correction: Correction, report: dict | None, reference_name: str, counts: dict) -> dict:
    """What the viewer needs to word the alignment honestly."""

    out = {"schema": "dynacity-beirut001-alignment/1", "reference": reference_name or "Overture Maps building footprints (OpenStreetMap)",
           "correction": correction.to_json(),
           "wording": "aligned to OSM footprints"}
    if report:
        cv = report["cross_validation"][report["selected_model"]]
        out.update(
            model=report["selected_model"],
            heldout_median_m=cv["median_m"], heldout_p90_m=cv["p90_m"],
            heldout_median_before_m=report["cross_validation"]["current"]["median_m"],
            heldout_p90_before_m=report["cross_validation"]["current"]["p90_m"],
            validation=f"{report['cross_validation']['folds']['count']}-fold spatial block cross-validation, "
                       f"{int(report['cross_validation']['folds']['tile_m'])} m tiles",
            pairs=cv["n"])
    out["bbed_links"] = counts.get("linked")
    out["bbed_links_before"] = counts.get("linked_before_refinement")
    return out


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ pipeline


def link_changes(before: Beirut001Pack, after: Beirut001Pack) -> dict:
    """How the BBED links moved when the buildings did."""

    a, b = before.bbed_objectid, after.bbed_objectid
    return {"before": int((a >= 0).sum()), "after": int((b >= 0).sum()),
            "kept_same_record": int(((a >= 0) & (a == b)).sum()),
            "lost": int(((a >= 0) & (b < 0)).sum()), "gained": int(((a < 0) & (b >= 0)).sum()),
            "changed_record": int(((a >= 0) & (b >= 0) & (a != b)).sum())}


def _same_outlines(a: Beirut001Pack, b: Beirut001Pack, tol_m: float = 0.01) -> bool:
    if len(a.buildings) != len(b.buildings) or not np.array_equal(a.building_ids, b.building_ids):
        return False
    return bool(np.abs(centroids(a.buildings) - centroids(b.buildings)).max() <= tol_m)


def refine_beirut001(pack_path, reference_path, output, report_path, *, bbed_path=None, terrain_path=None,
                     structures_path=None, structures_output=None, display_path=None, display_output=None,
                     seed: int = 0) -> dict:
    """Measure the alignment of `pack_path` against `reference_path`, write the refined pack and a report.

    The correction found on `pack_path` is applied identically to the structures
    pack and the display JSON when given (after checking they hold the same
    outlines). Inputs are never overwritten. Needs no Rhino source."""

    inputs = [pack_path, structures_path, display_path]
    outputs = {"pack": output, "structures pack": structures_output, "display JSON": display_output, "report": report_path}
    resolved_in = {Path(v).resolve() for v in inputs if v}
    for name, v in outputs.items():
        if v and Path(v).resolve() in resolved_in:
            raise ValueError(f"{name} output {v} would overwrite an input")
    if (structures_path and not structures_output) or (display_path and not display_output):
        raise ValueError("a structures pack or display JSON input needs its own output path")

    pack = Beirut001Pack.load(pack_path)
    if pack.meta.get("alignment"):
        raise ValueError("this pack is already refined; refine the original pack instead")
    reference, datasets = load_reference(reference_path)
    bbed_shapes = load_bbed_footprints(bbed_path)[0] if bbed_path else None
    measured = measure_alignment(pack, reference, bbed_shapes, seed=seed)
    report, correction = measured["report"], measured["correction"]
    report["reference"].update(
        path=Path(reference_path).name, datasets={name: datasets.count(name) for name in sorted(set(datasets))},
        sha256=file_sha256(reference_path),
        caveat="Overture footprints are traced from imagery; their own position error is not separable from the model's.")

    name = "Overture Maps building footprints (OpenStreetMap)"
    refined = apply_correction(pack, correction, bbed_path, report=report, reference_name=name)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    refined.save(output)
    report["bbed_links"] = {**link_changes(pack, refined), "rule": pack.meta["matching"]}
    report["registration"] = {
        "initial": pack.meta["registration"],
        "refined": {k: refined.meta["registration"].get(k) for k in
                    ("method", "rotation_degrees", "scale", "rotation_matrix", "translation_xy_m")}}
    if terrain_path:
        other = GroundModel.load(terrain_path)
        report["vertical"] = {"reference": Path(terrain_path).name, "before": vertical_check(pack.ground, other),
                              "after_resample": vertical_check(refined.ground, other)}

    files = {"pack": {"input": str(pack_path), "output": str(output), "sha256": file_sha256(output)}}
    refined_structures = None
    if structures_path:
        structures = Beirut001Pack.load(structures_path)
        if not _same_outlines(pack, structures):
            raise ValueError("the structures pack holds different building outlines; refine it on its own")
        refined_structures = apply_correction(structures, correction, bbed_path, report=report, reference_name=name)
        Path(structures_output).parent.mkdir(parents=True, exist_ok=True)
        refined_structures.save(structures_output)
        files["structures_pack"] = {"input": str(structures_path), "output": str(structures_output),
                                    "bridge_triangles": int(len(structures.bridge_triangles)),
                                    "sha256": file_sha256(structures_output)}
    if display_path:
        from .beirut001_pack import viewer_payload
        original = json.loads(Path(display_path).read_text(encoding="utf-8"))
        bbed_collection = json.loads(Path(bbed_path).read_text(encoding="utf-8")) if bbed_path else None
        payload = viewer_payload(refined_structures or refined, bbed_collection)
        files["display_json"] = {
            "input": str(display_path), "output": str(display_output),
            "buildings_original_vs_regenerated": [len(original["buildings"]), len(payload["buildings"])],
            "matched_original_vs_regenerated": [original.get("matched"), payload["matched"]],
            "bridge_mesh_in_original": bool(original.get("bridge_mesh")), "bridge_mesh_regenerated": bool(payload.get("bridge_mesh"))}
        Path(display_output).parent.mkdir(parents=True, exist_ok=True)
        Path(display_output).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    report["files"] = files
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
