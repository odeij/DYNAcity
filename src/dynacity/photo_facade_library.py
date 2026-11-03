"""Reusable Beirut appearance library from the cached street-photo evidence.

Offline and deterministic: no network, no LLM, no Unreal. Every cached photo is
checked (existence, checksum, decode, exposure, sharpness) and scanned for clean
wall-like tiles (sky, vegetation, road, vehicles and dark/blown areas excluded).
Tiles are grouped into a few reference families by conservative colour rules that
were chosen after looking at the photos. Colours are apparent, sun-lit values,
never surveyed albedo, and a photo candidate is never a confirmed building identity.

Families carry reusable material parameters plus procedurally generated, tileable
albedo/roughness/normal maps. The maps are derived from family statistics, not
cropped from photos, so no street photo is pasted onto a wall. Building jobs pick a
family per building from height/footprint typology and nearby clean photo evidence.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

LIBRARY_SCHEMA = "dynacity-appearance-library/1"
JOBS_SCHEMA = "dynacity-building-appearance-jobs/1"
ANALYSIS_WIDTH = 960
ANALYSIS_VERSION = 3  # bump when tile rules change; invalidates the analysis cache
SKY_MATCH_DISTANCE = 0.15  # RGB distance to the photo's own sky colour that marks a bright tile as sky/overcast
TILE = 48
BLOCK = 2  # tiles per reference-crop side
ROW_RANGE = (0.04, 0.62)  # image rows where wall-like tiles are accepted (road/bonnet below)
MIN_SHARPNESS = 2.0
MIN_LUMA, MAX_LUMA = 0.18, 0.88
MIN_CLEAN_TILES = 6
LGB_SOURCE_ID = "beirut001:d31eb718-f947-49d5-b82d-14838045a9c7"
# Photos reviewed as showing the existing LGB tower: not used as family references, so the
# photo-authored tower is never transferred to other buildings.
LGB_REFERENCE_PHOTOS = frozenset({"1288771577", "1288771293"})
# Photos whose clean tiles were visually found to be windscreen speckle or netting, not wall.
VISUALLY_EXCLUDED_PHOTOS = {"1288656605": "windscreen speckle / construction netting; tiles are not wall texture"}
DEFAULT_EXCLUDED_SOURCES = {
    LGB_SOURCE_ID: ("confirmed_historical", "lgb_photo_authored_preserved"),
    "beirut001:163e027c-d254-482c-9c91-58de29e1f1bd": ("unchanged", "captured_real_building_ac_726"),
    "beirut001:3bdd34b3-d625-4c5f-bc39-48a6208d79d8": ("unchanged", "capture_overlap_candidate_unknown_detail"),
    "beirut001:efa2b104-1b1f-4814-81d7-adfa0d50220a": ("unchanged", "capture_overlap_candidate_unknown_detail"),
}
# Grey is the catch-all neutral bin (retaining walls, tunnels, shade), so its tiles count less as local evidence.
NEUTRAL_SHARE_WEIGHT = {"weathered_grey_concrete_frame": 0.4}
MIN_ACCEPTED_PATCHES = 4  # fewer curated patches than this leaves a family flagged uncertain
CURATED_SCHEMA = "dynacity-curated-facade-references/1"
MIN_BUILDING_HEIGHT_M = 3.5
MIN_FOOTPRINT_M2 = 12.0

# Families chosen after inspecting the 412 photos (contact sheets + originals). Typology
# fields are visual estimates from those photos, not measurements.
FAMILIES = [
    {
        "id": "cream_limestone_balcony_midrise",
        "name": "Cream limestone/plaster mid-rise with balcony strips",
        "cues": "Warm cream rendered or stone-faced residential blocks; continuous slab-edge balconies; "
                "regular punched or strip windows; typical of the Corniche, Ras Beirut and Hamra frontages.",
        "bay_width_m": 3.4, "floor_height_m": 3.1, "window_ratio": 0.30, "balcony": "slab_strip",
        "trim_rgb": [0.80, 0.76, 0.66], "glass_rgb": [0.16, 0.19, 0.22],
        "height_prior": {"low": 0.16, "mid": 0.34, "high": 0.28, "tower": 0.26},
    },
    {
        "id": "weathered_grey_concrete_frame",
        "name": "Weathered grey concrete frame with recessed balconies",
        "cues": "Exposed or thinly rendered grey concrete, dark recessed balcony bays and unfinished/stained "
                "frames; common for 1960s-90s infill and some stalled construction.",
        "bay_width_m": 3.6, "floor_height_m": 3.0, "window_ratio": 0.34, "balcony": "recessed",
        "trim_rgb": [0.50, 0.50, 0.48], "glass_rgb": [0.10, 0.11, 0.12],
        "height_prior": {"low": 0.28, "mid": 0.30, "high": 0.22, "tower": 0.14},
    },
    {
        "id": "blue_grey_glazed_tower",
        "name": "Blue-grey glazed or curtain-wall tower",
        "cues": "Reflective blue-grey glazing with pale or dark mullion grid; modern commercial and "
                "residential towers; little visible wall.",
        "bay_width_m": 1.5, "floor_height_m": 3.6, "window_ratio": 0.72, "balcony": "none",
        "trim_rgb": [0.58, 0.61, 0.64], "glass_rgb": [0.28, 0.36, 0.44],
        "height_prior": {"low": 0.0, "mid": 0.06, "high": 0.18, "tower": 0.38},
    },
    {
        "id": "ochre_terracotta_masonry_lowrise",
        "name": "Ochre / terracotta masonry low-rise",
        "cues": "Warm sandstone, ochre or terracotta-washed masonry; smaller punched or arched openings with "
                "shutters; older Achrafieh/Gemmayze/Mar Mikhael type fabric.",
        "bay_width_m": 3.0, "floor_height_m": 3.4, "window_ratio": 0.18, "balcony": "small_projecting",
        "trim_rgb": [0.72, 0.60, 0.44], "glass_rgb": [0.14, 0.15, 0.15],
        "height_prior": {"low": 0.30, "mid": 0.16, "high": 0.04, "tower": 0.02},
    },
    {
        "id": "white_panel_commercial",
        "name": "White / pale panel commercial block",
        "cues": "Pale smooth cladding or render with dark glazing bands and storefront strips; shops, "
                "offices, supermarkets and institutional blocks.",
        "bay_width_m": 4.2, "floor_height_m": 3.6, "window_ratio": 0.38, "balcony": "none",
        "trim_rgb": [0.74, 0.74, 0.72], "glass_rgb": [0.13, 0.15, 0.17],
        "height_prior": {"low": 0.22, "mid": 0.12, "high": 0.18, "tower": 0.16},
    },
    {
        "id": "reddish_brown_framed_residential",
        "name": "Reddish-brown framed residential with dark balcony bays",
        "cues": "Brown-red painted frames or cladding with dark recessed balconies; seen on several "
                "residential towers. Colour cue only: the photo-authored LGB tower is excluded.",
        "bay_width_m": 3.4, "floor_height_m": 3.1, "window_ratio": 0.28, "balcony": "recessed",
        "trim_rgb": [0.52, 0.38, 0.30], "glass_rgb": [0.12, 0.12, 0.13],
        "height_prior": {"low": 0.02, "mid": 0.02, "high": 0.10, "tower": 0.04},
    },
]
FAMILY_IDS = [f["id"] for f in FAMILIES]


# --------------------------------------------------------------------------- helpers
def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def stable_unit(*parts) -> float:
    """Deterministic number in [0, 1) from text parts."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def _rgb_to_hsv_arrays(rgb: np.ndarray):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(-1), rgb.min(-1)
    delta = mx - mn
    sat = np.where(mx > 1e-6, delta / np.maximum(mx, 1e-6), 0)
    hue = np.zeros_like(mx)
    nz = delta > 1e-6
    rc = np.where(nz & (mx == r), ((g - b) / np.maximum(delta, 1e-6)) % 6, 0)
    gc = np.where(nz & (mx == g) & (mx != r), (b - r) / np.maximum(delta, 1e-6) + 2, 0)
    bc = np.where(nz & (mx == b) & (mx != r) & (mx != g), (r - g) / np.maximum(delta, 1e-6) + 4, 0)
    hue = (rc + gc + bc) * 60.0
    return hue, sat, mx


