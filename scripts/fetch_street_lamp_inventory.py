"""Fetch mapped lighting points and lit-road references; never invent poles."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bbox', nargs=4, type=float, default=[35.4693, 33.8288, 35.572, 33.9076])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    west, south, east, north = args.bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        parser.error('Invalid bounds')
    output = args.output.resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('Raw source data must remain outside the repository')
    box = f'({south},{west},{north},{east})'
    selectors = ['nwr["highway"="street_lamp"]', 'nwr["man_made"="street_lamp"]',
                 'nwr["light_source"]', 'nwr["tower:type"="lighting"]',
                 'nwr["lamp_mount"]', 'nwr["light:mount"]',
                 'way["highway"]["lit"]']
    query = '[out:json][timeout:45];(' + ''.join(s+box+';' for s in selectors) + ');out meta geom;'
    errors = []
    for endpoint in ('https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter'):
        try:
            req = urllib.request.Request(endpoint+'?'+urllib.parse.urlencode({'data':query}),
                                         headers={'User-Agent':'DYNAcity-lighting-inventory/1.0'})
            with urllib.request.urlopen(req, timeout=55) as response:
                raw = json.load(response)
            if raw.get('remark') or not isinstance(raw.get('elements'), list):
                raise ValueError(raw.get('remark', 'Invalid response'))
            break
        except Exception as error:
            errors.append(str(error))
    else:
        raise RuntimeError('; '.join(errors))
    output.mkdir(parents=True, exist_ok=True)
    raw.update(query=query, query_bbox=args.bbox, endpoint=endpoint,
               retrieved_at=datetime.now(timezone.utc).isoformat())
    (output/'osm-lighting-raw.json').write_text(json.dumps(raw), encoding='utf-8')
    features = []; counts = Counter(); lit = Counter()
    for element in raw['elements']:
        tags = element.get('tags', {})
        street_lamp = tags.get('highway') == 'street_lamp' or tags.get('man_made') == 'street_lamp'
        if street_lamp:
            kind = 'mapped_street_lamp'
        elif tags.get('light_source') or tags.get('tower:type') == 'lighting' or tags.get('lamp_mount') or tags.get('light:mount'):
            kind = 'other_lighting_candidate'
        else:
            kind = 'lit_road_reference'; lit[tags.get('lit','unknown')] += 1
        counts[kind] += 1
        if element['type'] == 'node':
            geometry = {'type':'Point', 'coordinates':[element['lon'], element['lat']]}
        elif element.get('geometry'):
            geometry = {'type':'LineString', 'coordinates':[[p['lon'], p['lat']] for p in element['geometry'] if 'lon' in p and 'lat' in p]}
            if len(geometry['coordinates']) < 2:
                geometry = None
        else:
            geometry = None
        features.append({'type':'Feature', 'geometry':geometry, 'properties':{
            'source_id':f"{element['type']}/{element['id']}", 'kind':kind, 'tags':tags,
            'source_timestamp':element.get('timestamp'), 'source_version':element.get('version')}})
    (output/'lighting-inventory.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}), encoding='utf-8')
    report = {'bbox':args.bbox, 'counts':dict(counts), 'lit_road_values':dict(lit),
              'retrieved_at':raw['retrieved_at'], 'source_as_of':raw.get('osm3s',{}).get('timestamp_osm_base'),
              'source':'OpenStreetMap contributors, ODbL', 'endpoint':endpoint,
              'limits':['Mapped does not mean field verified or complete.',
                        'Lit road tags do not locate individual poles or establish current operating status.',
                        'Other lighting candidates require review; no automatic import performed.']}
    (output/'report.json').write_text(json.dumps(report,indent=2), encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
