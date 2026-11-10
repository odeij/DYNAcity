"""A bounded, opt-in adapter for one retained historical balcony estimate."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import struct

from pyproj import Transformer

PARENT_ID = '106849cb-0a30-45f7-ba11-dc219083a1c1'
ASSET_SHA256 = '89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f'
ORIGIN = [730784.7582802251, 3751551.1589951646, 51.10578950470706]
PHOTO_URI = 'https://kartaview.org/map/1288770597'
PHOTO_LICENSE = 'CC BY-SA 4.0 (Grab and KartaView Contributors)'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _index(value, length):
    _require(type(value) is int and 0 <= value < length, 'Invalid GLB index')
    return value


def _parse_glb(raw):
    """Accept only the audited identity-node, packed POSITION/index layout."""
    _require(len(raw) >= 28, 'Truncated GLB')
    _require(struct.unpack_from('<III', raw) == (0x46546C67, 2, len(raw)), 'Invalid GLB header')
    size, kind = struct.unpack_from('<II', raw, 12)
    _require(kind == 0x4E4F534A and size % 4 == 0 and 28 + size <= len(raw), 'Invalid GLB JSON chunk')
    gltf = json.loads(raw[20:20+size])
    bin_size, kind = struct.unpack_from('<II', raw, 20+size)
    _require(kind == 0x004E4942 and bin_size % 4 == 0 and 28+size+bin_size == len(raw), 'Invalid GLB BIN chunk')
    binary = raw[28+size:]
    _require(isinstance(gltf, dict) and not any(gltf.get(k) for k in ('animations', 'skins', 'extensionsUsed', 'extensionsRequired')), 'Unsupported GLB features')
    _require(gltf.get('scene', 0) == 0 and len(gltf.get('scenes', [])) == 1 and gltf['scenes'][0].get('nodes') == [0], 'Unsupported GLB scene')
    _require(len(gltf.get('nodes', [])) == 1 and len(gltf.get('meshes', [])) == 1, 'Unsupported GLB hierarchy')
    node = gltf['nodes'][0]
    _require(node.get('mesh') == 0 and set(node) <= {'mesh', 'name'}, 'Transformed or unsupported GLB node')
    buffers = gltf.get('buffers', [])
    _require(len(buffers) == 1 and 'uri' not in buffers[0] and buffers[0].get('byteLength') == len(binary), 'Unsupported GLB buffer')
    primitives = gltf['meshes'][0].get('primitives', [])
    _require(len(primitives) == 1, 'Expected one primitive')
    primitive = primitives[0]
    _require(primitive.get('mode', 4) == 4 and not primitive.get('targets') and not primitive.get('extensions'), 'Unsupported primitive')
    _require(set(primitive.get('attributes', {})) == {'POSITION'}, 'Unsupported primitive attributes')

    def accessor(index, component, layout, count, format_string, width):
        accessors, views = gltf.get('accessors', []), gltf.get('bufferViews', [])
        acc = accessors[_index(index, len(accessors))]
        _require(acc.get('componentType') == component and acc.get('type') == layout and type(acc.get('count')) is int and acc['count'] == count, 'Unsupported accessor layout/count')
        _require(not acc.get('sparse') and not acc.get('normalized') and acc.get('byteOffset', 0) == 0, 'Unsupported accessor offset/sparse/normalization')
        view = views[_index(acc.get('bufferView'), len(views))]
        start, length = view.get('byteOffset', 0), view.get('byteLength')
        _require(view.get('buffer', 0) == 0 and 'byteStride' not in view, 'Unsupported buffer view')
        _require(type(start) is int and type(length) is int and start >= 0 and length >= count*width and start % 4 == 0 and start+length <= len(binary), 'Invalid accessor byte range')
        return list(struct.iter_unpack(format_string, binary[start:start+count*width]))

    positions = accessor(primitive['attributes']['POSITION'], 5126, 'VEC3', 728, '<fff', 12)
    indices = [v[0] for v in accessor(primitive.get('indices'), 5125, 'SCALAR', 3276, '<I', 4)]
    _require(all(_finite(v) for p in positions for v in p), 'Non-finite position')
    _require(all(0 <= i < len(positions) for i in indices), 'Triangle index outside vertices')
    return positions, indices


def _validate_components(positions, indices):
    parents = list(range(len(positions)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    triangles = [indices[i:i+3] for i in range(0, len(indices), 3)]
    for a, b, c in triangles:
        u = [positions[b][i]-positions[a][i] for i in range(3)]
        v = [positions[c][i]-positions[a][i] for i in range(3)]
        cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        _require(math.hypot(*cross) > 1e-10, 'Degenerate panel triangle')
        parents[find(b)] = find(a)
        parents[find(c)] = find(a)
    vertices = Counter(find(i) for i in range(len(positions)))
    faces = Counter(find(t[0]) for t in triangles)
    edges = Counter(tuple(sorted(e)) for a, b, c in triangles for e in ((a, b), (b, c), (c, a)))
    _require(len(vertices) == 91 and set(vertices.values()) == {8} and set(faces.values()) == {12} and set(edges.values()) == {2}, 'Expected 91 closed eight-vertex panel boxes')


def load_granite_overlay(manifest_path: str | Path, parent_snapshot_path: str | Path) -> dict:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    _require(isinstance(manifest, dict) and manifest.get('schema') == 'dynacity-corridor-appearance-bundle/1', 'Invalid bundle schema')
    frame = manifest.get('coordinate_system', {})
    _require(manifest.get('horizontal_crs') == frame.get('horizontal_crs') == 'EPSG:32636' and frame.get('units') == 'metres', 'Invalid CRS/units')
    _require(frame.get('gltf_axis') == 'Y-up (X=East, Y=Up, Z=-North)' and frame.get('local_enu_axis') == 'X=East, Y=North, Z=Up', 'Invalid declared axes')
    origin = manifest.get('origin_utm_m')
    _require(isinstance(origin, list) and len(origin) == 3 and all(_finite(v) and abs(v-expected) < 1e-8 for v, expected in zip(origin, ORIGIN)), 'Retained asset origin changed')
    candidates = [c for c in manifest.get('candidates', []) if c.get('key') == 'granite91']
    _require(len(candidates) == 1, 'Expected exactly one granite91 candidate')
    candidate = candidates[0]
    _require(candidate.get('source_building_uuid') == 'beirut001:'+PARENT_ID and candidate.get('geometry_kind') == 'optional_per_balcony_panels' and candidate.get('source_coordinate_units') == 'metres', 'Invalid candidate identity/geometry')
    _require(candidate.get('source_photo_date') == '2018-07-21' and candidate.get('source_license') == PHOTO_LICENSE and candidate.get('source_uri') == PHOTO_URI, 'Unexpected source provenance')
    asset = candidate.get('glb_asset', {})
    name = asset.get('path')
    _require(isinstance(name, str) and name and not PurePosixPath(name).is_absolute() and not PureWindowsPath(name).drive and not PureWindowsPath(name).is_absolute() and '..' not in PurePosixPath(name.replace('\\', '/')).parts, 'Asset path must be portable and contained')
    path, directory = (manifest_path.parent/name).resolve(), manifest_path.parent.resolve()
    _require(path.is_relative_to(directory), 'Asset path escapes bundle')
    raw = path.read_bytes()  # Verify and parse this same immutable byte buffer.
    digest = hashlib.sha256(raw).hexdigest()
    _require(asset.get('sha256') == digest == ASSET_SHA256 and asset.get('size_bytes') == len(raw), 'Asset hash/size mismatch')
    positions, indices = _parse_glb(raw)
    _validate_components(positions, indices)
    snapshot = json.loads(Path(parent_snapshot_path).read_text(encoding='utf-8'))
    matches = [b for b in snapshot.get('buildings', []) if b.get('id') == PARENT_ID]
    _require(len(matches) == 1, 'Expected one matching plain parent UUID')
    parent = matches[0]
    ground, top, height = (parent.get(k) for k in ('ground_z', 'top_z', 'height_m'))
    _require(all(_finite(v) for v in (ground, top, height)) and top > ground and height > 0 and abs(top-ground-height) <= .02, 'Invalid parent height reference')
    parts = parent.get('parts')
    _require(isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], list) and parts[0], 'Unsupported parent parts')
    for ring in parts[0]:
        _require(isinstance(ring, list) and len(ring) >= 3, 'Invalid parent ring')
        for xy in ring:
            _require(isinstance(xy, list) and len(xy) == 2 and all(_finite(v) for v in xy) and abs(xy[0]) <= 180 and abs(xy[1]) <= 85, 'Invalid parent coordinates')
    _require(all(ground-.01 <= origin[2]+p[1] <= top+.01 for p in positions), 'Panel outside parent model vertical span')
    to_geo = Transformer.from_crs(32636, 4326, always_xy=True)
    to_utm = Transformer.from_crs(4326, 32636, always_xy=True)
    boundary = [to_utm.transform(*xy) for xy in parts[0][0]]
    xs, ys = [p[0] for p in boundary], [p[1] for p in boundary]
    _require(all(min(xs)-1 <= origin[0]+p[0] <= max(xs)+1 and min(ys)-1 <= origin[1]-p[2] <= max(ys)+1 for p in positions), 'Panel geometry outside parent neighborhood')
    vertices = []
    for east, up, south in positions:
        lon, lat = to_geo.transform(origin[0]+east, origin[1]-south)
        vertices.append([lon, lat, origin[2]+up-ground])
    return {
        'schema': 'dynacity-granite-overlay/1',
        'parent': {k: parent[k] for k in ('id', 'parts', 'ground_z', 'top_z', 'height_m')},
        'anchor': [sum(v[i] for v in vertices)/len(vertices) for i in (0, 1)],
        'vertices_lonlat_height': [vertices[i] for i in indices],
        'triangle_count': len(indices)//3, 'panel_count': 91, 'asset_sha256': digest,
        'source': {'date': '2018-07-21', 'license': PHOTO_LICENSE, 'uri': PHOTO_URI,
                   'attribution': 'Grab and KartaView Contributors',
                   'claim': 'Estimated balcony panels from a historical photograph; not surveyed.'},
        'limits': ['Panel dimensions and source-building identity are estimates.',
                   'The original mapped building remains unchanged; this is an optional visual layer.',
                   'The photograph records 2018 conditions. Current appearance and physical support are unverified.'],
    }
