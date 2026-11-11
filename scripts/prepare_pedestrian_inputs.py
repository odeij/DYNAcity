"""Prepare Beirut inputs for an Urban Network Analysis (UNA) pedestrian-flow run.

Follows the layer types of Sevtsuk et al. (2024), "Pedestrian-oriented development in Beirut",
Cities 149, 104927, whose PM-peak calibration combines four flows: amenities->amenities,
jobs->homes and parks->amenities (from high-amenity-access origins) and homes->places of worship
(from low-amenity-access origins). Their input layers were not published, so ours are proxies:

  network       OSM walkable ways (motorways, trunk roads and their links excluded)
  amenities     OSM shops and street-facing services (one point each, weight 1)
  homes         Beirut 001 model buildings: footprint x floors (height / 3 m) / 100 m2 of building per resident
  jobs          PROXY: OSM offices, shops, services, schools and hospitals with rough job weights
  parks         OSM leisure=park/garden centroids (open/closed status is not known)
  worship       OSM places of worship
  bus_stops     OSM highway=bus_stop

All layers are written as GeoJSON in EPSG:32636 metres. Run with the repository's .venv.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'engine' / 'unreal' / 'Content' / 'Python'))
import location_core  # noqa: E402

# Beirut municipality plus a margin (south, west, north, east).
BBOX = (33.862, 35.465, 33.912, 35.540)
NOT_WALKABLE = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'construction', 'proposed',
                'raceway', 'bus_guideway', 'escape', 'platform'}
AMENITY_SERVICES = {'restaurant', 'cafe', 'bar', 'pub', 'fast_food', 'ice_cream', 'pharmacy', 'bank',
                    'atm', 'clinic', 'dentist', 'doctors', 'bureau_de_change', 'post_office',
                    'marketplace', 'theatre', 'cinema', 'library', 'arts_centre', 'nightclub', 'food_court'}
# Illustrative jobs per point; no Beirut employment layer is available.
JOB_WEIGHTS = {'office': 15, 'shop': 3, 'service': 4, 'school': 40, 'university': 150, 'college': 60,
               'hospital': 250, 'government': 60}
# Building floor area per resident, including non-residential space. Chosen so the modeled buildings
# hold about 450,000 people, the commonly cited order for Beirut municipality; not a census figure.
M2_PER_RESIDENT = 100.0
CRS = {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:EPSG::32636'}}

QUERY = """[out:json][timeout:180];
(
  way["highway"]({b});
  node["shop"]({b}); way["shop"]({b});
  node["amenity"]({b}); way["amenity"]({b});
  node["office"]({b}); way["office"]({b});
  way["leisure"~"^(park|garden)$"]({b}); relation["leisure"~"^(park|garden)$"]({b});
  node["highway"="bus_stop"]({b});
);
out center geom tags;"""


def fetch(path):
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))
    b = ','.join(str(v) for v in BBOX)
    data = urllib.parse.urlencode({'data': QUERY.format(b=b)}).encode()
    req = urllib.request.Request('https://overpass-api.de/api/interpreter', data=data,
                                 headers={'User-Agent': 'DynaCITY research (pedestrian inputs)'})
    with urllib.request.urlopen(req, timeout=240) as response:
        raw = response.read()
    path.write_bytes(raw)
    return json.loads(raw)


def utm(lon, lat):
    return list(location_core.lonlat_to_utm(lon, lat))


def point(element):
    if element['type'] == 'node':
        return utm(element['lon'], element['lat'])
    c = element.get('center')
    if c:
        return utm(c['lon'], c['lat'])
    # `out geom` returns outlines without a centre: use the outline's mean (members for relations).
    coords = element.get('geometry') or [g for m in element.get('members', []) for g in m.get('geometry', [])]
    coords = [g for g in coords if g]
    if not coords:
        b = element.get('bounds')
        return utm((b['minlon'] + b['maxlon']) / 2, (b['minlat'] + b['maxlat']) / 2) if b else None
    return utm(sum(g['lon'] for g in coords) / len(coords), sum(g['lat'] for g in coords) / len(coords))


def feature_collection(features):
    return {'type': 'FeatureCollection', 'crs': CRS, 'features': features}


def points(rows):
    return feature_collection([{'type': 'Feature', 'properties': props,
                                'geometry': {'type': 'Point', 'coordinates': xy}} for xy, props in rows])


def classify(elements):
    network, amenities, jobs, parks, worship, bus = [], [], [], [], [], []
    for e in elements:
        t = e.get('tags', {})
        if e['type'] == 'way' and 'highway' in t and 'geometry' in e:
            if t['highway'] in NOT_WALKABLE or t.get('foot') == 'no' or t.get('access') == 'private':
                continue
            coords = [utm(g['lon'], g['lat']) for g in e['geometry']]
            if len(coords) > 1:
                network.append({'type': 'Feature', 'properties': {
                    'osm_id': e['id'], 'highway': t['highway'], 'name': t.get('name', ''),
                    'sidewalk': t.get('sidewalk', '')},
                    'geometry': {'type': 'LineString', 'coordinates': coords}})
            continue
        xy = point(e)
        if xy is None:
            continue
        a = t.get('amenity')
        if t.get('highway') == 'bus_stop':
            bus.append((xy, {'osm_id': e['id'], 'weight': 1}))
        if t.get('leisure') in ('park', 'garden'):
            parks.append((xy, {'osm_id': e['id'], 'name': t.get('name', ''), 'weight': 1}))
        if a == 'place_of_worship':
            worship.append((xy, {'osm_id': e['id'], 'religion': t.get('religion', ''), 'weight': 1}))
        if 'shop' in t or a in AMENITY_SERVICES:
            amenities.append((xy, {'osm_id': e['id'], 'kind': t.get('shop') or a, 'weight': 1}))
        kind = ('office' if 'office' in t and t['office'] != 'government' else
                'government' if t.get('office') == 'government' else
                a if a in ('school', 'university', 'college', 'hospital') else
                'shop' if 'shop' in t else 'service' if a in AMENITY_SERVICES else None)
        if kind:
            jobs.append((xy, {'osm_id': e['id'], 'kind': kind, 'jobs_proxy': JOB_WEIGHTS[kind]}))
    return network, amenities, jobs, parks, worship, bus


def split_at_junctions(network):
    """Cut each way at vertices shared with another way, so crossings become routable nodes.

    OSM ways cross at shared nodes without ending there; UNA keeps input lines as edges, so an
    unsplit network is mostly disconnected.
    """
    key = lambda c: (round(c[0], 2), round(c[1], 2))
    uses = {}
    for f in network:
        for c in {key(c) for c in f['geometry']['coordinates']}:
            uses[c] = uses.get(c, 0) + 1
    out = []
    for f in network:
        coords = f['geometry']['coordinates']
        piece = [coords[0]]
        for c in coords[1:]:
            piece.append(c)
            if uses[key(c)] > 1 and c is not coords[-1] and len(piece) > 1:
                out.append({**f, 'geometry': {'type': 'LineString', 'coordinates': piece}})
                piece = [c]
        if len(piece) > 1:
            out.append({**f, 'geometry': {'type': 'LineString', 'coordinates': piece}})
    return out


def homes(pack_path):
    from dynacity.beirut001_pack import Beirut001Pack
    pack = Beirut001Pack.load(pack_path)
    heights = pack.heights()
    rows = []
    for i, shape in enumerate(pack.buildings):
        h = float(heights[i])
        if not math.isfinite(h) or h < 2.5 or shape.area < 20:
            continue
        floors = max(1, round(h / 3.0))
        c = shape.representative_point()
        area = shape.area * floors
        rows.append(([c.x, c.y], {'building_id': str(pack.building_ids[i]), 'floors': floors,
                                  'floor_area_m2': round(area, 1),
                                  'residents_est': round(area / M2_PER_RESIDENT, 1)}))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pack', required=True, help='beirut001-pack.npz (EPSG:32636 building outlines, heights)')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
    osm = fetch(out / 'osm-pedestrian-source.json')
    network, amenities, jobs, parks, worship, bus = classify(osm['elements'])
    network = split_at_junctions(network)
    layers = {'network.geojson': feature_collection(network), 'amenities.geojson': points(amenities),
              'jobs.geojson': points(jobs), 'parks.geojson': points(parks), 'worship.geojson': points(worship),
              'bus_stops.geojson': points(bus), 'homes.geojson': points(homes(a.pack))}
    for name, collection in layers.items():
        (out / name).write_text(json.dumps(collection), encoding='utf-8')
    summary = {k: len(v['features']) for k, v in layers.items()}
    summary['source'] = 'OpenStreetMap contributors (ODbL), Overpass API; Beirut 001 model (private)'
    (out / 'inputs-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
