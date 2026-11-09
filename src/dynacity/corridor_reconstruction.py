"""Scene integration for source-supported facade reconstruction (no component geometry lives here).

Source buildings are extruded footprints. This module finds the street-facing walls of a chosen source
building, places externally generated facade parts onto them and writes a GLB in the same axes as the
city batches (east, up, -north), so Unreal imports it into the batch frame. The source batch triangles are
omitted separately by ``capture_building.omit_source_triangles``; originals are never edited.

Part contract (provided by the facade-geometry module): ``{'name', 'material', 'vertices' (N,3), 'triangles' (M,3)}``
in the producer's right-handed frame: X along the frontage to the viewer's right, Y into the building (street side is -Y),
Z up from the wall base. Triangles are counter-clockwise by the right-hand rule when the normal points outward (-Y).
"""
from __future__ import annotations

import json
import struct

import numpy as np


def building_ring(positions, triangles, merge_m=0.15, wall_nz=0.2):
    """Ordered footprint ring (K,2), counter-clockwise, from the vertical wall triangles of an extruded building."""
    pos = np.asarray(positions, float)
    key = np.round(pos[:, :2] / merge_m).astype(np.int64)
    exact = {}
    for k, q in zip(map(tuple, key), pos[:, :2]):
        exact.setdefault(k, []).append(q)
    edges = {}
    for tri in np.asarray(triangles):
        p = pos[tri]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        norm = np.linalg.norm(n)
        if norm < 1e-9 or abs(n[2]) / norm > wall_nz:
            continue
        cols = {tuple(key[i]) for i in tri}
        if len(cols) != 2:
            continue
        a, b = sorted(cols)
        edges[(a, b)] = edges.get((a, b), 0) + 1
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    if not adj:
        raise ValueError('no vertical wall edges found')
    start = min(adj)
    ring, prev, cur = [start], None, start
    while True:
        nxt = [c for c in adj[cur] if c != prev]
        if not nxt:
            break
        nxt = nxt[0] if prev is not None or len(nxt) == 1 else nxt[0]
        if nxt == start:
            break
        ring.append(nxt)
        prev, cur = cur, nxt
        if len(ring) > len(adj):
            raise ValueError('footprint ring does not close')
    pts = np.array([np.mean(exact[c], axis=0) for c in ring])
    area = .5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
    return pts if area > 0 else pts[::-1]


def street_frontages(ring, toward_xy, base_z, top_z, min_length_m=6., min_facing=0.35):
    """Walls whose outward normal faces ``toward_xy`` (e.g. the camera/street centre), longest first."""
    out = []
    toward = np.asarray(toward_xy, float)
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        d = b - a
        length = float(np.linalg.norm(d))
        if length < min_length_m:
            continue
        t = d / length
        n = np.array([t[1], -t[0]])  # outward for a counter-clockwise ring
        mid = (a + b) / 2
        to = toward - mid
        facing = float(n @ to / (np.linalg.norm(to) + 1e-9))
        if facing >= min_facing:
            out.append({'index': i, 'start': a.tolist(), 'tangent': t.tolist(), 'normal': n.tolist(),
                        'length_m': round(length, 3), 'base_z': float(base_z), 'top_z': float(top_z),
                        'facing': round(facing, 3)})
    return sorted(out, key=lambda f: -f['length_m'])


def frontage_wall_triangles(positions, triangles, frontage, tol_m=0.25, min_dot=0.5):
    """Indices (into ``triangles``) of the vertical triangles lying on the frontage wall: all three vertices within
    ``tol_m`` of the wall line, inside its extent, with a horizontal normal facing outward. Nothing else is selected."""
    pos = np.asarray(positions, float)
    a = np.asarray(frontage['start'], float)
    t = np.asarray(frontage['tangent'], float)
    n = np.asarray(frontage['normal'], float)
    chosen = []
    for k, tri in enumerate(np.asarray(triangles)):
        p = pos[tri]
        nn = np.cross(p[1] - p[0], p[2] - p[0])
        norm = np.linalg.norm(nn)
        if norm < 1e-9 or abs(nn[2]) / norm > 0.2:
            continue
        rel = p[:, :2] - a
        along, off = rel @ t, rel @ n
        if (np.abs(off) <= tol_m).all() and (along >= -tol_m).all() and (along <= frontage['length_m'] + tol_m).all()                 and float(nn[:2] @ n) / norm >= min_dot:
            chosen.append(k)
    return np.array(chosen, dtype=np.int64)


