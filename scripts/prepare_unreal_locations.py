"""Prepare an offline OSM gazetteer and source road locations outside Git."""
import argparse
import json
from pathlib import Path


def build(context, roads):
    places = []
    for feature in context['features']:
        props = feature.get('properties', {})
        tags = props.get('tags', {})
        names = [v for k, v in tags.items() if k == 'name' or k.startswith('name:') or k in ('alt_name', 'short_name', 'official_name')]
        if not names: continue
        geometry = feature.get('geometry') or {}
        def pairs(value):
            if isinstance(value, list) and len(value) >= 2:
                if isinstance(value[0], (int, float)): yield value[:2]
                else:
                    for item in value: yield from pairs(item)
        points = list(pairs(geometry.get('coordinates', [])))
        if not points: continue
        # Representative point for navigation, not a surveyed entrance.
        lon = (min(p[0] for p in points)+max(p[0] for p in points))/2
        lat = (min(p[1] for p in points)+max(p[1] for p in points))/2
        places.append({'id': props['id'], 'name': tags.get('name:en') or tags.get('name') or names[0],
                       'aliases': sorted(set(names)), 'lat': lat, 'lon': lon,
                       'kind': props.get('kind', 'place')})
    prepared_roads = []
    for row in roads['roads']:
        path = row['path']
        if len(path) < 2: continue
        prepared_roads.append({'id': row.get('road_id', row.get('id')), 'name': row.get('name', ''),
            'path': path, 'bounds': [min(p[0] for p in path), min(p[1] for p in path),
                                     max(p[0] for p in path), max(p[1] for p in path)]})
    return {'schema': 'dynacity-locations/1', 'origin_utm_m': roads['coordinates']['origin_utm_m'],
            'places': places, 'roads': prepared_roads, 'source': 'OpenStreetMap contributors (ODbL)',
            'retrieved_on': context.get('retrieved_on'), 'bounds_lonlat': context.get('bounds'),
            'limits': 'Downloaded names only; representative points are not surveyed entrances. Coverage is incomplete.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--roads', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('Save private location data outside the repository.')
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    data = build(read(args.context), read(args.roads))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    print(json.dumps({'places': len(data['places']), 'roads': len(data['roads']), 'output': str(output)}))


if __name__ == '__main__': main()
