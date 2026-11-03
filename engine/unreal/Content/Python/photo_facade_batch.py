"""Citywide photo-informed facade appearance on the existing source building batches.

Source buildings are 12 district batch meshes whose material slots are shared flat
colours, so one building's identity cannot live in a material slot and the meshes
must not be split. Instead each district gets a small data texture baked from the
real building roof footprints (world X/Y -> family, floor height, ground level,
variation seed) and one shared master material reads it. Walls are shaded from
the texel just inside their own surface normal; roofs, unassigned buildings and
excluded detailed buildings keep their exact source colour. The only editor
change is a component material override per used slot, saved beforehand and
restored exactly by rollback. No geometry, transform, collision or instance is
added, so the geometry/instance budget is zero by construction.

Two halves. The offline half (validate, plan, bake) needs numpy and no editor.
The native half (inventory, apply, verify, rollback, capture) imports `unreal`
lazily and must run on the single serialized editor owner, never during Play.

Limits that are true by construction:
 - Bay phase is world-aligned per wall, not anchored to each building corner;
   windows can be cut at corners. Footprint corners are not stored.
 - Cell size (1-4 m) limits footprint fidelity; thin or overlapping buildings can
   lose texels. The coverage report counts them.
 - Every pattern is an estimate informed by reference photos, never a survey, and
   a photo is never applied verbatim to a wall.
"""
import hashlib
import json
import math
import struct
import sys
import zlib
from pathlib import Path

VERSION = 'photo-facade-batch/1'
EXPECTED_INVENTORY = 17397
LIBRARY_SCHEMA_PREFIXES = ('dynacity-appearance-library', 'dynacity-photo-appearance-library')
JOBS_SCHEMA_PREFIX = 'dynacity-building-appearance-jobs'
EVIDENCE_CLASSES = ('confirmed_historical', 'reference_informed_estimate', 'unchanged')
CLASS_CODE = {'confirmed_historical': 1, 'reference_informed_estimate': 2}
MAX_FAMILIES = 63
MAX_TEXTURE_PX = 2048
CELLS_M = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
MAX_TOTAL_TEXTURE_MB = 160
MAX_MATERIAL_INSTANCES = 96
MARGIN_TEXELS = 4
DILATE_TEXELS = 2
INSET_TEXELS = 1.25
FLOOR_H_RANGE = (2.0, 4.55)

ROOT = '/Game/DynaCity/PhotoFacadeBatch'
MASTER = 'M_PhotoFacadeBatch_v1'
TAG = 'DYNACITY_PHOTO_FACADE_BATCH'
STATE_DIR = 'photo-facade-batch'
DEFAULT_BATCHES = Path('C:/private/dynacity-data/engine/city-batches')
DEFAULT_ENGINE = Path('C:/private/dynacity-data/engine')
DEFAULT_SAVED = Path('C:/Users/PC/DYNAcity/engine/unreal/Saved/DynaCity')

FAMILY_DEFAULTS = {'trim_colour': '#d8d2c4', 'window_colour': '#2a3338', 'floor_height_m': 3.2,
                   'bay_width_m': 3.0, 'glazing_fraction': 0.5, 'ground_floor': 'plain',
                   'shutter_fraction': 0.15, 'balcony_fraction': 0.2, 'weathering': 0.3,
                   'roughness': 0.85}


class ContractError(ValueError):
    """Input does not satisfy the shared appearance contract; nothing is applied."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# --------------------------------------------------------------------------- contract

def _require(condition, message):
    if not condition:
        raise ContractError(message)


def parse_colour(value, name):
    """Hex string, or three numbers (0-1 or 0-255), to an sRGB byte triple."""
    if isinstance(value, str):
        text = value.lstrip('#')
        _require(len(text) == 6 and all(c in '0123456789abcdefABCDEF' for c in text), name + ' is not #rrggbb')
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    _require(isinstance(value, (list, tuple)) and len(value) in (3, 4)
             and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value),
             name + ' is not a colour')
    values = list(value[:3])
    scale = 255 if max(values) <= 1.0 else 1
    result = tuple(int(round(min(max(v * scale, 0), 255))) for v in values)
    return result


BALCONY_FRACTION = {'none': 0.0, 'small_projecting': 0.3, 'slab_strip': 0.35, 'recessed': 0.5}
BALCONY_CODE = {'none': 0, 'small_projecting': 1, 'slab_strip': 2, 'recessed': 3}


def _from_material_estimates(material):
    """Agent 1 `appearance_estimates.material` -> this installer's appearance keys.

    window_to_wall_ratio is window area over wall area; an opening spans about 0.6 of a
    floor, so the bay-width fraction is ratio / 0.6 (clamped to 0.15-0.9).
    Shutter and shopfront cues are not in that schema and fall back to documented defaults.
    """
    if not isinstance(material, dict):
        return None
    out = {'wall_colour': material.get('base_color_rgb'), 'trim_colour': material.get('trim_color_rgb'),
           'window_colour': material.get('glass_color_rgb'), 'roughness': material.get('roughness'),
           'weathering': material.get('weathering'), 'bay_width_m': material.get('bay_width_m'),
           'floor_height_m': material.get('floor_height_m')}
    ratio = material.get('window_to_wall_ratio')
    if isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
        out['glazing_fraction'] = min(max(ratio / 0.6, 0.15), 0.9)
    balcony = material.get('balcony_type')
    if balcony in BALCONY_FRACTION:
        out['balcony_fraction'] = BALCONY_FRACTION[balcony]
        out['balcony_type'] = balcony
        # Estimate rules from Agent 1's own cues: no balconies plus a high window ratio is curtain-wall
        # glazing; no balconies with a modest ratio is commercial (storefront ground floor).
        out['curtain_wall'] = balcony == 'none' and isinstance(ratio, (int, float)) and ratio > 0.6
        out['ground_floor'] = 'shopfront' if balcony == 'none' and not out['curtain_wall'] else 'plain'
    return {k: v for k, v in out.items() if v is not None}


def family_style(family):
    """Resolve a library family's appearance estimate into bounded shader parameters.

    Accepted keys under `appearance` (or `appearance_estimate`): wall_colour (required),
    trim_colour, window_colour, floor_height_m, bay_width_m, glazing_fraction,
    ground_floor ('shopfront'|'plain'), shutter_fraction, balcony_fraction,
    weathering, roughness. Missing optional keys take documented defaults and are
    listed in `defaulted`, so they are never mistaken for photo-derived values.
    """
    appearance = family.get('appearance') or family.get('appearance_estimate')
    if appearance is None and isinstance(family.get('appearance_estimates'), dict):
        appearance = _from_material_estimates(family['appearance_estimates'].get('material'))
    _require(isinstance(appearance, dict), 'Family %s has no appearance estimate' % family.get('id'))
    wall = appearance.get('wall_colour', appearance.get('wall_color'))
    _require(wall is not None, 'Family %s has no wall_colour' % family.get('id'))
    defaulted = []

    def get(key, alias=None):
        for name in (key, alias):
            if name and name in appearance:
                return appearance[name]
        defaulted.append(key)
        return FAMILY_DEFAULTS[key]

    def number(key, low, high, alias=None):
        value = get(key, alias)
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high,
                 'Family %s %s must be within %s..%s' % (family.get('id'), key, low, high))
        return float(value)

    style = {'wall': parse_colour(wall, 'wall_colour'),
             'trim': parse_colour(get('trim_colour', 'trim_color'), 'trim_colour'),
             'window': parse_colour(get('window_colour', 'window_color'), 'window_colour'),
             'floor_height_m': number('floor_height_m', 2.2, 6.0),
             'bay_width_m': number('bay_width_m', 1.2, 12.0),
             'glazing_fraction': number('glazing_fraction', 0.1, 0.9),
             'shutter_fraction': number('shutter_fraction', 0, 1),
             'balcony_fraction': number('balcony_fraction', 0, 1),
             'weathering': number('weathering', 0, 1),
             'roughness': number('roughness', 0.2, 1.0)}
    shop = get('ground_floor')
    _require(shop in ('shopfront', 'plain'), 'Family %s ground_floor must be shopfront or plain' % family.get('id'))
    style['shopfront'] = shop == 'shopfront'
    style['balcony_type'] = appearance.get('balcony_type', 'none')
    _require(style['balcony_type'] in BALCONY_CODE, 'Family %s has an unknown balcony_type' % family.get('id'))
    style['curtain'] = bool(appearance.get('curtain_wall', False))
    style['defaulted'] = sorted(defaulted)
    return style


def validate_library(library, base_dir=None):
    _require(isinstance(library, dict), 'Library is not an object')
    _require(str(library.get('schema', '')).startswith(LIBRARY_SCHEMA_PREFIXES), 'Unexpected library schema')
    catalog = library.get('source_catalog')
    _require(isinstance(catalog, dict) and catalog.get('path') and catalog.get('sha256'),
             'Library needs source_catalog path and sha256')
    path = Path(catalog['path'])
    if not path.is_absolute() and base_dir:
        path = Path(base_dir) / path
    _require(path.is_file(), 'Source catalog missing: %s' % path)
    _require(sha256_file(path) == catalog['sha256'], 'Source catalog hash differs from the library record')
    photos = library.get('photos') if isinstance(library.get('photos'), dict) else library
    counts = {}
    for key in ('processed', 'reviewed', 'usable'):
        value = photos.get(key, photos.get('photos_' + key))
        _require(isinstance(value, int) and not isinstance(value, bool) and value >= 0, 'Library missing photos_' + key)
        counts[key] = value
    _require(counts['usable'] <= counts['processed'] and counts['reviewed'] <= counts['processed'], 'Photo counts are inconsistent')
    families = library.get('families')
    _require(isinstance(families, list) and families, 'Library has no families')
    _require(isinstance(library.get('exclusions'), (list, dict)), 'Library missing exclusions')
    _require(isinstance(library.get('uncertainties'), list), 'Library missing uncertainties')
    ids = []
    for family in families:
        fid = family.get('id')
        _require(isinstance(fid, str) and fid and family.get('name'), 'Family needs id and name')
        for key in ('reference_photo_ids', 'source_capture_dates'):
            _require(isinstance(family.get(key), list) and family[key], 'Family %s missing %s' % (fid, key))
        _require(family.get('license') and family.get('attribution'), 'Family %s missing licence/attribution' % fid)
        family_style(family)
        ids.append(fid)
    _require(len(set(ids)) == len(ids), 'Duplicate family ids')
    _require(len(ids) <= MAX_FAMILIES, 'More than %d families cannot be encoded' % MAX_FAMILIES)
    return {family['id']: family for family in families}


def job_fields(job):
    return {'source_id': job.get('source_id'),
            'family': job.get('family_id', job.get('proposed_family', job.get('family'))),
            'reason': job.get('unchanged_reason', job.get('reason')),
            'class': job.get('evidence_class'),
            'protected': bool(job.get('protected') or job.get('manual_override')),
            'references': job.get('reference_paths') or []}


def validate_jobs(jobs_doc, families, inventory_ids, expected_count=EXPECTED_INVENTORY):
    jobs = jobs_doc.get('jobs') if isinstance(jobs_doc, dict) else jobs_doc
    if isinstance(jobs_doc, dict):
        _require(str(jobs_doc.get('schema', '')).startswith(JOBS_SCHEMA_PREFIX), 'Unexpected jobs schema')
    _require(isinstance(jobs, list), 'Jobs are not a list')
    table = {}
    for raw in jobs:
        job = job_fields(raw)
        sid = job['source_id']
        _require(isinstance(sid, str) and sid, 'Job without source_id')
        _require(sid not in table, 'Duplicate job for ' + sid)
        _require(job['class'] in EVIDENCE_CLASSES, 'Bad evidence_class for %s: %r' % (sid, job['class']))
        if job['class'] in ('unchanged', 'confirmed_historical'):
            _require(job['reason'], 'Unchanged/confirmed job without reason: ' + sid)
        else:
            _require(job['family'] in families, 'Unknown family for %s: %r' % (sid, job['family']))
            _require(job['references'], 'Applied job without reference paths: ' + sid)
        table[sid] = job
    inventory = set(inventory_ids)
    _require(len(inventory) == expected_count, 'Source inventory has %d ids, expected %d' % (len(inventory), expected_count))
    missing = inventory - set(table)
    extra = set(table) - inventory
    _require(not missing, '%d inventory ids have no job, e.g. %s' % (len(missing), sorted(missing)[:3]))
    _require(not extra, '%d jobs are not in the inventory, e.g. %s' % (len(extra), sorted(extra)[:3]))
    return table


def discover_exclusions(engine_root=DEFAULT_ENGINE, saved_dir=DEFAULT_SAVED, extra_files=()):
    """Source IDs that already carry detailed or photo-authored facades -> {id: reason}."""
    result = {}

    def add(sid, reason, origin):
        if isinstance(sid, str) and sid:
            result.setdefault(sid, {'reason': reason, 'origin': str(origin)})

    def read(path):
        try:
            return json.loads(Path(path).read_text(encoding='utf8'))
        except (OSError, ValueError):
            return None

    root = Path(engine_root)
    for path in sorted(root.glob('facade-*/**/placements.json')) + sorted(root.glob('facade-*/placements.json')):
        data = read(path)
        for item in (data or {}).get('placements', []) if isinstance(data, dict) else []:
            if isinstance(item, dict):
                add(item.get('source_id'), 'detailed_facade_modules', path)
    for path in sorted(root.glob('facade-reference-*/*.json')):
        data = read(path)
        if isinstance(data, dict) and isinstance(data.get('source_id'), str):
            add(data['source_id'], 'lgb_photo_authored_reference', path)
    for path in sorted(Path(saved_dir).glob('captured-building-install-*.json')) if Path(saved_dir).is_dir() else []:
        data = read(path)
        if isinstance(data, dict):
            add(data.get('source_id'), 'captured_building_replacement', path)
            for sid in data.get('placeholder_source_ids') or []:
                add(sid, 'captured_building_replacement', path)
    for path in extra_files:
        data = read(path)
        _require(data is not None, 'Unreadable exclusion file ' + str(path))
        ids = data.get('source_ids', []) if isinstance(data, dict) else data
        for sid in ids:
            add(sid, 'manual_exclusion', path)
    return result


# --------------------------------------------------------------------------- offline bake

def read_batch_glb(path):
    """Minimal reader: {mesh name: (east/north/up metres (n,3), triangles (m,3), base colour)}.

    The exporter writes glTF Y-up (x east, y up, z = -north); that is rotated (det +1,
    winding kept) into the index's east/north/up frame so bounds can be cross-checked.
    """
    import numpy as np
    raw = Path(path).read_bytes()
    magic, version, total = struct.unpack_from('<4sII', raw)
    _require(magic == b'glTF' and version == 2 and total == len(raw), 'Invalid GLB ' + str(path))
    size, kind = struct.unpack_from('<I4s', raw, 12)
    _require(kind == b'JSON', 'GLB JSON chunk missing')
    doc = json.loads(raw[20:20 + size])
    offset = 20 + size
    length, kind = struct.unpack_from('<I4s', raw, offset)
    _require(kind == b'BIN\0', 'GLB BIN chunk missing')
    binary = memoryview(raw)[offset + 8:offset + 8 + length]
    dtypes = {5126: '<f4', 5125: '<u4', 5123: '<u2', 5121: 'u1'}
    widths = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3}

    def accessor(index):
        item = doc['accessors'][index]
        view = doc['bufferViews'][item['bufferView']]
        _require(not item.get('sparse') and not view.get('byteStride'), 'Sparse/strided accessors unsupported')
        width = widths[item['type']]
        array = np.frombuffer(binary, dtype=dtypes[item['componentType']], count=item['count'] * width,
                              offset=view.get('byteOffset', 0) + item.get('byteOffset', 0))
        return array.reshape(-1, width) if width > 1 else array

    meshes = {}
    for mesh in doc['meshes']:
        _require(len(mesh['primitives']) == 1, 'Batch mesh needs one primitive')
        prim = mesh['primitives'][0]
        raw_positions = accessor(prim['attributes']['POSITION']).astype('float64')
        meshes[mesh['name']] = (np.column_stack([raw_positions[:, 0], -raw_positions[:, 2], raw_positions[:, 1]]),
                                accessor(prim['indices']).reshape(-1, 3).astype('int64'),
                                doc['materials'][prim['material']]['pbrMetallicRoughness']['baseColorFactor'][:3]
                                if 'material' in prim else [.6, .6, .6])
    return meshes


def choose_grid(bounds, cells=CELLS_M, max_px=MAX_TEXTURE_PX):
    (x0, y0), (x1, y1) = bounds
    for cell in cells:
        width = int(math.ceil((x1 - x0) / cell)) + 2 * MARGIN_TEXELS
        height = int(math.ceil((y1 - y0) / cell)) + 2 * MARGIN_TEXELS
        if max(width, height) <= max_px:
            return {'cell_m': cell, 'width': width, 'height': height,
                    'min_east_m': x0 - MARGIN_TEXELS * cell, 'max_north_m': y1 + MARGIN_TEXELS * cell}
    raise ContractError('District extent exceeds %d px at %s m cells' % (max_px, cells[-1]))


def _rasterise(owner, ztop, tri, zs, grid, index):
    """Mark texel centres inside one triangle's plan projection (columns east, rows south)."""
    import numpy as np
    cell = grid['cell_m']
    cols = (tri[:, 0] - grid['min_east_m']) / cell
    rows = (grid['max_north_m'] - tri[:, 1]) / cell
    c0, c1 = int(math.floor(cols.min())), int(math.ceil(cols.max()))
    r0, r1 = int(math.floor(rows.min())), int(math.ceil(rows.max()))
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, grid['width'] - 1), min(r1, grid['height'] - 1)
    if c1 < c0 or r1 < r0:
        return 0
    gc, gr = np.meshgrid(np.arange(c0, c1 + 1) + .5, np.arange(r0, r1 + 1) + .5)
    (ax, ay), (bx, by), (cx, cy) = zip(cols, rows)
    det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(det) < 1e-12:
        return 0
    l1 = ((by - cy) * (gc - cx) + (cx - bx) * (gr - cy)) / det
    l2 = ((cy - ay) * (gc - cx) + (ax - cx) * (gr - cy)) / det
    inside = (l1 >= -.02) & (l2 >= -.02) & (1 - l1 - l2 >= -.02)
    region = owner[r0:r1 + 1, c0:c1 + 1]
    if index != PRESERVE:
        inside = inside & (region != PRESERVE)
    region[inside] = index
    height = l1 * zs[0] + l2 * zs[1] + (1 - l1 - l2) * zs[2]
    top = ztop[r0:r1 + 1, c0:c1 + 1]
    top[inside] = height[inside]
    return int(inside.sum())


