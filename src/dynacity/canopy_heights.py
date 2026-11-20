"""Meta/WRI canopy height model over a corridor: tree candidates, OSM tree heights, canopy cover per road.

Source: High Resolution Canopy Height Maps v1 (Meta and WRI, CC BY 4.0), ~1 m cells of canopy
height above ground, a model estimate from Maxar satellite imagery. Nothing here is an observed
tree: a candidate is a local maximum of the height model, a likely crown top, with an estimated
height. Trunk positions, species, ground contact and present-day condition are unverified, and the
imagery behind each cell was captured on a date recorded only per footprint (`acq_date`).

The functions work on plain arrays so they can be tested on synthetic grids; `build_canopy_dataset`
wires them to the raw tile, the corridor line, the traffic model and the OSM extract.
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MIN_HEIGHT_M = 3.0
MIN_SPACING_M = 4.0
ROAD_BUFFER_M = 8.0
UNMAPPED_DISTANCE_M = 5.0
TREE_HEIGHT_RADIUS_M = 3.0
SAMPLE_STEP_M = 1.0
SOURCE_URL = "https://registry.opendata.aws/dataforgood-fb-forests/"
TILE_URL = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/{tile}.tif"
METADATA_URL = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/metadata/{tile}.geojson"
LICENCE = "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
ATTRIBUTION = (
    "Meta and World Resources Institute (WRI), 2024. High Resolution Canopy Height Maps (CHM). "
    "Source imagery for CHM (c) Maxar. Accessed {accessed}."
)
CANDIDATE_KIND = "model_estimate_not_observed_trunk"


def quadkey(lon: float, lat: float, zoom: int = 9) -> str:
    """Bing quadkey of the web-mercator tile holding (lon, lat); the publisher names its 1 m tiles by zoom-9 quadkey."""
    n = 2 ** zoom
    x = min(n - 1, int((lon + 180.0) / 360.0 * n))
    lat_r = math.radians(lat)
    y = min(n - 1, int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n))
    key = ""
    for i in range(zoom, 0, -1):
        mask = 1 << (i - 1)
        key += str((1 if x & mask else 0) + (2 if y & mask else 0))
    return key


def tiles_for_bounds(west: float, south: float, east: float, north: float, zoom: int = 9) -> list[str]:
    """Quadkeys of the tiles touched by a lon/lat box (its four corners; tiles are far larger than a corridor)."""
    return sorted({quadkey(lon, lat, zoom) for lon in (west, east) for lat in (south, north)})


# --- arrays -----------------------------------------------------------------------------------------

def disc(radius_px: float) -> np.ndarray:
    """Boolean disc footprint of the given radius in pixels."""
    r = int(math.ceil(radius_px))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= radius_px * radius_px


def find_peaks(chm: np.ndarray, *, min_height_m: float = MIN_HEIGHT_M, spacing_px: float) -> np.ndarray:
    """(row, col) of local maxima of `chm` at least `min_height_m` high and no closer than `spacing_px`.

    A pixel is a peak when it is the maximum of the disc of radius `spacing_px / 2` around it. A flat top
    (several equal pixels) counts once, at the pixel nearest its centre. NaN counts as no canopy. A taller
    peak and a shorter one closer than `spacing_px` are not both kept: the shorter is dropped.
    """
    from scipy import ndimage

    height = np.nan_to_num(chm, nan=0.0)
    footprint = disc(max(spacing_px / 2.0, 1.0))
    top = ndimage.maximum_filter(height, footprint=footprint, mode="constant", cval=0.0)
    mask = (height >= min_height_m) & (height == top)
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if count == 0:
        return np.zeros((0, 2), dtype=np.int64)
    # One cell per flat top: the member nearest its centroid (vectorised; labels can number in the 100,000s).
    cells = np.argwhere(mask)
    label = labels[mask]
    size = np.bincount(label, minlength=count + 1)[1:]
    centre = np.column_stack([np.bincount(label, weights=cells[:, a], minlength=count + 1)[1:] / size for a in (0, 1)])
    dist = ((cells - centre[label - 1]) ** 2).sum(axis=1)
    order_cells = np.lexsort((cells[:, 1], cells[:, 0], dist, label))
    first = np.r_[True, label[order_cells][1:] != label[order_cells][:-1]]
    peaks = cells[order_cells[first]]
    # Plateaus can sit just under `spacing_px` apart through their offset centres: keep the taller one.
    values = height[peaks[:, 0], peaks[:, 1]]
    order = np.argsort(-values, kind="stable")
    kept: list[int] = []
    from scipy.spatial import cKDTree
    tree = cKDTree(peaks.astype(float))
    removed = np.zeros(len(peaks), bool)
    for i in order:
        if removed[i]:
            continue
        kept.append(int(i))
        for j in tree.query_ball_point(peaks[i], spacing_px * 0.999):
            if j != i:
                removed[j] = True
    return peaks[sorted(kept, key=lambda i: (peaks[i, 0], peaks[i, 1]))]


def canopy_near(chm: np.ndarray, min_height_m: float, radius_px: float) -> np.ndarray:
    """Boolean grid: True where the height model reaches `min_height_m` within `radius_px` of the cell."""
    from scipy import ndimage

    present = (np.nan_to_num(chm, nan=0.0) >= min_height_m).astype(np.uint8)
    return ndimage.maximum_filter(present, footprint=disc(radius_px), mode="constant", cval=0).astype(bool)


def max_height_near(chm: np.ndarray, rows: np.ndarray, cols: np.ndarray, radius_px: float) -> np.ndarray:
    """Maximum height within `radius_px` of each (row, col); NaN if the point is off the grid or every cell is NaN."""
    r = int(math.ceil(radius_px))
    foot = disc(radius_px)
    padded = np.pad(chm, r, mode="constant", constant_values=np.nan)
    out = np.full(len(rows), np.nan)
    for k, (row, col) in enumerate(zip(rows, cols, strict=True)):
        if not (0 <= row < chm.shape[0] and 0 <= col < chm.shape[1]):
            continue
        patch = padded[row:row + 2 * r + 1, col:col + 2 * r + 1]
        values = patch[foot]
        if np.isfinite(values).any():
            out[k] = np.nanmax(values)
    return out


def sample_line(points: Sequence[Sequence[float]], step_m: float = SAMPLE_STEP_M) -> np.ndarray:
    """Points every `step_m` along a polyline (metres), always including both ends."""
    from shapely.geometry import LineString

    line = LineString(points)
    if line.length == 0:
        return np.asarray([points[0]], dtype=float)
    n = max(int(math.ceil(line.length / step_m)), 1)
    return np.asarray([line.interpolate(d).coords[0] for d in np.linspace(0.0, line.length, n + 1)])


def road_canopy_cover(
    roads: Sequence[Sequence[Sequence[float]]], near: np.ndarray, valid: np.ndarray,
    to_pixel, *, step_m: float = SAMPLE_STEP_M,
) -> list[dict]:
    """Per road: share of centreline samples with canopy within the buffer.

    `roads` are polylines in the working CRS, `near` / `valid` grids on the raster, `to_pixel(xy)` maps an
    (n, 2) array to (row, col) arrays. Samples off the raster or on NaN cells are excluded from the share and
    counted in `valid_fraction`; a road with no valid sample gets `canopy_cover` None.
    """
    result = []
    for line in roads:
        pts = sample_line(line, step_m)
        rows, cols = to_pixel(pts)
        inside = (rows >= 0) & (rows < near.shape[0]) & (cols >= 0) & (cols < near.shape[1])
        ok = np.zeros(len(pts), bool)
        hit = np.zeros(len(pts), bool)
        ok[inside] = valid[rows[inside], cols[inside]]
        hit[inside] = near[rows[inside], cols[inside]]
        n_ok = int(ok.sum())
        result.append({
            "samples": len(pts),
            "valid_fraction": round(n_ok / len(pts), 4),
            "canopy_cover": round(float((hit & ok).sum()) / n_ok, 4) if n_ok else None,
        })
    return result


def nearest_distance_m(points: np.ndarray, others: np.ndarray) -> np.ndarray:
    """Distance from each point to the nearest of `others` (inf if there are none); both (n, 2) in metres."""
    if len(points) == 0:
        return np.zeros(0)
    if len(others) == 0:
        return np.full(len(points), np.inf)
    from scipy.spatial import cKDTree

    return cKDTree(others).query(points)[0]


def height_summary(values: Sequence[float]) -> dict:
    a = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if a.size == 0:
        return {"count": 0}
    q = np.percentile(a, [5, 25, 50, 75, 95])
    return {"count": int(a.size), "min": round(float(a.min()), 2), "p05": round(float(q[0]), 2),
            "p25": round(float(q[1]), 2), "median": round(float(q[2]), 2), "mean": round(float(a.mean()), 2),
            "p75": round(float(q[3]), 2), "p95": round(float(q[4]), 2), "max": round(float(a.max()), 2)}


# --- raster ------------------------------------------------------------------------------------------

@dataclass
class ChmWindow:
    """A window of the height model with the maps between its pixels and the working (UTM) CRS."""

    chm: np.ndarray  # float32, NaN = no data
    transform: object  # affine, raster CRS
    crs: str
    work_crs: str
    ground_px_m: float

    def _tf(self, src: str, dst: str, x, y):
        from rasterio.warp import transform

        a, b = transform(src, dst, list(map(float, x)), list(map(float, y)))
        return np.asarray(a), np.asarray(b)

    def work_to_pixel(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x, y = self._tf(self.work_crs, self.crs, xy[:, 0], xy[:, 1])
        t = self.transform
        col = np.floor((x - t.c) / t.a).astype(int)
        row = np.floor((y - t.f) / t.e).astype(int)
        return row, col

    def pixel_to_work(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        t = self.transform
        x = t.c + (np.asarray(cols) + 0.5) * t.a
        y = t.f + (np.asarray(rows) + 0.5) * t.e
        a, b = self._tf(self.crs, self.work_crs, x, y)
        return np.column_stack([a, b])


def read_window(tif_path: str | Path, work_bounds: tuple[float, float, float, float], work_crs: str,
                pad_m: float = 30.0) -> ChmWindow:
    """The part of the tile covering `work_bounds` (x0, y0, x1, y1 in `work_crs`) plus `pad_m`."""
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window

    x0, y0, x1, y1 = work_bounds
    xs, ys = [x0 - pad_m, x1 + pad_m], [y0 - pad_m, y1 + pad_m]
    with rasterio.open(tif_path) as src:
        corner_x, corner_y = transform(work_crs, src.crs, [xs[0], xs[0], xs[1], xs[1]], [ys[0], ys[1], ys[0], ys[1]])
        t = src.transform
        c0 = math.floor((min(corner_x) - t.c) / t.a)
        c1 = math.ceil((max(corner_x) - t.c) / t.a)
        r0 = math.floor((max(corner_y) - t.f) / t.e)
        r1 = math.ceil((min(corner_y) - t.f) / t.e)
        window = Window(c0, r0, c1 - c0, r1 - r0)
        data = src.read(1, window=window, boundless=True, fill_value=np.nan).astype(np.float32)
        if src.nodata is not None and np.isfinite(src.nodata):
            data[data == src.nodata] = np.nan
        transform_w = src.window_transform(window)
        crs = src.crs.to_string()
        px = abs(t.a)
        cx, cy = (corner_x[0] + corner_x[3]) / 2, (corner_y[0] + corner_y[3]) / 2
    # Ground size of one pixel: map two neighbouring pixel centres into the working CRS.
    a, b = transform(crs, work_crs, [cx, cx + px], [cy, cy])
    ground = math.hypot(a[1] - a[0], b[1] - b[0])
    return ChmWindow(chm=data, transform=transform_w, crs=crs, work_crs=work_crs, ground_px_m=ground)


# --- orchestration --------------------------------------------------------------------------------------

def _osm_trees(osm: dict) -> tuple[list[dict], list[dict]]:
    elements = osm.get("elements", [])
    nodes = [e for e in elements if e.get("type") == "node" and (e.get("tags") or {}).get("natural") == "tree"]
    rows = [e for e in elements if e.get("type") == "way" and (e.get("tags") or {}).get("natural") == "tree_row"]
    return nodes, rows


def acquisition_dates(metadata: dict, lonlat: np.ndarray, area_lonlat=None) -> tuple[list[str | None], dict]:
    """The footprint `acq_date` containing each (lon, lat), and, if `area_lonlat` is given, each date's share of that area."""
    from shapely import STRtree, points
    from shapely.geometry import shape

    shapes, dates = [], []
    for feature in metadata.get("features", []):
        shapes.append(shape(feature["geometry"]))
        dates.append((feature.get("properties") or {}).get("acq_date"))
    tree = STRtree(shapes)
    found: list[str | None] = [None] * len(lonlat)
    if len(lonlat):
        pts = points(lonlat[:, 0], lonlat[:, 1])
        hit_points, hit_shapes = tree.query(pts, predicate="within")
        for p, s in zip(hit_points, hit_shapes, strict=True):
            found[p] = dates[s] if found[p] is None else min(found[p], dates[s])
    shares: dict[str, float] = {}
    if area_lonlat is not None:
        total = area_lonlat.area
        covered = 0.0
        for geometry, date in zip(shapes, dates, strict=True):
            if geometry.intersects(area_lonlat):
                part = geometry.intersection(area_lonlat).area
                shares[str(date)] = shares.get(str(date), 0.0) + part / total
                covered += part
        shares["no footprint"] = max(0.0, 1.0 - covered / total)
        shares = {k: round(v, 4) for k, v in sorted(shares.items())}
    return found, shares


