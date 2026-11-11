"""Create a private, identity-bound road map for a subset SUMO network."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from dynacity.traffic import TrafficModel
from dynacity.traffic_sumo import road_identity


def index_identity(index):
    return hashlib.sha256(json.dumps(index, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def prepare(pilot, city, index):
    candidates = defaultdict(list)
    for i, (way, geom) in enumerate(zip(city.way_id, city.geometry)):
        candidates[(int(way), tuple(map(tuple, geom)))].append(i)
    indexed = {int(road['id']): road for road in index['roads']}
    origin = index['origin_utm_m']
    roads = {}
    for i, (way, geom) in enumerate(zip(pilot.way_id, pilot.geometry)):
        matches = candidates[(int(way), tuple(map(tuple, geom)))]
        if len(matches) != 1 or matches[0] not in indexed:
            raise ValueError(f'Pilot road {i} has no unique indexed city match')
        path = indexed[matches[0]]['path']
        if not path or any(min((point[0]+origin[0]-end[0])**2 +
                               (point[1]+origin[1]-end[1])**2 for point in path) > 4
                           for end in (geom[0], geom[-1])):
            raise ValueError(f'City index geometry does not match pilot road {i}')
        roads[str(i)] = str(matches[0])
    return {'schema': 'dynacity-traffic-road-map/1', 'source_road_identity': road_identity(pilot),
            'city_index_identity': index_identity(index), 'roads': roads}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pilot', 'city', 'index', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    index = json.loads(Path(args.index).read_text(encoding='utf-8'))
    result = prepare(TrafficModel.load(args.pilot), TrafficModel.load(args.city), index)
    with Path(args.output).open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print(f"Prepared {len(result['roads'])} uniquely matched roads")


if __name__ == '__main__':
    main()