PRESERVE = -2  # owner value: footprint of a building that is NOT painted; painted ownership and dilation never overwrite it
LGB_BATCHES = ('buildings_remeil_material_9',)  # the LGB replaced-batch actor: skipped natively, so its buildings must not leak paint
CORNER_DEG = 25.0
MIN_WALL_M = 1.0  # roof jogs shorter than this never win the nearest-wall choice over a real wall
MAX_PIECE_M = 120.0  # walls longer than this are split into equal runs so the 0.5 m, 8 bit length never clamps
MAX_WALL_M = 163.8   # s0 is 14 bits of centimetres; walls are split at MAX_PIECE_M (120 m) so this never clamps
MAX_LENGTH_M = 127.5
MAX_FLOORS = 63       # 6 bits
NORMAL_BINS = 16      # outward normal direction in 22.5 degree steps (4 bits)


def wall_chains(roof_tris):
    """Walls from the roof outline of one building: [{'pts': (k,2), 'cum': (k,), 'L': metres}].

    Boundary edges are roof-triangle edges with no twin (keyed on x, y AND z so a step between
    roof levels also yields a wall). A CCW roof triangle keeps its interior on the left of a->b,
    so walls run b->a with the interior on the right; that makes the along-wall direction
    (north_n, -east_n) of the outward normal, the convention the shader uses. Chains are split
    where the outline turns by more than CORNER_DEG, so gentle arcs stay one wall.
    """
    import numpy as np
    key = lambda p: (round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3))
    directed = set()
    for tri in roof_tris:
        keys = [key(v) for v in tri]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if keys[a] != keys[b]:
                directed.add((keys[a], keys[b]))
    walls_edges = [(b, a) for (a, b) in directed if (b, a) not in directed]
    nexts = {}
    for index, (a, b) in enumerate(sorted(walls_edges)):
        nexts.setdefault(a, []).append(b)
    rings, used = [], set()
    for start in sorted(nexts):
        for first in list(nexts[start]):
            if (start, first) in used:
                continue
            ring, a, b = [start], start, first
            while (a, b) not in used:
                used.add((a, b))
                ring.append(b)
                a, b = b, None
                options = [n for n in nexts.get(ring[-1], []) if (ring[-1], n) not in used]
                if not options:
                    break
                b = options[0]
            rings.append(ring)
    walls = []
    for ring in rings:
        pts = np.array([[p[0], p[1]] for p in ring], dtype='float64')
        closed = len(ring) > 2 and ring[0] == ring[-1]
        if closed:
            pts = pts[:-1]
        if len(pts) < 2:
            continue
        vectors = np.diff(np.vstack([pts, pts[:1]]) if closed else pts, axis=0)
        lengths = np.linalg.norm(vectors, axis=1)
        keep = lengths > 1e-6
        angles = np.arctan2(vectors[:, 1], vectors[:, 0])
        k = len(vectors)
        corner = [False] * k
        for i in range(k):
            if i == 0 and not closed:
                continue
            turn = abs((angles[i] - angles[(i - 1) % k] + np.pi) % (2 * np.pi) - np.pi)
            corner[i] = bool(np.degrees(turn) > CORNER_DEG)
        starts = [i for i in range(k) if corner[i]]
        if closed and not starts:
            pieces = [list(range(k))]
        else:
            order = list(range(starts[0], k)) + list(range(starts[0])) if closed else list(range(k))
            pieces, current = [], []
            for edge in order:
                if corner[edge] and current:
                    pieces.append(current)
                    current = []
                current.append(edge)
            pieces.append(current)
        for piece in pieces:
            seg_a = np.array([pts[e] for e in piece])
            seg_b = np.array([pts[(e + 1) % len(pts)] for e in piece])
            seg_len = np.linalg.norm(seg_b - seg_a, axis=1)
            cum = np.concatenate([[0.0], np.cumsum(seg_len)])
            if cum[-1] > 1e-6:
                walls.extend(split_long_wall({'a': seg_a, 'b': seg_b, 'len': seg_len, 'cum': cum, 'L': float(cum[-1])}))
    return walls


def split_long_wall(wall, max_len=MAX_PIECE_M):
    """Cut a wall longer than max_len into equal runs (virtual piers); each run restarts its own along-wall distance."""
    import numpy as np
    if wall['L'] <= max_len:
        return [wall]
    parts = int(math.ceil(wall['L'] / max_len))
    edges = np.linspace(0.0, wall['L'], parts + 1)
    out = []
    for lo_s, hi_s in zip(edges[:-1], edges[1:]):
        a_pts, b_pts = [], []
        for a, b, start, length in zip(wall['a'], wall['b'], wall['cum'][:-1], wall['len']):
            end = start + length
            if end <= lo_s + 1e-9 or start >= hi_s - 1e-9:
                continue
            direction = (b - a) / length
            a_pts.append(a + direction * (max(lo_s, start) - start))
            b_pts.append(a + direction * (min(hi_s, end) - start))
        seg_a, seg_b = np.array(a_pts), np.array(b_pts)
        seg_len = np.linalg.norm(seg_b - seg_a, axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg_len)])
        out.append({'a': seg_a, 'b': seg_b, 'len': seg_len, 'cum': cum, 'L': float(cum[-1])})
    return out


def box_walls(lo, hi):
    """Walls of a footprint box (interior on the right) for buildings whose roof gave no triangles."""
    import numpy as np
    x0, y0, x1, y1, z = lo[0], lo[1], hi[0], hi[1], hi[2]
    quad = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
    return wall_chains(np.array([[quad[0], quad[1], quad[2]], [quad[0], quad[2], quad[3]]], dtype='float64'))


