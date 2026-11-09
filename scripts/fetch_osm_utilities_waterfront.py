"""Targeted OSM fetch for utilities, waterfront and rail (agent-3 audit).

Run: python scripts/fetch_osm_utilities_waterfront.py --output PRIVATE/osm-utilities-waterfront.json
Keeps tags, geometry, ids, timestamps; output must be outside the repository.
"""
import argparse, json, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BBOX = (33.8288, 35.4693, 33.9076, 35.5720)  # south west north east
SELECTORS = [
    'nwr["natural"~"^(coastline|bay|cape|reef|strait|shoal|rock|beach|sand|water|wetland)$"]',
    'nwr["waterway"]', 'nwr["railway"]', 'nwr["harbour"]', 'nwr["landuse"~"^(port|railway|industrial|harbour)$"]',
    'nwr["industrial"]', 'nwr[~"^seamark:"~"."]', 'nwr["power"]', 'nwr["telecom"]', 'nwr["utility"]',
    'nwr["man_made"~"^(pier|breakwater|quay|groyne|jetty|embankment|pipeline|street_cabinet|cable_cabinet|telecom|transformer|utility_pole|lamp|crane|beacon|tower|mast|lighthouse|flagpole|chimney|water_tower|water_tank|water_well|water_works|storage_tank|silo|gasometer|surveillance|antenna|communications_tower|monitoring_station|bunker_silo|pumping_station|reservoir_covered|wastewater_plant|petroleum_well|tank)$"]',
    'nwr["amenity"~"^(fire_hydrant|post_box|post_office|telephone|ferry_terminal|fountain|water_point|drinking_water|fuel)$"]',
    'nwr["emergency"~"^(fire_hydrant|water_tank|fire_water_pond|suction_point|defibrillator|phone)$"]',
    'nwr["advertising"]', 'nwr["route"~"^(ferry|railway|train|power|pipeline)$"]', 'nwr["bridge"="yes"]["waterway"]',
    'nwr["leisure"~"^(marina|slipway|harbour)$"]', 'nwr["building"~"^(service|transformer_tower|water_tower|industrial|kiosk)$"]',
]
def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True); a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    if a.output.resolve().is_relative_to(root): p.error('keep raw data outside the repository')
    box = '(%s,%s,%s,%s)' % BBOX
    query = '[out:json][timeout:120];(' + ''.join(s + box + ';' for s in SELECTORS) + ');out body geom;'
    errors = []
    for ep in ('https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter'):
        try:
            req = urllib.request.Request(ep, data=urllib.parse.urlencode({'data': query}).encode(), headers={'User-Agent': 'DYNAcity-local-map/1.0'})
            data = json.load(urllib.request.urlopen(req, timeout=200))
            if data.get('remark') or not isinstance(data.get('elements'), list): raise RuntimeError(data.get('remark', 'bad response'))
            data.update(query=query, query_bbox_swne=BBOX, endpoint=ep, retrieved_on=datetime.now(timezone.utc).date().isoformat())
            a.output.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            print(len(data['elements']), 'elements', a.output); return
        except Exception as e: errors.append(str(e))
    raise SystemExit('; '.join(errors))
if __name__ == '__main__': main()
