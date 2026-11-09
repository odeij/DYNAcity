"""Named production groups from source sector polygons, never a fabricated grid.

One owner per whole source building; overlaps and unassigned objects remain
explicit. These are production groupings, not verified legal boundaries.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request
import numpy as np
import shapely
from shapely.geometry import LineString,mapping,shape
from shapely.ops import transform,unary_union
from pyproj import Transformer
from .beirut001_pack import Beirut001Pack
from .traffic import TrafficModel

SOURCE='https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/BASEMAP/FeatureServer/4'
# An explicit production alias, retaining both source labels and their polygons.
# This is not a claim about a legal/cadastral boundary or official spelling.
ALIASES={'Achrafiye':'Achrafieh'}


def fetch_sectors():
    features=[];offset=0
    while True:
        query=urllib.parse.urlencode({'where':'1=1','outFields':'OBJECTID_1,ZoneName,CadastralN',
            'outSR':4326,'f':'geojson','resultRecordCount':2000,'resultOffset':offset,'orderByFields':'OBJECTID_1'})
        with urllib.request.urlopen(SOURCE+'/query?'+query,timeout=60) as response:data=json.load(response)
        if 'error' in data:raise ValueError(data['error'])
        batch=data.get('features',[]);features.extend(batch)
        if not data.get('exceededTransferLimit'):break
        if not batch:raise ValueError('Sector pagination returned no progress')
        offset+=len(batch)
    return {'type':'FeatureCollection','features':features,'source':SOURCE,
            'retrieved_at':datetime.now(timezone.utc).isoformat()}


def groups_from_sectors(collection,aliases=None):
    aliases=ALIASES if aliases is None else aliases
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    groups=defaultdict(list);unnamed=[]
    for feature in collection['features']:
        props=feature['properties'];label=(props.get('CadastralN') or '').strip()
        if not label or not feature.get('geometry'):
            unnamed.append(props.get('OBJECTID_1'));continue
        geometry=shapely.make_valid(transform(project,shape(feature['geometry'])))
        if geometry.is_empty or geometry.area<=0:raise ValueError('Invalid sector polygon')
        groups[aliases.get(label,label)].append((geometry,props,label))
    result=[]
    for name,items in sorted(groups.items()):
        geometry=shapely.normalize(unary_union([x[0] for x in items]))
        result.append({'name':name,'geometry':geometry,'source_labels':sorted({x[2] for x in items}),
                       'sector_ids':sorted(x[1]['OBJECTID_1'] for x in items),
                       'sector_names':sorted({x[1].get('ZoneName') or '' for x in items})})
    if not result:raise ValueError('No named source sectors')
    return result,unnamed


def assign_buildings(pack,groups):
    tree=shapely.STRtree([g['geometry'] for g in groups]);objects=[];group_members=defaultdict(list)
    roof_levels=getattr(pack,'roof_levels',None);bbed_ids=getattr(pack,'bbed_objectid',None)
    for i,(identifier,polygon) in enumerate(zip(pack.building_ids,pack.buildings)):
        geometry=shapely.make_valid(polygon)
        overlap=[(float(geometry.intersection(groups[int(j)]['geometry']).area),groups[int(j)]['name'])
                 for j in tree.query(geometry,predicate='intersects')]
        overlap=sorted((a,n) for a,n in overlap if a>0)
        owner=min(overlap,key=lambda v:(-v[0],v[1]))[1] if overlap else None
        def finite(value):return float(value) if np.isfinite(value) else None
        record={'id':str(identifier),'owner':owner,'intersects':[n for _,n in overlap],
                'source_index':i,'roof_levels':int(roof_levels[i]) if roof_levels is not None else None,
                'provisional_bbed_id':int(bbed_ids[i]) if bbed_ids is not None else None,
                'ownership':'largest footprint overlap; whole object retained' if owner else 'outside named source coverage',
                'ground_m':finite(pack.ground_z[i]),'roof_m':finite(pack.top_z[i]),
                'footprint_wkb':shapely.normalize(geometry).wkb_hex}
        fingerprint=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        record.pop('footprint_wkb');record['content_hash']=fingerprint
        objects.append(record)
        if owner:group_members[owner].append((record['id'],fingerprint))
    return objects,group_members


def build_manifest(pack,sectors,traffic=None):
    if pack.meta.get('crs')!='EPSG:32636':raise ValueError('Pack must use EPSG:32636')
    if traffic is not None and traffic.crs!='EPSG:32636':raise ValueError('Traffic must use EPSG:32636')
    groups,unnamed=groups_from_sectors(sectors)
    objects,members=assign_buildings(pack,groups)
    roads=[]
    if traffic is not None:
        tree=shapely.STRtree([g['geometry'] for g in groups])
        for i,coords in enumerate(traffic.geometry):
            line=LineString(coords)
            names=sorted(groups[int(j)]['name'] for j in tree.query(line,predicate='intersects'))
            roads.append({'road_id':i,'source_way':int(traffic.way_id[i]),'groups':names,
                          'crosses_groups':len(names)>1,
                          'content_hash':hashlib.sha256(line.wkb+json.dumps({'way':int(traffic.way_id[i]),
                            'lanes':int(traffic.lanes[i]),'oneway':int(traffic.oneway[i]),
                            'highway':traffic.highway[i],
                            'level':int(traffic.level[i]) if traffic.level is not None else None},sort_keys=True).encode()).hexdigest()})
    features=[]
    ground=pack.ground
    ground_hash=hashlib.sha256(ground.ground.tobytes()+json.dumps([ground.x0,ground.y0,ground.cell_m]).encode()).hexdigest() if ground else None
    to_lonlat=Transformer.from_crs(32636,4326,always_xy=True).transform
    for group in groups:
        name=group['name'];geometry=group['geometry']
        road_hashes=sorted((r['road_id'],r['content_hash']) for r in roads if name in r['groups'])
        digest=hashlib.sha256(geometry.wkb+json.dumps([sorted(members[name]),road_hashes,ground_hash]).encode()).hexdigest()
        properties={k:v for k,v in group.items() if k!='geometry'}
        building_hash=hashlib.sha256(json.dumps(sorted(members[name])).encode()).hexdigest()
        properties.update(building_count=len(members[name]),area_km2=geometry.area/1e6,
                          content_hash=digest,building_content_hash=building_hash)
        features.append({'type':'Feature','properties':properties,'geometry':mapping(transform(to_lonlat,geometry))})
    return {'schema':'dynacity-engine-districts/1','crs':'EPSG:32636','source':sectors.get('source',SOURCE),
            'source_retrieved_at':sectors.get('retrieved_at'),'status':'source-labelled production groups; boundary currency unverified',
            'aliases':ALIASES,'alias_note':'Draft production merge of Achrafiye/Achrafieh spellings; original labels retained.',
            'ground_hash':ground_hash,'hash_scope':'Boundary, owned building footprints/heights, crossing road geometry/lanes/direction/class/level, shared ground grid; capture layer has its own inventory.',
            'unnamed_sector_ids':unnamed,'groups':{'type':'FeatureCollection','features':features},
            'buildings':objects,'roads':roads,
            'counts':{'groups':len(groups),'buildings':len(objects),'unassigned_buildings':sum(x['owner'] is None for x in objects),
                      'roads':len(roads),'cross_group_roads':sum(x['crosses_groups'] for x in roads)},
            'continuity':'Road IDs and full paths remain authoritative in the shared traffic model; groups do not create separate simulations.'}


def city_boundary(manifest):
    """Union of the named production areas in EPSG:32636: the municipal Beirut extent."""
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    return shapely.make_valid(unary_union([shapely.make_valid(transform(project,shape(f['geometry'])))
                                           for f in manifest['groups']['features']]))


def change_summary(previous,current):
    old={f['properties']['name']:f['properties']['content_hash'] for f in previous['groups']['features']}
    new={f['properties']['name']:f['properties']['content_hash'] for f in current['groups']['features']}
    return {'rebuild':sorted(name for name,digest in new.items() if old.get(name)!=digest),
            'removed':sorted(set(old)-set(new)),'unchanged':sorted(name for name in new if old.get(name)==new[name])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',required=True);parser.add_argument('--sectors',required=True)
    parser.add_argument('--traffic');parser.add_argument('--output',required=True);parser.add_argument('--previous');parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args();source=Path(args.sectors)
    if args.fetch:
        source.parent.mkdir(parents=True,exist_ok=True);source.write_text(json.dumps(fetch_sectors()),encoding='utf-8')
    data=build_manifest(Beirut001Pack.load(args.pack),json.loads(source.read_text()),TrafficModel.load(args.traffic) if args.traffic else None)
    if args.previous:data['changes']=change_summary(json.loads(Path(args.previous).read_text()),data)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,allow_nan=False,separators=(',',':')),encoding='utf-8')
    path.with_suffix('.geojson').write_text(json.dumps(data['groups']),encoding='utf-8')
    print(json.dumps(data['counts']))


if __name__=='__main__':main()