def nearest_wall_data(walls, centres):
    """For texel centres (m,2): along-wall distance from the wall's corner, wall length and the wall's OUTWARD normal (E, N)."""
    import numpy as np
    count = len(centres)
    if not walls:
        return np.zeros(count), np.zeros(count), np.zeros(count), np.zeros(count)
    A = np.vstack([w['a'] for w in walls])
    B = np.vstack([w['b'] for w in walls])
    seg_len = np.concatenate([w['len'] for w in walls])
    start = np.concatenate([w['cum'][:-1] for w in walls])
    total = np.concatenate([np.full(len(w['len']), w['L']) for w in walls])
    d = B - A
    run = d / np.maximum(seg_len, 1e-12)[:, None]
    outward = np.column_stack([-run[:, 1], run[:, 0]])      # interior is on the right of the run, so outward is on the left
    denom = np.maximum((d * d).sum(axis=1), 1e-12)
    rel = centres[:, None, :] - A[None, :, :]
    t = np.clip((rel * d[None]).sum(axis=2) / denom[None], 0, 1)
    nearest = A[None] + t[..., None] * d[None]
    dist2 = ((centres[:, None, :] - nearest) ** 2).sum(axis=2) + np.where(total < MIN_WALL_M, 625.0, 0.0)[None, :]
    pick = dist2.argmin(axis=1)
    rows = np.arange(count)
    return start[pick] + t[rows, pick] * seg_len[pick], total[pick], outward[pick, 0], outward[pick, 1]


def nearest_wall_params(walls, centres):
    """(along-wall distance from the corner, wall length) only; see nearest_wall_data for the wall normal too."""
    s0, length, _, _ = nearest_wall_data(walls, centres)
    return s0, length


def normal_bin(normal_e, normal_n):
    import numpy as np
    return np.round(np.arctan2(normal_n, normal_e) / (2 * np.pi / NORMAL_BINS)).astype('int64') % NORMAL_BINS


def floors_below_roof(top_z, base_z, floor_h):
    """Whole floors that fit under the local roof (5% of a floor of tolerance); windows never go above this."""
    import numpy as np
    return np.floor((np.asarray(top_z) - base_z) / floor_h + 0.05)


def encode_wall_texels(s0_m, length_m, floors, bins=None):
    """RGBA8: R = s0 cm low byte; G = s0 high 6 bits | normal-bin bits 0-1 << 6; B = wall length in 0.5 m; A = floors (6 bits) | normal-bin bits 2-3 << 6.

    s0 is the along-wall position from the wall's corner (14 bits of cm, <= 163.8 m), the normal bin is the wall's outward
    direction in 22.5 degree steps so the shader can refuse data that belongs to a DIFFERENT wall (texels within the inset
    distance of a corner are nearest to the neighbouring wall).
    """
    import numpy as np
    s0 = np.clip(np.round(np.minimum(s0_m, MAX_WALL_M) * 100), 0, 16383).astype('int64')
    bins = np.zeros(len(s0), dtype='int64') if bins is None else np.asarray(bins, dtype='int64') % NORMAL_BINS
    out = np.zeros((len(s0), 4), dtype='uint8')
    out[:, 0] = s0 & 255
    out[:, 1] = ((s0 >> 8) & 63) | ((bins & 3) << 6)
    out[:, 2] = np.where(length_m > 0, np.clip(np.round(np.minimum(length_m, MAX_LENGTH_M) * 2), 1, 255), 0)
    out[:, 3] = np.clip(floors, 0, MAX_FLOORS) | (((bins >> 2) & 3) << 6)
    return out


def decode_wall_texel(r, g, b, a):
    r, g, b, a = int(r), int(g), int(b), int(a)
    return {'s0_m': (r + 256 * (g & 63)) / 100.0, 'length_m': b / 2.0, 'floors': a & 63, 'normal_bin': (g >> 6) | ((a >> 6) << 2)}


def _dilate(owner, passes=DILATE_TEXELS):
    import numpy as np
    for _ in range(passes):
        empty = owner == -1          # PRESERVE footprints are neither empty nor a source
        if not empty.any():
            break
        padded = np.pad(owner, 1, constant_values=-1)
        best = np.full(owner.shape, -1, dtype=owner.dtype)
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            shifted = padded[1 + dr:1 + dr + owner.shape[0], 1 + dc:1 + dc + owner.shape[1]]
            take = (best < 0) & (shifted >= 0)
            best[take] = shifted[take]
        owner[empty] = best[empty]  # best only ever holds owners >= 0


