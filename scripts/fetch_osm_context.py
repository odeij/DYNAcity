"""Fetch a reproducible, cached OSM context extract outside the repository.

Run with --bbox WEST SOUTH EAST NORTH --output PRIVATE/context.json.
Keeps source tags and geometry; no observed activity or completeness is inferred.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.parse
import urllib.request

GROUPS = {
    'landscape': ['nwr["natural"]', 'nwr["landcover"]', 'nwr["landuse"]', 'nwr["leisure"]',
                  'nwr["surface"~"^(grass|grass_paver|ground|earth|dirt|mud|woodchips|wood)$"]',
                  'nwr["leaf_type"]', 'nwr["leaf_cycle"]', 'nwr["species"]', 'nwr["genus"]',
                  'nwr["taxon"]', 'nwr["garden:type"]', 'nwr["garden:style"]',
                  'nwr["vegetation"]', 'nwr["green_roof"]', 'nwr["roof:material"~"^(grass|vegetation|green_roof)$"]',
                  'nwr["man_made"="planter"]', 'nwr["barrier"~"^(hedge|hedge_bank)$"]',
                  'nwr["amenity"="parking"]'],
    'mobility': ['way["highway"]', 'node["highway"]', 'nwr["public_transport"]',
                 'way["railway"]', 'way["waterway"]', 'nwr["barrier"]',
                 'nwr["man_made"~"^(pier|breakwater|embankment|retaining_wall)$"]'],
    'places': ['nwr["amenity"]', 'nwr["shop"]', 'nwr["tourism"]', 'nwr["historic"]'],
    'buildings': ['way["building"]', 'relation["building"]', 'nwr["building:part"]'],
    # These keys are not covered by the four original thematic queries unless
    # an object happens to have a second, already selected tag.
    'infrastructure': ['nwr["emergency"]', 'nwr["power"]', 'nwr["utility"]',
        'nwr["man_made"]', 'nwr["traffic_calming"]', 'nwr["traffic_sign"]',
        'nwr["advertising"]', 'nwr["kerb"]', 'nwr["tactile_paving"]',
        'nwr["parking"]', 'nwr["parking:lane"]', 'nwr["playground"]'],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bbox', nargs=4, type=float, required=True, metavar=('WEST','SOUTH','EAST','NORTH'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--refresh', action='store_true', help='Bypass saved group responses')
    parser.add_argument('--groups', nargs='+', choices=tuple(GROUPS), default=list(GROUPS),
                        help='Fetch only these groups to a separate output; defaults to all groups')
    args = parser.parse_args()
    west,south,east,north = args.bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        parser.error('invalid geographic bounds')
    root = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(root):
        parser.error('save raw map data outside the repository')
    cache = args.output.parent / (args.output.stem+'-sources')
    cache.mkdir(parents=True, exist_ok=True)
    box = f'({south},{west},{north},{east})'

    def fetch(name):
        path = cache/(name+'.json')
        query = '[out:json][timeout:60];('+''.join(q+box+';' for q in GROUPS[name])+');out body geom;'
        if path.exists() and not args.refresh:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('query_bbox') == args.bbox and data.get('query') == query:
                return name,data
        errors = []
        for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter'):
            try:
                request = urllib.request.Request(endpoint+'?'+urllib.parse.urlencode({'data':query}),
                    headers={'User-Agent':'DYNAcity-local-map/1.0'})
                with urllib.request.urlopen(request,timeout=80) as response:
                    data=json.load(response)
                if data.get('remark') or not isinstance(data.get('elements'),list):
                    raise RuntimeError(data.get('remark','invalid OSM response'))
                data.update(query_bbox=args.bbox,query=query,endpoint=endpoint,
                            retrieved_on=datetime.now(timezone.utc).date().isoformat())
                path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                return name,data
            except Exception as error:
                errors.append(str(error))
        raise RuntimeError(f'{name}: '+ '; '.join(errors))

    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(fetch,name) for name in args.groups]):
            name,data=future.result()
            results[name]=data
            print(f'{name}: {len(data["elements"]):,} source objects',flush=True)
    elements = {}
    for name in dict.fromkeys(args.groups):
        data=results[name]
        for element in data['elements']:
            elements[element['type'],element['id']]={**element,
                'source_as_of':data.get('osm3s',{}).get('timestamp_osm_base'),
                'retrieved_on':data['retrieved_on']}
    output={'version':.6,'generator':'DYNAcity OSM context fetch', 'query_bbox':args.bbox,
        'retrieved_on':datetime.now(timezone.utc).date().isoformat(),
        'sources':{name:{k:data.get(k) for k in ('endpoint','osm3s','retrieved_on')} for name,data in results.items()},
        'elements':list(elements.values())}
    args.output.write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
    print(f'Saved {len(elements):,} unique objects to {args.output}',flush=True)


if __name__ == '__main__':
    main()
