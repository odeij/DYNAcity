"""Stage mapped OSM traffic_calming=island ways as surface-geometry footprints (agent 2).

Closed ways keep their mapped outline. Open ways are mapped lines with no width, so
they are buffered by an explicitly inferred width. Heights are traced on the
existing roads inside Unreal at import time. Node-only islands are not drawn.
"""
import argparse, hashlib, json, math, sys
from pathlib import Path
import pyproj, shapely
from shapely.geometry import LineString, Polygon

root = Path(__file__).resolve().parents[1]
LINE_WIDTH_M = 0.8


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context', type=Path, nargs='+', required=True)
    p.add_argument('--origin-manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.resolve().is_relative_to(root): p.error('Keep outputs outside the repository')
    origin = json.loads(a.origin_manifest.read_text(encoding='utf8'))['origin_utm_m']
    tr = pyproj.Transformer.from_crs(4326, 32636, always_xy=True)
    els = {}; hashes = {}
    for f in a.context:
        raw = f.read_bytes(); hashes[str(f.resolve())] = hashlib.sha256(raw).hexdigest()
        for e in json.loads(raw)['elements']: els[(e['type'], e['id'])] = e
    out = []; rejected = []
    for (ty, i), e in sorted(els.items()):
        t = e.get('tags', {})
        if ty != 'way' or t.get('traffic_calming') != 'island': continue
        g = e['geometry']; pts = [tr.transform(q['lon'], q['lat']) for q in g]
        loc = [(x - origin[0], y - origin[1]) for x, y in pts]
        closed = g[0] == g[-1] and len(g) >= 4
        shape = Polygon(loc) if closed else LineString(loc).buffer(LINE_WIDTH_M / 2, cap_style=2, join_style=2)
        if not shape.is_valid: shape = shape.buffer(0)
        if shape.is_empty or shape.geom_type != 'Polygon': rejected.append({'id': f'way/{i}', 'reason': 'invalid geometry'}); continue
        if not closed:  # densify so heights follow the road along long lines
            dens = []; 
            L = LineString(loc); n = max(1, math.ceil(L.length / 4))
            pts_l = [L.interpolate(L.length * k / n) for k in range(n + 1)]
            tris = []
            for k in range(n):
                a0, a1 = pts_l[k], pts_l[k + 1]
                dx, dy = a1.x - a0.x, a1.y - a0.y; d = math.hypot(dx, dy) or 1
                nx, ny = -dy / d * LINE_WIDTH_M / 2, dx / d * LINE_WIDTH_M / 2
                q = [(a0.x + nx, a0.y + ny), (a1.x + nx, a1.y + ny), (a1.x - nx, a1.y - ny), (a0.x - nx, a0.y - ny)]
                tris += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
        else:
            tris = [[list(c) for c in t_.exterior.coords[:3]] for t_ in shapely.constrained_delaunay_triangles(shape).geoms]
        out.append({'source_id': f'way/{i}', 'tags': t, 'osm_as_of': e.get('source_as_of'), 'retrieved_on': e.get('retrieved_on'),
            'geometry_kind': 'closed outline (mapped)' if closed else f'open line buffered to {LINE_WIDTH_M} m (width inferred)',
            'area_m2': round(shape.area, 2), 'centroid_local_m': list(shape.centroid.coords[0]),
            'triangles_local_xy_m': [[list(v) for v in tri] for tri in tris], 'source_node_ids': e.get('nodes')})
    res = {'schema': 'dynacity-agent2-traffic-islands/1', 'origin_utm_m': origin, 'context_sha256': hashes,
        'line_width_m_inferred': LINE_WIDTH_M, 'height_m_inferred': 0.12,
        'islands': out, 'rejected': rejected}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(res), encoding='utf8')
    print(json.dumps({'islands': len(out), 'triangles': sum(len(x['triangles_local_xy_m']) for x in out), 'rejected': rejected}))

if __name__ == '__main__': main()