def encode_texel(family_index, class_code, floor_h_m, base_z_cm, seed):
    """R family|class<<6, G floor height (2.0 m + G/100), B/A ground level cm (low 3 bits = seed)."""
    code = int(round((min(max(floor_h_m, FLOOR_H_RANGE[0]), FLOOR_H_RANGE[1]) - FLOOR_H_RANGE[0]) * 100))
    zq = (int(base_z_cm) // 8) * 8 + (seed & 7)
    _require(0 <= zq <= 65535, 'Ground level outside 16-bit range')
    return family_index | (class_code << 6), code, zq >> 8, zq & 255


def decode_texel(r, g, b, a):
    zq = (b << 8) | a
    return {'family': r & 63, 'class': r >> 6, 'floor_h_m': FLOOR_H_RANGE[0] + g / 100.0,
            'base_z_cm': (zq // 8) * 8, 'seed': zq & 7}


def stable_seed(source_id, family_id):
    return hashlib.sha256((source_id + '|' + str(family_id)).encode()).digest()[0] & 7


def fitted_floor_height(height_m, nominal):
    floors = max(1, int(round(height_m / nominal)))
    return min(max(height_m / floors, FLOOR_H_RANGE[0]), FLOOR_H_RANGE[1])


def write_png(path, rgba):
    import numpy as np
    array = np.ascontiguousarray(rgba, dtype='uint8')
    height, width, _ = array.shape
    raw = b''.join(b'\x00' + array[row].tobytes() for row in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)

    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def family_table(families, order):
    """64x8 RGBA8 data texture; colours stay sRGB bytes and are linearised in the shader."""
    import numpy as np
    table = np.zeros((8, 64, 4), dtype='uint8')
    for slot, fid in enumerate(order, start=1):
        s = family_style(families[fid])
        table[0, slot] = (*s['wall'], int(round(s['roughness'] * 255)))
        table[1, slot] = (*s['trim'], int(round(s['weathering'] * 255)))
        table[2, slot] = (*s['window'], int(round(s['glazing_fraction'] * 255)))
        stated = 'shutter_fraction' not in s['defaulted']   # louvres only where the library says so; a default is not evidence
        table[3, slot] = (min(int(round(s['bay_width_m'] / .05)), 255), int(round(s['shutter_fraction'] * 255)) if stated else 0,
                          int(round(s['balcony_fraction'] * 255)), 255 if s['shopfront'] else 0)
        table[4, slot] = (BALCONY_CODE[s['balcony_type']] * 80, 255 if s['curtain'] else 0, 0, 0)
    return table


def bake(library_path, jobs_path, out_dir, batches_dir=DEFAULT_BATCHES, exclusion_files=(),
         engine_root=DEFAULT_ENGINE, saved_dir=DEFAULT_SAVED, bounds=None, districts=None,
         expected_count=EXPECTED_INVENTORY, area_name='citywide', validate_only=False, preserve_batches=LGB_BATCHES):
    """Validate everything, then write lookup PNGs, the family table, plan.json and assignments.json.

    Fails closed (nothing written) when any input is missing or inconsistent. Re-running
    with identical inputs writes byte-identical files. `bounds` = (east0, north0, east1,
    north1) in source metres limits assignments to a test area; every other building is
    left unchanged, so the same installer and rollback serve the pilot and the full city.
    """
    import numpy as np
    library_path, jobs_path, out_dir = Path(library_path), Path(jobs_path), Path(out_dir)
    for path in (library_path, jobs_path):
        _require(path.is_file(), 'Input missing: %s' % path)
    library = json.loads(library_path.read_text(encoding='utf8'))
    families = validate_library(library, library_path.parent)
    jobs_doc = json.loads(jobs_path.read_text(encoding='utf8'))
    indexes = sorted(Path(batches_dir).glob('buildings-*.objects.json'))
    _require(indexes, 'No building batch indices in %s' % batches_dir)
    index_docs = []
    inventory = []
    for path in indexes:
        doc = json.loads(path.read_text(encoding='utf8'))
        _require(doc.get('kind') == 'buildings', 'Not a buildings index: %s' % path)
        glb = Path(doc['asset'])
        if not glb.is_file():
            glb = Path(batches_dir) / glb.name
        _require(glb.is_file(), 'Batch GLB missing: %s' % glb)
        inventory.extend(o['source_id'] for o in doc['objects'])
        index_docs.append((path, glb, doc))
    jobs = validate_jobs(jobs_doc, families, inventory, expected_count)
    exclusions = discover_exclusions(engine_root, saved_dir, exclusion_files)
    if validate_only:
        return {'status': 'valid', 'inventory': len(inventory), 'families': len(families),
                'applied_jobs': sum(1 for j in jobs.values() if j['class'] == 'reference_informed_estimate'),
                'exclusions_in_inventory': sum(1 for sid in exclusions if sid in jobs)}
    used = sorted({job['family'] for job in jobs.values() if job['class'] == 'reference_informed_estimate'})
    order = used
    _require(len(order) <= MAX_FAMILIES, 'Too many families in use')
    family_slot = {fid: i for i, fid in enumerate(order, start=1)}
    out_dir.mkdir(parents=True, exist_ok=True)

    plan_districts, assignments, estimate_mb = {}, [], 0.0
    totals = {'objects': 0, 'applied': 0, 'excluded': 0, 'unchanged': 0, 'outside_area': 0, 'no_footprint': 0}
    for path, glb_path, doc in index_docs:
        group = doc['production_group']
        key = group.lower().replace(' ', '-')
        if districts and key not in districts:
            continue
        meshes = read_batch_glb(glb_path)
        objects = doc['objects']
        xs = [o['local_bounds_m'][i][0] for o in objects for i in (0, 1)]
        ys = [o['local_bounds_m'][i][1] for o in objects for i in (0, 1)]
        zs = [o['local_bounds_m'][0][2] for o in objects]
        grid = choose_grid(((min(xs), min(ys)), (max(xs), max(ys))))
        z0 = min(zs)
        owner = np.full((grid['height'], grid['width']), -1, dtype='int32')
        ztop = np.full((grid['height'], grid['width']), np.nan, dtype='float64')
        roofs = {}
        decisions = []
        reason_of = {}
        for i, o in enumerate(objects):
            sid = o['source_id']
            lo, hi = o['local_bounds_m']
            job = jobs[sid]
            reason, fam = None, job['family']
            if sid in exclusions:
                reason = 'excluded:' + exclusions[sid]['reason']
            elif job['protected']:
                reason = 'excluded:manual_override'
            elif job['class'] == 'confirmed_historical':  # observed facade: preserve, never overwrite
                reason = 'excluded:confirmed_historical_preserved'
            elif o['batch'] in preserve_batches:
                reason = 'excluded:lgb_replaced_batch'
            elif job['class'] == 'unchanged':
                reason = 'unchanged:' + str(job['reason'])
            elif bounds and not (bounds[0] <= (lo[0] + hi[0]) / 2 <= bounds[2] and bounds[1] <= (lo[1] + hi[1]) / 2 <= bounds[3]):
                reason = 'outside_test_area'
            reason_of[i] = reason
            decisions.append((sid, None if reason else fam, job['class'], reason))

        def roof_triangles(o):
            lo = o['local_bounds_m'][0]
            positions, faces, _ = meshes[o['batch']]
            tris = positions[faces[o['first_triangle']:o['first_triangle'] + o['triangle_count']]]
            got = np.abs(tris.reshape(-1, 3).min(axis=0) - np.array(lo)).max()
            _require(got < 0.02, 'Mesh and index bounds disagree for ' + o['source_id'])
            normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
            length = np.linalg.norm(normal, axis=1)
            roof = (length > 1e-9) & (normal[:, 2] / np.where(length > 1e-9, length, 1) > .5)
            return tris[roof]

        def box_cells(lo, hi):
            c0 = max(int((lo[0] - grid['min_east_m']) / grid['cell_m']), 0)
            c1 = min(int((hi[0] - grid['min_east_m']) / grid['cell_m']), grid['width'] - 1)
            r0 = max(int((grid['max_north_m'] - hi[1]) / grid['cell_m']), 0)
            r1 = min(int((grid['max_north_m'] - lo[1]) / grid['cell_m']), grid['height'] - 1)
            return r0, r1, c0, c1

        # pass 1: every building that will NOT be painted (excluded, unchanged, outside the test area, skipped batch)
        # claims its footprint as protected, so a neighbour's paint or dilation can never reach its walls
        for i, o in enumerate(objects):
            if not reason_of[i]:
                continue
            lo, hi = o['local_bounds_m']
            marked = sum(_rasterise(owner, ztop, tri[:, :2], tri[:, 2], grid, PRESERVE) for tri in roof_triangles(o))
            if marked == 0:
                r0, r1, c0, c1 = box_cells(lo, hi)
                owner[r0:r1 + 1, c0:c1 + 1] = PRESERVE
        # pass 2: painted buildings, lowest roof first so taller roofs win overlaps; protected footprints are never overwritten
        order_idx = sorted((i for i in range(len(objects)) if not reason_of[i]),
                           key=lambda i: (objects[i]['local_bounds_m'][1][2], objects[i]['source_id']))
        for i in order_idx:
            o = objects[i]
            lo, hi = o['local_bounds_m']
            roofs[i] = roof_triangles(o)
            marked = sum(_rasterise(owner, ztop, tri[:, :2], tri[:, 2], grid, i) for tri in roofs[i])
            if marked == 0:  # roof too small for a texel centre: claim the footprint box instead
                r0, r1, c0, c1 = box_cells(lo, hi)
                block = owner[r0:r1 + 1, c0:c1 + 1]
                free = block != PRESERVE
                block[free] = i
                ztop[r0:r1 + 1, c0:c1 + 1][free] = hi[2]
        _dilate(owner)
        decision_of = {d[0]: d for d in decisions}
        owned = np.bincount(owner[owner >= 0], minlength=len(objects))
        protected_texels = int((owner == PRESERVE).sum())
        palette = np.zeros((len(objects) + 1, 4), dtype='uint8')  # last row = unowned texel
        for i, o in enumerate(objects):
            sid, fam, klass, reason = decision_of[o['source_id']]
            if reason:
                continue
            if owned[i] == 0:
                decision_of[sid] = (sid, None, klass, 'no_footprint_texels')
                continue
            lo, hi = o['local_bounds_m']
            style = family_style(families[fam])
            palette[i] = encode_texel(family_slot[fam], CLASS_CODE[klass], fitted_floor_height(hi[2] - lo[2], style['floor_height_m']),
                                      round((lo[2] - z0) * 100), stable_seed(sid, fam))
        rgba = palette[np.where(owner >= 0, owner, -1)]  # unowned (-1) and protected (-2) texels both read as 'no overlay'
        # Second texture: per-texel wall run (corner-anchored along-wall distance, wall length) and the
        # whole floors below the LOCAL roof, all from the real roof outlines of each building.
        walls_rgba = np.zeros((grid['height'], grid['width'], 4), dtype='uint8')
        flat_owner = np.where(owner >= 0, owner, -1).ravel()
        order_texels = np.argsort(flat_owner, kind='stable')
        sorted_owner = flat_owner[order_texels]
        for i in np.unique(sorted_owner[sorted_owner >= 0]):
            if palette[i].any() == 0:
                continue
            lo_i, hi_i = objects[i]['local_bounds_m']
            lo_idx = np.searchsorted(sorted_owner, i, 'left')
            hi_idx = np.searchsorted(sorted_owner, i, 'right')
            flat = order_texels[lo_idx:hi_idx]
            rows_i, cols_i = np.divmod(flat, grid['width'])
            centres = np.column_stack([grid['min_east_m'] + (cols_i + .5) * grid['cell_m'],
                                       grid['max_north_m'] - (rows_i + .5) * grid['cell_m']])
            walls = (wall_chains(roofs[i]) if i in roofs else []) or box_walls(lo_i, hi_i)
            s0_m, length_m, wall_ne, wall_nn = nearest_wall_data(walls, centres)
            top_z = ztop.ravel()[flat]
            top_z = np.where(np.isnan(top_z), hi_i[2], top_z)
            fh = FLOOR_H_RANGE[0] + palette[i][1] / 100.0
            floors = floors_below_roof(top_z, lo_i[2], fh).astype('int64')
            walls_rgba.reshape(-1, 4)[flat] = encode_wall_texels(s0_m, length_m, floors, normal_bin(wall_ne, wall_nn))
        png = out_dir / ('lookup_%s.png' % key)
        write_png(png, rgba)
        png2 = out_dir / ('lookup2_%s.png' % key)
        write_png(png2, walls_rgba)
        mb = (rgba.nbytes + walls_rgba.nbytes) / 1048576
        estimate_mb += mb
        material_colours = {}
        for name, (positions, faces, colour) in meshes.items():
            material_colours[name.rsplit('_', 1)[1]] = [round(float(c), 6) for c in colour]
        counts = {'objects': len(objects)}
        counts['applied'] = sum(1 for d in decision_of.values() if d[3] is None)
        counts['excluded'] = sum(1 for d in decision_of.values() if (d[3] or '').startswith('excluded'))
        counts['outside_area'] = sum(1 for d in decision_of.values() if d[3] == 'outside_test_area')
        counts['no_footprint'] = sum(1 for d in decision_of.values() if d[3] == 'no_footprint_texels')
        counts['unchanged'] = counts['objects'] - counts['applied'] - counts['excluded'] - counts['outside_area'] - counts['no_footprint']
        for k in totals:
            totals[k] += counts[k]
        batch_of = {o['source_id']: o['batch'] for o in objects}
        batches = {}
        for o in objects:
            suffix = o['batch'].rsplit('_', 1)[1]
            _require(suffix == str(o['material']) and suffix in material_colours, 'Batch name does not end in its material index: ' + o['batch'])
            batches[o['batch']] = suffix
        assignments.extend([sid, key, d[1], d[2], d[3], batch_of[sid]] for sid, d in sorted(decision_of.items()))
        plan_districts[key] = {'production_group': group, 'index': str(path), 'index_sha256': sha256_file(path),
                               'glb': str(glb_path), 'glb_sha256': sha256_file(glb_path), 'lookup': png.name,
                               'lookup_sha256': sha256_file(png), 'lookup2': png2.name, 'lookup2_sha256': sha256_file(png2),
                               'grid': dict(grid, z0_m=z0),
                               'source_materials': material_colours, 'batches': batches, 'counts': counts,
                               'texture_mb': round(mb, 2), 'protected_footprint_texels': protected_texels, 'triangle_total': sum(o['triangle_count'] for o in objects),
                               'vertex_total': sum(o['vertex_count'] for o in objects)}
    _require(plan_districts, 'No districts selected')
    _require(estimate_mb <= MAX_TOTAL_TEXTURE_MB, 'Lookup textures need %.0f MB, budget %d MB' % (estimate_mb, MAX_TOTAL_TEXTURE_MB))
    instances = sum(len(d['source_materials']) for d in plan_districts.values())
    _require(instances <= MAX_MATERIAL_INSTANCES, 'Material instance budget exceeded')
    table_png = out_dir / 'family_table.png'
    write_png(table_png, family_table(families, order))
    attribution = {fid: {k: families[fid].get(k) for k in ('name', 'reference_photo_ids', 'source_capture_dates', 'license', 'attribution')}
                   for fid in order}
    plan = {'schema': VERSION, 'area': area_name, 'bounds_m': bounds, 'library': str(library_path),
            'library_sha256': sha256_file(library_path), 'jobs': str(jobs_path), 'jobs_sha256': sha256_file(jobs_path),
            'inventory_count': len(inventory), 'family_order': order, 'family_table': table_png.name,
            'family_table_sha256': sha256_file(table_png), 'families': attribution,
            'exclusions': {sid: v for sid, v in sorted(exclusions.items()) if sid in set(inventory)},
            'districts': plan_districts, 'totals': totals,
            'budgets': {'texture_mb': round(estimate_mb, 2), 'texture_mb_limit': MAX_TOTAL_TEXTURE_MB,
                        'material_instances': instances, 'material_instance_limit': MAX_MATERIAL_INSTANCES,
                        'master_materials': 5, 'added_geometry_instances': 0, 'added_triangles': 0},
            'limitations': ['Per-building identity is carried by a world-XY lookup texture, not by material slots.',
                            'Bays are fitted between corner piers using each wall run from the roof outline; curved walls and walls over 409 m are approximated.',
                            'Windows stop at whole floors below the local roof height; parapets stay plain.',
                            'Cell size limits footprint fidelity; counts of buildings without texels are reported.',
                            'All patterns are reference-informed estimates, not surveys.']}
    plan['plan_hash'] = canonical_hash({k: v for k, v in plan.items() if k != 'exclusions'}
                                       | {'exclusion_ids': sorted(plan['exclusions'])})
    (out_dir / 'assignments.json').write_text(json.dumps(
        {'columns': ['source_id', 'district', 'family_id', 'evidence_class', 'unchanged_or_excluded_reason', 'batch'],
         'rows': assignments}, separators=(',', ':')))
    (out_dir / 'plan.json').write_text(json.dumps(plan, indent=1, sort_keys=True))
    return plan


# --------------------------------------------------------------------------- shader

SHADER = r'''
float3 n = normalize(Normal);
float wall = step(abs(n.z), 0.5);
float2 nh = normalize(n.xy + float2(0.00001, 0.0));
float2 nsrc = normalize(float2(I00 * nh.x + I01 * nh.y, I10 * nh.x + I11 * nh.y) + float2(0.00001, 0.0));
float2 tsrc = float2(nsrc.y, -nsrc.x);
float2 shiftUE = float2(-nh.y, nh.x) * GridB.z * 2.0;
float2 dq0 = Position.xy - nh * GridB.z - GridC.xy;
float eq0 = I00 * dq0.x + I01 * dq0.y;
float nq0 = I10 * dq0.x + I11 * dq0.y;
float2 uv0 = float2((eq0 - GridA.x) / (GridA.z * GridB.x), (GridA.y - nq0) / (GridA.z * GridB.y));
float inside0 = step(0.0, uv0.x) * step(uv0.x, 1.0) * step(0.0, uv0.y) * step(uv0.y, 1.0);
uv0 = saturate(uv0);
float2 cell0 = (floor(uv0 * GridB.xy) + 0.5) / GridB.xy;
float4 t0 = floor(Texture2DSampleLevel(Lookup, LookupSampler, uv0, 0) * 255.0 + 0.5);
float4 w0 = floor(Texture2DSampleLevel(Lookup2, Lookup2Sampler, uv0, 0) * 255.0 + 0.5);
float nb0 = floor(w0.y / 64.0) + 4.0 * floor(w0.w / 64.0);
float m0 = step(0.80, dot(nsrc, float2(cos(nb0 * 0.39269908), sin(nb0 * 0.39269908)))) * step(0.5, fmod(t0.x, 64.0)) * inside0;
float2 dq1 = Position.xy - nh * GridB.z + shiftUE - GridC.xy;
float eq1 = I00 * dq1.x + I01 * dq1.y;
float nq1 = I10 * dq1.x + I11 * dq1.y;
float2 uv1 = float2((eq1 - GridA.x) / (GridA.z * GridB.x), (GridA.y - nq1) / (GridA.z * GridB.y));
float inside1 = step(0.0, uv1.x) * step(uv1.x, 1.0) * step(0.0, uv1.y) * step(uv1.y, 1.0);
uv1 = saturate(uv1);
float2 cell1 = (floor(uv1 * GridB.xy) + 0.5) / GridB.xy;
float4 t1 = floor(Texture2DSampleLevel(Lookup, LookupSampler, uv1, 0) * 255.0 + 0.5);
float4 w1 = floor(Texture2DSampleLevel(Lookup2, Lookup2Sampler, uv1, 0) * 255.0 + 0.5);
float nb1 = floor(w1.y / 64.0) + 4.0 * floor(w1.w / 64.0);
float m1 = step(0.80, dot(nsrc, float2(cos(nb1 * 0.39269908), sin(nb1 * 0.39269908)))) * step(0.5, fmod(t1.x, 64.0)) * inside1;
float2 dq2 = Position.xy - nh * GridB.z - shiftUE - GridC.xy;
float eq2 = I00 * dq2.x + I01 * dq2.y;
float nq2 = I10 * dq2.x + I11 * dq2.y;
float2 uv2 = float2((eq2 - GridA.x) / (GridA.z * GridB.x), (GridA.y - nq2) / (GridA.z * GridB.y));
float inside2 = step(0.0, uv2.x) * step(uv2.x, 1.0) * step(0.0, uv2.y) * step(uv2.y, 1.0);
uv2 = saturate(uv2);
float2 cell2 = (floor(uv2 * GridB.xy) + 0.5) / GridB.xy;
float4 t2 = floor(Texture2DSampleLevel(Lookup, LookupSampler, uv2, 0) * 255.0 + 0.5);
float4 w2 = floor(Texture2DSampleLevel(Lookup2, Lookup2Sampler, uv2, 0) * 255.0 + 0.5);
float nb2 = floor(w2.y / 64.0) + 4.0 * floor(w2.w / 64.0);
float m2 = step(0.80, dot(nsrc, float2(cos(nb2 * 0.39269908), sin(nb2 * 0.39269908)))) * step(0.5, fmod(t2.x, 64.0)) * inside2;
float same1 = step(abs(t1.x - t0.x) + abs(t1.y - t0.y) + abs(t1.z - t0.z) + abs(t1.w - t0.w), 0.5);
float same2 = step(abs(t2.x - t0.x) + abs(t2.y - t0.y) + abs(t2.z - t0.z) + abs(t2.w - t0.w), 0.5);
float m1s = m1 * same1;
float m2s = m2 * same2;
float use1 = (1.0 - m0) * m1s;
float use2 = (1.0 - m0) * (1.0 - m1s) * m2s;
float use0 = 1.0 - use1 - use2;
float4 t = t0;
float4 w = w0 * use0 + w1 * use1 + w2 * use2;
float2 cell = cell0 * use0 + cell1 * use1 + cell2 * use2;
float inside = inside0;
float wallMatch = max(m0, max(m1s, m2s));
float fam = fmod(t.x, 64.0);
float cls = floor(t.x / 64.0);
float valid = wall * inside * step(0.5, fam);
float fu = (fam + 0.5) / 64.0;
float4 r0 = Texture2DSampleLevel(Families, FamiliesSampler, float2(fu, 0.0625), 0);
float4 r1 = Texture2DSampleLevel(Families, FamiliesSampler, float2(fu, 0.1875), 0);
float4 r2 = Texture2DSampleLevel(Families, FamiliesSampler, float2(fu, 0.3125), 0);
float4 r3 = Texture2DSampleLevel(Families, FamiliesSampler, float2(fu, 0.4375), 0);
float4 r4 = Texture2DSampleLevel(Families, FamiliesSampler, float2(fu, 0.5625), 0);
float3 trimC = pow(r1.rgb, 2.2);
float3 glassC = pow(r2.rgb, 2.2);
float fh = 2.0 + t.y * 0.01;
float zq = t.z * 256.0 + t.w;
float seed = fmod(t.w, 8.0);
float baseZ = (zq - seed) + GridC.z;
float h = (Position.z - baseZ) / 100.0;
float vb = h / fh;
float fl = floor(vb);
float v = frac(vb);
float rnd = frac(sin(seed * 12.9898 + fam * 78.233) * 43758.5453);
float2 d0 = Position.xy - GridC.xy;
float ep = I00 * d0.x + I01 * d0.y;
float np = I10 * d0.x + I11 * d0.y;
float ec = GridA.x + cell.x * GridA.z * GridB.x;
float nc = GridA.y - cell.y * GridA.z * GridB.y;
float s0 = (w.x + 256.0 * fmod(w.y, 64.0)) * 0.01;
float wl = w.z * 0.5;
float along = s0 + dot(float2(ep - ec, np - nc), tsrc);
float floorsLocal = fmod(w.w, 64.0);
float bw0 = max(r3.x * 255.0 * 0.05, 1.2) * (0.92 + 0.16 * frac(rnd * 7.0));
float pad = min(clamp(0.18 * bw0 + 0.25, 0.35, 0.9), 0.3 * wl);
float usable = wl - 2.0 * pad;
float nb = max(1.0, floor(usable / bw0 + 0.5));
float bw = max(usable, 0.01) / nb;
float xw = along - pad;
float inwall = step(0.0, xw) * step(xw, usable) * step(0.6 * bw0, usable) * wallMatch;   // every overlay feature below is gated by this
float ub = xw / bw;
float bay = floor(ub);
float u = frac(ub);
float rows = step(0.0, h) * step(fl, floorsLocal - 1.0);
float parapet = step(0.0, h) * step(floorsLocal, fl);
float curtain = step(0.5, r4.y);
float shop = step(0.5, r3.w) * step(fl, 0.5);
float bcode = floor(r4.x / 80.0 + 0.5);
float gw = r2.w;
float lo = lerp(0.5 - 0.5 * gw, 0.03, curtain);
float hi = lerp(0.5 + 0.5 * gw, 0.97, curtain);
float vlo = lerp(lerp(0.20, 0.08, shop), 0.10, curtain);
float vhi = lerp(lerp(0.80, 0.84, shop), 0.90, curtain);
float bayR = frac(sin((bay + seed * 3.1) * 127.1 + fl * 311.7 + fam * 17.0) * 43758.5453);
float ou0 = lerp(lo, 0.06, shop);
float ou1 = lerp(hi, 0.94, shop);
float openU = step(ou0, u) * step(u, ou1);
float win = openU * step(vlo, v) * step(v, vhi) * rows * inwall;
float shut = step(1.0 - r3.y, bayR) * (1.0 - shop) * (1.0 - curtain) * step(0.5, fl);
float topFloor = step(abs(fl - (floorsLocal - 1.0)), 0.5);
float slab = topFloor * step(0.93, v) + parapet * step(v, 0.10) + step(v, 0.04) * step(0.5, fl) * rows;
float pierEdge = (1.0 - inwall) * (rows + parapet);
float mullion = (1.0 - curtain) * win * step(abs(u - 0.5), 0.02) * step(0.5, frac(bayR * 3.7));
float spandrel = curtain * rows * inwall * (1.0 - win);
float streak = frac(sin(floor(ub * 2.0) * 91.7 + fam) * 437.5);
float stain = r1.a * (0.25 + 0.75 * streak) * saturate(0.55 - v) * 0.35 + r1.a * 0.10 * (1.0 - saturate(h / 6.0));
float3 plaster = Texture2DSample(WallTex, WallTexSampler, float2(dot(Position.xy, float2(-nh.y, nh.x)), Position.z) / 300.0).rgb;
float3 wallC = pow(r0.rgb, 2.2) * (0.93 + 0.14 * rnd) * (0.78 + 0.45 * dot(plaster, float3(0.3, 0.59, 0.11)));
float3 col = wallC * (1.0 - stain);
float trimMask = saturate(slab * 0.7 + pierEdge * 0.15);
float slabShadow = step(0.5, bcode) * (1.0 - curtain) * rows * inwall * step(0.04, v) * step(v, 0.12);   // slab / projecting types: a soft shadow under each floor slab
col = lerp(col, trimC * (1.0 - 0.5 * stain), trimMask);
col = lerp(col, col * 0.88, slabShadow * (1.0 - win));
float3 shutC = wallC * 0.55 * (0.80 + 0.20 * step(0.5, frac(v * 14.0)));   // dark louvres derived from the wall, never a pale box
float vin = saturate((v - vlo) / max(vhi - vlo, 0.01));
float3 sky = float3(0.16, 0.22, 0.31) * (0.45 + 0.9 * vin);
float3 pane = lerp(glassC * (0.85 + 0.25 * frac(bayR * 5.3)) + 0.012, sky, 0.38);   // one tone family: no bright pane variant
pane = pane * (1.0 - 0.45 * step(vhi - 0.07, v));
float fw = 0.05 + 0.04 * step(2.5, bcode);   // recessed type: the SAME single frame, a little thicker (no second line)
float frame = win * (step(u, ou0 + fw) + step(ou1 - fw, u) + step(v, vlo + fw) + step(vhi - fw, v)) * (1.0 - curtain);
col = lerp(col, shutC, win * shut);
col = lerp(col, pane, win * (1.0 - shut));
col = lerp(col, trimC * 0.85, saturate(frame) * (1.0 - shut) * 0.5);
col = lerp(col, trimC * 0.35 + glassC, 0.5 * spandrel);
col = lerp(col, trimC, mullion * 0.5);
float footprint = (fwidth(Position.x) + fwidth(Position.y)) * 0.01;
float aa = saturate(1.0 - 2.5 * max(footprint / bw, fwidth(Position.z) * 0.01 / fh));
float3 avg = lerp(wallC * (1.0 - stain), glassC, gw * 0.35);
float3 outc = lerp(avg, col, aa);
float dbgClass = step(0.5, Debug) * (1.0 - step(1.5, Debug));
float3 tint = lerp(float3(1.0, 0.6, 0.1), float3(0.2, 0.4, 1.0), step(cls, 1.5));
outc = lerp(outc, tint, dbgClass * 0.6);
float flatMode = step(1.5, Debug) * (1.0 - step(2.5, Debug));
outc = lerp(outc, float3(0.25, 0.25, 0.25), flatMode);
outc = lerp(outc, float3(1.0, 0.0, 1.0), step(2.5, Debug) * (1.0 - step(3.5, Debug)));
outc = lerp(outc, Orig, step(3.5, Debug));
float gloss = win * (1.0 - shut) * aa * (1.0 - step(1.5, Debug));
float pbr = 1.0 - step(3.5, Debug);   // Debug 4 (bypass) keeps the ORIGINAL roughness
return float4(outc, valid * (0.1 + 0.4 * pbr + 0.1 * gloss * pbr));
'''

VECTOR_PARAMS = ('GridA', 'GridB', 'GridC')
SCALAR_PARAMS = ('I00', 'I01', 'I10', 'I11', 'Debug')
GLASS_ROUGHNESS = 0.35   # semi-matte: less grazing-angle reflection and speckle than the 0.14 first used
TEXTURE_PARAMS = (('Lookup', 'LookupTexture'), ('Lookup2', 'Lookup2Texture'), ('Families', 'FamilyTable'), ('WallTex', 'WallTexture'))


def shader_has_derivative_safe_sampling():
    """Data lookups use explicit LOD; the two tiled finish textures use implicit derivatives and the shader has no flow control."""
    return (SHADER.count('Texture2DSampleLevel(') == 11 and SHADER.count('Texture2DSample(') == 1
            and 'if (' not in SHADER and 'for (' not in SHADER)


# --------------------------------------------------------------------------- native half

def _unreal():
    import unreal
    return unreal


def _state_dir():
    unreal = _unreal()
    path = Path(unreal.Paths.project_dir()).resolve() / 'Saved' / 'DynaCity' / STATE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _refuse_during_play():
    unreal = _unreal()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before changing materials or compiling shaders')


def component_snapshot(actor, component, mesh_path, signature_only=False):
    """Everything that must be identical before and after (geometry, transform, collision)."""
    loc, rot, scale = actor.get_actor_location(), actor.get_actor_rotation(), actor.get_actor_scale3d()
    snap = {'actor': actor.get_path_name(), 'mesh': mesh_path,
            'transform': [loc.x, loc.y, loc.z, rot.pitch, rot.yaw, rot.roll, scale.x, scale.y, scale.z],
            'collision': str(component.get_collision_enabled()),
            'collision_profile': str(component.get_collision_profile_name()),
            'slots': component.get_num_materials()}
    try:  # geometry persistence: LOD0 vertex count of the untouched mesh
        mesh = component.get_editor_property('static_mesh')
        snap['lod0_vertices'] = int(_unreal().EditorStaticMeshLibrary.get_number_verts(mesh, 0))
    except Exception:
        snap['lod0_vertices'] = None
    if not signature_only:
        snap['materials'] = [_path(component.get_material(i)) for i in range(component.get_num_materials())]
        snap['override_materials'] = [_path(m) for m in component.get_editor_property('override_materials')]
    return snap


def _path(asset):
    return asset.get_path_name() if asset else None


def restore_component(component, saved, load):
    """Put override_materials back exactly as saved ([] means no overrides existed)."""
    originals = saved['override_materials']
    component.set_editor_property('override_materials', [load(p) if p else None for p in originals])


MASTER_VERSION = 1
HASH_TAG = 'DynaCityPhotoFacadeHash'
OWNED_PREFIX = ROOT + '/MI_PFB_'
# The only pre-existing facade materials this installer may replace: the standard city
# finishes assigned to every source batch. Anything else in a planned slot (module,
# reference, captured, manual or earlier generic overrides) is protected.
STANDARD_FACADE_DEFAULTS = frozenset('/Game/Beirut/CityFinishes/M_Facade_' + name for name in
                                     ('Limestone', 'FadedRose', 'Concrete', 'WarmPlaster', 'Ivory'))
VECTOR_TOLERANCE = 1e-4
FINISH_TEXTURES = '/Game/Beirut/CityFinishes/Textures/'
ROOF_TEXTURE = FINISH_TEXTURES + 'Concrete030_1K_JPG_Color'
# Wall texture each standard finish uses, so untouched walls and roofs keep a textured look close to the original.
WALL_TEXTURE = {'Limestone': FINISH_TEXTURES + 'plastered_stone_wall_diff_1k'}
DEFAULT_WALL_TEXTURE = FINISH_TEXTURES + 'Plaster001_1K_JPG_Color'


def asset_key(path):
    """'/Game/a/B.B' -> '/Game/a/B'."""
    return path.split('.')[0] if path else None


def classify_actor(info, batches, lgb_actor=None):
    """Decide what the installer may do to one loaded actor -> (decision, reason).

    `info`: label, path, hidden, visible, materials (effective slot material paths).
    Only 'apply' actors are modified. Everything else keeps its current materials and is
    reported with a reason; nothing is silently replaced.
    """
    if info['label'] not in batches:
        return 'not_in_plan', 'not a planned building batch'
    if lgb_actor and info['path'] == lgb_actor:
        return 'skip', 'lgb_replaced_batch'
    if info.get('hidden') or info.get('visible') is False:
        return 'skip', 'hidden_or_invisible'
    slots = info['materials']
    if len(slots) != 1:
        return 'skip', 'slot_layout_unmapped:%d_slots' % len(slots)
    key = asset_key(slots[0])
    if key in STANDARD_FACADE_DEFAULTS or (key or '').startswith(OWNED_PREFIX):
        return 'apply', 'standard_city_finish' if key in STANDARD_FACADE_DEFAULTS else 'already_installed'
    return 'skip', 'protected_override:%s' % (key or 'empty_slot')


def reconcile_coverage(rows, selected, decisions, inventory_total):
    """Every inventory ID lands in exactly one category; raises if they do not add up.

    rows: assignments.json rows [source_id, district, family, class, reason, batch].
    decisions: {batch: (decision, reason)} for batches whose actor is loaded (absent = not loaded).
    """
    counts, ids = {}, {}

    def put(category, sid):
        counts[category] = counts.get(category, 0) + 1
        if category.startswith(('skipped:', 'plan_excluded')):
            ids.setdefault(category, []).append(sid)

    for sid, district, _family, _class, reason, batch in rows:
        if selected is not None and district not in selected:
            put('outside_selection', sid)
        elif reason:
            kind = reason.split(':')[0]
            put({'excluded': 'plan_excluded', 'unchanged': 'plan_unchanged'}.get(kind, 'plan_' + kind), sid)
        else:
            decision = decisions.get(batch)
            if decision is None:
                put('skipped:actor_not_loaded', sid)
            elif decision[0] == 'apply':
                put('applied', sid)
            else:
                put('skipped:' + decision[1].split(':')[0], sid)
    total = sum(counts.values())
    if total != len(rows) or total != inventory_total:
        raise RuntimeError('Coverage does not reconcile: %d categorised, %d rows, %d inventory' % (total, len(rows), inventory_total))
    return {'total': total, 'counts': dict(sorted(counts.items())),
            'planned_not_applied': sum(v for k, v in counts.items() if k.startswith('skipped:')),
            'ids': ids}


def master_fingerprint(finish_hash=''):
    """Identity of a replacement master: shader text, parameter list, version and the original finish's bytes."""
    return canonical_hash({'version': MASTER_VERSION, 'shader': SHADER, 'vectors': VECTOR_PARAMS, 'scalars': SCALAR_PARAMS,
                           'textures': TEXTURE_PARAMS, 'finish': finish_hash})


def master_action(exists, stored_hash, fingerprint):
    """create / current / update. The asset name carries the fingerprint, so 'update' means a new asset."""
    if not exists:
        return 'create'
    return 'current' if stored_hash == fingerprint else 'update'


def finish_name(finish):
    return (asset_key(finish) or '').rsplit('/', 1)[-1].replace('M_Facade_', '')


def master_asset_path(finish, fingerprint):
    """Each replacement master is a duplicate of one original finish with the overlay added."""
    return '%s/M_PFB_%s_%s' % (ROOT, finish_name(finish), fingerprint[:8])


def instance_expectations(plan, key, district, material_index, origin, inv, debug=False,
                          finish='/Game/Beirut/CityFinishes/M_Facade_Concrete', finish_hash=''):
    """What one material instance must hold for the current plan (parent, parameters, textures)."""
    grid = district['grid']
    return {'parent': master_asset_path(finish, master_fingerprint(finish_hash)),
            'vectors': {'GridA': (grid['min_east_m'], grid['max_north_m'], grid['cell_m'], 0.0),
                        'GridB': (grid['width'], grid['height'], INSET_TEXELS * grid['cell_m'] * 100, 0.0),
                        'GridC': (origin[0], origin[1], origin[2] + grid['z0_m'] * 100, 0.0)},
            'scalars': dict(zip(('I00', 'I01', 'I10', 'I11'), inv), Debug=float(debug)),
            'textures': {'LookupTexture': ROOT + '/Data/T_PFB_Lookup_' + key,
                         'Lookup2Texture': ROOT + '/Data/T_PFB_Lookup2_' + key,
                         'FamilyTable': ROOT + '/Data/T_PFB_Families'},
            'static_textures': {'WallTexture': WALL_TEXTURE.get(finish_name(finish), DEFAULT_WALL_TEXTURE)},
            'texture_hashes': {'LookupTexture': district['lookup_sha256'], 'Lookup2Texture': district['lookup2_sha256'],
                               'FamilyTable': plan['family_table_sha256']}}


def check_instance(expected, read):
    """Compare an instance against its expectation using read.parent/vector/scalar/texture/tag."""
    errors = []
    if asset_key(read.parent()) != expected['parent']:
        errors.append('parent is %s' % read.parent())
    for name, want in expected['vectors'].items():
        got = read.vector(name)
        if got is None or any(abs(a - b) > VECTOR_TOLERANCE * max(1.0, abs(b)) for a, b in zip(got, want)):
            errors.append('vector %s is %s, expected %s' % (name, got, want))
    for name, want in expected['scalars'].items():
        got = read.scalar(name)
        if got is None or abs(got - want) > VECTOR_TOLERANCE * max(1.0, abs(want)):
            errors.append('scalar %s is %s, expected %s' % (name, got, want))
    for name, want in expected['textures'].items():
        got = asset_key(read.texture(name))
        if got != want:
            errors.append('texture %s is %s, expected %s' % (name, got, want))
        elif read.tag(want, HASH_TAG) != expected['texture_hashes'][name]:
            errors.append('texture %s content hash differs from the plan' % name)
    for name, want in expected.get('static_textures', {}).items():
        if asset_key(read.texture(name)) != want:
            errors.append('texture %s is %s, expected %s' % (name, read.texture(name), want))
    return errors


# --------------------------------------------------------------------------- native editor access

def _lgb_replaced_actor():
    """Path of the source batch whose visuals were replaced by the LGB reference (skip fail-closed)."""
    for path in (Path('C:/private/dynacity-data/engine/facade-reference-20261030/native-install.json'),):
        if path.is_file():
            return json.loads(path.read_text()).get('source_actor')
    return None


def _live_actors(batches):
    """{batch label: [actors]} for loaded level actors whose label is a planned batch."""
    import city_world
    found = {}
    for actor in city_world._actors():
        label = actor.get_actor_label()
        if label in batches:
            found.setdefault(label, []).append(actor)
    return found


def _plan_rows(bake_dir):
    return json.loads((Path(bake_dir) / 'assignments.json').read_text())['rows']


def native_inventory(bake_dir, districts=None):
    """Dry run. Reads the editor only: per-actor decision, exact source-ID coverage, master status."""
    unreal = _unreal()
    bake_dir = Path(bake_dir)
    plan = json.loads((bake_dir / 'plan.json').read_text())
    selected = set(districts) if districts else None
    chosen = {k: d for k, d in plan['districts'].items() if selected is None or k in selected}
    batches = {b: (k, idx) for k, d in chosen.items() for b, idx in d['batches'].items()}
    lgb = _lgb_replaced_actor()
    report = {'plan_hash': plan['plan_hash'], 'selected': sorted(chosen), 'lgb_replaced_actor': lgb, 'blockers': [],
              'in_play': unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor(), 'actors': []}
    live = _live_actors(batches)
    decisions = {}
    for label, actors in sorted(live.items()):
        if len(actors) != 1:
            decisions[label] = ('skip', 'duplicate_actor_label')
            report['actors'].append({'label': label, 'decision': 'skip', 'reason': 'duplicate_actor_label'})
            continue
        actor = actors[0]
        comp = actor.get_component_by_class(unreal.StaticMeshComponent)
        if comp is None:
            decisions[label] = ('skip', 'no_static_mesh_component')
            continue
        mesh = comp.get_editor_property('static_mesh')
        snapshot = component_snapshot(actor, comp, _path(mesh))
        info = {'label': label, 'path': actor.get_path_name(), 'materials': snapshot['materials'],
                'hidden': bool(comp.get_editor_property('hidden_in_game')), 'visible': bool(comp.is_visible())}
        decision, reason = classify_actor(info, batches, lgb)
        decisions[label] = (decision, reason)
        report['actors'].append({'label': label, 'path': info['path'], 'decision': decision, 'reason': reason,
                                 'district': batches[label][0], 'material_index': batches[label][1],
                                 'materials': info['materials'], 'snapshot': snapshot})
    in_bake = sum(d['counts']['objects'] for d in plan['districts'].values())
    report['coverage'] = reconcile_coverage(_plan_rows(bake_dir), selected and set(chosen), decisions, in_bake)
    report['coverage']['inventory_total'] = plan['inventory_count']
    report['coverage']['not_in_this_bake'] = plan['inventory_count'] - in_bake  # other districts, untouched
    for key in chosen:
        if not any(a['district'] == key and a['decision'] == 'apply' for a in report['actors']) and chosen[key]['counts']['applied']:
            report['blockers'].append('%s: no loaded actor can be painted' % key)
    report['master'] = {}
    state_file = _state_dir() / 'originals.json'
    saved = json.loads(state_file.read_text()).get('components', {}) if state_file.is_file() else {}
    for finish in sorted({_finish_key(a['materials'][0], saved.get(a['path'], {}).get('snapshot', {}).get('materials'))
                          for a in report['actors'] if a['decision'] == 'apply'}):
        fingerprint = master_fingerprint(_finish_hash(finish))
        path = master_asset_path(finish, fingerprint)
        exists = unreal.EditorAssetLibrary.does_asset_exist(path)
        stored = unreal.EditorAssetLibrary.get_metadata_tag(unreal.load_asset(path), HASH_TAG) if exists else None
        report['master'][finish_name(finish)] = {'action': master_action(exists, stored, fingerprint), 'asset': path}
    return report


# Master material and instances ------------------------------------------------

def _finish_key(current, saved_snapshot_materials):
    """Original standard finish for a slot's current material (saved originals once ours)."""
    key = asset_key(current)
    if (key or '').startswith(OWNED_PREFIX) and saved_snapshot_materials:
        key = asset_key(saved_snapshot_materials[0])
    return key


def _finish_hash(finish):
    """SHA-256 of the original finish's package file, so a changed original invalidates its replacement master."""
    unreal = _unreal()
    rel = (asset_key(finish) or '')[len('/Game/'):] + '.uasset'
    path = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())) / rel
    return sha256_file(path) if path.is_file() else 'missing:' + str(finish)


def _node(lib, mat, cls, x, y, **props):
    node = lib.create_material_expression(mat, cls, x, y)
    for name, value in props.items():
        node.set_editor_property(name, value)
    return node


def _build_overlay(mat):
    """Add the facade overlay to a DUPLICATE of an original finish.

    base colour = lerp(original base colour, overlay colour, mask) and roughness = lerp(original
    roughness, glass, gloss). With mask 0 (every pixel the overlay does not own: roofs, excluded and
    unassigned buildings, outside the lookup) the original graph's output passes through unchanged,
    as do its normal, metallic, specular and every other input. Fails closed if the original's
    inputs cannot be read.
    """
    unreal = _unreal()
    lib = unreal.MaterialEditingLibrary
    prop = unreal.MaterialProperty
    base_node = lib.get_material_property_input_node(mat, prop.MP_BASE_COLOR)
    if base_node is None:
        raise RuntimeError('Original finish has no readable base colour graph; refusing to overlay')
    base_out = str(lib.get_material_property_input_node_output_name(mat, prop.MP_BASE_COLOR) or '')
    rough_node = lib.get_material_property_input_node(mat, prop.MP_ROUGHNESS)
    rough_out = str(lib.get_material_property_input_node_output_name(mat, prop.MP_ROUGHNESS) or '') if rough_node else ''
    if rough_node is None:
        rough_node = _node(lib, mat, unreal.MaterialExpressionConstant, 1200, 700, r=0.5)
    code = _node(lib, mat, unreal.MaterialExpressionCustom, 1400, 0, output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    names = ['Position', 'Normal', 'Orig', *VECTOR_PARAMS, *SCALAR_PARAMS, *[n for n, _ in TEXTURE_PARAMS]]
    inputs = []
    for name in names:
        item = unreal.CustomInput()
        item.set_editor_property('input_name', name)
        inputs.append(item)
    code.set_editor_property('inputs', inputs)
    code.set_editor_property('code', SHADER)
    y = 1000
    for cls, name in ((unreal.MaterialExpressionWorldPosition, 'Position'), (unreal.MaterialExpressionVertexNormalWS, 'Normal')):
        lib.connect_material_expressions(_node(lib, mat, cls, 800, y), '', code, name)
        y += 150
    lib.connect_material_expressions(base_node, base_out, code, 'Orig')  # the original base colour, for the Debug=4 bypass
    for name in VECTOR_PARAMS:
        lib.connect_material_expressions(_node(lib, mat, unreal.MaterialExpressionVectorParameter, 800, y, parameter_name=name), '', code, name)
        y += 150
    for name in SCALAR_PARAMS:
        lib.connect_material_expressions(_node(lib, mat, unreal.MaterialExpressionScalarParameter, 800, y, parameter_name=name), '', code, name)
        y += 150
    for name, parameter in TEXTURE_PARAMS:
        lib.connect_material_expressions(_node(lib, mat, unreal.MaterialExpressionTextureObjectParameter, 800, y, parameter_name=parameter,
                                               sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR), '', code, name)
        y += 150
    rgb = _node(lib, mat, unreal.MaterialExpressionComponentMask, 1700, 0, r=True, g=True, b=True, a=False)
    alpha = _node(lib, mat, unreal.MaterialExpressionComponentMask, 1700, 200, r=False, g=False, b=False, a=True)
    lib.connect_material_expressions(code, '', rgb, '')
    lib.connect_material_expressions(code, '', alpha, '')
    scaled = _node(lib, mat, unreal.MaterialExpressionMultiply, 1900, 200, const_b=20.0)
    valid = _node(lib, mat, unreal.MaterialExpressionSaturate, 2100, 200)
    lib.connect_material_expressions(alpha, '', scaled, 'A')
    lib.connect_material_expressions(scaled, '', valid, '')
    shifted = _node(lib, mat, unreal.MaterialExpressionSubtract, 1900, 400, const_b=0.5)
    doubled = _node(lib, mat, unreal.MaterialExpressionMultiply, 2100, 400, const_b=10.0)
    gloss = _node(lib, mat, unreal.MaterialExpressionSaturate, 2300, 400)
    lib.connect_material_expressions(alpha, '', shifted, 'A')
    lib.connect_material_expressions(shifted, '', doubled, 'A')
    lib.connect_material_expressions(doubled, '', gloss, '')
    base_mix = _node(lib, mat, unreal.MaterialExpressionLinearInterpolate, 2500, 0)
    lib.connect_material_expressions(base_node, base_out, base_mix, 'A')
    lib.connect_material_expressions(rgb, '', base_mix, 'B')
    lib.connect_material_expressions(valid, '', base_mix, 'Alpha')
    below = _node(lib, mat, unreal.MaterialExpressionSubtract, 1900, 800, const_b=0.35)
    above = _node(lib, mat, unreal.MaterialExpressionMultiply, 2100, 800, const_b=100.0)
    pbr = _node(lib, mat, unreal.MaterialExpressionSaturate, 2300, 800)
    lib.connect_material_expressions(alpha, '', below, 'A')
    lib.connect_material_expressions(below, '', above, 'A')
    lib.connect_material_expressions(above, '', pbr, '')
    wall_rough = _node(lib, mat, unreal.MaterialExpressionConstant, 2300, 600, r=0.85)
    glass_rough = _node(lib, mat, unreal.MaterialExpressionConstant, 2300, 700, r=GLASS_ROUGHNESS)
    overlay_rough = _node(lib, mat, unreal.MaterialExpressionLinearInterpolate, 2500, 700)
    lib.connect_material_expressions(wall_rough, '', overlay_rough, 'A')
    lib.connect_material_expressions(glass_rough, '', overlay_rough, 'B')
    lib.connect_material_expressions(gloss, '', overlay_rough, 'Alpha')
    rough_mix = _node(lib, mat, unreal.MaterialExpressionLinearInterpolate, 2700, 600)
    lib.connect_material_expressions(rough_node, rough_out, rough_mix, 'A')   # the ORIGINAL roughness where the overlay does not own the pixel, or in bypass
    lib.connect_material_expressions(overlay_rough, '', rough_mix, 'B')       # matte wall / glossy glass where it does
    lib.connect_material_expressions(pbr, '', rough_mix, 'Alpha')
    lib.connect_material_property(base_mix, '', prop.MP_BASE_COLOR)
    lib.connect_material_property(rough_mix, '', prop.MP_ROUGHNESS)


def _master(finish):
    """The replacement master for one original finish: a fingerprint-named duplicate plus the overlay.

    A changed shader (or a changed original) gets a NEW asset name, so nothing is rebuilt in place;
    instances are re-parented on the next apply. Compiling is refused during Play.
    """
    unreal = _unreal()
    lib, assets = unreal.MaterialEditingLibrary, unreal.EditorAssetLibrary
    fingerprint = master_fingerprint(_finish_hash(finish))
    path = master_asset_path(finish, fingerprint)
    if assets.does_asset_exist(path):
        mat = unreal.load_asset(path)
        if assets.get_metadata_tag(mat, HASH_TAG) != fingerprint:
            raise RuntimeError('Master %s exists but its stored fingerprint differs; refusing to trust it' % path)
        return mat
    _refuse_during_play()
    mat = assets.duplicate_asset(asset_key(finish), path)
    if mat is None:
        raise RuntimeError('Could not duplicate original finish %s' % finish)
    try:
        _build_overlay(mat)
        lib.recompile_material(mat)
        assets.set_metadata_tag(mat, HASH_TAG, fingerprint)
        assets.save_loaded_asset(mat)
    except Exception:
        assets.delete_asset(path)  # never leave a half-built master behind
        raise
    return mat


def _import_texture(png, name, sha):
    """Import once per content hash; the hash is stored on the asset so staleness is detectable."""
    unreal = _unreal()
    assets = unreal.EditorAssetLibrary
    path = ROOT + '/Data/' + name
    if assets.does_asset_exist(path) and assets.get_metadata_tag(unreal.load_asset(path), HASH_TAG) == sha:
        return unreal.load_asset(path)
    task = unreal.AssetImportTask()
    task.filename = str(png)
    task.destination_path = ROOT + '/Data'
    task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = False
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    texture = unreal.load_asset(path)
    if texture is None:
        raise RuntimeError('Texture import failed: ' + name)
    for prop, value in (('srgb', False), ('compression_settings', unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP),
                        ('mip_gen_settings', unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS),
                        ('filter', unreal.TextureFilter.TF_NEAREST), ('never_stream', True)):
        texture.set_editor_property(prop, value)
    assets.set_metadata_tag(texture, HASH_TAG, sha)
    assets.save_loaded_asset(texture)
    return texture


def _affine():
    """UE xy (cm) of source east/north metres: origin and inverse 2x2; checked to be a 100 cm/m horizontal map."""
    import explore_city
    o = explore_city._world([0, 0, 0])
    e = explore_city._world([1, 0, 0]) - o
    n = explore_city._world([0, 1, 0]) - o
    u = explore_city._world([0, 0, 1]) - o
    if abs(e.z) > 1e-6 or abs(n.z) > 1e-6 or abs(u.x) > 1e-6 or abs(u.y) > 1e-6 or abs(u.z - 100) > 1e-3:
        raise RuntimeError('City basis is not a horizontal 100 cm/m affine map')
    for v in (e, n):
        if abs(math.hypot(v.x, v.y) - 100) > 1e-3:
            raise RuntimeError('City basis horizontal scale is not 100 cm/m')
    det = e.x * n.y - n.x * e.y
    if abs(det) < 1e-6:
        raise RuntimeError('Degenerate city basis')
    return (o.x, o.y, o.z), (n.y / det, -n.x / det, -e.y / det, e.x / det)


class _NativeReader:
    def __init__(self, mi):
        self.mi, self.lib, self.unreal = mi, _unreal().MaterialEditingLibrary, _unreal()

    def parent(self):
        parent = self.mi.get_editor_property('parent')
        return parent.get_path_name() if parent else None

    @staticmethod
    def _unwrap(value):
        return value[-1] if isinstance(value, tuple) else value

    def vector(self, name):
        value = self._unwrap(self.lib.get_material_instance_vector_parameter_value(self.mi, name))
        return (value.r, value.g, value.b, value.a)

    def scalar(self, name):
        return float(self._unwrap(self.lib.get_material_instance_scalar_parameter_value(self.mi, name)))

    def texture(self, name):
        value = self._unwrap(self.lib.get_material_instance_texture_parameter_value(self.mi, name))
        return value.get_path_name() if value else None

    def tag(self, asset_path, key):
        asset = self.unreal.load_asset(asset_path)
        return self.unreal.EditorAssetLibrary.get_metadata_tag(asset, key) if asset else None


def _finish_of(entry, originals):
    """The standard finish the actor had before installation (saved originals once ours)."""
    current = entry['materials'][0]
    if (asset_key(current) or '').startswith(OWNED_PREFIX):
        return originals['components'][entry['path']]['snapshot']['materials'][0]
    return current


def _configure_instance(mi, expected, textures):
    unreal = _unreal()
    lib = unreal.MaterialEditingLibrary
    for name, value in expected['vectors'].items():
        lib.set_material_instance_vector_parameter_value(mi, name, unreal.LinearColor(*value))
    for name, value in expected['scalars'].items():
        lib.set_material_instance_scalar_parameter_value(mi, name, value)
    for name, texture in textures.items():
        lib.set_material_instance_texture_parameter_value(mi, name, texture)
    for name, path in expected['static_textures'].items():
        lib.set_material_instance_texture_parameter_value(mi, name, unreal.load_asset(path))


def apply(bake_dir, districts=None, debug=False, replace=False, allow_skips=False):
    """Install the baked plan on loaded, unprotected building actors. Idempotent; originals saved first.

    Refuses when planned buildings would be skipped (protected override, LGB batch, unloaded
    actor, ...) unless allow_skips=True; either way the exact coverage is returned and written.
    """
    unreal = _unreal()
    bake_dir = Path(bake_dir)
    plan = json.loads((bake_dir / 'plan.json').read_text())
    _refuse_during_play()
    for district in plan['districts'].values():
        for name, digest in ((district['lookup'], district['lookup_sha256']), (district['lookup2'], district['lookup2_sha256'])):
            if sha256_file(bake_dir / name) != digest:
                raise RuntimeError('Baked lookup changed after planning: ' + name)
        if sha256_file(district['index']) != district['index_sha256'] or sha256_file(district['glb']) != district['glb_sha256']:
            raise RuntimeError('Source batch changed since planning: ' + district['production_group'])
    if sha256_file(bake_dir / plan['family_table']) != plan['family_table_sha256']:
        raise RuntimeError('Family table changed after planning')
    report = native_inventory(bake_dir, districts)
    if report['blockers']:
        raise RuntimeError('Dry run blockers: %s' % report['blockers'])
    coverage = report['coverage']
    if coverage['planned_not_applied'] and not allow_skips:
        raise RuntimeError('%d planned buildings would be skipped (%s); inspect the report or pass allow_skips=True'
                           % (coverage['planned_not_applied'], {k: v for k, v in coverage['counts'].items() if k.startswith('skipped:')}))
    state_file = _state_dir() / 'originals.json'
    originals = json.loads(state_file.read_text()) if state_file.is_file() else {}
    if originals.get('applied_plan') not in (None, plan['plan_hash']) and not replace:
        raise RuntimeError('A different plan is installed; pass replace=True (originals are kept) or roll back first')
    origin, inv = _affine()
    lib = unreal.MaterialEditingLibrary
    family_texture = _import_texture(bake_dir / plan['family_table'], 'T_PFB_Families', plan['family_table_sha256'])
    components = originals.setdefault('components', {})
    packages, rows, lookups, instances, masters = [], [], {}, {}, {}
    for entry in report['actors']:
        if entry['decision'] != 'apply':
            continue
        key, index = entry['district'], entry['material_index']
        district = plan['districts'][key]
        finish = _finish_of(entry, originals)
        if key not in lookups:
            lookups[key] = (_import_texture(bake_dir / district['lookup'], 'T_PFB_Lookup_' + key, district['lookup_sha256']),
                            _import_texture(bake_dir / district['lookup2'], 'T_PFB_Lookup2_' + key, district['lookup2_sha256']))
        if finish not in masters:
            masters[finish] = _master(finish)
        if (key, index) not in instances:
            name = 'MI_PFB_%s_m%s' % (key, index)
            path = '%s/%s' % (ROOT, name)
            mi = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else \
                unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, ROOT, unreal.MaterialInstanceConstant,
                                                                        unreal.MaterialInstanceConstantFactoryNew())
            lib.set_material_instance_parent(mi, masters[finish])
            expected = instance_expectations(plan, key, district, index, origin, inv, debug, finish, _finish_hash(finish))
            _configure_instance(mi, expected, {'LookupTexture': lookups[key][0], 'Lookup2Texture': lookups[key][1],
                                               'FamilyTable': family_texture})
            unreal.EditorAssetLibrary.save_loaded_asset(mi)
            instances[(key, index)] = mi
        actor = unreal.load_object(None, entry['path'])
        comp = actor.get_component_by_class(unreal.StaticMeshComponent)
        if entry['path'] not in components:
            if entry['reason'] == 'already_installed':
                raise RuntimeError('Installed materials found without saved originals; refusing to guess: ' + entry['path'])
            components[entry['path']] = {'snapshot': entry['snapshot'], 'tags_before': [str(t) for t in actor.tags]}
            state_file.write_text(json.dumps(originals, indent=1))
        actor.modify()
        comp.set_material(0, instances[(key, index)])
        if TAG not in [str(t) for t in actor.tags]:
            actor.tags = list(actor.tags) + [TAG]
        packages.append(actor.get_package())
        rows.append({'actor': entry['path'], 'material': instances[(key, index)].get_path_name(), 'finish': finish})
    originals['applied_plan'] = plan['plan_hash']
    state_file.write_text(json.dumps(originals, indent=1))
    if packages and not unreal.EditorLoadingAndSavingUtils.save_packages(list(dict.fromkeys(packages)), True):
        raise RuntimeError('Scoped save failed')
    result = verify(bake_dir, districts=districts, allow_skips=allow_skips)
    result['rows'] = rows
    result['skipped_actors'] = [{k: a[k] for k in ('label', 'path', 'decision', 'reason') if k in a}
                                for a in report['actors'] if a['decision'] != 'apply']
    (bake_dir / 'apply-result.json').write_text(json.dumps(result, indent=1))
    return result


def verify(bake_dir, districts=None, allow_skips=False):
    """Geometry/transform/collision unchanged, ownership, instance parameters vs plan, exact coverage."""
    unreal = _unreal()
    bake_dir = Path(bake_dir)
    plan = json.loads((bake_dir / 'plan.json').read_text())
    originals = json.loads((_state_dir() / 'originals.json').read_text())
    report = native_inventory(bake_dir, districts)
    origin, inv = _affine()
    errors, checked = [], 0
    for entry in report['actors']:
        owned = [m for m in entry.get('materials', []) if (asset_key(m) or '').startswith(OWNED_PREFIX)]
        if entry['decision'] != 'apply':
            if owned:
                errors.append('Skipped actor carries installer material: ' + entry['label'])
            continue
        saved = originals['components'].get(entry['path'])
        if saved is None:
            errors.append('No saved originals: ' + entry['path'])
            continue
        now, before = entry['snapshot'], saved['snapshot']
        for field in ('mesh', 'transform', 'collision', 'collision_profile', 'slots', 'lod0_vertices'):
            if now[field] != before[field]:
                errors.append('%s changed on %s' % (field, entry['path']))
        key, index = entry['district'], entry['material_index']
        want = '%s/MI_PFB_%s_m%s' % (ROOT, key, index)
        if len(owned) != 1 or asset_key(owned[0]) != want:
            errors.append('Slot is not the planned instance %s on %s' % (want, entry['label']))
            continue
        mi = unreal.load_asset(want)
        finish = _finish_of(entry, originals)
        expected = instance_expectations(plan, key, plan['districts'][key], index, origin, inv, _debug_value(mi),
                                         finish, _finish_hash(finish))
        errors.extend('%s: %s' % (want, e) for e in check_instance(expected, _NativeReader(mi)))
        checked += 1
    coverage = report['coverage']
    if coverage['planned_not_applied'] and not allow_skips:
        errors.append('Planned buildings skipped: %d' % coverage['planned_not_applied'])
    result = {'status': 'pass' if not errors else 'fail', 'errors': errors, 'components_checked': checked,
              'plan_hash': plan['plan_hash'], 'master': report['master'], 'coverage': {k: v for k, v in coverage.items() if k != 'ids'}}
    (bake_dir / 'native-coverage.json').write_text(json.dumps(coverage, indent=1))
    if errors:
        raise RuntimeError('Photo facade verification failed: %s' % errors[:10])
    return result


def _debug_value(mi):
    """Debug is a viewing aid (0 off, 1 evidence-class tint, 2 flat albedo, 3 painted-mask magenta, 4 original-colour bypass), not plan content."""
    return _NativeReader(mi).scalar('Debug')


def rollback(bake_dir=None):
    """Restore every saved override list exactly, then verify against the saved snapshots."""
    unreal = _unreal()
    _refuse_during_play()
    state_file = _state_dir() / 'originals.json'
    originals = json.loads(state_file.read_text())
    packages, errors = [], []
    for path, saved in originals['components'].items():
        actor = unreal.load_object(None, path)
        if actor is None:
            errors.append('Actor not loaded: ' + path)
            continue
        comp = actor.get_component_by_class(unreal.StaticMeshComponent)
        actor.modify()
        restore_component(comp, saved['snapshot'], unreal.load_asset)
        actor.tags = [t for t in actor.tags if str(t) != TAG] if TAG not in saved['tags_before'] else actor.tags
        packages.append(actor.get_package())
    if errors:
        raise RuntimeError('Rollback incomplete, saved state retained: %s' % errors[:5])
    if packages and not unreal.EditorLoadingAndSavingUtils.save_packages(list(dict.fromkeys(packages)), True):
        raise RuntimeError('Rollback save failed')
    for path, saved in originals['components'].items():
        comp = unreal.load_object(None, path).get_component_by_class(unreal.StaticMeshComponent)
        now = [_path(m) for m in comp.get_editor_property('override_materials')]
        if now != saved['snapshot']['override_materials']:
            errors.append('Override mismatch after rollback: ' + path)
    if errors:
        raise RuntimeError(str(errors[:5]))
    state_file.replace(state_file.with_name('originals.rolled-back.json'))
    return {'status': 'restored', 'components': len(packages)}


def capture(label, out_dir, views, width=1920, height=1080, seconds=3.0):
    """Screenshots plus frame-time samples. Camera use: only after the coordinator lease."""
    import time
    unreal = _unreal()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, (location, rotation) in views.items():
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(unreal.Vector(*location), unreal.Rotator(roll=0, pitch=rotation[0], yaw=rotation[1]))
        samples, last = [], [time.perf_counter()]

        def tick(delta):
            now = time.perf_counter()
            samples.append(now - last[0])
            last[0] = now

        handle = unreal.register_slate_post_tick_callback(tick)
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            time.sleep(.05)
        unreal.unregister_slate_post_tick_callback(handle)
        shot = out_dir / ('%s_%s.png' % (label, name))
        unreal.AutomationLibrary.take_high_res_screenshot(width, height, str(shot))
        samples.sort()
        rows.append({'view': name, 'screenshot': str(shot), 'frames': len(samples),
                     'median_ms': 1000 * samples[len(samples) // 2] if samples else None,
                     'p95_ms': 1000 * samples[int(len(samples) * .95) - 1] if len(samples) > 20 else None})
    (out_dir / (label + '-frame-cost.json')).write_text(json.dumps(rows, indent=1))
    return rows


def shader_statistics(mat):
    """Compiled instruction/sampler counts for one master (zeros mean not compiled, or compiled to the default material)."""
    unreal = _unreal()
    try:
        return unreal.MaterialEditingLibrary.get_statistics(mat).export_text()
    except Exception as error:  # statistics API differs between engine builds
        return 'unavailable: %s' % error


# --------------------------------------------------------------------------- command line

def _main(argv):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'bake'):
        p = sub.add_parser(name)
        p.add_argument('--library', required=True)
        p.add_argument('--jobs', required=True)
        p.add_argument('--output', required=True)
        p.add_argument('--batches', default=str(DEFAULT_BATCHES))
        p.add_argument('--exclude-file', action='append', default=[])
        p.add_argument('--district', action='append')
        p.add_argument('--bounds', nargs=4, type=float, metavar=('E0', 'N0', 'E1', 'N1'))
        p.add_argument('--area', default='citywide')
        p.add_argument('--preserve-batch', action='append', help='batch actor label whose buildings are never painted (default: the LGB batch)')
    args = parser.parse_args(argv)
    try:
        plan = bake(args.library, args.jobs, args.output, batches_dir=args.batches, exclusion_files=args.exclude_file,
                    bounds=args.bounds, districts=args.district, area_name=args.area,
                    validate_only=args.command == 'validate',
                    preserve_batches=tuple(args.preserve_batch) if args.preserve_batch else LGB_BATCHES)
        if args.command == 'validate':
            print(json.dumps(plan, indent=1))
            return 0
    except ContractError as error:
        print('REFUSED:', error, file=sys.stderr)
        return 2
    print(json.dumps({'plan_hash': plan['plan_hash'], 'totals': plan['totals'], 'budgets': plan['budgets']}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
