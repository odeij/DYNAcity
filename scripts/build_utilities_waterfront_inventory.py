"""Write a source-linked GeoJSON + inventory of utilities/waterfront/rail OSM objects (agent-3).

Lines and areas are kept as geometry/metadata; no 3D object is implied. Output stays outside Git.
"""
import argparse, collections, json
from pathlib import Path

CATS = [('coastline', 'natural', {'coastline', 'bay'}), ('waterway', 'waterway', None), ('rail', 'railway', None),
        ('pier_breakwater_harbour', 'man_made', {'pier', 'breakwater', 'quay', 'groyne', 'jetty'}), ('harbour_seamark', 'harbour', None),
        ('power', 'power', None), ('tank_gasometer_silo', 'man_made', {'storage_tank', 'gasometer', 'silo', 'water_tank'}),
        ('water_works_tower', 'man_made', {'water_works', 'water_tower'}), ('mast_tower_lighthouse_chimney_flagpole', 'man_made', {'mast', 'tower', 'lighthouse', 'chimney', 'flagpole'}),
        ('surveillance', 'man_made', {'surveillance'}), ('telecom', 'telecom', None), ('advertising', 'advertising', None),
        ('post_phone', 'amenity', {'post_box', 'telephone'}), ('landuse_port_industrial', 'landuse', {'port', 'harbour', 'industrial', 'railway'})]

def category(tags):
    for name, key, vals in CATS:
        if key in tags and (vals is None or tags[key] in vals): return name
    if any(k.startswith('seamark:') for k in tags): return 'harbour_seamark'
    return None

def geometry(e):
    if e['type'] == 'node': return {'type': 'Point', 'coordinates': [e['lon'], e['lat']]}
    if e['type'] == 'way' and e.get('geometry'):
        c = [[g['lon'], g['lat']] for g in e['geometry']]
        return {'type': 'Polygon', 'coordinates': [c]} if len(c) > 3 and c[0] == c[-1] and 'natural' not in e['tags'] or (len(c) > 3 and c[0] == c[-1] and e['tags'].get('natural') != 'coastline') else {'type': 'LineString', 'coordinates': c}
    if e['type'] == 'relation': return {'type': 'GeometryCollection', 'geometries': [{'type': 'LineString', 'coordinates': [[g['lon'], g['lat']] for g in m['geometry']]} for m in e.get('members', []) if m.get('geometry')]}
    return None

def main():
    p = argparse.ArgumentParser(); p.add_argument('--osm', type=Path, required=True); p.add_argument('--output', type=Path, required=True); a = p.parse_args()
    data = json.loads(a.osm.read_text(encoding='utf8')); feats = []; counts = collections.Counter()
    for e in data['elements']:
        c = category(e.get('tags', {}))
        if not c: counts['uncategorised'] += 1; continue
        g = geometry(e)
        counts[(c, e['type'])] += 1
        feats.append({'type': 'Feature', 'geometry': g, 'properties': {'id': f"{e['type']}/{e['id']}", 'category': c, 'tags': e['tags'],
            'timestamp': e.get('timestamp'), 'retrieved_on': data['retrieved_on'], 'source': 'OpenStreetMap contributors (ODbL)',
            'representation': 'geometry/metadata only; no 3D asset implied'}})
    out = {'type': 'FeatureCollection', 'features': feats, 'inventory': {f'{k[0]}:{k[1]}' if isinstance(k, tuple) else k: v for k, v in sorted(counts.items(), key=str)},
           'query_bbox_swne': data['query_bbox_swne'], 'retrieved_on': data['retrieved_on']}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(out, ensure_ascii=False), encoding='utf8')
    print(json.dumps(out['inventory'], indent=1))
if __name__ == '__main__': main()
