"""Typed display context from public OSM tags; no operational activity inferred."""
from __future__ import annotations
from collections import Counter
import math
import re
from .osm_geometry import osm_geometry

PATHS={'footway','path','pedestrian','steps','cycleway','bridleway','corridor'}
FURNITURE={'bench','waste_basket','drinking_water','fountain','shelter','bicycle_parking'}
EXTRA_FURNITURE={'recycling','waste_disposal','post_box','telephone','parking_meter',
                 'bicycle_rental','bicycle_repair_station','charging_station'}


def metres(value, default=None, maximum=1000):
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(m)?\s*',str(value or ''))
    number=float(match[1]) if match else None
    return number if number is not None and math.isfinite(number) and 0<number<=maximum else default


def context_features(osm, existing_ids=()):
    elements=osm.get('elements',[])
    nodes={e['id']:e for e in elements if e.get('type')=='node'}
    ways={e['id']:e for e in elements if e.get('type')=='way'}
    existing=set(existing_ids);features=[];rejected=Counter()
    for e in elements:
        tags=e.get('tags',{});kind=None;group=None
        identifier=f"{e.get('type')}/{e.get('id')}"
        if not tags or identifier in existing:
            continue
        highway=tags.get('highway');amenity=tags.get('amenity');barrier=tags.get('barrier')
        if tags.get('building:part') not in (None,'no'):
            kind='building_part';group='buildings'
        elif tags.get('building') not in (None,'no'):
            kind='osm_building';group='buildings'
        elif highway in PATHS:
            kind=highway;group='paths'
        elif highway=='street_lamp':
            kind='street_lamp';group='furniture'
        elif amenity in FURNITURE:
            kind=amenity;group='furniture'
        elif barrier:
            kind=barrier;group='boundaries'
        elif tags.get('man_made') in ('pier','breakwater','embankment','retaining_wall'):
            kind=tags['man_made'];group='boundaries'
        elif highway in ('bus_stop','crossing','traffic_signals') or tags.get('public_transport'):
            kind='bus_stop' if highway=='bus_stop' else highway or tags['public_transport'];group='places'
        elif e.get('type')=='node' and highway in ('traffic_sign','speed_camera','street_cabinet','give_way','stop','mini_roundabout','kerb'):
            kind=highway;group='infrastructure'
        elif tags.get('waterway'):
            kind='waterway';group='water'
        elif tags.get('railway'):
            kind='railway';group='transport'
        elif e.get('type')=='node' and tags.get('traffic_calming'):
            kind='traffic_calming';group='infrastructure'
        elif highway:
            kind='road_attributes';group='roads'
        elif tags.get('traffic_calming'):
            kind='traffic_calming';group='infrastructure'
        elif tags.get('traffic_sign'):
            kind='traffic_sign';group='infrastructure'
        elif e.get('type')=='node' and (tags.get('kerb') or tags.get('tactile_paving')) and not highway:
            kind='kerb' if tags.get('kerb') else 'tactile_paving';group='infrastructure'
        elif tags.get('emergency') not in (None,'yes','no'):
            kind='fire_hydrant' if tags['emergency']=='fire_hydrant' else 'emergency_'+tags['emergency'];group='infrastructure'
        elif amenity in EXTRA_FURNITURE:
            kind=amenity;group='infrastructure'
        elif tags.get('power'):
            kind='power_'+tags['power'];group='infrastructure'
        elif tags.get('man_made'):
            kind='man_made_'+tags['man_made'];group='infrastructure'
        elif tags.get('advertising'):
            kind='advertising_'+tags['advertising'];group='infrastructure'
        elif tags.get('utility'):
            kind='utility_'+tags['utility'];group='infrastructure'
        elif tags.get('playground'):
            kind='playground_'+tags['playground'];group='infrastructure'
        elif amenity or tags.get('shop') or tags.get('tourism') or tags.get('historic'):
            kind=amenity or tags.get('shop') or tags.get('tourism') or tags.get('historic');group='places'
        if not kind:
            continue
        geometry=osm_geometry(e,nodes,ways)
        if not geometry:
            rejected[group]+=1;continue
        # A ring-shaped highway is still a linear route unless explicitly an area.
        if group in ('paths','roads','transport','boundaries','water') and geometry['type']=='Polygon' and tags.get('area')!='yes':
            geometry={'type':'LineString','coordinates':geometry['coordinates'][0]}
        width=metres(tags.get('width'),None,100)
        height=metres(tags.get('height'),None,300)
        levels=metres(tags.get('building:levels'),None,80)
        if group=='buildings' and height is None and levels is not None:
            height=levels*3.2;height_source='OSM floor count × illustrative 3.2 m'
        else:
            height_source='OSM height tag; unverified' if height is not None else 'illustrative fallback'
        if group=='buildings' and geometry['type'] not in ('Polygon','MultiPolygon'):
            rejected[group]+=1;continue
        props={'id':identifier,'kind':kind,'context_group':group,
            'name':tags.get('name:en') or tags.get('name') or tags.get('brand') or kind.replace('_',' ').title(),
            'tags':tags,'surface':tags.get('surface'), 'width_m':width,
            'width_source':'OSM width tag; unverified' if width is not None else 'illustrative',
            'height_m':height if height is not None else 6 if group=='buildings' else None,
            'height_source':height_source,
            'as_of':e.get('source_as_of',osm.get('osm3s',{}).get('timestamp_osm_base')),
            'retrieved_on':e.get('retrieved_on',osm.get('retrieved_on'))}
        features.append({'type':'Feature','geometry':geometry,'properties':props})
    return features,dict(rejected)
