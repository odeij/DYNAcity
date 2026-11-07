"""Fill the city map around Beirut 001 with mapped building footprints.

Beirut 001 covers municipal Beirut only; the map's terrain reaches the southern
suburbs and east of the river. Overture Maps buildings (OSM outlines plus
Microsoft machine-learned footprints, ODbL) fill that area and any gaps inside
the city. Beirut 001 stays authoritative: a footprint overlapping one of its
buildings is dropped, never merged or moved.

Few footprints carry a height. Missing heights use the median Beirut 001
height for a similar footprint area, an estimate labelled per building.
Packages use the city-batch format so the existing Unreal import queue takes them.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from pyproj import Transformer
import shapely
from shapely.geometry import shape
from shapely.ops import transform

from .beirut001_pack import Beirut001Pack
from .engine_asset import export_glb
from .engine_city_batches import batch_glb
from .engine_districts import city_boundary
from .engine_scene import polygon_mesh
from .engine_street_context import TerrainSurface
from .terrain import GroundModel
from .heights import RECORDED, mapped_height, resolve_height
from .viewer import utm_to_lonlat

VERSION='context-buildings/1'
from .heights import AREA_BINS
AREA_BINS=list(AREA_BINS)
MIN_AREA_M2=8.
OVERLAP_SHARE=.1
TILE_M=1500.


def height_bins(pack):
    """Median Beirut 001 height per footprint-area bin, the fallback for unmeasured buildings."""
    areas=np.array([b.area for b in pack.buildings]);heights=pack.top_z-pack.ground_z
    ok=np.isfinite(heights)&(heights>1)
    medians=[]
    for low,high in zip(AREA_BINS,AREA_BINS[1:]):
        chosen=heights[ok&(areas>=low)&(areas<high)]
        medians.append(float(np.median(chosen)) if len(chosen) else float(np.median(heights[ok])))
    return medians


def context_height(props,tags,area,medians):
    """`heights.Height` by the shared order: a mapped height tag, then a floor count x the
    fitted storey height, then the Beirut 001 area-bin median."""
    recorded=next((h for h in (mapped_height(props.get('height')),mapped_height(tags.get('height'))) if h is not None),None)
    floors=next((v for v in (props.get('num_floors'),tags.get('building:levels')) if v is not None),None)
    return resolve_height(recorded=recorded,recorded_label='mapped height',floors=floors,area_m2=area,
                          medians=medians,median_label='similar footprint area in Beirut 001')


def building_height(props,tags,area,medians):
    """(metres, provenance label)."""
    result=context_height(props,tags,area,medians)
    return result.height_m,result.source


def read_footprints(overture=None,osm=None):
    """Projected candidate footprints with source identity. Overture already includes OSM."""
    project=Transformer.from_crs(4326,32636,always_xy=True).transform;rows=[]
    if overture:
        for feature in json.loads(Path(overture).read_text(encoding='utf-8'))['features']:
            props=feature['properties']
            if props.get('is_underground') or not feature.get('geometry'):continue
            sources=props.get('sources') or [{}];identifier=feature.get('id') or props.get('id')
            rows.append({'geometry':transform(project,shape(feature['geometry'])),'lonlat':feature['geometry'],'props':props,'tags':{},
                'source':sources[0].get('dataset','Overture'),'record_id':sources[0].get('record_id') or identifier,
                'overture_id':identifier})
    elif osm:
        for element in json.loads(Path(osm).read_text(encoding='utf-8'))['elements']:
            tags=element.get('tags',{});points=element.get('geometry') or []
            if 'building' not in tags or len(points)<4 or tags.get('location')=='underground':continue
            try:geometry=shapely.Polygon([project(p['lon'],p['lat']) for p in points])
            except (ValueError,shapely.errors.GEOSException):continue
            rows.append({'geometry':geometry,'props':{},'tags':tags,'source':'OpenStreetMap',
                'record_id':f"{element['type']}/{element['id']}",'overture_id':None})
    else:raise ValueError('Supply Overture buildings or an OSM extract')
    return rows


def select_footprints(rows,pack):
    """Drop invalid, tiny and Beirut 001-overlapping footprints; keep the rest unchanged."""
    existing=[shapely.make_valid(b) for b in pack.buildings];tree=shapely.STRtree(existing)
    kept=[];skipped=Counter()
    for row in rows:
        geometry=shapely.make_valid(row['geometry'])
        polygons=[g for g in getattr(geometry,'geoms',[geometry]) if g.geom_type=='Polygon' and not g.is_empty]
        if not polygons:skipped['invalid footprint']+=1;continue
        geometry=shapely.MultiPolygon(polygons) if len(polygons)>1 else polygons[0]
        if geometry.area<MIN_AREA_M2:skipped['footprint under 8 m2']+=1;continue
        overlap=False
        for i in tree.query(geometry,predicate='intersects'):
            shared=geometry.intersection(existing[int(i)]).area
            if shared>=OVERLAP_SHARE*min(geometry.area,existing[int(i)].area):overlap=True;break
        if overlap:skipped['overlaps a Beirut 001 building']+=1;continue
        kept.append({**row,'geometry':geometry})
    return kept,skipped


def footprint_scene(rows,origin,surface,medians,name):
    origin=np.asarray(origin,float);meshes=[];skipped=Counter()
    for row in rows:
        geometry=row['geometry'];xy=shapely.get_coordinates(geometry)
        z=[surface.sample(x,y) for x,y in xy];z=[v for v in z if v is not None]
        if not z:skipped['no terrain under footprint']+=1;continue
        resolved=context_height(row['props'],row['tags'],geometry.area,medians)
        height,height_source,height_kind=resolved.height_m,resolved.source,resolved.kind
        # Sink the base below the lowest ground point so slopes show no gap; roof sits above the median.
        base=min(z)-1.;roof=float(np.median(z))+height
        try:vertices,faces=polygon_mesh(geometry,lambda x,y,z=roof:z,bottom=lambda x,y,z=base:z)
        except (ValueError,shapely.errors.GEOSException):skipped['invalid footprint']+=1;continue
        if not faces:skipped['degenerate footprint']+=1;continue
        identifier=row['overture_id'] or row['record_id']
        meshes.append({'id':'context:'+str(identifier),'kind':'building',
            'vertices':(np.asarray(vertices)-origin).round(4).tolist(),'triangles':faces,
            'metadata':{'source':row['source'],'source_record':str(row['record_id']),'overture_id':row['overture_id'],
                'production_group':name,'height_m':round(height,2),'height_source':height_source,'height_kind':height_kind,
                'ground_source':'extended visual ground (Beirut 001 + Copernicus GLO-30)',
                'alignment':'source footprint as published; not checked against Beirut 001'}})
    lon,lat=utm_to_lonlat(origin[0],origin[1],36,True)
    return {'schema':'dynacity-engine-scene/1','coordinates':{'crs':'EPSG:32636','origin_utm_m':origin.tolist(),
                'origin_lonlat':[lon,lat],'axes':'X east, Y north, Z up','units':'metres',
                'unreal_axes':'X east, Y south, Z up; centimetres','vertical_datum':'source model datum undeclared'},
            'meshes':meshes,'trees':[],'roads':[],'bounds_lonlat':None,
            'limitations':['Mapped context footprints; heights are mostly estimates.',
                           'Overture/OSM data is ODbL; attribution required.'],
            'skipped':dict(skipped),'counts':dict(Counter(m['kind'] for m in meshes))}


def tile_name(geometry):
    point=geometry.representative_point()
    return f'Context E{int(point.x//TILE_M)} N{int(point.y//TILE_M)}'


def build_context_buildings(rows,pack,ground,origin,output,source_hash='',boundary=None):
    """`boundary` (EPSG:32636) keeps only footprints whose centre lies inside it."""
    output=Path(output);scenes=output/'scenes';scenes.mkdir(parents=True,exist_ok=True)
    outside=0
    if boundary is not None:
        inside=[r for r in rows if boundary.covers(r['geometry'].representative_point())]
        outside=len(rows)-len(inside);rows=inside
    kept,skipped=select_footprints(rows,pack);medians=height_bins(pack);surface=TerrainSurface(ground)
    if outside:skipped['outside boundary']=outside
    tiles={}
    for row in kept:tiles.setdefault(tile_name(row['geometry']),[]).append(row)
    jobs=[];heights=Counter();exported=0
    for name,members in sorted(tiles.items()):
        scene=footprint_scene(members,origin,surface,medians,name)
        skipped.update(scene['skipped']);exported+=len(scene['meshes'])
        heights.update(m['metadata']['height_source'] for m in scene['meshes'])
        if not scene['meshes']:continue
        slug=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')
        signature=hashlib.sha256(json.dumps([VERSION,source_hash,slug,list(origin),boundary is not None and boundary.wkb_hex]).encode()).hexdigest()[:16]
        source=scenes/f'{slug}-{signature}.glb'
        if not source.exists():export_glb(scene,source,include_calibration=False)
        jobs.extend(batch_glb(source,lambda identifier,extras,n=name:(n,'context tile by footprint centre'),output,kind='buildings'))
    result={'schema':'dynacity-city-batches/1','layer':'context-buildings','builder':VERSION,
        'origin_utm_m':list(origin),'jobs':jobs,'mesh_count':sum(j['mesh_count'] for j in jobs),
        'source_objects':sum(j['object_count'] for j in jobs),'bytes':sum(j['bytes'] for j in jobs),
        'boundary':'municipal Beirut production areas' if boundary is not None else None,
        'counts':{'candidates':len(rows)+outside,'kept':len(kept),'exported':exported,
                  'by_source':dict(Counter(r['source'] for r in kept)),'height_source':dict(heights)},
        'skipped':dict(skipped),'height_bins_m':dict(zip([f'{a}-{b}' for a,b in zip(AREA_BINS,AREA_BINS[1:])],[round(m,1) for m in medians])),
        'attribution':'Buildings: Overture Maps Foundation, OpenStreetMap contributors, Microsoft (ODbL-1.0).',
        'limitations':['Beirut 001 buildings take precedence; overlapping footprints are dropped, not merged.',
            'Missing heights use Beirut 001 area-bin medians; they are estimates, not measurements.',
            'Footprints are not aligned to Beirut 001; offsets of a few metres are possible.',
            'Flat roofs and illustrative facades; no BBED status or forecast data is attached.']}
    (output/'context-buildings.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def osm_identifier(record_id):
    """Overture's OSM record id (`w123@4`) as the viewer's `way/123`; None for other sources."""
    match=re.fullmatch(r'([nwr])(\d+)(@\d+)?',str(record_id or ''))
    return f"{dict(n='node',w='way',r='relation')[match[1]]}/{match[2]}" if match else None


def web_buildings(rows,pack):
    """Browser companion: heights for OSM outlines the viewer already has, plus the
    non-OSM footprints it lacks. Nothing is dropped for Beirut 001 overlap here; the
    viewer hides covered buildings itself, per building mode."""
    medians=height_bins(pack);heights={};features=[];counts=Counter()
    for row in rows:
        area=row['geometry'].area
        if area<MIN_AREA_M2:continue
        resolved=context_height(row['props'],row['tags'],area,medians)
        height,height_source,height_kind=resolved.height_m,resolved.source,resolved.kind
        osm=osm_identifier(row['record_id']) if row['source']=='OpenStreetMap' else None
        counts[row['source']]+=1
        if osm:heights[osm]=[round(height,1),height_source,height_kind];continue
        if row.get('lonlat') is None or row['lonlat'].get('type') not in ('Polygon','MultiPolygon'):continue
        features.append({'type':'Feature','geometry':row['lonlat'],'properties':{
            'id':'overture/'+str(row['overture_id']),'kind':'osm_building','context_group':'buildings',
            'name':'Building','tags':{},'source':row['source'],'height_m':round(height,1),'height_source':height_source,
            'height_kind':height_kind,'estimated':height_kind!=RECORDED,
            'representation':'machine-learned footprint from satellite imagery'}})
    return {'schema':'dynacity-context-buildings-web/1','osm_heights':heights,'features':features,
        'counts':dict(counts),'height_bins_m':dict(zip([f'{a}-{b}' for a,b in zip(AREA_BINS,AREA_BINS[1:])],[round(m,1) for m in medians])),
        'attribution':'Buildings: Overture Maps Foundation, OpenStreetMap contributors, Microsoft (ODbL-1.0).'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overture',help='Overture building GeoJSON (overturemaps download -t building)')
    parser.add_argument('--osm',help='Overpass JSON with geometry; used only without --overture')
    parser.add_argument('--pack',required=True);parser.add_argument('--ground',required=True,
        help='The extended visual ground the Unreal terrain extension was built from')
    parser.add_argument('--city',required=True,help='city-batches.json, for the shared origin')
    parser.add_argument('--output',required=True)
    parser.add_argument('--web-output',help='also write the browser companion (serve --context-buildings)')
    parser.add_argument('--boundary',help='beirut-districts.json; keep only footprints inside the 12 municipal areas')
    args=parser.parse_args()
    if not (args.overture or args.osm):parser.error('--overture or --osm is required')
    source=Path(args.overture or args.osm)
    origin=json.loads(Path(args.city).read_text())['origin_utm_m']
    rows=read_footprints(args.overture,args.osm);pack=Beirut001Pack.load(args.pack)
    boundary=city_boundary(json.loads(Path(args.boundary).read_text(encoding='utf-8'))) if args.boundary else None
    result=build_context_buildings(rows,pack,GroundModel.load(args.ground),origin,args.output,
                                   hashlib.sha256(source.read_bytes()).hexdigest(),boundary)
    if args.web_output:
        Path(args.web_output).write_text(json.dumps(web_buildings(rows,pack),separators=(',',':')),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('counts','skipped','height_bins_m','mesh_count','bytes')},indent=2))


if __name__=='__main__':main()