def footprint_flags(path: str | Path, lonlat: np.ndarray) -> list[bool]:
    """Whether each (lon, lat) lies inside a building footprint of a lon/lat GeoJSON (e.g. Overture, OSM-derived)."""
    from shapely import STRtree, points
    from shapely.geometry import shape

    collection = json.loads(Path(path).read_text(encoding="utf-8"))
    west, south = lonlat.min(axis=0) - 0.001 if len(lonlat) else (0, 0)
    east, north = lonlat.max(axis=0) + 0.001 if len(lonlat) else (0, 0)
    shapes = []
    for feature in collection.get("features", []):
        g = shape(feature["geometry"])
        if g.bounds[2] >= west and g.bounds[0] <= east and g.bounds[3] >= south and g.bounds[1] <= north:
            shapes.append(g)
    flags = np.zeros(len(lonlat), bool)
    if shapes and len(lonlat):
        hit_points, _ = STRtree(shapes).query(points(lonlat[:, 0], lonlat[:, 1]), predicate="within")
        flags[hit_points] = True
    return flags.tolist()


def build_canopy_dataset(
    *, chm_path: str | Path, corridor_path: str | Path, traffic_path: str | Path, osm_path: str | Path,
    metadata_path: str | Path | None, output_dir: str | Path, tile_id: str, crs: str = "EPSG:32636",
    buffer_m: float = 250.0, min_height_m: float = MIN_HEIGHT_M, min_spacing_m: float = MIN_SPACING_M,
    road_buffer_m: float = ROAD_BUFFER_M, unmapped_distance_m: float = UNMAPPED_DISTANCE_M,
    sources: dict | None = None, footprints_path: str | Path | None = None,
) -> dict:
    """Write the corridor's canopy products to `output_dir` and return the summary (also saved as canopy-summary.json)."""
    import rasterio
    from rasterio.features import geometry_mask
    from shapely import contains_xy
    from shapely.geometry import LineString, shape
    from shapely.ops import transform as shp_transform

    from .corridor import Corridor
    from .mobility_inputs import pilot_network
    from .traffic import TrafficModel
    from .viewer import _utm_zone, lonlat_to_utm

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    corridor = Corridor.load(corridor_path, crs, buffer_m)
    area = corridor.area
    zone, north = _utm_zone(crs)

    # Roads: the same subset `serve --corridor` hands the planner (whole roads touching the buffer's box).
    full = TrafficModel.load(traffic_path)
    subset, keep = pilot_network(full, list(corridor.bounds()))
    roads_xy = [list(map(tuple, g)) for g in subset.geometry]
    in_buffer = [LineString(g).intersects(area) for g in roads_xy]
    # The planner serves the whole-city model by default (id = index in the city's road list) and the
    # corridor subset with `--corridor` (id = position in `keep`).
    bx = [p[0] for g in roads_xy for p in g] + list(area.bounds[0::2])
    by = [p[1] for g in roads_xy for p in g] + list(area.bounds[1::2])
    window = read_window(chm_path, (min(bx), min(by), max(bx), max(by)), crs)
    chm, px = window.chm, window.ground_px_m
    valid = np.isfinite(chm)

    # Candidate crown tops inside the buffer.
    spacing_px = min_spacing_m / px
    peaks = find_peaks(chm, min_height_m=min_height_m, spacing_px=spacing_px)
    xy = window.pixel_to_work(peaks[:, 0], peaks[:, 1]) if len(peaks) else np.zeros((0, 2))
    inside = contains_xy(area, xy[:, 0], xy[:, 1]) if len(xy) else np.zeros(0, bool)
    peaks, xy = peaks[inside], xy[inside]
    heights = chm[peaks[:, 0], peaks[:, 1]] if len(peaks) else np.zeros(0)
    lonlat = np.column_stack(window._tf(crs, "EPSG:4326", xy[:, 0], xy[:, 1])) if len(xy) else np.zeros((0, 2))

    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8")) if metadata_path else {"features": []}
    area_ll = shp_transform(lambda x, y, z=None: tuple(np.asarray(t) for t in window._tf(crs, "EPSG:4326", x, y)), area)
    dates, date_shares = acquisition_dates(metadata, lonlat, area_ll)

    # OSM trees: heights from the model, and which candidates have no mapped tree near them.
    osm = json.loads(Path(osm_path).read_text(encoding="utf-8"))
    nodes, tree_rows = _osm_trees(osm)
    tree_xy = np.asarray([lonlat_to_utm(n["lon"], n["lat"], zone, north) for n in nodes]).reshape(-1, 2)
    tree_in = contains_xy(area, tree_xy[:, 0], tree_xy[:, 1]) if len(tree_xy) else np.zeros(0, bool)
    tr_rows, tr_cols = window.work_to_pixel(tree_xy[tree_in]) if tree_in.any() else (np.zeros(0, int), np.zeros(0, int))
    tree_h = max_height_near(chm, tr_rows, tr_cols, TREE_HEIGHT_RADIUS_M / px)
    tree_candidate_d = nearest_distance_m(tree_xy[tree_in], xy)
    row_lines = []
    for way in tree_rows:
        pts = [lonlat_to_utm(p["lon"], p["lat"], zone, north) for p in way.get("geometry", [])]
        if len(pts) > 1 and LineString(pts).intersects(area):
            row_lines.append(LineString(pts))
    cand_to_tree = nearest_distance_m(xy, tree_xy)
    if row_lines and len(xy):
        from shapely import distance, points as shp_points
        from shapely.geometry import MultiLineString
        cand_to_row = distance(shp_points(xy[:, 0], xy[:, 1]), MultiLineString(row_lines))
    else:
        cand_to_row = np.full(len(xy), np.inf)

    on_footprint = footprint_flags(footprints_path, lonlat) if footprints_path else [None] * len(xy)
    features = []
    for i in range(len(xy)):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lonlat[i, 0]), 7), round(float(lonlat[i, 1]), 7)]},
            "properties": {
                "id": f"canopy-{tile_id}-{i + 1:05d}",
                "canopy_height_at_peak_m": round(float(heights[i]), 2),
                "kind": CANDIDATE_KIND,
                "acq_date": dates[i],
                "nearest_mapped_tree_m": None if not np.isfinite(cand_to_tree[i]) else round(float(cand_to_tree[i]), 1),
                "nearest_mapped_tree_row_m": None if not np.isfinite(cand_to_row[i]) else round(float(cand_to_row[i]), 1),
                "likely_unmapped": bool(cand_to_tree[i] > unmapped_distance_m),
                "on_building_footprint": on_footprint[i],
            },
        })
    accessed = datetime.now(timezone.utc).date().isoformat()
    attribution = ATTRIBUTION.format(accessed=accessed)
    meta = {
        "name": "Corridor canopy tree candidates (model estimates)",
        "source": {"name": "Meta and WRI High Resolution Canopy Height Maps v1", "url": SOURCE_URL, "tile": tile_id,
                   "licence": LICENCE, "attribution": attribution},
        "note": "Local maxima of a canopy height model: likely crown tops with estimated heights, not observed trunks.",
    }
    (out / "corridor-canopy-candidates.geojson").write_text(
        json.dumps({"type": "FeatureCollection", **meta, "features": features}, separators=(",", ":")), encoding="utf-8")

    osm_trees = []
    inside_nodes = [n for n, keep_ in zip(nodes, tree_in, strict=True) if keep_]
    for n, h, d in zip(inside_nodes, tree_h, tree_candidate_d, strict=True):
        osm_trees.append({
            "osm_id": n["id"], "lon": n["lon"], "lat": n["lat"],
            "chm_max_height_m": None if not np.isfinite(h) else round(float(h), 2),
            "nearest_candidate_m": None if not np.isfinite(d) else round(float(d), 1),
            "osm_height_tag": (n.get("tags") or {}).get("height"),
        })
    (out / "corridor-osm-tree-heights.json").write_text(json.dumps({
        **meta, "name": "Canopy model heights at OSM-mapped trees in the corridor buffer",
        "height_radius_m": TREE_HEIGHT_RADIUS_M, "trees": osm_trees}, separators=(",", ":")), encoding="utf-8")

    # Canopy within `road_buffer_m` of the centreline, per road.
    near = canopy_near(chm, min_height_m, road_buffer_m / px)
    cover = road_canopy_cover(roads_xy, near, valid, window.work_to_pixel)
    road_records = []
    for new_id, (old_id, rec, inside_buffer) in enumerate(zip(keep, cover, in_buffer, strict=True)):
        road_records.append({
            "city_road_id": int(old_id), "corridor_road_id": new_id, "name": subset.name[new_id] if subset.name else "",
            "cls": subset.highway[new_id], "length_m": round(float(subset.length_m[new_id]), 1),
            "intersects_buffer": bool(inside_buffer), **rec,
        })
    (out / "corridor-road-canopy-cover.json").write_text(json.dumps({
        **meta, "name": "Canopy cover along roads of the corridor traffic subset",
        "keys": {"city_road_id": "index in the citywide traffic model (planner id without --corridor)",
                 "corridor_road_id": "position in the corridor subset (planner id with --corridor)"},
        "canopy_min_height_m": min_height_m, "road_buffer_m": road_buffer_m, "sample_step_m": SAMPLE_STEP_M,
        "definition": "share of centreline samples (every 1 m) with canopy height >= canopy_min_height_m within road_buffer_m",
        "roads": road_records}, separators=(",", ":")), encoding="utf-8")

    # Clipped height model: the buffer's cells, NaN outside it.
    clip_path = out / "chm-corridor-clip.tif"
    mask_inside = ~geometry_mask([_to_raster_crs(area, window)], out_shape=chm.shape, transform=window.transform)
    clipped = np.where(mask_inside, chm, np.nan).astype(np.float32)
    rows_any, cols_any = np.where(mask_inside)
    r0, r1, c0, c1 = rows_any.min(), rows_any.max() + 1, cols_any.min(), cols_any.max() + 1
    from rasterio.windows import Window as W
    from rasterio.windows import transform as win_transform
    sub = clipped[r0:r1, c0:c1]
    with rasterio.open(clip_path, "w", driver="GTiff", height=sub.shape[0], width=sub.shape[1], count=1, dtype="float32",
                       crs=window.crs, transform=win_transform(W(c0, r0, c1 - c0, r1 - r0), window.transform),
                       nodata=float("nan"), compress="lzw") as dst:
        dst.write(sub, 1)
        dst.update_tags(source=SOURCE_URL, licence=LICENCE, attribution=attribution, tile=tile_id,
                        note="canopy height model estimate; clipped to the corridor buffer")

    # Sensitivity of the candidate count to the minimum spacing.
    in_area = lambda p: int(contains_xy(area, *window.pixel_to_work(p[:, 0], p[:, 1]).T).sum()) if len(p) else 0
    sensitivity = {f"{s:g}": in_area(find_peaks(chm, min_height_m=min_height_m, spacing_px=s / px)) for s in (3.0, 4.0, 6.0, 8.0)}

    h_tree = [t["chm_max_height_m"] for t in osm_trees]
    covers = [r["canopy_cover"] for r in road_records if r["canopy_cover"] is not None]
    summary = {
        "tile": tile_id, "accessed": accessed, "raster_crs": window.crs, "ground_pixel_m": round(px, 4),
        "buffer_m": buffer_m, "buffer_area_ha": round(area.area / 1e4, 2),
        "valid_cell_share_in_buffer": round(float((valid & mask_inside).sum() / mask_inside.sum()), 4),
        "parameters": {"min_height_m": min_height_m, "min_spacing_m": min_spacing_m, "road_buffer_m": road_buffer_m,
                       "unmapped_distance_m": unmapped_distance_m, "osm_tree_height_radius_m": TREE_HEIGHT_RADIUS_M},
        "candidates": {"count": len(features), "height_m": height_summary(heights),
                       "count_by_min_spacing_m": sensitivity,
                       "per_hectare": round(len(features) / (area.area / 1e4), 2),
                       "on_building_footprint": None if footprints_path is None else int(sum(bool(v) for v in on_footprint)),
                       "off_building_footprint_height_m": None if footprints_path is None else
                       height_summary([h for h, v in zip(heights, on_footprint, strict=True) if not v]),
                       "by_acq_date": _count([f["properties"]["acq_date"] for f in features])},
        "acquisition_date_share_of_buffer": date_shares,
        "osm_trees": {
            "nodes_in_buffer": len(osm_trees), "tree_rows_touching_buffer": len(row_lines),
            "with_canopy_ge_min_height": int(sum(1 for h in h_tree if h is not None and h >= min_height_m)),
            "height_m": height_summary(h_tree),
            "with_osm_height_tag": sum(1 for t in osm_trees if t["osm_height_tag"]),
        },
        "unmapped": {
            "distance_m": unmapped_distance_m,
            "candidates_farther_than": int((cand_to_tree > unmapped_distance_m).sum()),
            "candidates_farther_than_trees_and_tree_rows": int(((cand_to_tree > unmapped_distance_m) & (cand_to_row > unmapped_distance_m)).sum()),
            "share": round(float((cand_to_tree > unmapped_distance_m).mean()), 4) if len(xy) else None,
            "candidates_farther_than_and_off_building_footprint": None if footprints_path is None else int(
                sum(1 for far, on in zip(cand_to_tree > unmapped_distance_m, on_footprint, strict=True) if far and not on)),
            "by_distance_m": {f"{d:g}": int((cand_to_tree > d).sum()) for d in (3, 5, 8, 10, 15)},
            "mapped_trees_with_candidate_within": {f"{d:g}": int((tree_candidate_d <= d).sum()) for d in (3, 5, 8)},
        },
        "roads": {
            "count": len(road_records), "intersecting_buffer": int(sum(in_buffer)),
            "with_cover": len(covers), "mean_cover": round(float(np.mean(covers)), 4) if covers else None,
            "length_weighted_mean_cover": _weighted_cover(road_records),
            "top10_by_canopy_cover_buffer_roads_ge_50m": _top_roads(road_records),
        },
        "outputs": sorted(p.name for p in out.iterdir() if p.is_file()),
        "limits": [
            "Candidates are model estimates (local maxima of a canopy height model), not observed trunks or confirmed placements.",
            "Per-pixel capture date is not in the raster; acq_date is the capture date of the footprint polygon "
            "containing the point, and a footprint may be a mosaic of several scenes.",
            "Heights are estimated from satellite imagery, stored as whole metres (uint8), biased low for tall trees and uncertain by metres.",
            "Canopy can appear displaced by about 10 m from OSM building footprints (scripts/check_canopy_registration.py): "
            "treat candidate positions and road cover (8 m) as approximate, not lot-accurate.",
            "Some canopy lies on building footprints (roof edges, off-nadir parallax); `on_building_footprint` flags candidates there.",
            "OSM maps very few trees in the corridor, so `likely_unmapped` is true for almost every candidate and says little about OSM completeness.",
            "Imagery is years old (see acq_date): trees may have been felled, planted or grown since.",
            "Tall buildings can leak into the model as canopy; a candidate on a roof edge is possible.",
        ],
        "source": meta["source"],
    }
    if sources:
        summary["downloads"] = sources
    (out / "canopy-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _to_raster_crs(area, window: ChmWindow):
    from shapely.ops import transform as shp_transform

    return shp_transform(lambda x, y, z=None: tuple(np.asarray(t) for t in window._tf(window.work_crs, window.crs, x, y)), area)


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def _weighted_cover(records: list[dict]) -> float | None:
    pairs = [(r["length_m"], r["canopy_cover"]) for r in records if r["canopy_cover"] is not None]
    total = sum(w for w, _ in pairs)
    return round(sum(w * c for w, c in pairs) / total, 4) if total else None


def _top_roads(records: list[dict], n: int = 10, min_length_m: float = 50.0) -> list[dict]:
    ranked = sorted((r for r in records if r["intersects_buffer"] and r["canopy_cover"] is not None and r["length_m"] >= min_length_m),
                    key=lambda r: (-r["canopy_cover"], -r["length_m"]))
    return [{k: r[k] for k in ("city_road_id", "corridor_road_id", "name", "cls", "length_m", "canopy_cover", "valid_fraction")}
            for r in ranked[:n]]
