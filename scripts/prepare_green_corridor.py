"""Write the Al Masar al Akhdar corridor line from a saved Overpass extract.

No network calls. The line is the abandoned railway as mapped in OpenStreetMap,
from Mar Mikhael station east to the river and south along Corniche el Nahr.
It stands for the proposed green route only by following the old rail bed;
the project's own design boundary has not been loaded.
"""
import argparse
import json
from pathlib import Path

# Mar Mikhael station tracks, the bend at the river, and the line south along
# Corniche el Nahr to the former yard. Branches to Tripoli, Naqoura and the
# Damascus line inland are left out.
WAYS = (83038929, 83038918, 85665492, 745902854, 1038301678, 1038301679, 745902853, 1038301685)
# Camera stops, north to south: (name, way, which end or 'mid', note).
STOPS = (
    ('Mar Mikhael station', 83038929, 0, 'Station building and yard under restoration (UNESCO, UN-Habitat).'),
    ('River bend', 745902854, -1, 'The track turns south at the Beirut River.'),
    ('Corniche el Nahr', 745902853, 'mid', 'The rail bed runs beside the river road.'),
    ('Southern end', 1038301685, -1, 'Former rail yard; the corridor end here is not confirmed.'),
)


def prepare(path, ways=WAYS):
    osm = json.loads(Path(path).read_text(encoding='utf-8'))
    found = {e['id']: e for e in osm['elements'] if e.get('type') == 'way' and e['id'] in ways}
    missing = [w for w in ways if w not in found]
    if missing:
        raise SystemExit(f'ways not in {path}: {missing}')
    def point(way, end):
        geometry = found[way]['geometry']
        p = geometry[len(geometry) // 2 if end == 'mid' else end]
        return [p['lon'], p['lat']]
    return {
        'type': 'FeatureCollection', 'name': 'Al Masar al Akhdar',
        'stops': [{'name': name, 'at': point(way, end), 'note': note} for name, way, end, note in STOPS],
        'source': f"OpenStreetMap abandoned railway ways {', '.join(map(str, ways))} "
                  f"(base {osm.get('osm3s', {}).get('timestamp_osm_base', 'unknown')}); ODbL.",
        'features': [{
            'type': 'Feature',
            'properties': {'osm_way': w, 'name': found[w].get('tags', {}).get('name:en') or found[w].get('tags', {}).get('name')},
            'geometry': {'type': 'LineString', 'coordinates': [[p['lon'], p['lat']] for p in found[w]['geometry']]},
        } for w in ways],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--osm', required=True, help='saved Overpass JSON with way geometry')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    corridor = prepare(args.osm)
    Path(args.output).write_text(json.dumps(corridor, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'ways': len(corridor['features']), 'output': args.output}))