def _luma(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


# ------------------------------------------------------------------ photo analysis
def load_analysis_image(path):
    with Image.open(path) as image:
        original = image.size
        image.draft("RGB", (ANALYSIS_WIDTH, ANALYSIS_WIDTH))
        image = image.convert("RGB")
        scale = ANALYSIS_WIDTH / image.width
        if scale < 1:
            image = image.resize((ANALYSIS_WIDTH, round(image.height * scale)), Image.LANCZOS)
        return original, np.asarray(image, dtype=np.float32) / 255.0


def laplacian_variance(luma: np.ndarray) -> float:
    lap = (-4 * luma[1:-1, 1:-1] + luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:])
    return float(lap.var() * 255 * 255 / 100)


def sky_mask(rgb: np.ndarray, hue, sat, val) -> np.ndarray:
    """Sky-like pixels connected to the top of each column (clouds included)."""
    blue_sky = (rgb[..., 2] >= rgb[..., 0] - 0.02) & (val > 0.45) & (sat > 0.06) & (hue > 170) & (hue < 260)
    haze = (val > 0.82) & (sat < 0.18)
    like = blue_sky | haze
    # Overcast scenes: the top rows are pale grey and uniform, but too dark/neutral for the rules above.
    top = np.median(rgb[: max(2, rgb.shape[0] // 33)].reshape(-1, 3), axis=0)
    top_hue, top_sat, top_val = [float(a) for a in _rgb_to_hsv_arrays(top.astype(np.float32))]
    if top_val > 0.68 and top_sat < 0.2:
        like = like | (np.linalg.norm(rgb - top, axis=-1) < 0.11)
    return np.cumprod(like, axis=0).astype(bool)


def vegetation_mask(rgb, hue, sat) -> np.ndarray:
    return ((rgb[..., 1] > rgb[..., 0] + 0.03) & (rgb[..., 1] > rgb[..., 2] + 0.03)) | (
        (hue > 55) & (hue < 170) & (sat > 0.22))


def classify_tile(median_rgb, dark_fraction=0.0):
    """Family id for a clean tile from its apparent colour, or None when ambiguous."""
    hue, sat, val = [float(a) for a in _rgb_to_hsv_arrays(np.asarray(median_rgb, dtype=np.float32))]
    if 0.2 < val < 0.55 and 182 <= hue <= 258 and sat > 0.13:
        return "blue_grey_glazed_tower"
    if (hue < 22 or hue > 340) and sat > 0.28 and val < 0.58:
        return "reddish_brown_framed_residential"
    if 8 <= hue <= 42 and sat > 0.30 and val >= 0.45:
        return "ochre_terracotta_masonry_lowrise"
    if 22 <= hue <= 65 and 0.09 < sat <= 0.32 and val > 0.5:
        return "cream_limestone_balcony_midrise"
    if sat <= 0.09 and val > 0.74:
        return "white_panel_commercial"
    if sat < 0.15 and 0.28 <= val <= 0.72:
        return "weathered_grey_concrete_frame"
    return None


def periodicity(luma_block: np.ndarray) -> tuple[float, float]:
    """(lag px, strength) of the strongest horizontal periodicity of vertical edges."""
    profile = np.abs(np.diff(luma_block, axis=1)).mean(0)
    profile = profile - profile.mean()
    denom = float((profile ** 2).sum())
    if denom < 1e-9:
        return 0.0, 0.0
    best, lag = 0.0, 0
    for k in range(6, len(profile) // 2):
        value = float((profile[:-k] * profile[k:]).sum() / denom)
        if value > best:
            best, lag = value, k
    return float(lag), best


def road_gains(rgb: np.ndarray, strength=0.7):
    """Per-channel gains that pull the road surface (assumed neutral) towards grey.

    The frames are shot through a windscreen with a cyan-green cast. Clamped and partial,
    so colours stay approximate; the gains are stored in the photo report.
    """
    h, w, _ = rgb.shape
    road = np.median(rgb[int(h * 0.75):int(h * 0.9), int(w * 0.3):int(w * 0.7)].reshape(-1, 3), axis=0)
    if not 0.10 < float(road.mean()) < 0.65:
        return np.ones(3, np.float32)
    gains = np.clip(road.mean() / np.maximum(road, 1e-3), 0.85, 1.25)
    return (1 + strength * (gains - 1)).astype(np.float32)


def analyse_photo(record: dict, extra_paths=()) -> dict:
    """Process one catalog photo. Returns a JSON-safe report plus ``_tiles`` for grouping."""
    out = {"photo_id": record["id"], "path": record["path"], "capture_date": record.get("capture_date"),
           "expected_sha256": record["sha256"], "expected_bytes": record.get("bytes")}
    path = Path(record["path"])
    if not path.is_file():
        for alt in extra_paths:
            if (Path(alt) / record["file"]).is_file():
                path = Path(alt) / record["file"]
                out["path"] = str(path)
                break
    out["exists"] = path.is_file()
    if not out["exists"]:
        out["usable"], out["reject_reasons"] = False, ["missing_file"]
        return out
    out["bytes"] = path.stat().st_size
    out["sha256"] = sha256_file(path)
    out["checksum_ok"] = out["sha256"] == record["sha256"]
    try:
        original, rgb = load_analysis_image(path)
    except Exception as exc:  # corrupt/undecodable file
        out.update(decode_ok=False, decode_error=str(exc), usable=False, reject_reasons=["decode_failed"])
        return out
    out.update(decode_ok=True, image_size_px=list(original), analysis_size_px=[rgb.shape[1], rgb.shape[0]],
               catalog_image_size_px=record.get("image_size_px"),
               camera_xy_m=record.get("camera_xy_m"), heading_grid_deg=record.get("heading_grid_deg"))
    gains = road_gains(rgb)
    out["road_neutral_gains"] = [round(float(g), 3) for g in gains]
    rgb = np.clip(rgb * gains, 0, 1)
    luma = _luma(rgb)
    out["mean_luma"] = round(float(luma.mean()), 3)
    out["luma_p05_p95"] = [round(float(np.percentile(luma, 5)), 3), round(float(np.percentile(luma, 95)), 3)]
    out["blown_fraction"] = round(float((luma > 0.97).mean()), 3)
    out["dark_fraction"] = round(float((luma < 0.08).mean()), 3)
    out["sharpness"] = round(laplacian_variance(luma), 1)
    hue, sat, val = _rgb_to_hsv_arrays(rgb)
    sky, veg = sky_mask(rgb, hue, sat, val), vegetation_mask(rgb, hue, sat)
    out["sky_fraction"], out["vegetation_fraction"] = round(float(sky.mean()), 3), round(float(veg.mean()), 3)
    ok_pixel = ~sky & ~veg & (val > MIN_LUMA) & (val < 0.97)
    sky_ref = None
    if sky.sum() > 400:  # the photo's own sky colour: overcast or hazy sky that the column rule missed
        sky_ref = np.median(rgb[sky].reshape(-1, 3), axis=0)
        out["sky_rgb"] = [round(float(c), 3) for c in sky_ref]
    height, width = luma.shape
    y0, y1 = int(height * ROW_RANGE[0]) // TILE * TILE, int(height * ROW_RANGE[1]) // TILE * TILE
    gx = np.zeros_like(luma)
    gy = np.zeros_like(luma)
    gx[:, 1:-1] = np.abs(luma[:, 2:] - luma[:, :-2]) / 2
    gy[1:-1] = np.abs(luma[2:] - luma[:-2]) / 2
    tiles = []
    for ty in range(y0, y1 - TILE + 1, TILE):
        for tx in range(0, width - TILE + 1, TILE):
            sl = (slice(ty, ty + TILE), slice(tx, tx + TILE))
            if ok_pixel[sl].mean() < 0.96:
                continue
            patch = rgb[sl]
            if float(luma[sl].std()) < 0.012:  # flat haze / smooth sky remnants
                continue
            median = np.median(patch.reshape(-1, 3), axis=0)
            if (sky_ref is not None and float(np.linalg.norm(median - sky_ref)) < SKY_MATCH_DISTANCE
                    and float(median.mean()) > 0.62):
                continue
            family = classify_tile(median)
            if family is None:
                continue
            ref = float(np.median(luma[sl]))
            tiles.append({
                "ty": ty, "tx": tx, "family": family, "median_rgb": [round(float(c), 4) for c in median],
                "luma_std": round(float(luma[sl].std()), 4),
                "dark_fraction": round(float((luma[sl] < ref * 0.55).mean()), 4),
                "gx": round(float(gx[sl].mean()), 5), "gy": round(float(gy[sl].mean()), 5),
                "sharp": round(laplacian_variance(luma[sl]), 1)})
    out["clean_tiles"] = len(tiles)
    out["clean_tile_fraction"] = round(len(tiles) * TILE * TILE / (width * (y1 - y0)), 3)
    reasons = []
    if out["mean_luma"] < MIN_LUMA or out["mean_luma"] > MAX_LUMA:
        reasons.append("exposure_out_of_range")
    if out["sharpness"] < MIN_SHARPNESS:
        reasons.append("blurry")
    if len(tiles) < MIN_CLEAN_TILES:
        reasons.append("too_few_clean_wall_tiles")
    if record.get("capture_date") is None:
        reasons.append("no_capture_date")
    if not out["checksum_ok"]:
        reasons.append("checksum_mismatch")
    out["reject_reasons"] = reasons
    out["usable"] = not reasons
    out["_tiles"] = tiles
    return out


_RGB_CACHE: dict = {}


def photo_rgb(photo: dict) -> np.ndarray:
    """Cast-corrected analysis image for a photo report (re-read lazily, small LRU)."""
    key = photo["photo_id"]
    if key not in _RGB_CACHE:
        if len(_RGB_CACHE) > 24:
            _RGB_CACHE.pop(next(iter(_RGB_CACHE)))
        _, rgb = load_analysis_image(photo["path"])
        _RGB_CACHE[key] = np.clip(rgb * np.array(photo["road_neutral_gains"], np.float32), 0, 1)
    return _RGB_CACHE[key]


def block_tile_stats(rgb: np.ndarray, ty: int, tx: int) -> dict:
    """Statistics for one TILE x TILE patch straight from pixels (used for curated patches)."""
    luma = _luma(rgb)
    sl = (slice(ty, ty + TILE), slice(tx, tx + TILE))
    patch = rgb[sl]
    gx = np.abs(np.diff(luma[sl], axis=1)).mean()
    gy = np.abs(np.diff(luma[sl], axis=0)).mean()
    ref = float(np.median(luma[sl]))
    return {"ty": ty, "tx": tx, "median_rgb": [round(float(c), 4) for c in np.median(patch.reshape(-1, 3), axis=0)],
            "luma_std": round(float(luma[sl].std()), 4),
            "dark_fraction": round(float((luma[sl] < ref * 0.55).mean()), 4),
            "gx": round(float(gx), 5), "gy": round(float(gy), 5), "sharp": round(laplacian_variance(luma[sl]), 1)}


def analyse_all(photos: list[dict], extra_paths=(), cache_path=None, catalog_sha=None) -> tuple[list[dict], bool]:
    """Analyse every photo, or reuse the cache when catalog and rules are unchanged."""
    if cache_path and Path(cache_path).is_file():
        cached = read_json(cache_path)
        if cached.get("version") == ANALYSIS_VERSION and cached.get("catalog_sha256") == catalog_sha                 and [p["photo_id"] for p in cached["photos"]] == [p["id"] for p in photos]:
            for report in cached["photos"]:
                report["_tiles"] = report.pop("tiles", [])
            return cached["photos"], True
    analyses = [analyse_photo(p, extra_paths) for p in photos]
    if cache_path:
        write_json(cache_path, {"version": ANALYSIS_VERSION, "catalog_sha256": catalog_sha, "photos": [
            {**{k: v for k, v in a.items() if not k.startswith("_")}, "tiles": a.get("_tiles", [])} for a in analyses]})
    return analyses, False


# ---------------------------------------------------------------- family derivation
def conservative_albedo(median_rgb, sat_shrink=0.80, luma_range=(0.34, 0.74)):
    """Apparent sun-lit tile colour -> modest-contrast albedo-like value. Not surveyed albedo."""
    r, g, b = [float(c) for c in median_rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    v = min(max(v, luma_range[0]), luma_range[1])
    return [round(c, 4) for c in colorsys.hsv_to_rgb(h, s * sat_shrink, v)]


def derive_families(analyses: list[dict], review_by_photo: dict) -> dict:
    """Group clean tiles into families. Returns per-family stats and 2x2-tile crops."""
    by_family = {fid: {"tiles": [], "photos": {}} for fid in FAMILY_IDS}
    for photo in analyses:
        if (not photo.get("usable") or photo["photo_id"] in LGB_REFERENCE_PHOTOS
                or photo["photo_id"] in VISUALLY_EXCLUDED_PHOTOS):
            continue
        review = review_by_photo.get(photo["photo_id"], {})
        if review.get("status") == "rejected_non_facade":
            continue
        for tile in photo["_tiles"]:
            entry = by_family[tile["family"]]
            entry["tiles"].append({**tile, "photo_id": photo["photo_id"]})
            entry["photos"][photo["photo_id"]] = entry["photos"].get(photo["photo_id"], 0) + 1
    return by_family


def reference_crops(family_tiles: list[dict], analyses_by_id: dict, limit: int, per_photo_cap: int = 2):
    """Pick 2x2-tile blocks whose four tiles all belong to the family, sharpest first."""
    index = {}
    for tile in family_tiles:
        index[(tile["photo_id"], tile["ty"], tile["tx"])] = tile
    blocks, seen = [], set()
    for (pid, ty, tx), tile in index.items():
        cells = [(pid, ty + dy * TILE, tx + dx * TILE) for dy in range(BLOCK) for dx in range(BLOCK)]
        if all(c in index for c in cells) and (pid, ty, tx) not in seen:
            seen.add((pid, ty, tx))
            blocks.append((float(np.mean([index[c]["sharp"] for c in cells])), pid, ty, tx))
    blocks.sort(key=lambda b: (-b[0], b[1], b[2], b[3]))
    chosen, per_photo = [], {}
    for sharp, pid, ty, tx in blocks:
        if per_photo.get(pid, 0) >= per_photo_cap or len(chosen) >= limit:
            continue
        per_photo[pid] = per_photo.get(pid, 0) + 1
        chosen.append({"photo_id": pid, "ty": ty, "tx": tx, "sharpness": round(sharp, 1)})
    return chosen


def validate_curated(curated: dict, analyses_by_id: dict, review_by_photo: dict) -> dict:
    """Check a curated accepted-patch list. Raises ValueError listing every problem.

    A patch is a BLOCK x BLOCK tile block anchored at (ty, tx) in the 960 px analysis image.
    Accepted patches must be clean wall: never in a rejected photo, an LGB reference photo,
    a photo reviewed as non-facade, or a patch listed as rejected.
    """
    problems = []
    rejected_photos = dict(curated.get("rejected_photos", {}))
    rejected_patches = {(r["photo_id"], r["ty"], r["tx"]) for r in curated.get("rejected_patches", [])}
    seen = set()
    for entry in curated.get("accepted", []):
        key = (entry.get("photo_id"), entry.get("ty"), entry.get("tx"))
        label = f"{entry.get('family')}:{key}"
        photo = analyses_by_id.get(entry.get("photo_id"))
        if entry.get("family") not in FAMILY_IDS:
            problems.append(f"{label} unknown family")
        if photo is None or not photo.get("decode_ok"):
            problems.append(f"{label} photo missing or undecodable")
            continue
        if key in seen:
            problems.append(f"{label} duplicate patch")
        seen.add(key)
        if entry["photo_id"] in rejected_photos:
            problems.append(f"{label} photo rejected: {rejected_photos[entry['photo_id']]}")
        if entry["photo_id"] in LGB_REFERENCE_PHOTOS:
            problems.append(f"{label} LGB reference photo")
        if review_by_photo.get(entry["photo_id"], {}).get("status") == "rejected_non_facade":
            problems.append(f"{label} photo reviewed as non-facade")
        if key in rejected_patches:
            problems.append(f"{label} patch listed as rejected")
        width, height = photo["analysis_size_px"]
        side = TILE * BLOCK
        y0, y1 = int(height * ROW_RANGE[0]) // TILE * TILE, int(height * ROW_RANGE[1]) // TILE * TILE
        if not (0 <= entry["tx"] <= width - side and y0 <= entry["ty"] and entry["ty"] + side <= y1):
            problems.append(f"{label} patch outside the wall-row range or image")
    if problems:
        raise ValueError("Invalid curated references: " + "; ".join(problems))
    return curated


def curated_family_tiles(curated: dict, analyses_by_id: dict) -> dict:
    """family -> tiles (computed from pixels) for the four tiles of every accepted patch."""
    out = {fid: {"tiles": [], "photos": {}, "patches": []} for fid in FAMILY_IDS}
    for entry in curated["accepted"]:
        photo = analyses_by_id[entry["photo_id"]]
        rgb = photo_rgb(photo)
        for dy in range(BLOCK):
            for dx in range(BLOCK):
                tile = block_tile_stats(rgb, entry["ty"] + dy * TILE, entry["tx"] + dx * TILE)
                out[entry["family"]]["tiles"].append({**tile, "photo_id": entry["photo_id"], "family": entry["family"]})
        out[entry["family"]]["photos"][entry["photo_id"]] = out[entry["family"]]["photos"].get(
            entry["photo_id"], 0) + BLOCK * BLOCK
        out[entry["family"]]["patches"].append({"photo_id": entry["photo_id"], "ty": entry["ty"], "tx": entry["tx"],
                                                "note": entry.get("note")})
    return out


def candidate_sheets(groups: dict, analyses_by_id: dict, out_dir: Path, per_family=48) -> dict:
    """Numbered crop sheets of algorithmic candidates for human curation (not accepted references)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    listing = {}
    for fid in FAMILY_IDS:
        crops = reference_crops(groups[fid]["tiles"], analyses_by_id, per_family, per_photo_cap=3)
        listing[fid] = [{"index": i, **c} for i, c in enumerate(crops)]
        cell = TILE * BLOCK * 2
        columns = 8
        rows = max(1, math.ceil(len(crops) / columns))
        sheet = Image.new("RGB", (columns * cell, rows * (cell + 16)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, crop in enumerate(crops):
            rgb = photo_rgb(analyses_by_id[crop["photo_id"]])
            side = TILE * BLOCK
            patch = (rgb[crop["ty"]:crop["ty"] + side, crop["tx"]:crop["tx"] + side] * 255).astype(np.uint8)
            x, y = (i % columns) * cell, (i // columns) * (cell + 16)
            sheet.paste(Image.fromarray(patch).resize((cell, cell), Image.LANCZOS), (x, y))
            draw.text((x + 3, y + cell + 2), f"#{i} {crop['photo_id']}", fill="black")
        sheet.save(out_dir / f"{fid}.jpg", quality=88)
    write_json(out_dir / "candidates.json", {"candidates": listing})
    return listing


def family_statistics(tiles: list[dict], analyses_by_id: dict) -> dict:
    medians = np.array([t["median_rgb"] for t in tiles])
    rgb = np.median(medians, axis=0)
    spread = np.percentile(medians, [10, 90], axis=0)
    gx = np.array([t["gx"] for t in tiles])
    gy = np.array([t["gy"] for t in tiles])
    std = np.array([t["luma_std"] for t in tiles])
    dark = np.array([t["dark_fraction"] for t in tiles])
    lags = []
    for t in tiles[:400]:
        photo = analyses_by_id.get(t["photo_id"])
        if photo is None:
            continue
        block = _luma(photo_rgb(photo)[t["ty"]:t["ty"] + TILE, t["tx"]:t["tx"] + TILE])
        lag, strength = periodicity(block)
        if strength > 0.3:
            lags.append(lag)
    return {
        "tile_count": len(tiles),
        "apparent_median_rgb": [round(float(c), 4) for c in rgb],
        "apparent_rgb_p10": [round(float(c), 4) for c in spread[0]],
        "apparent_rgb_p90": [round(float(c), 4) for c in spread[1]],
        "texture_luma_std_median": round(float(np.median(std)), 4),
        "dark_opening_fraction_median": round(float(np.median(dark)), 4),
        "vertical_vs_horizontal_edge_ratio": round(float(np.median(gx) / max(np.median(gy), 1e-6)), 3),
        "window_rhythm_period_px_at_960w_median": round(float(np.median(lags)), 1) if lags else None,
        "window_rhythm_note": "Perspective-dependent pixels; not metric. Bay widths come from typology estimates.",
    }


def material_parameters(family: dict, stats: dict) -> dict:
    light = family["id"] in ("cream_limestone_balcony_midrise", "white_panel_commercial")
    albedo = conservative_albedo(stats["apparent_median_rgb"], luma_range=(0.52, 0.82) if light else (0.34, 0.74))
    texture = stats["texture_luma_std_median"]
    roughness = min(max(0.55 + 6.0 * texture, 0.55), 0.92)
    weathering = min(max((texture - 0.015) * 14.0, 0.15), 0.85)
    glazed = family["id"] == "blue_grey_glazed_tower"
    return {
        "base_color_rgb": albedo,
        "trim_color_rgb": family["trim_rgb"],
        "glass_color_rgb": family["glass_rgb"],
        "roughness": round(0.2 if glazed else roughness, 3),
        "roughness_range": [0.08, 0.3] if glazed else [round(roughness - 0.1, 3), round(min(roughness + 0.06, 0.97), 3)],
        "metallic": 0.0,
        "weathering": round(0.2 if glazed else weathering, 3),
        "window_to_wall_ratio": family["window_ratio"],
        "bay_width_m": family["bay_width_m"],
        "floor_height_m": family["floor_height_m"],
        "balcony_type": family["balcony"],
        "colour_basis": "Median of clean photo tiles, saturation x0.8 and brightness clamped to 0.34-0.74 (0.52-0.82 for light cream/white families): "
                        "apparent historical colour, not surveyed albedo.",
    }


# --------------------------------------------------------------- texture synthesis
def periodic_noise(size, rng, cutoff=0.07):
    spectrum = np.fft.fft2(rng.standard_normal((size, size)))
    fy = np.fft.fftfreq(size)[:, None]
    fx = np.fft.fftfreq(size)[None, :]
    spectrum *= 1.0 / (1.0 + (np.sqrt(fx ** 2 + fy ** 2) / cutoff) ** 3)
    field = np.fft.ifft2(spectrum).real
    return (field - field.mean()) / (field.std() + 1e-9)


def synthesise_maps(params: dict, size=1024, bays=4, floors=4, seed="") -> dict:
    """Tileable albedo, roughness and normal maps (uint8 arrays) from material parameters."""
    rng = np.random.default_rng(int(stable_unit(seed) * 2 ** 31))
    base = np.array(params["base_color_rgb"], dtype=np.float32)
    trim = np.array(params["trim_color_rgb"], dtype=np.float32)
    glass = np.array(params["glass_color_rgb"], dtype=np.float32)
    weather = params["weathering"]
    glazed = params["balcony_type"] == "none" and params["window_to_wall_ratio"] > 0.6
    bay_px, floor_px = size / bays, size / floors
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    u = (xx % bay_px) / bay_px
    v = (yy % floor_px) / floor_px
    ratio = params["window_to_wall_ratio"]
    albedo = np.broadcast_to(base, (size, size, 3)).copy()
    height = np.zeros((size, size), np.float32)
    # slow tonal drift plus fine grain
    drift = periodic_noise(size, rng, 0.04) * 0.05 * (0.5 + weather)
    grain = periodic_noise(size, rng, 0.35) * 0.025 * (0.5 + weather)
    # vertical rain streaks fall from sills and slab edges
    streaks = periodic_noise(size, rng, 0.5)[0][None, :] * 0.5 + periodic_noise(size, rng, 0.15) * 0.5
    albedo *= (1 + drift + grain - 0.045 * weather * np.clip(streaks, 0, 2))[..., None]
    # opening geometry
    if glazed:
        wmask = (np.abs(u - 0.5) < 0.46) & (np.abs(v - 0.5) < 0.42)
        albedo = np.where(wmask[..., None], glass * (1 + 0.35 * periodic_noise(size, rng, 0.05)[..., None] * 0.2), trim)
        height += np.where(wmask, -0.4, 0.0)
        rough = np.where(wmask, 0.16, 0.45).astype(np.float32)
    else:
        width_u = min(0.78, 0.45 + ratio * 0.9)
        height_v = min(0.62, 0.30 + ratio * 0.8)
        win = (np.abs(u - 0.5) < width_u / 2) & (v > 0.36) & (v < 0.36 + height_v)
        if params["balcony_type"] in ("recessed", "slab_strip"):
            slab = v < 0.10
            albedo = np.where(slab[..., None], trim * (1 + 0.1 * grain[..., None]), albedo)
            height += np.where(slab, 0.5, 0.0)
        if params["balcony_type"] == "recessed":
            bay = (np.abs(u - 0.5) < 0.40) & (v > 0.12) & (v < 0.92)
            albedo = np.where(bay[..., None], albedo * 0.45, albedo)
            height += np.where(bay, -0.6, 0.0)
        albedo = np.where(win[..., None], glass, albedo)
        frame = win & ((np.abs(u - 0.5) > width_u / 2 - 0.035) | (v < 0.40) | (v > 0.36 + height_v - 0.04))
        albedo = np.where(frame[..., None], trim * 0.9, albedo)
        sill = (np.abs(u - 0.5) < width_u / 2 + 0.03) & (v > 0.33) & (v <= 0.36)
        albedo = np.where(sill[..., None], trim, albedo)
        height += np.where(win, -0.5, 0.0) + np.where(sill, 0.3, 0.0)
        # staining below sills
        stain = (np.abs(u - 0.5) < width_u / 2) & (v < 0.33) & (v > 0.12)
        albedo = np.where(stain[..., None], albedo * (1 - 0.10 * weather), albedo)
        rough = np.where(win, 0.18, params["roughness"]).astype(np.float32)
        rough += grain * 0.6
    albedo = np.clip(albedo, 0.02, 0.95)
    rough = np.clip(rough + periodic_noise(size, rng, 0.2) * 0.04, 0.05, 0.98)
    # normal map from the relief height (tileable via roll)
    h = height + periodic_noise(size, rng, 0.4) * 0.05
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 2.5
    dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 2.5
    normal = np.stack([-dx, dy, np.ones_like(dx)], -1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    return {
        "albedo": (albedo * 255).round().astype(np.uint8),
        "roughness": (rough * 255).round().astype(np.uint8),
        "normal": ((normal * 0.5 + 0.5) * 255).round().astype(np.uint8),
        "tile_size_m": [round(bays * params["bay_width_m"], 2), round(floors * params["floor_height_m"], 2)],
    }


# ------------------------------------------------------------------ contact sheets
def contact_sheets(analyses: list[dict], out_dir: Path, per_sheet=30, thumb=(240, 160)):
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for start in range(0, len(analyses), per_sheet):
        sheet = Image.new("RGB", (6 * thumb[0], 5 * (thumb[1] + 16)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, photo in enumerate(analyses[start:start + per_sheet]):
            x, y = (i % 6) * thumb[0], (i // 6) * (thumb[1] + 16)
            if photo.get("decode_ok"):
                with Image.open(photo["path"]) as image:
                    image.draft("RGB", (480, 320))
                    image = image.convert("RGB")
                    image.thumbnail(thumb)
                    sheet.paste(image, (x, y))
            flag = "OK" if photo.get("usable") else "x:" + ",".join(photo.get("reject_reasons", []))[:22]
            draw.text((x + 2, y + thumb[1] + 2), f"{photo['photo_id']} {flag}", fill="black")
        path = out_dir / f"photos-{start // per_sheet + 1:02d}.jpg"
        sheet.save(path, quality=84)
        paths.append(str(path))
    return paths


def family_mosaic(family_id, crops, analyses_by_id, path: Path, columns=6):
    cell = TILE * BLOCK
    if not crops:
        return None
    rows = math.ceil(len(crops) / columns)
    scale = 2
    sheet = Image.new("RGB", (columns * cell * scale, rows * (cell * scale + 14)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, crop in enumerate(crops):
        rgb = photo_rgb(analyses_by_id[crop["photo_id"]])
        patch = (rgb[crop["ty"]:crop["ty"] + cell, crop["tx"]:crop["tx"] + cell] * 255).astype(np.uint8)
        image = Image.fromarray(patch).resize((cell * scale, cell * scale), Image.LANCZOS)
        x, y = (i % columns) * cell * scale, (i // columns) * (cell * scale + 14)
        sheet.paste(image, (x, y))
        draw.text((x + 2, y + cell * scale + 1), crop["photo_id"], fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=88)
    return str(path)


def save_png(array, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path)
    return str(path)


# ---------------------------------------------------------------------------- jobs
def load_building_geometry(plan_records: list[dict]) -> dict:
    """source_id -> UTM centre and footprint extent (m) from the staged object indexes."""
    geometry = {}
    for index_path in sorted({r["source_index"] for r in plan_records}):
        doc = read_json(index_path)
        ox, oy, _ = doc["coordinates"]["origin_utm_m"]
        for obj in doc["objects"]:
            (x0, y0, z0), (x1, y1, z1) = obj["local_bounds_m"]
            geometry[obj["source_id"]] = {
                "centre_utm_m": [round(ox + (x0 + x1) / 2, 2), round(oy + (y0 + y1) / 2, 2)],
                "bbox_m": [round(x1 - x0, 2), round(y1 - y0, 2)], "z_extent_m": round(z1 - z0, 2),
                "batch": obj.get("batch")}
    return geometry


def height_class(height_m: float) -> str:
    if height_m < 12:
        return "low"
    if height_m < 28:
        return "mid"
    if height_m < 60:
        return "high"
    return "tower"


def photo_family_shares(analyses: list[dict], excluded_photos=frozenset(), allowed_families=None) -> dict:
    """photo_id -> {family: share of its clean tiles}. Only usable, non-LGB, non-excluded photos.

    ``allowed_families`` limits the evidence to families whose references were curated.
    """
    shares = {}
    for photo in analyses:
        if (not photo.get("usable") or photo["photo_id"] in LGB_REFERENCE_PHOTOS
                or photo["photo_id"] in VISUALLY_EXCLUDED_PHOTOS or photo["photo_id"] in excluded_photos
                or not photo.get("camera_xy_m")):
            continue
        counts = {}
        for tile in photo["_tiles"]:
            if allowed_families is None or tile["family"] in allowed_families:
                counts[tile["family"]] = counts.get(tile["family"], 0) + NEUTRAL_SHARE_WEIGHT.get(tile["family"], 1.0)
        total = sum(counts.values())
        if total:
            shares[photo["photo_id"]] = {k: v / total for k, v in counts.items()}
    return shares


def candidate_summary(candidate: dict) -> dict:
    wall = candidate.get("wall_sample") or {}
    return {"photo_id": candidate["photo_id"], "rank_cost": round(float(candidate.get("rank_cost", 99)), 2),
            "wall_distance_m": wall.get("distance_m"), "heading_offset_deg": wall.get("heading_offset_deg")}


def candidate_quality(candidate: dict) -> float:
    """0.2-1: lower geometric rank cost means a better-aimed candidate. Never an identity score."""
    return min(1.0, max(0.2, 1.0 - float(candidate.get("rank_cost", 40)) / 40.0))


def build_jobs(plan_records, passes_records, geometry, shares, photo_xy, family_assets,
               excluded, candidate_pairs_excluded, rejected_photos, landmark_ids, candidate_map=None):
    pass_info = {r["source_id"]: r for r in passes_records}
    photo_ids = list(photo_xy)
    cam = np.array([photo_xy[p] for p in photo_ids]) if photo_ids else np.zeros((0, 2))
    jobs = []
    for record in sorted(plan_records, key=lambda r: r["source_id"]):
        sid = record["source_id"]
        meta = record["source_metadata"]
        geo = geometry.get(sid, {})
        passinfo = pass_info.get(sid, {})
        height = float(meta.get("height_m") or geo.get("z_extent_m") or 0)
        bbox = geo.get("bbox_m", [0, 0])
        footprint = bbox[0] * bbox[1]
        job = {
            "source_id": sid, "group": meta.get("production_group"), "district": record["district"],
            "owner_tile": record["owner_tile"], "pass_number": passinfo.get("pass_number"),
            "height_m": round(height, 2), "bbox_m": bbox, "centre_utm_m": geo.get("centre_utm_m"),
            "previous_template": record["facade"].get("template"),
            "previous_facade_status": record["facade"].get("status"),
            "candidate_photo_ids_unconfirmed": [],
        }
        raw = candidate_map.get(sid, []) if candidate_map is not None else record.get("street_candidates", [])
        best = {}
        for c in raw:
            if c["photo_id"] in rejected_photos or (sid, c["photo_id"]) in candidate_pairs_excluded:
                continue
            if c["photo_id"] not in best or c.get("rank_cost", 99) < best[c["photo_id"]].get("rank_cost", 99):
                best[c["photo_id"]] = c
        candidates = [best[k] for k in sorted(best)]
        job["candidate_photo_ids_unconfirmed"] = [c["photo_id"] for c in candidates]
        if sid in excluded:
            cls, reason = excluded[sid]
            job.update(evidence_class=cls, proposed_family=None, unchanged_reason=reason, family_basis=None,
                       reference_paths=[])
        elif record["facade"].get("status", "").startswith("observed"):
            job.update(evidence_class="confirmed_historical", proposed_family=None,
                       unchanged_reason="existing_observed_historical_facade_preserved", family_basis=None,
                       reference_paths=[])
        elif meta.get("kind") not in (None, "building") or height < MIN_BUILDING_HEIGHT_M or (
                bbox[0] and footprint < MIN_FOOTPRINT_M2):
            job.update(evidence_class="unchanged", proposed_family=None,
                       unchanged_reason="too_small_for_facade_treatment", family_basis=None, reference_paths=[])
        else:
            hclass = height_class(height)
            weights = {f["id"]: f["height_prior"][hclass] for f in FAMILIES}
            local, nearest, near_photos = {fid: 0.0 for fid in FAMILY_IDS}, None, []
            if geo and len(cam):
                d = np.hypot(cam[:, 0] - geo["centre_utm_m"][0], cam[:, 1] - geo["centre_utm_m"][1])
                nearest = float(d.min())
                for pid, dist in zip(photo_ids, d):
                    if dist < 300 and pid in shares:
                        w = math.exp(-dist / 120.0)
                        near_photos.append((dist, pid))
                        for fid, share in shares[pid].items():
                            local[fid] += w * share
            total_local = sum(local.values())
            evidence_weight = 0.0
            if total_local:
                evidence_weight = min(1.0, total_local)
                for fid in FAMILY_IDS:
                    weights[fid] *= 1.0 + 3.0 * evidence_weight * local[fid] / total_local * len(FAMILY_IDS) / 2
            boosted = []
            for c in candidates:
                if c["photo_id"] in shares:
                    boosted.append(c["photo_id"])
                    quality = candidate_quality(c)
                    for fid, share in shares[c["photo_id"]].items():
                        weights[fid] *= 1.0 + 2.5 * quality * share
            norm = sum(weights.values())
            draw = stable_unit(sid, "appearance-family-v1") * norm
            acc, chosen = 0.0, FAMILY_IDS[-1]
            for fid in FAMILY_IDS:
                acc += weights[fid]
                if draw < acc:
                    chosen = fid
                    break
            fam = next(f for f in FAMILIES if f["id"] == chosen)
            assets = family_assets[chosen]
            basis = ("typology_plus_geometric_candidate_photos" if boosted else
                     "typology_plus_nearby_photos" if total_local else "typology_prior_only")
            floors = max(1, round(height / fam["floor_height_m"]))
            job.update(
                evidence_class="reference_informed_estimate", proposed_family=chosen, unchanged_reason=None,
                family_basis=basis, height_class=hclass,
                candidate_status="geometric_candidate_unverified" if candidates else "no_cached_candidate",
                candidate_support=[{**candidate_summary(c), "used_for_style": c["photo_id"] in boosted}
                                   for c in candidates], nearest_clean_photo_m=None if nearest is None else round(nearest, 1),
                nearby_photo_ids=[p for _, p in sorted(near_photos)[:5]],
                landmark_candidate_requires_visual_confirmation=sid in landmark_ids,
                variant={
                    "floors": floors,
                    "tint_luma_scale": round(0.94 + 0.12 * stable_unit(sid, "tint"), 3),
                    "hue_shift_deg": round(-3 + 6 * stable_unit(sid, "hue"), 2),
                    "weathering_scale": round(0.7 + 0.6 * stable_unit(sid, "weather"), 3),
                    "texture_offset_uv": [round(stable_unit(sid, "u"), 3), round(stable_unit(sid, "v"), 3)],
                    "bay_width_m": round(fam["bay_width_m"] * (0.92 + 0.16 * stable_unit(sid, "bay")), 2)},
                reference_paths=assets["reference_paths"] + [f"photo:{p}" for p in (boosted or [])])
        if "candidate_status" not in job:
            job["candidate_status"] = "geometric_candidate_unverified" if candidates else "no_cached_candidate"
            job["candidate_support"] = [{**candidate_summary(c), "used_for_style": False} for c in candidates]
        jobs.append(job)
    return jobs


def refresh_review_overlay(evidence_root, apply_script, out_root) -> Path:
    """Re-run the existing review overlay against the CURRENT all-batches in a new folder.

    Copies only the catalog, pass files and review decisions (no photos) so the original
    evidence is untouched; recorded reject/conflict decisions are applied unchanged and
    nothing is auto-confirmed. Returns the folder holding ``pass-0*.json`` overlay files.
    """
    import importlib.util
    import shutil
    evidence_root, out_root = Path(evidence_root), Path(out_root)
    (out_root / "all-batches").mkdir(parents=True, exist_ok=True)
    for name in ["photo-catalog.json", *[p.name for p in (evidence_root / "all-batches").glob("pass-0*.json")]]:
        shutil.copy2(evidence_root / "all-batches" / name, out_root / "all-batches" / name)
    shutil.copy2(evidence_root / "review-decisions.json", out_root / "review-decisions.json")
    spec = importlib.util.spec_from_file_location("apply_batch_facade_reviews", apply_script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.apply(out_root)
    return out_root / "reviewed"


def load_candidates(overlay_dir, collector_dir, analyses_by_id):
    """Surviving cached catalog-photo candidates per building, exact reconciliation counts and camera
    positions recovered from the collector's candidate records (the catalog itself only has 17)."""
    overlay_dir = Path(overlay_dir)
    candidate_map, camera_xy = {}, {}
    raw_buildings = raw_pairs = metadata_only = cached_pairs = 0
    raw_ids = set()
    if collector_dir:
        for path in sorted(Path(collector_dir).glob("pass-0*.json")):
            for record in read_json(path)["records"]:
                if record["candidates"]:
                    raw_buildings += 1
                    raw_ids.add(record["source_id"])
                raw_pairs += len(record["candidates"])
                for c in record["candidates"]:
                    if c.get("camera_xy_m"):
                        camera_xy.setdefault(c["photo_id"], (c["camera_xy_m"], c.get("heading_grid_deg")))
                    if c["photo_id"] in analyses_by_id and c.get("photo_cached"):
                        cached_pairs += 1
                    else:
                        metadata_only += 1
    kept_pairs = excluded_pairs = 0
    for path in sorted(overlay_dir.glob("pass-0*.json")):
        for record in read_json(path)["records"]:
            excluded_pairs += len(record.get("excluded_candidates", []))
            usable = [c for c in record["candidates"] if c["photo_id"] in analyses_by_id and c.get("photo_cached")]
            kept_pairs += len(usable)
            if usable:
                candidate_map[record["source_id"]] = usable
    rec = {
        "current_collection_buildings_with_geometric_candidates": raw_buildings,
        "current_collection_candidate_pairs": raw_pairs,
        "pairs_with_cached_catalog_photo": cached_pairs,
        "pairs_metadata_only_photo_not_cached": metadata_only,
        "pairs_removed_by_review_overlay": excluded_pairs,
        "pairs_kept_after_review": kept_pairs,
        "buildings_with_kept_cached_candidate": len(candidate_map),
        "buildings_without_eligible_cached_candidate": sorted(raw_ids - set(candidate_map)),
        "why_those_buildings_lost_candidates": "each has only review-excluded pairs (rejected or identity-conflict "
                                               "photo) plus pairs whose photo was never cached in the catalog",
        "catalog_photos_with_camera_position_from_candidates": sum(1 for p in analyses_by_id if p in camera_xy),
        "overlay_dir": str(overlay_dir),
        "overlay_note": "Overlay re-run against current all-batches with recorded reject/conflict decisions; "
                        "no identity was auto-confirmed. Candidates are geometric shortlists only."}
    return candidate_map, rec, camera_xy


def reconcile_jobs(jobs: list[dict]) -> dict:
    with_candidates = [j for j in jobs if j["candidate_photo_ids_unconfirmed"]]
    informed = sum(1 for j in jobs if any(c["used_for_style"] for c in j.get("candidate_support", [])))
    return {
        "jobs_listing_candidates": len(with_candidates),
        "jobs_listing_candidates_by_class": {
            cls: sum(1 for j in with_candidates if j["evidence_class"] == cls)
            for cls in sorted({j["evidence_class"] for j in with_candidates})},
        "jobs_where_candidate_photo_informed_style": informed,
        "jobs_with_candidates_but_no_usable_family_evidence": sum(
            1 for j in with_candidates if j["evidence_class"] == "reference_informed_estimate"
            and not any(c["used_for_style"] for c in j.get("candidate_support", []))),
        "identity_verified_jobs": 0}


# ----------------------------------------------------------------------- top level
def build_library(catalog_path, pass_paths, review_path, review_report_path, plan_dir, passes_dir,
                  retained_path, landmark_path, out_dir, inspection_notes=None, extra_photo_dirs=(),
                  excluded_sources=None, cache_path=None, curated_path=None, overlay_dir=None,
                  collector_dir=None, candidate_sheet_dir=None):
    """Run the whole offline generation. Returns the library and jobs documents.

    ``curated_path``: accepted clean-wall patches (see ``validate_curated``). Family statistics,
    reference IDs and crop sheets come only from those. ``overlay_dir``: refreshed reviewed pass
    folder (``refresh_review_overlay``); its surviving candidates feed style assignment.
    With ``candidate_sheet_dir`` only numbered candidate sheets are written for curation.
    """
    started = time.time()
    out_dir = Path(out_dir)
    catalog = read_json(catalog_path)
    reviews = read_json(review_path)
    review_by_photo = {p["photo_id"]: p for p in reviews["photos"]}
    review_report = read_json(review_report_path)
    t0 = time.time()
    catalog_sha = sha256_file(catalog_path)
    analyses, from_cache = analyse_all(catalog["photos"], extra_photo_dirs, cache_path, catalog_sha)
    photo_seconds = time.time() - t0
    by_id = {a["photo_id"]: a for a in analyses}
    for photo in analyses:
        review = review_by_photo.get(photo["photo_id"])
        if review and review["status"] == "rejected_non_facade":
            photo["usable"] = False
            photo["reject_reasons"] = photo.get("reject_reasons", []) + ["reviewed_rejected_non_facade"]
    groups = derive_families(analyses, review_by_photo)
    if candidate_sheet_dir:
        return candidate_sheets(groups, by_id, Path(candidate_sheet_dir)), None, analyses
    curated = None
    if curated_path:
        curated = validate_curated(read_json(curated_path), by_id, review_by_photo)
        curated_groups = curated_family_tiles(curated, by_id)
    sheet_dir = out_dir / "contact-sheets"
    sheets = (sorted(str(x) for x in sheet_dir.glob("*.jpg")) if from_cache and sheet_dir.is_dir()
              else contact_sheets(analyses, sheet_dir))
    family_records, family_assets, family_status = [], {}, {}
    capture_dates = {p["id"]: p["capture_date"] for p in catalog["photos"]}
    for family in FAMILIES:
        algorithmic_tiles = len(groups[family["id"]]["tiles"])
        if curated is not None:
            source = curated_groups[family["id"]]
            patch_count = len(source["patches"])
            status = ("curated_accepted" if patch_count >= MIN_ACCEPTED_PATCHES else
                      "uncertain_few_accepted_patches" if patch_count else "typology_only_no_accepted_patches")
        else:
            source, patch_count, status = groups[family["id"]], None, "algorithmic_uncurated"
        tiles = source["tiles"]
        family_status[family["id"]] = status
        if tiles:
            stats = family_statistics(tiles, by_id)
        else:  # keep the family usable, but say it rests on typology only
            stats = {"tile_count": 0, "apparent_median_rgb": family["trim_rgb"],
                     "texture_luma_std_median": 0.03, "dark_opening_fraction_median": None,
                     "note": "No accepted clean wall patches; typology estimate only."}
        stats["algorithmic_screening_tile_count"] = algorithmic_tiles
        stats["accepted_patch_count"] = patch_count
        params = material_parameters(family, stats)
        photos = source["photos"]
        refs = [pid for pid, _ in sorted(photos.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
        crops = source["patches"] if curated is not None else reference_crops(tiles, by_id, 24)
        fam_dir = out_dir / "families" / family["id"]
        mosaic = family_mosaic(family["id"], crops, by_id, fam_dir / "reference-crops.jpg")
        maps = synthesise_maps(params, seed=family["id"])
        paths = {k: save_png(maps[k], fam_dir / f"{k}.png") for k in ("albedo", "roughness", "normal")}
        family_assets[family["id"]] = {"reference_paths": [paths["albedo"], paths["roughness"], paths["normal"]]}
        family_records.append({
            "id": family["id"], "name": family["name"], "visual_cues": family["cues"],
            "reference_photo_ids": refs,
            "reference_photo_tile_counts": {pid: photos[pid] for pid in refs},
            "source_capture_dates": sorted({capture_dates[p][:10] for p in refs}),
            "license": catalog["license"], "attribution": catalog["attribution"],
            "evidence_basis": "curated_clean_wall_patches" if curated is not None else "photo_tile_statistics_uncurated",
            "reference_status": status,
            "label_note": "Interpretive appearance family; colour bins alone do not establish architectural typology.",
            "appearance_estimates": {"photo_statistics": stats, "material": params,
                                     "typology_height_prior": family["height_prior"]},
            "texture_assets": {**paths, "tile_size_m": maps["tile_size_m"], "size_px": [1024, 1024],
                               "derivation": "Procedural, tileable; built from family statistics and "
                                             "typology estimates. Not a photo crop and not surveyed.",
                               "uv_note": "One tile = 4 bays x 4 floors; scale UV by tile_size_m, not by building."},
            "reference_crop_sheet": mosaic, "reference_crops": crops,
            "identity_status": "appearance_family_only; no building identity implied"})
    usable = [a for a in analyses if a.get("usable")]
    for a in analyses:
        a["clean_tile_family_counts"] = {}
        for tile in a.get("_tiles", []):
            a["clean_tile_family_counts"][tile["family"]] = a["clean_tile_family_counts"].get(tile["family"], 0) + 1
        a["review_status"] = review_by_photo.get(a["photo_id"], {}).get("status")
    # building inventory
    plan_records = []
    plan_dir = Path(plan_dir)
    for tile_path in sorted(plan_dir.glob("*_*.json")):
        plan_records.extend(read_json(tile_path)["records"])
    passes_records = []
    for p in sorted(Path(passes_dir).glob("pass-0*.json")):
        doc = read_json(p)
        for r in doc["records"]:
            passes_records.append({**r, "pass_number": doc["pass_number"]})
    geometry = load_building_geometry(plan_records)
    pair_excluded = {(r["source_id"], r["photo_id"]) for r in reviews["photos"]
                     if r.get("source_id") and r["status"] in ("identity_conflict", "rejected_non_facade")}
    rejected = {p["photo_id"] for p in reviews["photos"] if p["status"] == "rejected_non_facade"}
    landmark_ids = set()
    if landmark_path and Path(landmark_path).is_file():
        landmark_ids = {c["source_id"] for c in read_json(landmark_path)["candidates"]}
    excluded = dict(DEFAULT_EXCLUDED_SOURCES)
    excluded.update(excluded_sources or {})
    curated_rejected = set(curated.get("rejected_photos", {})) if curated else set()
    allowed = {f for f, st in family_status.items() if st == "curated_accepted"} if curated else None
    candidate_map, reconciliation = None, None
    if overlay_dir:
        candidate_map, reconciliation, camera_xy = load_candidates(overlay_dir, collector_dir, by_id)
        for photo in analyses:  # the catalog lacks most camera positions; candidate records carry them
            if not photo.get("camera_xy_m") and photo["photo_id"] in camera_xy:
                photo["camera_xy_m"], photo["heading_grid_deg"] = camera_xy[photo["photo_id"]]
                photo["camera_position_source"] = "collector_candidate_record"
    shares = photo_family_shares(analyses, curated_rejected, allowed)
    if reconciliation is not None:
        reconciliation["photos_with_camera_position_and_accepted_family_evidence"] = len(shares)
    photo_xy = {a["photo_id"]: a["camera_xy_m"] for a in analyses if a["photo_id"] in shares}
    jobs = build_jobs(plan_records, passes_records, geometry, shares, photo_xy, family_assets, excluded,
                      pair_excluded, rejected, landmark_ids, candidate_map)
    if reconciliation is not None:
        reconciliation.update(reconcile_jobs(jobs))
        unused = {}
        for job in jobs:
            if job["evidence_class"] != "reference_informed_estimate":
                continue
            for c in job["candidate_support"]:
                if not c["used_for_style"]:
                    photo = by_id[c["photo_id"]]
                    reasons = ",".join(photo.get("reject_reasons", [])) or (
                        "excluded_by_curation_or_no_accepted_family_tiles")
                    unused[reasons] = unused.get(reasons, 0) + 1
        reconciliation["unused_candidate_pairs_in_estimated_jobs_by_photo_reason"] = unused
    counts = {}
    for job in jobs:
        key = job["proposed_family"] or "unchanged"
        counts[key] = counts.get(key, 0) + 1
    classes = {}
    for job in jobs:
        classes[job["evidence_class"]] = classes.get(job["evidence_class"], 0) + 1
    # photo analysis (no arrays)
    clean_photo_reports = []
    for a in analyses:
        clean_photo_reports.append({k: v for k, v in a.items() if not k.startswith("_")})
    write_json(out_dir / "photo-analysis.json", {
        "schema": "dynacity-photo-analysis/1", "photos": clean_photo_reports,
        "thresholds": {"min_sharpness": MIN_SHARPNESS, "luma_range": [MIN_LUMA, MAX_LUMA],
                       "min_clean_tiles": MIN_CLEAN_TILES, "tile_px_at_960w": TILE, "row_range": ROW_RANGE}})
    reject_counts = {}
    for a in analyses:
        for reason in a.get("reject_reasons", []):
            reject_counts[reason] = reject_counts.get(reason, 0) + 1
    library = {
        "schema": LIBRARY_SCHEMA, "generated_on": time.strftime("%Y-%m-%d"),
        "source_catalog": {"path": str(catalog_path), "sha256": sha256_file(catalog_path),
                           "license": catalog["license"], "attribution": catalog["attribution"]},
        "photos": {
            "catalogued": len(catalog["photos"]), "processed": len(analyses),
            "reviewed": len(reviews["photos"]),
            "reviewed_note": "Explicit per-photo review decisions in review-decisions.json (assistant inspection, "
                             "not a survey). Remaining photos are algorithmically screened; see "
                             "visual_inspection for sheets/originals inspected by eye.",
            "usable": len(usable), "unusable": len(analyses) - len(usable), "reject_reason_counts": reject_counts,
            "checksum_ok": sum(1 for a in analyses if a.get("checksum_ok")),
            "decoded": sum(1 for a in analyses if a.get("decode_ok")),
            "capture_year_counts": {y: sum(1 for p in catalog["photos"] if p["capture_date"].startswith(y))
                                    for y in sorted({p["capture_date"][:4] for p in catalog["photos"]})},
            "analysis_json": str(out_dir / "photo-analysis.json"), "contact_sheets": sheets},
        "review_inputs": {"review_decisions": str(review_path), "review_decisions_sha256": sha256_file(review_path),
                          "review_report_sha256": sha256_file(review_report_path),
                          "review_report_building_records": review_report.get("building_records")},
        "visual_inspection": inspection_notes or {"status": "none_recorded"},
        "curation": ({"curated_references": str(curated_path), "curated_sha256": sha256_file(curated_path),
                      "accepted_patches": len(curated["accepted"]),
                      "rejected_photos": curated.get("rejected_photos", {}),
                      "rejected_patches": curated.get("rejected_patches", []),
                      "reviewer": curated.get("reviewer"), "family_reference_status": family_status}
                     if curated else {"status": "none"}),
        "candidate_reconciliation": reconciliation or {"status": "not_computed"},
        "families": family_records,
        "exclusions": {
            "source_ids": {sid: {"evidence_class": cls, "reason": reason} for sid, (cls, reason) in excluded.items()},
            "photos_not_used_for_family_references": {
                **{pid: "existing LGB tower reference; appearance not transferred" for pid in sorted(LGB_REFERENCE_PHOTOS)},
                **{pid: reason for pid, reason in VISUALLY_EXCLUDED_PHOTOS.items()},
                **{pid: review_by_photo[pid]["reason"] for pid in sorted(rejected)}},
            "candidate_pairs_excluded": sorted(f"{s}|{p}" for s, p in pair_excluded),
            "rules": ["Preserve photo-authored LGB and the captured Ac_726 building.",
                      "Buildings below 3.5 m height or 12 m2 footprint are left unchanged.",
                      "No Overture buildings; source meshes, footprints, collision and IDs are untouched."]},
        "uncertainties": [
            "All photos are 2018 dashcam frames with sky, road and vehicles; clean wall tiles are small.",
            "Colours are apparent sun-lit values (sat x0.8, brightness clamp), never surveyed albedo.",
            "Family assignment for ~17k buildings is a typology + nearby-photo estimate, not identification.",
            "Street photo candidates are geometric shortlists; none is a confirmed building identity.",
            "Bay width, floor height, window ratio and balcony type are visual typology estimates.",
            "Algorithmic tile rules were tuned on these photos and are only a screening aid; family statistics and "
            "references come from curated accepted patches (a few dozen), so they are small samples.",
            "Family labels and height priors are interpretive estimates; a colour bin does not establish typology.",
            "Building facade appearance may have changed after 2018 and the August 2020 port blast."],
        "timing_seconds": {"photo_processing": round(photo_seconds, 1), "analysis_from_cache": from_cache,
                           "total": round(time.time() - started, 1)},
    }
    jobs_doc = {
        "schema": JOBS_SCHEMA, "library": str(out_dir / "appearance-library.json"),
        "inventory": {"source_buildings": len(jobs), "expected": 17397,
                      "plan_dir": str(plan_dir), "passes_dir": str(passes_dir)},
        "counts_by_family": counts, "counts_by_evidence_class": classes,
        "evidence_class_definitions": {
            "confirmed_historical": "Existing reviewed historical facade observation; preserve, do not overwrite.",
            "reference_informed_estimate": "Family chosen from typology and nearby clean photo evidence; "
                                           "not identity confirmation.",
            "unchanged": "Leave the current assets as they are; see unchanged_reason."},
        "jobs": jobs}
    write_json(out_dir / "appearance-library.json", library)
    write_json(out_dir / "building-appearance-jobs.json", jobs_doc)
    # the arrays are only needed during generation
    return library, jobs_doc, analyses