def frontage_basis(frontage):
    """Producer frame -> local east/north/up, columns (X, Y, Z), a proper rotation (det +1).

    Producer frame (reference_facade_geometry): X along the frontage, increasing to the RIGHT of a viewer facing the
    facade; Y into the building (street-facing normal is -Y); Z up; right-handed. With the ring counter-clockwise the
    viewer's right is the wall tangent t and the outward normal n points at the viewer, so X -> t, Y -> -n, Z -> up.
    """
    t = np.asarray(frontage['tangent'], float)
    n = np.asarray(frontage['normal'], float)
    return np.array([[t[0], -n[0], 0.], [t[1], -n[1], 0.], [0., 0., 1.]])


def place_parts(parts, frontage, along_m=0.):
    """Producer frame -> local east/north/up by a pure rotation plus translation: winding is preserved unchanged."""
    basis = frontage_basis(frontage)
    if abs(np.linalg.det(basis) - 1.) > 1e-9:
        raise ValueError('frontage basis is not a proper rotation')
    origin = np.asarray(frontage['start'], float)
    placed = []
    for part in parts:
        v = np.asarray(part['vertices'], float).copy()
        v[:, 0] += along_m
        world = v @ basis.T
        world[:, :2] += origin
        world[:, 2] += frontage['base_z']
        placed.append({**part, 'vertices': world, 'triangles': np.asarray(part['triangles'], np.int64)})
    return placed


def triangle_normals(vertices, triangles):
    """Unit normals from winding (counter-clockwise = outward)."""
    v = np.asarray(vertices, float)
    t = np.asarray(triangles)
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def wall_coverage(parts, width_m, height_m, cell_m=0.1):
    """Fraction of the frontage rectangle (X 0..width, Z 0..height) covered by some part triangle seen from outside.
    A closed street skin needs ~1.0; gaps would show the interior once the source wall is omitted."""
    nx, nz = int(np.ceil(width_m / cell_m)), int(np.ceil(height_m / cell_m))
    gx, gz = np.meshgrid((np.arange(nx) + .5) * cell_m, (np.arange(nz) + .5) * cell_m)
    px, pz = gx.ravel(), gz.ravel()
    covered = np.zeros(px.size, bool)
    for part in parts:
        v = np.asarray(part['vertices'], float)
        for tri in np.asarray(part['triangles']):
            a, b, c = v[tri][:, [0, 2]]
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-12:
                continue
            l1 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (pz - c[1])) / d
            l2 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (pz - c[1])) / d
            covered |= (l1 >= -1e-9) & (l2 >= -1e-9) & (1 - l1 - l2 >= -1e-9)
    return float(covered.mean())


def backing_parts(width_m, height_m, depth_m, material='interior_dark'):
    """Opaque closing skin in the producer frame, ``depth_m`` (+5 cm) INTO the building: a backing plane plus two end
    returns. It shows only through gaps and openings and never sits in front of a recess back wall (no z-fighting).
    Right-hand-rule normals: backing -Y (street-facing), x=0 return +X, x=width return -X (both face the opening)."""
    d = abs(depth_m) + 0.05
    w, h = float(width_m), float(height_m)
    back = np.array([[0, d, 0], [w, d, 0], [w, d, h], [0, d, h]], float)
    near = np.array([[0, 0, 0], [0, d, 0], [0, d, h], [0, 0, h]], float)
    far = np.array([[w, 0, 0], [w, 0, h], [w, d, h], [w, d, 0]], float)
    quad = np.array([[0, 1, 2], [0, 2, 3]])
    return [{'name': n, 'material': material, 'vertices': q, 'triangles': quad}
            for n, q in (('backing', back), ('return_x0', near), ('return_xw', far))]


