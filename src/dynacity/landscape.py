"""Mapped OSM ground cover, trees and illustrative spacing along mapped tree rows."""
from __future__ import annotations

import math
from collections import Counter
from .osm_geometry import osm_geometry
from .osm_context import context_features
from .greenery import vegetation_kind, greenery_inventory, POINT_KINDS
from .osm_context import metres


def landscape_payload(osm: dict) -> dict:
    features = []
    rows = []
    nodes = {n['id']: n for n in osm.get('elements', []) if n.get('type') == 'node'}
    ways = {w['id']: w for w in osm.get('elements', []) if w.get('type') == 'way'}
    for element in osm.get('elements', []):
        tags = element.get('tags', {})
        kind = vegetation_kind(tags)
        # A point-labelled playground/pitch gives a location, not the extent of
        # its surface or the location of equipment. Retain it as a place rather
        # than dropping it for lacking a polygon or inventing a ground area.
        if kind is None and element.get('type') == 'node' and tags.get('leisure') in {'playground', 'pitch'}:
            geometry = osm_geometry(element, nodes, ways)
            if geometry:
                kind = tags['leisure']
                features.append({'type': 'Feature', 'geometry': geometry, 'properties': {
                    'id': f"node/{element['id']}", 'kind': kind,
                    'context_group': 'places', 'name': tags.get('name', kind.replace('_', ' ').title()),
                    'tags': tags, 'representation': 'mapped location only; area and equipment unknown',
                    'as_of': element.get('source_as_of', osm.get('osm3s', {}).get('timestamp_osm_base')),
                    'retrieved_on': element.get('retrieved_on', osm.get('retrieved_on')),
                }})
            continue
        if tags.get('natural') == 'tree_row' and element.get('type') == 'way':
            points = element.get('geometry') or [nodes[n] for n in element.get('nodes', []) if n in nodes]
            if len(points) >= 2 and (element.get('geometry') or len(points) == len(element.get('nodes', []))):
                line = [[p.get('lon'), p.get('lat')] for p in points]
                if all(isinstance(v, (int, float)) and math.isfinite(v) for p in line for v in p):
                    rows.append((element, line))
            continue
        if tags.get('natural') == 'tree' and element.get('type') == 'node':
            kind = 'tree'
            coordinates = [element.get('lon'), element.get('lat')]
            if not all(isinstance(x, (float, int)) and math.isfinite(x) for x in coordinates):
                continue
            geometry = {'type': 'Point', 'coordinates': coordinates}
        elif kind is not None and element.get('type') == 'node':
            geometry = osm_geometry(element,nodes,ways)
            if not geometry: continue
        elif kind == 'hedge':
            geometry = osm_geometry(element,nodes,ways)
            if not geometry: continue
            if geometry['type'] == 'Polygon' and tags.get('area') != 'yes':
                geometry = {'type':'LineString','coordinates':geometry['coordinates'][0]}
        else:
            natural, landuse, leisure = (tags.get(k) for k in ('natural', 'landuse', 'leisure'))
            if kind is not None:
                pass
            elif natural in ('wood', 'scrub') or landuse == 'forest':
                kind = 'wood' if natural != 'scrub' else 'scrub'
            elif landuse in ('grass', 'meadow', 'recreation_ground'):
                kind = 'grass'
            elif leisure in ('park', 'garden'):
                kind = leisure
            elif natural in ('sand', 'beach'):
                kind = 'sand'
            elif leisure in ('pitch', 'playground'):
                kind = leisure
            elif tags.get('amenity') == 'parking':
                kind = 'parking'
            elif natural in ('water','wetland') or tags.get('water'):
                kind = 'water'
            elif natural == 'grassland':
                kind = 'grass'
            elif landuse in ('cemetery','allotments','farmland','orchard','brownfield','construction'):
                kind = landuse
            if not kind:
                continue
            geometry = osm_geometry(element,nodes,ways)
            if not geometry:
                continue
            if geometry['type'] not in ('Polygon','MultiPolygon'):
                # Retain an unclosed vegetation outline as mapped reference
                # geometry. Never close it or infer the enclosed vegetation.
                if vegetation_kind(tags) and geometry['type']=='LineString':
                    features.append({'type':'Feature','geometry':geometry,'properties':{
                        'id':f"{element['type']}/{element['id']}",'kind':'greenery_reference',
                        'name':tags.get('name','Unclosed mapped vegetation outline'),
                        'reference_kind':kind,'tags':tags,'representation':'reference outline only',
                        'as_of':element.get('source_as_of'),'retrieved_on':element.get('retrieved_on')}})
                continue
        height = metres(tags.get('height'),None,100)
        measured_height = height is not None
        default_height = {'tree':6,'shrub':1.2,'tree_stump':.4,'hedge':1.5,'planter':.6}.get(kind)
        features.append({'type': 'Feature', 'geometry': geometry, 'properties': {
            'id': f"{element['type']}/{element['id']}", 'kind': kind,
            'name': tags.get('name', kind.replace('_', ' ').title()),
            'surface': tags.get('surface'),
            'height_m': height if measured_height else default_height,
            'height_source': 'OSM tag; unverified' if measured_height else 'illustrative' if default_height else None,
            'width_m':metres(tags.get('width'),None,100),
            'crown_diameter_m':metres(tags.get('diameter_crown') or tags.get('crown_diameter'),None,60),
            'position_source': 'OSM individual tree node' if kind == 'tree' else None,
            'species': tags.get('species'), 'leaf_type': tags.get('leaf_type'),
            'tags':tags,
            'as_of':element.get('source_as_of', osm.get('osm3s', {}).get('timestamp_osm_base')),
            'retrieved_on':element.get('retrieved_on', osm.get('retrieved_on')),
        }})
    # OSM rows map a line, not a count of individual trunks. Keep its source
    # identity and label generated display spacing explicitly. Never fill parks.
    def distance(a, b):
        return math.hypot((a[0]-b[0])*111320*math.cos(math.radians(b[1])), (a[1]-b[1])*111320)
    point_trees = [f for f in features if f['geometry']['type'] == 'Point' and f['properties']['kind']=='tree']
    occupied = {}
    def cell(p):
        return math.floor(p[0]*111320*math.cos(math.radians(34))/3), math.floor(p[1]*111320/3)
    def remember(p):
        occupied.setdefault(cell(p), []).append(p)
    for f in point_trees:
        remember(f['geometry']['coordinates'])
    for element, line in rows:
        tags = element.get('tags', {})
        try:
            height = float(tags.get('height', ''))
        except (ValueError, TypeError):
            height = None
        tagged = height is not None and math.isfinite(height) and 1 <= height <= 60
        along = [0.0]
        for a, b in zip(line, line[1:]):
            along.append(along[-1]+distance(a,b))
        if along[-1] < .1:
            continue
        count = max(1, math.ceil(along[-1]/8))
        segment = 1
        for i in range(count+1):
            d = along[-1]*i/count
            while segment < len(line)-1 and along[segment] < d:
                segment += 1
            f = (d-along[segment-1])/(along[segment]-along[segment-1] or 1)
            a,b = line[segment-1],line[segment]
            p = [a[0]+f*(b[0]-a[0]), a[1]+f*(b[1]-a[1])]
            x,y = cell(p)
            if any(distance(p,q)<3 for dx in (-1,0,1) for dy in (-1,0,1) for q in occupied.get((x+dx,y+dy), [])):
                continue
            remember(p)
            features.append({'type':'Feature','geometry':{'type':'Point','coordinates':p},'properties':{
                'id':f"way/{element['id']}", 'instance_id':f"way/{element['id']}#{i}", 'kind':'tree',
                'name':tags.get('name','Mapped tree row'), 'height_m':height if tagged else 6,
                'height_source':'OSM tag; unverified' if tagged else 'illustrative',
                'position_source':'Illustrative spacing along OSM tree row; individual tree count and positions unverified',
                'species':tags.get('species'),'leaf_type':tags.get('leaf_type'),
                'tags':tags,
                'as_of':element.get('source_as_of', osm.get('osm3s', {}).get('timestamp_osm_base')),
                'retrieved_on':element.get('retrieved_on', osm.get('retrieved_on')),
            }})
    context,rejected=context_features(osm,{f['properties']['id'] for f in features if f['properties']['kind']!='green_roof'})
    features.extend(context)
    counts=Counter(f['properties'].get('context_group','trees' if f['properties']['kind']=='tree' else 'surfaces') for f in features)
    return {'type': 'FeatureCollection', 'features': features,
            'source': 'OpenStreetMap contributors', 'as_of': osm.get('osm3s', {}).get('timestamp_osm_base'),
            'retrieved_on': osm.get('retrieved_on'),
            'tree_nodes':len(point_trees), 'tree_rows':len(rows),
            'bounds':osm.get('query_bbox'), 'counts':dict(counts), 'geometry_rejected':rejected,
            'building_coverage_complete':'buildings' in osm.get('sources', {}),
            'greenery_inventory':greenery_inventory(osm),
            'source_rows':[{'id':f"{e['type']}/{e['id']}",'tags':e.get('tags',{}),
                            'geometry':{'type':'LineString','coordinates':line}} for e,line in rows],
            'limits': 'Mapped OSM context within extraction bounds. Tree-row spacing, furniture forms and untagged dimensions are illustrative; parks do not imply individual tree locations. Incomplete geometry is rejected. Missing mapping means unknown.'}


def apply_context_buildings(payload: dict, companion: dict) -> dict:
    """Overture companion from engine_context_buildings: give OSM outlines without a
    mapped height the same Beirut 001 area-bin estimate Unreal uses, and add the
    machine-learned footprints OSM lacks. OSM tags and mapped heights are kept."""
    if companion.get('schema') != 'dynacity-context-buildings-web/1':
        raise ValueError('not an engine_context_buildings --web-output file')
    heights = companion['osm_heights']
    for f in payload['features']:
        props = f['properties']
        if props.get('context_group') != 'buildings' or props.get('height_source') != 'illustrative fallback':
            continue
        if props['id'] in heights:
            props['height_m'], props['height_source'] = heights[props['id']]
    payload['features'].extend(companion['features'])
    payload['counts']['buildings'] = payload['counts'].get('buildings', 0) + len(companion['features'])
    payload['context_buildings'] = {'added_footprints': len(companion['features']),
                                    'height_bins_m': companion['height_bins_m'],
                                    'attribution': companion['attribution']}
    return payload
