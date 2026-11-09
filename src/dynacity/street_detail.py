"""Conservative, deterministic street-detail proposals; never edits source geometry.

Coordinates are local east/north/up metres. Missing measurements remain defaults.
Only finished modular mesh instances are emitted; rejected space remains a gap.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

import shapely
from shapely.geometry import LineString, Point, Polygon, mapping, shape

VERSION = 'dynacity-street-detail/1'
TYPES = {
    'commercial_avenue': {'sidewalk_m': 2.4, 'curb_m': .18, 'height_m': .15},
    'residential_street': {'sidewalk_m': 1.4, 'curb_m': .18, 'height_m': .15},
    'major_road': {'sidewalk_m': 2., 'curb_m': .18, 'height_m': .15},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def positive(value):
    try:
        result = float(str(value).removesuffix(' m'))
        return result if math.isfinite(result) and 0 < result < 100 else None
    except (ValueError, TypeError):
        return None


def enrich(road, candidates):
    """OSM wins; match Overture by source ID, otherwise require a unique close line.

    Overture geometry is never added. Ambiguous matches cannot fill measurements.
    """
    tags = dict(road.get('tags', {})); line = LineString(road['path'])
    matches = []
    for candidate in candidates:
        if candidate.get('type') != 'segment':
            continue
        exact = str(road['source_way']) in candidate.get('osm_way_ids', [])
        other = shape(candidate['geometry'])
        close = (other.geom_type == 'LineString' and
                 line.intersection(other.buffer(2)).length >= .85 * line.length)
        if exact or close:
            matches.append((exact, candidate))
    exacts = [c for exact, c in matches if exact]
    chosen = exacts if exacts else [c for _, c in matches]
    provenance = {key: 'OSM tag' for key in tags}
    if len(chosen) == 1:
        for key, value in chosen[0].get('attributes', {}).items():
            if tags.get(key) in (None, '') and value is not None:
                tags[key] = value; provenance[key] = 'Overture ' + chosen[0]['id']
    return tags, provenance, [c['id'] for c in chosen]


def footprint(position, yaw, length, width):
    a = math.radians(yaw); dx, dy = math.cos(a), math.sin(a)
    x, y = position[:2]
    return Polygon([(x+dx*u-dy*v, y+dy*u+dx*v)
                    for u, v in ((-length/2, -width/2), (length/2, -width/2),
                                 (length/2, width/2), (-length/2, width/2))])


def plan(street, roads, buildings, sample, *, rules=None, overrides=None,
         overture=(), entrances=()):
    """Plan fixed-station tiles on both road sides, conservatively reducing width.

    sample(x,y) must return the existing terrain elevation, or None for a hole.
    Nearby roads, buildings and entrances participate even if outside the batch.
    """
    rules = rules or TYPES; overrides = overrides or {}
    profile = rules[street['type']]
    if any(not positive(profile[k]) for k in ('sidewalk_m', 'curb_m', 'height_m')):
        raise ValueError('Street dimensions must be finite and positive')
    selected = {str(i) for i in street['road_ids']}
    source = sorted([r for r in roads if str(r['id']) in selected], key=lambda r: str(r['id']))
    if {str(r['id']) for r in source} != selected:
        raise ValueError('Selected road is missing from existing city geometry')
    road_space = shapely.union_all([LineString(r['path']).buffer(r['width_m']/2, cap_style=2)
                                    for r in roads if r.get('level', 0) == 0])
    obstacle = shapely.union_all([shape(b['geometry']) for b in buildings])
    entrance_space = shapely.union_all([Point(p[:2]).buffer(.9) for p in entrances])
    claimed = Polygon(); features = []; inventory = []; exceptions = []; layouts = []
    suppressed = []; used_overrides = set()
    for road in source:
        rid = str(road['id']); line = LineString(road['path'])
        if road.get('level', 0) != 0:
            exceptions.append({'road_id': rid, 'reason': 'elevated_or_underground_road'}); continue
        tags, provenance, matches = enrich(road, overture)
        measured = positive(tags.get('width'))
        missing = [k for k in ('width', 'sidewalk:width', 'kerb:height', 'entrances', 'right_of_way_width')
                   if k not in tags]
        inventory.append({'road_id': rid, 'osm_way': road['source_way'], 'name': road.get('name'),
                          'type': street['type'], 'carriageway_m': road['width_m'],
                          'carriageway_source': 'existing simulation geometry; lane-derived',
                          'tagged_width_m': measured, 'missing_measurements': missing,
                          'confidence': 'low' if missing else 'mapped_not_surveyed',
                          'tags': tags, 'attribute_sources': provenance, 'overture_matches': matches})
        if measured and abs(measured-road['width_m']) > .5:
            exceptions.append({'road_id': rid, 'reason': 'tagged_width_differs_from_preserved_geometry'})
        for side, sign in (('left', 1), ('right', -1)):
            sidewalk = tags.get('sidewalk:'+side, tags.get('sidewalk'))
            if sidewalk in ('no', 'none', 'separate') or sidewalk in ('left', 'right') and sidewalk != side:
                exceptions.append({'road_id': rid, 'side': side, 'reason': 'OSM_sidewalk_excluded'}); continue
            key = rid+'/'+side
            correction = overrides.get('streets', {}).get(key, {})
            if 'width_m' in correction and positive(correction['width_m']) is None:
                raise ValueError('Invalid manual width override: '+key)
            if correction: used_overrides.add('streets/'+key)
            width = positive(correction.get('width_m')) or positive(tags.get('sidewalk:'+side+':width')) or positive(tags.get('sidewalk:width')) or profile['sidewalk_m']
            width_source = 'manual override' if 'width_m' in correction else 'mapped tag' if positive(tags.get('sidewalk:'+side+':width')) or positive(tags.get('sidewalk:width')) else 'street-type default'
            band = line.buffer(road['width_m']/2+width, cap_style=2).difference(road_space).difference(obstacle.buffer(.1))
            layouts.append({'road_id': rid, 'side': side, 'width_m': width, 'width_source': width_source,
                            'geometry': mapping(band.intersection(line.buffer(road['width_m']/2+width, single_sided=True) if sign == 1 else line.buffer(-road['width_m']/2-width, single_sided=True))),
                            'parking': 'deferred; no traffic space reallocated',
                            'vegetation': 'deferred; requires measured clear width',
                            'crossings': 'reference only; no inferred markings'})
            accepted = []
            for station in range(math.ceil(line.length)):
                distance = station+.5
                if distance >= line.length: continue
                fid = f"{street['id']}/{rid}/{side}/{station:05d}"
                edit = overrides.get('features', {}).get(fid, {})
                if edit: used_overrides.add('features/'+fid)
                if edit.get('disabled'):
                    suppressed.append(fid); continue
                p = line.interpolate(distance); a = line.interpolate(max(0, distance-.45)); b = line.interpolate(min(line.length, distance+.45))
                yaw = math.degrees(math.atan2(b.y-a.y, b.x-a.x)); angle = math.radians(yaw)
                chosen = None
                # Keep at least 0.9 m walk space. Narrower candidates require review.
                for attempt in (width, min(width, 1.2)):
                    walk_width = attempt-profile['curb_m']-.04
                    if walk_width < .9: continue
                    offset = road['width_m']/2+profile['curb_m']+.02+walk_width/2
                    xy = [p.x-sign*math.sin(angle)*offset, p.y+sign*math.cos(angle)*offset]
                    poly = footprint(xy, yaw, .98, walk_width)
                    if poly.intersection(road_space).area > 1e-5 or poly.intersection(obstacle.buffer(.1)).area > 1e-5 or poly.intersection(claimed).area > 1e-5:
                        continue
                    samples = [sample(x,y) for x,y in list(poly.exterior.coords)[:4]]
                    z = sample(*xy)
                    if z is None or any(v is None or not math.isfinite(v) for v in samples): continue
                    # Fit a plane through the corners; reject excessive warping.
                    import numpy as np
                    points = np.asarray(list(poly.exterior.coords)[:4]); matrix = np.column_stack([points-np.asarray(xy), np.ones(4)])
                    fit = np.linalg.lstsq(matrix, samples, rcond=None)[0]
                    error = float(max(abs(matrix@fit-np.asarray(samples))))
                    if error > .04: continue
                    chosen = (xy, float(z), poly, attempt, fit.tolist(), error); break
                if chosen is None:
                    exceptions.append({'feature_id': fid, 'reason': 'insufficient_clear_space_or_ground'}); continue
                xy, z, poly, available, plane, error = chosen
                if available < width-.01:
                    exceptions.append({'feature_id': fid, 'reason': 'constrained_width', 'available_m': available})
                base = {'id': fid+'/sidewalk', 'kind': 'sidewalk', 'road_id': rid, 'side': side,
                        'station_m': distance, 'position_local_m': [*xy, z+.12],
                        'yaw_degrees': yaw, 'length_m': .98, 'width_m': available-profile['curb_m']-.04,
                        'height_m': profile['height_m'], 'ground_plane': plane,
                        'ground_fit_error_m': error, 'geometry': mapping(poly)}
                features.append(base); accepted.append(poly); claimed = shapely.union_all([claimed, poly])
                # A curb is omitted across mapped entrance clearances, retaining walking surface.
                curb_xy = [p.x-sign*math.sin(angle)*(road['width_m']/2+profile['curb_m']/2+.015), p.y+sign*math.cos(angle)*(road['width_m']/2+profile['curb_m']/2+.015)]
                curb_poly = footprint(curb_xy, yaw, .98, profile['curb_m'])
                cz = sample(*curb_xy)
                if cz is not None and curb_poly.intersection(road_space).area < 1e-5 and curb_poly.intersection(obstacle.buffer(.1)).area < 1e-5 and not curb_poly.intersects(entrance_space):
                    features.append({**base, 'id': fid+'/curb', 'kind': 'curb', 'position_local_m': [*curb_xy, cz+.12],
                                     'width_m': profile['curb_m'], 'geometry': mapping(curb_poly)})
            # Small seam tolerance joins adjacent modules; larger holes are actual review items.
            connected = shapely.union_all([p.buffer(.06) for p in accepted])
            components = len(list(shapely.get_parts(connected))) if not connected.is_empty else 0
            if components != 1:
                exceptions.append({'road_id': rid, 'side': side, 'reason': 'disconnected_sidewalk', 'components': components})
    # Connect source splits with the same finished modules. A connector cannot
    # cross a carriageway or a building; actual crossings remain review items.
    connected_pairs=set()
    for road in source:
        rid=str(road['id'])
        for end in (road['path'][0],road['path'][-1]):
            neighbors=[r for r in source if str(r['id'])!=rid and
                       min(Point(end[:2]).distance(Point(p[:2])) for p in (r['path'][0],r['path'][-1]))<.6]
            for neighbor in neighbors:
                pair=tuple(sorted((rid,str(neighbor['id']))))
                if pair in connected_pairs: continue
                connected_pairs.add(pair)
                for side in ('left','right'):
                    for kind in ('sidewalk','curb'):
                        candidates=[[f for f in features if f['road_id']==r and f['side']==side and f['kind']==kind]
                                    for r in pair]
                        if not all(candidates): continue
                        a,b=[min(rows,key=lambda f:shape(f['geometry']).distance(Point(end[:2]))) for rows in candidates]
                        pa,pb=shape(a['geometry']),shape(b['geometry'])
                        gap=shapely.shortest_line(pa,pb)
                        if gap.length<=.12 or gap.length>4: continue
                        angle=math.degrees(math.atan2(gap.coords[-1][1]-gap.coords[0][1],gap.coords[-1][0]-gap.coords[0][0]))
                        count=math.ceil(gap.length/.5);length=gap.length/count
                        width=min(a['width_m'],b['width_m'])
                        additions=[]
                        for n in range(count):
                            p=gap.interpolate((n+.5)*length);xy=[p.x,p.y]
                            poly=footprint(xy,angle,max(.01,length-.015),width)
                            forbidden=shapely.union_all([road_space,obstacle.buffer(.1),claimed])
                            # Avoid every existing feature, including curb/walk interfaces.
                            if poly.intersection(forbidden).area>1e-5 or any(poly.intersection(shape(f['geometry'])).area>1e-5 for f in features):
                                additions=[];break
                            z=sample(*xy);corners=list(poly.exterior.coords)[:4]
                            zz=[sample(x,y) for x,y in corners]
                            if z is None or any(v is None for v in zz): additions=[];break
                            import numpy as np
                            matrix=np.column_stack([np.asarray(corners)-np.asarray(xy),np.ones(4)])
                            fit=np.linalg.lstsq(matrix,zz,rcond=None)[0];error=float(max(abs(matrix@fit-np.asarray(zz))))
                            if error>.04: additions=[];break
                            additions.append({**a,'id':f"{street['id']}/junction/{pair[0]}-{pair[1]}/{side}/{kind}/{n}",
                                              'position_local_m':[*xy,z+.12],'yaw_degrees':angle,
                                              'length_m':max(.01,length-.015),'width_m':width,'ground_plane':fit.tolist(),
                                              'ground_fit_error_m':error,'geometry':mapping(poly),'junction_connector':True})
                        features.extend(additions)
                        if kind=='sidewalk' and additions:
                            claimed=shapely.union_all([claimed,*[shape(f['geometry']) for f in additions]])
    # Branch junctions need a crossing/corner review if continuity is interrupted.
    endpoints = []
    for road in source:
        for end in (road['path'][0], road['path'][-1]):
            neighbors = [str(r['id']) for r in roads if str(r['id']) != str(road['id']) and LineString(r['path']).distance(Point(end[:2])) < .6]
            if neighbors:
                same_street=[n for n in neighbors if n in selected]
                connected=True
                for side in ('left','right'):
                    walks=[shape(f['geometry']) for f in features if f['kind']=='sidewalk' and f['side']==side
                           and (f['road_id']==str(road['id']) or f['road_id'] in same_street)
                           and shape(f['geometry']).distance(Point(end[:2]))<10]
                    union=shapely.union_all([p.buffer(.08) for p in walks])
                    if union.is_empty or len(list(shapely.get_parts(union)))!=1: connected=False
                status='connected_source_split' if same_street and len(neighbors)==len(same_street) and connected else 'requires_corner_and_crossing_review'
                endpoints.append({'road_id': str(road['id']), 'position': end, 'neighbors': neighbors,'status':status})
    for junction in endpoints:
        if junction['status']!='connected_source_split':
            exceptions.append({'road_id': junction['road_id'], 'reason': 'intersection_continuity_review', 'neighbors': junction['neighbors']})
    checks = validate(features, road_space, obstacle, sample)
    return {'schema': VERSION, 'street': street, 'inventory': inventory, 'layouts': layouts,
            'features': features, 'suppressed': suppressed, 'used_overrides': sorted(used_overrides),
            'junctions': endpoints, 'exceptions': exceptions, 'checks': checks,
            'counts': dict(Counter(f['kind'] for f in features)),
            'ready_for_expansion': not exceptions and not checks['failures']}


def validate(features, road_space, buildings, sample):
    failures = []; ids = set(); groups = {'sidewalk': [], 'curb': []}
    for feature in features:
        fid = feature['id']; poly = shape(feature['geometry'])
        if fid in ids: failures.append({'id': fid, 'reason': 'duplicate_id'})
        ids.add(fid)
        if poly.intersection(road_space).area > 1e-5: failures.append({'id': fid, 'reason': 'blocked_traffic_space'})
        if poly.intersection(buildings).area > 1e-5: failures.append({'id': fid, 'reason': 'building_overlap'})
        z = sample(*feature['position_local_m'][:2])
        if z is None or abs(feature['position_local_m'][2]-.12-z) > .04:
            failures.append({'id': fid, 'reason': 'floating_or_missing_ground'})
        groups[feature['kind']].append((fid, poly))
    for rows in groups.values():
        tree = shapely.STRtree([p for _,p in rows])
        for i, (fid, poly) in enumerate(rows):
            for j in tree.query(poly, predicate='intersects'):
                if j > i and poly.intersection(rows[j][1]).area > .01:
                    failures.append({'id': fid, 'other': rows[j][0], 'reason': 'same_kind_overlap'})
    return {'checked_features': len(features), 'failures': failures,
            'scope': 'projected footprints and existing terrain; native collision and photographs pending'}