def write_glb(path, parts, materials, mesh_name='corridor_reconstruction'):
    """Write parts (east/north/up metres) as ONE mesh with one primitive (material slot) per material.

    Axes follow the city batches: glTF (east, up, -north). ``materials`` maps name ->
    {'base_color': [r,g,b,a], 'roughness': float, 'metallic': float}; slot order is sorted by name.
    """
    by_material = {}
    for part in parts:
        by_material.setdefault(part['material'], []).append(part)
    binary = bytearray()
    views, accessors, primitives, mats = [], [], [], []
    for mat_index, (name, group) in enumerate(sorted(by_material.items())):
        spec = materials[name]
        verts, tris, base = [], [], 0
        for part in group:
            v = np.asarray(part['vertices'], float)
            verts.append(np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1))
            # (east, north, up) -> (east, up, -north) is a pure rotation (det +1): winding is preserved, never reversed here
            tris.append(np.asarray(part['triangles'], np.int64) + base)
            base += len(v)
        verts = np.concatenate(verts).astype('<f4')
        tris = np.concatenate(tris).astype('<u4').reshape(-1)
        for array, target, ctype, kind in ((verts, 34962, 5126, 'VEC3'), (tris, 34963, 5125, 'SCALAR')):
            while len(binary) % 4:
                binary.append(0)
            views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes, 'target': target})
            binary.extend(array.tobytes())
            acc = {'bufferView': len(views) - 1, 'componentType': ctype, 'count': int(array.size // (3 if kind == 'VEC3' else 1)), 'type': kind}
            if kind == 'VEC3':
                acc['min'], acc['max'] = verts.min(0).tolist(), verts.max(0).tolist()
            accessors.append(acc)
        primitives.append({'attributes': {'POSITION': len(accessors) - 2}, 'indices': len(accessors) - 1, 'material': mat_index})
        mats.append({'name': name, 'doubleSided': True, 'pbrMetallicRoughness': {
            'baseColorFactor': spec['base_color'], 'metallicFactor': spec.get('metallic', 0.), 'roughnessFactor': spec.get('roughness', .8)}})
    doc = {'asset': {'version': '2.0', 'generator': 'dynacity corridor_reconstruction'}, 'scene': 0,
           'scenes': [{'nodes': [0]}], 'nodes': [{'name': mesh_name, 'mesh': 0}], 'meshes': [{'name': mesh_name, 'primitives': primitives}],
           'materials': mats, 'accessors': accessors, 'bufferViews': views, 'buffers': [{'byteLength': len(binary)}]}
    js = json.dumps(doc, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    raw = (struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(js) + 8 + len(binary)) + struct.pack('<I4s', len(js), b'JSON') + js
           + struct.pack('<I4s', len(binary), b'BIN\0') + bytes(binary))
    with open(path, 'wb') as handle:
        handle.write(raw)
    return {'triangles': int(sum(len(p['triangles']) for p in parts)), 'materials': sorted(by_material), 'mesh': mesh_name}


def polygon_area(ring):
    r = np.asarray(ring, float)
    return float(.5 * np.sum(r[:, 0] * np.roll(r[:, 1], -1) - np.roll(r[:, 0], -1) * r[:, 1]))


def triangulate_polygon(ring):
    """Ear-clipping triangulation of a simple polygon; returns (M,3) vertex indices, counter-clockwise triangles."""
    pts = np.asarray(ring, float)
    idx = list(range(len(pts)))
    if polygon_area(pts) < 0:
        idx.reverse()
    tris = []

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(inside(pts[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            raise ValueError('polygon cannot be triangulated (self-intersecting or degenerate)')
    tris.append(tuple(idx))
    return np.array(tris, dtype=np.int64)


def polygon_prism(ring, z0, z1):
    """Closed extruded polygon (outward counter-clockwise winding): vertices (N,3), triangles (M,3)."""
    r = np.asarray(ring, float)
    if polygon_area(r) < 0:
        r = r[::-1]
    n = len(r)
    tri = triangulate_polygon(r)
    bottom = np.column_stack([r, np.full(n, z0)])
    top = np.column_stack([r, np.full(n, z1)])
    verts = np.vstack([bottom, top])
    faces = [t[::-1] for t in tri] + [t + n for t in tri]  # bottom faces down, top faces up
    for i in range(n):
        j = (i + 1) % n
        faces += [(i, j, j + n), (i, j + n, i + n)]
    return verts, np.array(faces, dtype=np.int64)


def _box_parts(boxes):
    vs, fs, base = [], [], 0
    for x0, x1, y0, y1, z0, z1 in boxes:
        v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
        f = np.array([[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]])
        vs.append(v)
        fs.append(f + base)
        base += 8
    return np.concatenate(vs), np.concatenate(fs)


def raised_frame(ring, z0, z1, floor_h=3.1, col_w=0.45, slab_t=0.25, max_span=4.5, material='concrete_pale'):
    """Open concrete frame over the TRUE footprint from the source roof ``z0`` up to ``z1`` (world east/north/up).

    Perimeter columns at every ring vertex and at most ``max_span`` apart along each edge (each inset by its inward edge
    normal(s) so the column stays inside the footprint), a polygon floor plate every ``floor_h`` (the first one a storey
    above the existing roof, so nothing coincides with it) and a top plate. It is an under-construction massing estimate,
    not a survey. Returns parts in the plain part format (world coordinates).
    """
    r = np.asarray(ring, float)
    if polygon_area(r) < 0:
        r = r[::-1]
    n_pts = len(r)

    def inward(i):  # inward unit normal of edge i -> i+1 for a counter-clockwise ring
        t = r[(i + 1) % n_pts] - r[i]
        t = t / np.linalg.norm(t)
        return np.array([-t[1], t[0]])

    half = col_w / 2 + 0.03
    cols = []
    for i in range(n_pts):
        a, b = r[i], r[(i + 1) % n_pts]
        n_edge = max(1, int(np.ceil(np.linalg.norm(b - a) / max_span)))
        for k in range(n_edge):
            p = a + (b - a) * (k / n_edge)
            off = inward(i) * half
            if k == 0:
                off = off + inward((i - 1) % n_pts) * half  # a vertex column is inset from both of its edges
            q = p + off
            cols.append((q[0] - col_w / 2, q[0] + col_w / 2, q[1] - col_w / 2, q[1] + col_w / 2, z0, z1))
    parts = []
    v, f = _box_parts(cols)
    parts.append({'name': 'raised_columns', 'material': material, 'vertices': v, 'triangles': f})
    levels = [z0 + floor_h * k for k in range(1, int((z1 - z0) / floor_h + 1e-6) + 1)]
    if not levels or z1 - levels[-1] > 0.5:
        levels.append(z1)
    vs, fs, base = [], [], 0
    for z in levels:
        pv, pf = polygon_prism(r, z - slab_t, z)
        vs.append(pv)
        fs.append(pf + base)
        base += len(pv)
    parts.append({'name': 'raised_floor_plates', 'material': material, 'vertices': np.concatenate(vs), 'triangles': np.concatenate(fs)})
    return parts


def envelope_prism(ring, z0, z1, skip_edges=()):
    """Closed-envelope massing above an existing roof: a roof cap at ``z1`` plus side faces for every ring edge EXCEPT
    ``skip_edges`` (the edges whose facade is built separately). No bottom face: the existing roof is the floor.
    Outward counter-clockwise winding, world east/north/up."""
    r = np.asarray(ring, float)
    skip = set(skip_edges)
    if polygon_area(r) < 0:
        raise ValueError('ring must be counter-clockwise (as returned by building_ring)')
    n = len(r)
    tri = triangulate_polygon(r)
    verts = np.vstack([np.column_stack([r, np.full(n, z0)]), np.column_stack([r, np.full(n, z1)])])
    faces = [t + n for t in tri]
    for i in range(n):
        if i in skip:
            continue
        j = (i + 1) % n
        faces += [(i, j, j + n), (i, j + n, i + n)]
    return verts, np.array(faces, dtype=np.int64)
