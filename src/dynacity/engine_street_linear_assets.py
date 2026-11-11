"""Build mapped boundary props using existing source-ground street polylines.

Mapped position and tags are retained. Untagged heights and fence construction
are illustrative, not surveyed Beirut measurements. No bridges are generated.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

from .engine_asset import export_glb
from .engine_street_assets import _colour_glb

DEFAULT_HEIGHT = {'wall': 1.2, 'jersey_barrier': .81, 'fence': 1.4}
DEFAULT_HEIGHT['hedge']=1.5
COLOURS = {'wall': (.40, .38, .32), 'jersey_barrier': (.43, .43, .39),
           'fence': (.24, .26, .25)}
COLOURS['hedge']=(.12,.23,.08)


def _metres(value):
    """Only accept an unambiguous positive scalar, optionally suffixed by m."""
    match = re.fullmatch(r'\s*((?:\d+(?:\.\d+)?|\.\d+))\s*(?:m)?\s*', str(value or ''))
    if not match:
        return None
    number = float(match[1])
    return number if 0 < number <= 20 else None


def _beam(vertices, faces, a, b, width, low, high, top_width=None):
    """Open-bottom prism with sloping base/top following endpoint elevations."""
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = math.hypot(dx, dy)
    if length < 1e-5:
        return
    nx, ny = -dy/length*width/2, dx/length*width/2
    tx, ty = -dy/length*(top_width or width)/2, dx/length*(top_width or width)/2
    offset = len(vertices)
    for p in (a, b):
        vertices.extend([[p[0]+nx, p[1]+ny, p[2]+low],
                         [p[0]-nx, p[1]-ny, p[2]+low],
                         [p[0]-tx, p[1]-ty, p[2]+high],
                         [p[0]+tx, p[1]+ty, p[2]+high]])
    for quad in ((0, 4, 7, 3), (1, 2, 6, 5), (3, 7, 6, 2),
                 (0, 3, 2, 1), (4, 5, 6, 7)):
        faces.extend([[offset+quad[0], offset+quad[1], offset+quad[2]],
                      [offset+quad[0], offset+quad[2], offset+quad[3]]])


def line_mesh(line, *, max_gap_m=4.5):
    """Return geometry plus explicit measurement/illustration provenance."""
    kind = line['kind']
    tags = line.get('tags', {})
    height = _metres(tags.get('height'))
    height_source = 'OSM height tag' if height else 'illustrative fallback'
    height = height or DEFAULT_HEIGHT[kind]
    width = _metres(tags.get('width'))
    width_source = 'OSM width tag' if width else 'illustrative fallback'
    width = width or (.60 if kind == 'jersey_barrier' else .22)
    vertices, faces = [], []
    skips = Counter()
    posts = set()
    for a, b in zip(line.get('points_local_m', []), line.get('points_local_m', [])[1:]):
        if (len(a) != 3 or len(b) != 3 or
                not all(math.isfinite(v) for v in (*a, *b))):
            skips['invalid_endpoint'] += 1
            continue
        distance = math.hypot(b[0]-a[0], b[1]-a[1])
        if distance > max_gap_m:
            skips['missing_ground_gap'] += 1
            continue
        if distance < 1e-5:
            skips['zero_length'] += 1
            continue
        if kind == 'fence':
            for level in (.38, .86):
                _beam(vertices, faces, a, b, .045, max(0, height*level-.025), height*level+.025)
            for p in (a, b):
                key = tuple(p)
                if key not in posts:
                    posts.add(key)
                    _beam(vertices, faces, [p[0]-.035, p[1], p[2]],
                          [p[0]+.035, p[1], p[2]], .07, 0, height)
        else:
            # The simple concrete section is explicitly illustrative; ground
            # endpoints remain those already sampled from source terrain.
            _beam(vertices, faces, a, b, width, 0, height,
                  top_width=width*.35 if kind == 'jersey_barrier' else None)
    return vertices, faces, {'height_m': height, 'height_source': height_source,
                            'width_m': width, 'width_source': width_source,
                            'appearance_source': 'illustrative material and construction',
                            'skipped_segments': dict(skips)}


def build(manifest, output):
    source = Path(manifest)
    raw = source.read_bytes()
    signature = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    if data['schema'] != 'dynacity-engine-street-context/1':
        raise ValueError('Unsupported street manifest')
    folder = Path(output)/('linear-'+signature[:16])
    folder.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    skipped = Counter()
    for line in data['lines']:
        if line['kind'] not in DEFAULT_HEIGHT:
            continue
        if line.get('bridge_tagged') or not line.get('render_geometry', False):
            skipped['provenance_only'] += 1
            continue
        groups[(line.get('production_area', 'Unassigned'), line['kind'])].append(line)
    packages = []
    for (area, kind), lines in sorted(groups.items()):
        key = 'linear_'+re.sub('[^a-z0-9]+', '_', area.lower()).strip('_')+'_'+kind
        name = 'StreetLinear_'+key+'_'+signature[:8]
        vertices, faces, objects = [], [], []
        for line in lines:
            verts, tris, provenance = line_mesh(line)
            skipped.update(provenance['skipped_segments'])
            if not tris:
                skipped['empty_line'] += 1
                continue
            start, offset = len(faces), len(vertices)
            vertices.extend(verts)
            faces.extend([[offset+i for i in tri] for tri in tris])
            objects.append({'source_id': line['id'], 'instance_id': line.get('instance_id'),
                            'tags': line.get('tags', {}), 'source': line.get('source'),
                            'as_of': line.get('as_of'), 'retrieved_on': line.get('retrieved_on'),
                            'triangle_start': start, 'triangle_count': len(tris), **provenance})
        if not faces:
            continue
        scene = {'coordinates': {'origin_utm_m': data['origin_utm_m'],
                                 'frame': 'Local east/north/up metres'},
                 'limitations': ['Mapped boundaries; height/appearance illustrative unless tagged.'],
                 'bounds_lonlat': None, 'trees': [], 'roads': [],
                 'meshes': [{'id': name, 'kind': 'terrain', 'vertices': vertices,
                             'triangles': faces, 'metadata': {'source_manifest': str(source),
                             'production_area': area, 'mapped_kind': kind}}]}
        path = folder/(name+'.glb')
        if not path.exists():
            export_glb(scene, path, include_calibration=False)
            _colour_glb(path, COLOURS[kind])
        packages.append({'key': key, 'name': name, 'kind': kind, 'production_area': area,
                         'asset': str(path), 'objects': objects, 'triangles': len(faces)})
    result = {'schema': 'dynacity-street-cover-assets/1', 'origin_utm_m': data['origin_utm_m'],
              'source_manifest': str(source), 'source_sha256': signature,
              'packages': packages, 'skipped': dict(skipped),
              'limitations': ['No bridges or railways generated. Source ground gaps over 4.5 m skipped.',
                              'Fence rails/posts and concrete sections are illustrative.']}
    target = folder/'linear-assets.json'
    target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return target


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(build(args.manifest, args.output))
