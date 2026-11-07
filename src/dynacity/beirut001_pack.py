"""Portable Beirut 001 pack: building outlines and heights, bridge decks, ground.

Beirut 001 (Salim al-Kadi and team, January 2020) is a ~730 MB Rhino model.
Almost every building in it is a straight extrusion with one flat roof (17,500
of 17,575), so an outline and two heights describe it. `build_pack` reads the
model once and keeps only that, laid onto BBED's EPSG:32636, plus the outlines
and height ranges of bridges and tunnels and a ground grid, in one compressed
`.npz` of a few MB. The source file can then be archived.

The model has no geographic anchor: the alignment is inferred from building-
centre patterns (`beirut_overlay.register_rigid`), not surveyed, and BBED links
are provisional footprint agreement (`beirut_overlay.match_footprints`).
Heights are the model's own. Its ground agrees with Copernicus GLO-30 (metres
above the geoid) to a median 0.4 m, so it is read as height above sea level,
though the source declares no datum. Its buildings are sunk 5 m into that
ground, so a building's height is its roof minus the ground under it
(`ground_z`), not the solid's full extent. The pack
holds model geometry and the BBED OBJECTIDs it links to, no BBED attributes.
The model names no licence, so packs stay private, like the source.

The ground grid uses `terrain.GroundModel`'s keys, so `GroundModel.load` reads a
pack directly. Building a pack needs `rhino3dm` and `scipy` (`overlay` extra);
loading one needs numpy and shapely.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, shape

from .beirut_overlay import match_footprints, register_rigid
from .terrain import GroundModel

SCHEMA = "dynacity-beirut001-pack/1"
CRS = "EPSG:32636"
SOURCE_URL = "https://www.salimalkadi.com/07_beirut-001"
ATTRIBUTION = "Beirut 001: Salim al-Kadi and team, January 2020."
BUILDINGS_LAYER = "05_BUILDINGS"
BRIDGES_LAYER = "04_TUNNELS AND BRIDGES"
TOPOGRAPHY_LAYER = "01_TOPOGRAPHY"
# A triangle this flat is roof or floor, not wall.
FLAT_Z_M = .05
# Roof faces closer than this to the base are the floor.
ROOF_ABOVE_BASE_M = .3


@dataclass(frozen=True)
class Beirut001Pack:
    """Model buildings and bridge decks as EPSG:32636 outlines with model heights.

    `bbed_objectid[i]` is the BBED OBJECTID building i is provisionally linked
    to, or -1. `roof_levels[i] > 1` marks a stepped roof flattened to `top_z`.
    `ground_z[i]` is the model ground under the building (NaN off the ground mesh).
    """

    meta: dict
    building_ids: np.ndarray
    buildings: list
    base_z: np.ndarray
    top_z: np.ndarray
    ground_z: np.ndarray
    roof_levels: np.ndarray
    bbed_objectid: np.ndarray
    link_iou: np.ndarray
    link_distance_m: np.ndarray
    bridges: list = field(default_factory=list)
    bridge_z_min: np.ndarray = field(default_factory=lambda: np.zeros(0))
    bridge_z_max: np.ndarray = field(default_factory=lambda: np.zeros(0))
    ground: GroundModel | None = None
    bridge_triangles: np.ndarray = field(default_factory=lambda: np.zeros((0, 3, 3)))

    def heights(self) -> np.ndarray:
        """Height above the model ground; the full solid where there is no ground."""

        return np.where(np.isfinite(self.ground_z), self.top_z - self.ground_z, self.top_z - self.base_z)

    def save(self, path: str | Path) -> None:
        origin = np.asarray(self.meta["origin_xy"], dtype=float)
        arrays = {
            "meta": json.dumps(self.meta, separators=(",", ":"), allow_nan=False),
            "building_ids": np.asarray(self.building_ids, dtype="<U36"),
            "base_z": self.base_z.astype(np.float32), "top_z": self.top_z.astype(np.float32),
            "ground_z": self.ground_z.astype(np.float32),
            "roof_levels": self.roof_levels.astype(np.uint8),
            "bbed_objectid": self.bbed_objectid.astype(np.int64),
            "link_iou": self.link_iou.astype(np.float32), "link_distance_m": self.link_distance_m.astype(np.float32),
            "bridge_z_min": self.bridge_z_min.astype(np.float32), "bridge_z_max": self.bridge_z_max.astype(np.float32),
            **_flatten("building", self.buildings, origin), **_flatten("bridge", self.bridges, origin),
        }
        if self.ground is not None:
            arrays.update(crs=self.ground.crs, x0=self.ground.x0, y0=self.ground.y0, cell_m=self.ground.cell_m,
                          ground=self.ground.ground.astype(np.float32), source=self.ground.source)
        if len(self.bridge_triangles):
            triangles = self.bridge_triangles.copy()
            triangles[..., :2] -= origin
            arrays['bridge_mesh_triangles'] = triangles.astype(np.float32)
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path: str | Path) -> Beirut001Pack:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("schema") != SCHEMA:
                raise ValueError(f"not a Beirut 001 pack: {path}")
            origin = np.asarray(meta["origin_xy"], dtype=float)
            ground = GroundModel.load(path) if "ground" in data else None
            return cls(
                meta=meta, building_ids=data["building_ids"], buildings=_unflatten("building", data, origin),
                bridge_triangles=(data['bridge_mesh_triangles'].astype(float) + np.array([*origin, 0])
                                  if 'bridge_mesh_triangles' in data else np.zeros((0, 3, 3))),
                base_z=data["base_z"].astype(float), top_z=data["top_z"].astype(float),
                ground_z=data["ground_z"].astype(float),
                roof_levels=data["roof_levels"], bbed_objectid=data["bbed_objectid"],
                link_iou=data["link_iou"].astype(float), link_distance_m=data["link_distance_m"].astype(float),
                bridges=_unflatten("bridge", data, origin),
                bridge_z_min=data["bridge_z_min"].astype(float), bridge_z_max=data["bridge_z_max"].astype(float),
                ground=ground,
            )


def build_pack(model_path: str | Path, bbed_path: str | Path, *, cell_m: float = 10.0) -> Beirut001Pack:
    """Read the Rhino model once and keep outlines, heights, links and ground."""

    import rhino3dm

    model_path = Path(model_path)
    model = rhino3dm.File3dm.Read(str(model_path))
    if model is None:
        raise ValueError(f"could not read Rhino model {model_path}")
    collection = json.loads(Path(bbed_path).read_text(encoding="utf-8"))
    crs = collection.get("crs", {}).get("properties", {}).get("name", "")
    if not crs.endswith("32636"):
        raise ValueError(f"BBED must be in {CRS} (got {crs!r}); reproject it first")
    features = [f for f in collection["features"] if f.get("geometry")]
    polygons = [shape(f["geometry"]) for f in features]
    valid = [i for i, p in enumerate(polygons) if p.is_valid and not p.is_empty]

    buildings, bridges, terrain, skipped = [], [], [], {}
    bridge_triangles = []
    for obj in model.Objects:
        layer = model.Layers[obj.Attributes.LayerIndex].Name
        if layer not in (BUILDINGS_LAYER, BRIDGES_LAYER, TOPOGRAPHY_LAYER):
            continue
        triangles = _triangles(obj.Geometry, rhino3dm)
        if triangles is None or not len(triangles):
            kind = type(obj.Geometry).__name__
            skipped[f"{layer}:{kind}"] = skipped.get(f"{layer}:{kind}", 0) + 1
            continue
        if layer == TOPOGRAPHY_LAYER:
            terrain.append(triangles)
            continue
        if layer == BRIDGES_LAYER:
            bridge_triangles.append(triangles)
        outline = _outline(triangles)
        if outline is None:
            skipped[f"{layer}:no outline"] = skipped.get(f"{layer}:no outline", 0) + 1
            continue
        z = triangles[..., 2]
        entry = (str(obj.Attributes.Id), outline, float(z.min()), float(z.max()), _roof_levels(triangles))
        (buildings if layer == BUILDINGS_LAYER else bridges).append(entry)
    if not buildings:
        raise ValueError(f"no buildings found on layer {BUILDINGS_LAYER}")

    rotation, translation, registration = register_rigid(
        [b[1].centroid.coords[0] for b in buildings], [polygons[i].centroid.coords[0] for i in valid])
    matrix = [rotation[0, 0], rotation[0, 1], rotation[1, 0], rotation[1, 1], translation[0], translation[1]]
    structures = np.concatenate(bridge_triangles) if bridge_triangles else np.zeros((0, 3, 3))
    structures[..., :2] = structures[..., :2] @ rotation.T + translation
    placed = [affinity.affine_transform(b[1], matrix) for b in buildings]
    links = match_footprints(placed, [polygons[i] for i in valid])
    objectids = np.array([
        int(features[valid[link["bbed_index"]]]["properties"]["OBJECTID"]) if "bbed_index" in link else -1
        for link in links
    ], dtype=np.int64)
    ground = None
    ground_z = np.full(len(placed), np.nan)
    if terrain:
        ground = _ground_grid(np.concatenate(terrain), rotation, translation, cell_m)
        inside = [g.representative_point() for g in placed]
        ground_z = ground.sample([q.x for q in inside], [q.y for q in inside])

    bounds = shapely.total_bounds(placed)
    digest = hashlib.sha256()
    with model_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    matched = int((objectids >= 0).sum())
    meta = {
        "schema": SCHEMA, "crs": CRS, "origin_xy": [float(bounds[0]), float(bounds[1])],
        "source": {"name": model_path.name, "sha256": digest.hexdigest(), "bytes": model_path.stat().st_size,
                   "release": "January 2020", "url": SOURCE_URL},
        "attribution": ATTRIBUTION,
        "registration": registration,
        "matching": "Provisional footprint links: IoU >= 0.65, centroid <= 8 m, runner-up gap >= 0.15, one-to-one only. "
                    "Spatial agreement, not identity verification.",
        "vertical": "Model heights in metres; datum not declared by the source. Buildings are sunk into the "
                    "model ground: height = top_z - ground_z.",
        "counts": {"buildings": len(buildings), "linked": matched, "unlinked": len(buildings) - matched,
                   "stepped_roofs_flattened": int(sum(b[4] > 1 for b in buildings)),
                   "bridges": len(bridges), "skipped": skipped},
    }
    return Beirut001Pack(
        meta=meta,
        building_ids=np.array([b[0] for b in buildings]),
        buildings=placed,
        base_z=np.array([b[2] for b in buildings]), top_z=np.array([b[3] for b in buildings]), ground_z=ground_z,
        roof_levels=np.array([b[4] for b in buildings], dtype=np.uint8),
        bbed_objectid=objectids,
        link_iou=np.array([link.get("iou", 0.) for link in links]),
        link_distance_m=np.array([link.get("centroid_distance_m", np.nan) for link in links]),
        bridges=[affinity.affine_transform(b[1], matrix) for b in bridges],
        bridge_z_min=np.array([b[2] for b in bridges]), bridge_z_max=np.array([b[3] for b in bridges]),
        ground=ground, bridge_triangles=structures,
    )


# Deck heights outside this range are taken as a mismatch (a tunnel piece, or
# ground missing under the bridge), and the road keeps the viewer's assumption.
DECK_RANGE_M = (3.0, 40.0)


def bridge_decks(pack: Beirut001Pack, model, *, reach_m: float = 4.0, min_overlap_m: float = 20.0,
                 min_share: float = 0.5) -> dict[int, float]:
    """Deck height above the ground for the traffic model's bridge roads, from the pack.

    A road OSM marks as a bridge (level above 0) takes the median, over the
    pack's bridge pieces it runs along for `min_share` of its length or `min_overlap_m`,
    of each piece's top minus the model ground under that stretch. Roads with
    no such piece, or an implausible height, are left out. The pack's
    placement is inferred (see `docs/beirut001-pack.md`), so a deck is matched
    within `reach_m` of the road.
    """

    from shapely.geometry import LineString
    from shapely.strtree import STRtree

    if getattr(model, "level", None) is None or not pack.bridges or pack.ground is None:
        return {}
    # Some model outlines cross themselves; repair before widening them.
    pieces = [shapely.make_valid(piece).buffer(reach_m) for piece in pack.bridges]
    tree = STRtree(pieces)
    decks = {}
    for road in np.flatnonzero(np.asarray(model.level) > 0):
        line = LineString(model.geometry[road])
        heights = []
        for j in tree.query(line):
            part = pieces[j].intersection(line)
            if part.length < min(min_overlap_m, min_share * line.length):
                continue
            points = [part.interpolate(t, normalized=True) for t in np.linspace(0, 1, 9)]
            ground = pack.ground.sample([q.x for q in points], [q.y for q in points])
            if np.isfinite(ground).any():
                heights.append(float(pack.bridge_z_max[j] - np.nanmedian(ground)))
        if heights and DECK_RANGE_M[0] <= np.median(heights) <= DECK_RANGE_M[1]:
            decks[int(road)] = round(float(np.median(heights)), 1)
    return decks


def _model_height_source(pack: Beirut001Pack, i: int) -> dict:
    """Model height is a recorded height (roof minus model ground); without ground under
    the building it is the full solid, so it is flagged as an estimate."""

    if np.isfinite(pack.ground_z[i]):
        return {"height_kind": "recorded", "estimated": False,
                "height_source": "Beirut 001 model (roof minus model ground)"}
    return {"height_kind": "fallback", "estimated": True,
            "height_source": "Beirut 001 model, full solid: no model ground under this building"}


def viewer_payload(pack: Beirut001Pack, bbed_collection: dict | None = None) -> dict:
    """Buildings in lon/lat for the map, with the allowlisted BBED record of each linked one."""

    from .viewer import _utm_zone, utm_to_lonlat

    zone, north = _utm_zone(pack.meta["crs"])

    def ring(coords) -> list:
        return [[round(v, 7) for v in utm_to_lonlat(x, y, zone, north)] for x, y in np.asarray(coords)[:-1, :2]]

    heights = pack.heights()
    buildings = []
    for i, geom in enumerate(pack.buildings):
        linked = int(pack.bbed_objectid[i]) >= 0
        buildings.append({
            "id": str(pack.building_ids[i]),
            "parts": [[ring(p.exterior.coords), *[ring(h.coords) for h in p.interiors]] for p in getattr(geom, "geoms", [geom])],
            "height_m": round(float(heights[i]), 2), **_model_height_source(pack, i),
            "top_z": round(float(pack.top_z[i]), 2),
            "ground_z": None if not np.isfinite(pack.ground_z[i]) else round(float(pack.ground_z[i]), 2),
            "roof_levels": int(pack.roof_levels[i]),
            "objectid": int(pack.bbed_objectid[i]) if linked else None,
            "iou": round(float(pack.link_iou[i]), 3) if linked else None,
            "centroid_distance_m": round(float(pack.link_distance_m[i]), 2) if linked else None,
        })
    linked_ids = {b["objectid"] for b in buildings if b["objectid"] is not None}
    records = {}
    for feature in (bbed_collection or {}).get("features", []):
        props = feature.get("properties") or {}
        if props.get("OBJECTID") in linked_ids:
            records[str(props["OBJECTID"])] = props
    return {
        "attribution": pack.meta["attribution"], "source": pack.meta["source"],
        "matched": pack.meta["counts"]["linked"], "matching": pack.meta["matching"],
        "vertical": pack.meta["vertical"], "buildings": buildings, "bbed": records,
        "bridge_mesh": bridge_mesh_payload(pack),
    }


def bridge_mesh_payload(pack: Beirut001Pack) -> dict | None:
    """Source transport geometry above the display ground; no invented columns."""
    import base64
    from .viewer import _utm_zone, utm_to_lonlat

    if not len(pack.bridge_triangles) or pack.ground is None:
        return None
    triangles = pack.bridge_triangles.copy()
    ground = pack.ground.sample(triangles[..., 0].ravel(), triangles[..., 1].ravel()).reshape(-1, 3)
    triangles[..., 2] -= ground
    triangles = triangles[np.isfinite(triangles).all(axis=(1, 2))]
    # Clip tunnel geometry at the flat display ground rather than lifting it.
    above = triangles[..., 2] >= 0
    result = [triangles[above.all(axis=1)]]
    clipped = []
    for triangle in triangles[above.any(axis=1) & ~above.all(axis=1)]:
        polygon = []
        for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
            if a[2] >= 0:
                polygon.append(a)
            if (a[2] >= 0) != (b[2] >= 0):
                polygon.append(a + (b-a)*(-a[2]/(b[2]-a[2])))
        for i in range(1, len(polygon)-1):
            clipped.append([polygon[0], polygon[i], polygon[i+1]])
    if clipped:
        result.append(np.asarray(clipped))
    triangles = np.concatenate(result)
    if not len(triangles):
        return None
    origin = np.asarray(pack.meta['origin_xy'])
    zone, north = _utm_zone(pack.meta['crs'])
    lon, lat = utm_to_lonlat(*origin, zone, north)
    east = utm_to_lonlat(origin[0]+1, origin[1], zone, north)
    north_point = utm_to_lonlat(origin[0], origin[1]+1, zone, north)
    scale = np.array([111320*np.cos(np.radians(lat)), 111320])
    basis = np.array([(np.array(east)-[lon, lat])*scale,
                      (np.array(north_point)-[lon, lat])*scale])
    triangles[..., :2] = (triangles[..., :2]-origin) @ basis
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    good = lengths > 1e-8
    triangles, normals = triangles[good], normals[good]/lengths[good, None]
    encode = lambda a: base64.b64encode(np.asarray(a, dtype='<f4').tobytes()).decode('ascii')
    return {'origin': [lon, lat, 0], 'vertices': len(triangles)*3,
            'positions': encode(triangles), 'normals': encode(np.repeat(normals[:, None, :], 3, axis=1)),
            'triangles': len(triangles),
            'provenance': 'January 2020 source transport meshes; inferred horizontal alignment. '
                          'Heights relative to the model ground; underground portions clipped. '
                          'No columns or girders have been invented.'}


def _triangles(geometry, rhino3dm) -> np.ndarray | None:
    """(n, 3, 3) triangles from a mesh or the cached render mesh of a Brep/extrusion."""

    kind = type(geometry).__name__
    if kind == "Brep":
        meshes = [face.GetMesh(rhino3dm.MeshType.Render) for face in geometry.Faces]
    elif kind == "Extrusion":
        meshes = [geometry.GetMesh(rhino3dm.MeshType.Render)]
    elif kind == "Mesh":
        meshes = [geometry]
    else:
        return None
    out = []
    for mesh in meshes:
        if mesh is None or not len(mesh.Faces):
            continue
        v = np.array([(p.X, p.Y, p.Z) for p in mesh.Vertices], dtype=float)
        faces = np.array([mesh.Faces[i] for i in range(len(mesh.Faces))], dtype=np.int64)
        quads = faces[faces[:, 2] != faces[:, 3]]
        out.append(v[np.concatenate([faces[:, :3], quads[:, [0, 2, 3]]])])
    return np.concatenate(out) if out else None


def _outline(triangles: np.ndarray):
    """Plan outline: the union of every triangle seen from above (walls add nothing)."""

    xy = triangles[..., :2]
    u, v = xy[:, 1] - xy[:, 0], xy[:, 2] - xy[:, 0]
    area = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]) / 2
    flat = xy[area > 1e-4]
    if not len(flat):
        return None
    shape_ = shapely.union_all(shapely.polygons(np.concatenate([flat, flat[:, :1]], axis=1)))
    shape_ = shape_.simplify(.01)
    parts = [g for g in getattr(shape_, "geoms", [shape_]) if isinstance(g, Polygon) and g.area > .01]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _roof_levels(triangles: np.ndarray) -> int:
    z = triangles[..., 2]
    flat = (z.max(1) - z.min(1)) < FLAT_Z_M
    roofs = z[flat, 0]
    return len(set(np.round(roofs[roofs > z.min() + ROOF_ABOVE_BASE_M] * 2).tolist()))


def _ground_grid(triangles: np.ndarray, rotation, translation, cell_m: float) -> GroundModel:
    """Highest terrain surface at each cell centre of a north-up EPSG:32636 grid; NaN where none."""

    xy = triangles[..., :2] @ rotation.T + translation
    z = triangles[..., 2]
    x0, y0 = np.floor(xy[..., 0].min() / cell_m) * cell_m, np.ceil(xy[..., 1].max() / cell_m) * cell_m
    cols = int(np.ceil((xy[..., 0].max() - x0) / cell_m))
    rows = int(np.ceil((y0 - xy[..., 1].min()) / cell_m))
    ground = np.full((rows, cols), -np.inf)
    # Cell centres inside each triangle's box; every pair is tested at once.
    c_lo = np.ceil((xy[..., 0].min(1) - x0) / cell_m - .5).astype(np.int64)
    c_hi = np.floor((xy[..., 0].max(1) - x0) / cell_m - .5).astype(np.int64)
    r_lo = np.ceil((y0 - xy[..., 1].max(1)) / cell_m - .5).astype(np.int64)
    r_hi = np.floor((y0 - xy[..., 1].min(1)) / cell_m - .5).astype(np.int64)
    n_c, n_r = np.maximum(c_hi - c_lo + 1, 0), np.maximum(r_hi - r_lo + 1, 0)
    for chunk in np.array_split(np.arange(len(xy)), max(1, int((n_c * n_r).sum() // 2_000_000) + 1)):
        count = n_c[chunk] * n_r[chunk]
        tri = np.repeat(chunk, count)
        k = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
        col = c_lo[tri] + k % n_c[tri]
        row = r_lo[tri] + k // n_c[tri]
        px, py = x0 + (col + .5) * cell_m, y0 - (row + .5) * cell_m
        a, b, c = xy[tri, 0], xy[tri, 1], xy[tri, 2]
        det = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = np.abs(det) > 1e-9
        safe = np.where(ok, det, 1.)
        w1 = ((b[:, 1] - c[:, 1]) * (px - c[:, 0]) + (c[:, 0] - b[:, 0]) * (py - c[:, 1])) / safe
        w2 = ((c[:, 1] - a[:, 1]) * (px - c[:, 0]) + (a[:, 0] - c[:, 0]) * (py - c[:, 1])) / safe
        w3 = 1 - w1 - w2
        inside = ok & (w1 >= -1e-9) & (w2 >= -1e-9) & (w3 >= -1e-9)
        height = w1 * z[tri, 0] + w2 * z[tri, 1] + w3 * z[tri, 2]
        np.maximum.at(ground, (row[inside], col[inside]), height[inside])
    ground[~np.isfinite(ground)] = np.nan
    return GroundModel(crs=CRS, x0=float(x0), y0=float(y0), cell_m=float(cell_m), ground=ground,
                       source=ATTRIBUTION + " Ground mesh sampled on an inferred alignment; vertical datum not declared.")


def _flatten(prefix: str, shapes: list, origin: np.ndarray) -> dict:
    """Outlines as offset arrays: object i owns parts [part_start[i], part_start[i+1]),
    part p owns rings [ring_start[p], ring_start[p+1]) (exterior first), ring r owns
    points [point_start[r], point_start[r+1]) of `xy` (metres from `origin`)."""

    part_start, ring_start, point_start, points = [0], [0], [0], []
    for geom in shapes:
        for part in getattr(geom, "geoms", [geom]):
            for ring in [part.exterior, *part.interiors]:
                coords = np.asarray(ring.coords)[:-1, :2] - origin
                points.append(coords)
                point_start.append(point_start[-1] + len(coords))
            ring_start.append(len(point_start) - 1)
        part_start.append(len(ring_start) - 1)
    xy = np.concatenate(points).astype(np.float32) if points else np.zeros((0, 2), np.float32)
    return {f"{prefix}_part_start": np.array(part_start, np.int64), f"{prefix}_ring_start": np.array(ring_start, np.int64),
            f"{prefix}_point_start": np.array(point_start, np.int64), f"{prefix}_xy": xy}


def _unflatten(prefix: str, data, origin: np.ndarray) -> list:
    parts, rings, points = (data[f"{prefix}_{k}_start"] for k in ("part", "ring", "point"))
    xy = data[f"{prefix}_xy"].astype(float) + origin
    out = []
    for i in range(len(parts) - 1):
        polys = []
        for p in range(parts[i], parts[i + 1]):
            loops = [xy[points[r]:points[r + 1]] for r in range(rings[p], rings[p + 1])]
            polys.append(Polygon(loops[0], loops[1:]))
        out.append(polys[0] if len(polys) == 1 else MultiPolygon(polys))
    return out
