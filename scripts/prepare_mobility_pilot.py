"""Build a private pilot package from an already reachable/saved OSM XML extract.

No network calls. Historical classified counts are an optional scenario profile,
not observations of the pilot or present-day traffic.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from dynacity.mobility_inputs import MobilityInputs, pilot_network


def prepare(path, bounds, retrieved_on):
    root = ET.parse(path).getroot()
    nodes = {n.attrib['id']: (float(n.attrib['lon']), float(n.attrib['lat'])) for n in root.findall('node')}
    west, south, east, north = bounds
    inside = lambda p: west <= p[0] <= east and south <= p[1] <= north
    features = []
    for kind in ('node', 'way'):
        for element in root.findall(kind):
            tags = {t.attrib['k']: t.attrib['v'] for t in element.findall('tag')}
            feature_kind = ('parking' if tags.get('amenity') == 'parking' else
                            {'bus_stop': 'bus_stop', 'crossing': 'crossing', 'traffic_signals': 'signal'}.get(tags.get('highway')))
            if not feature_kind:
                continue
            points = [nodes[element.attrib['id']]] if kind == 'node' else [nodes.get(n.attrib['ref']) for n in element.findall('nd')]
            if not points or any(p is None or not inside(p) for p in points):
                continue
            if feature_kind == 'parking' and len(points) > 1 and (len(points) < 4 or points[0] != points[-1]):
                continue
            capacity = tags.get('capacity', '')
            features.append({'id': f'{kind}/{element.attrib["id"]}', 'kind': feature_kind,
                'label': tags.get('name', {'bus_stop': 'Mapped bus stop', 'parking': 'Mapped parking area',
                    'crossing': 'Mapped crossing', 'signal': 'Mapped traffic signal'}[feature_kind]),
                'path': points, 'capacity': int(capacity) if capacity.isdigit() else None,
                'source': 'osm', 'source_url': f'https://www.openstreetmap.org/{kind}/{element.attrib["id"]}'})
    weights = {'car': 81.3, 'taxi': 6.9, 'van': 1.2, 'minibus': 1.2,
               'light_goods': 6.4, 'truck1': 1.3, 'truck2': 1.5, 'bus': .1}
    classes = {'car': 'passenger', 'taxi': 'taxi', 'van': 'delivery', 'minibus': 'bus',
               'light_goods': 'delivery', 'truck1': 'truck', 'truck2': 'truck', 'bus': 'bus'}
    return MobilityInputs.model_validate({
        'title': 'Armenia Street / Mar Mikhael pilot', 'bounds': bounds,
        'sources': [
            {'id': 'osm', 'title': 'OpenStreetMap local extract', 'url': 'https://www.openstreetmap.org/copyright',
             'as_of': retrieved_on, 'kind': 'mapped',
             'limitations': 'ODbL. Mapping is not a current operational survey; occupancy and missing features are unknown.'},
            {'id': 'aub2015', 'title': 'AUB IFI Working Paper 28, table 5.1, printed page 14',
             'url': 'https://scholarworks.aub.edu.lb/server/api/core/bitstreams/07cae89a-f5c1-4878-af96-b179825303b9/content',
             'as_of': '2015-02 (publication; exact count dates not established)', 'kind': 'published',
             'limitations': 'Rounded aggregate peak-hour shares from historical Beirut surveys; motorcycles excluded. Not pilot-specific.'},
        ],
        'features': features,
        'types': [{'id': key, 'label': key.replace('_', ' ').title(), 'vclass': classes[key]} for key in weights],
        'fleet': {'source': 'aub2015', 'label': 'Historical vehicle mix · 2015 report',
                  'scope': 'Historical Beirut aggregate peak-hour mix transferred to this network as a scenario. '
                           'Reported 99.9% total normalized for rounding. SUMO class dimensions and behaviour are assumptions, '
                           'including generic bus dimensions for minibuses. Not calibrated Mar Mikhael or current traffic.',
                  'weights': weights, 'missing_classes': ['motorcycle']},
        'gaps': [
            'Motorcycle counts and local class-specific driving behaviour are unavailable.',
            'No surveyed local signal phases, timings or operating status are loaded.',
            'No pedestrian crossing demand, parking occupancy or pickup dwell observations are loaded.',
            'ACTC timetable B7 is Cola–Sidon; local B7 stop labels are not reconciled to that service.',
        ],
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--osm', required=True)
    parser.add_argument('--bounds', type=float, nargs=4, required=True, metavar=('W', 'S', 'E', 'N'))
    parser.add_argument('--retrieved-on', default=date.today().isoformat())
    parser.add_argument('--output', required=True)
    parser.add_argument('--traffic-model', help='optional existing private traffic model to subset')
    parser.add_argument('--output-traffic', help='private output for the separate pilot traffic model')
    args = parser.parse_args()
    if bool(args.traffic_model) != bool(args.output_traffic):
        parser.error('--traffic-model and --output-traffic must be supplied together')
    package = prepare(args.osm, args.bounds, args.retrieved_on)
    Path(args.output).write_text(package.model_dump_json(indent=2), encoding='utf-8')
    print(json.dumps({'features': len(package.features), 'types': len(package.types), 'output': args.output}))
    if args.traffic_model:
        from dynacity.traffic import TrafficModel
        model, original_roads = pilot_network(TrafficModel.load(args.traffic_model), args.bounds)
        model.save(args.output_traffic)
        Path(args.output_traffic+'.manifest.json').write_text(json.dumps({
            'source': args.traffic_model, 'bounds': args.bounds, 'original_road_indices': original_roads,
            'note': 'Whole-road pilot subset. Road indices are local to this output. Original node identities retained.'
        }, indent=2), encoding='utf-8')
        print(json.dumps({'pilot_roads': len(model), 'output': args.output_traffic}))
