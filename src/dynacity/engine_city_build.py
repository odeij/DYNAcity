"""Incrementally prepare whole-building GLB packages for named production areas.

Common coordinates allow seamless placement. This prepares geometry assets;
it does not configure Unreal World Partition or a packaged driving application.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import numpy as np
import shapely
from .beirut001_pack import Beirut001Pack
from .engine_asset import export_glb
from .engine_scene import polygon_mesh
from .terrain import GroundModel, merge_ground
from .viewer import utm_to_lonlat
from . import engine_asset,engine_scene

VERSION='district-buildings/2'
BUILDER_HASH=hashlib.sha256(Path(__file__).read_bytes()+Path(engine_asset.__file__).read_bytes()+Path(engine_scene.__file__).read_bytes()).hexdigest()


def fallback_base(ground,footprint):
    """Median merged-ground height under a footprint's outline, or None when it is all unknown."""
    xy=shapely.get_coordinates(footprint)
    z=ground.sample(xy[:,0],xy[:,1]);z=z[np.isfinite(z)]
    return float(np.median(z)) if len(z) else None


def build_group_scene(pack,identifiers,origin,name,ground=None):
    """`ground` is the merged (pack + GLO-30) ground used only for buildings the model ground
    does not reach, so they sit on the same surface as the roads drawn there."""
    origin=np.asarray(origin,float);wanted=set(identifiers);meshes=[];skipped=[]
    for i,identifier in enumerate(pack.building_ids):
        if str(identifier) not in wanted:continue
        base=float(pack.ground_z[i]);roof=float(pack.top_z[i]);source='pack'
        if not math.isfinite(base) and ground is not None and math.isfinite(roof):
            estimate=fallback_base(ground,pack.buildings[i])
            if estimate is not None:
                if estimate>=roof-1.:
                    skipped.append({'id':str(identifier),'reason':'estimated ground not below roof'});continue
                base=estimate;source='glo30'
        if not math.isfinite(base) or not math.isfinite(roof):
            skipped.append({'id':str(identifier),'reason':'missing source ground/roof'});continue
        try:vertices,faces=polygon_mesh(pack.buildings[i],lambda x,y,z=roof:z,bottom=lambda x,y,z=base:z)
        except (ValueError,shapely.errors.GEOSException):
            skipped.append({'id':str(identifier),'reason':'invalid source footprint'});continue
        if not faces:
            skipped.append({'id':str(identifier),'reason':'degenerate source footprint'});continue
        meshes.append({'id':'beirut001:'+str(identifier),'kind':'building',
                       'vertices':(np.asarray(vertices)-origin).round(4).tolist(),'triangles':faces,
                       'metadata':{'source':'Beirut 001, January 2020','source_index':i,'production_group':name,
                                   'bbed_objectid':int(pack.bbed_objectid[i]),'height_m':roof-base,'ground_source':source,
                                   'roof_levels':int(pack.roof_levels[i]),'alignment':'inferred; not surveyed'}})
    lon,lat=utm_to_lonlat(origin[0],origin[1],36,True)
    return {'schema':'dynacity-engine-scene/1','coordinates':{'crs':'EPSG:32636','origin_utm_m':origin.tolist(),
                'origin_lonlat':[lon,lat],'axes':'X east, Y north, Z up','units':'metres',
                'unreal_axes':'X east, Y south, Z up; centimetres','vertical_datum':'source model datum undeclared'},
            'meshes':meshes,'trees':[],'roads':[],'bounds_lonlat':None,
            'limitations':['Whole source building geometry; source alignment and currency remain unverified.',
                           'Building package only. Shared roads/terrain, capture assets and runtime streaming are separate layers.'],
            'skipped':skipped,'counts':dict(Counter(m['kind'] for m in meshes))}


def build_packages(pack,manifest,origin,output,fallback=None,fallback_hash=None):
    root=Path(output);root.mkdir(parents=True,exist_ok=True);packages=[]
    if fallback is not None and pack.ground is not None:fallback=merge_ground(pack.ground,fallback,extrapolate_m=400.)
    for feature in manifest['groups']['features']:
        group=feature['properties'];name=group['name']
        parts=[VERSION,BUILDER_HASH,group.get('building_content_hash',group['content_hash']),list(origin)]
        if fallback is not None:parts.append(fallback_hash)
        signature=hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()
        slug=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')
        folder=root/slug;folder.mkdir(exist_ok=True)
        asset=folder/(signature+'.glb');metadata=folder/(signature+'.json')
        cache_hit=False
        if asset.is_file() and metadata.is_file():
            try:
                with asset.open('rb') as stream:header=struct.unpack('<4sII',stream.read(12))
                record=json.loads(metadata.read_text())
                cache_hit=header==(b'glTF',2,asset.stat().st_size) and record['signature']==signature
            except (ValueError,KeyError,struct.error):pass
        if cache_hit:
            pass
        else:
            ids=[r['id'] for r in manifest['buildings'] if r['owner']==name]
            scene=build_group_scene(pack,ids,origin,name,fallback)
            temporary=asset.with_suffix('.glb.partial')
            if temporary.exists():temporary.unlink()
            stats=export_glb(scene,temporary,include_calibration=False)
            temporary.replace(asset)
            record={'name':name,'signature':signature,'asset':str(asset),'source_buildings':len(ids),
                    'exported_buildings':scene['counts'].get('building',0),'skipped':scene['skipped'],
                    'origin_utm_m':list(origin),'stats':stats,'layers':['source_buildings'],
                    'calibration_nodes':False,'builder':VERSION,'builder_hash':BUILDER_HASH}
            metadata.write_text(json.dumps(record,indent=2),encoding='utf-8')
        packages.append({**record,'cache_hit':cache_hit})
        print(json.dumps({'group':name,'buildings':record['exported_buildings'],'cache_hit':cache_hit}),flush=True)
    result={'schema':'dynacity-city-build/1','packages':packages,'origin_utm_m':list(origin),
            'unassigned_buildings':[r['id'] for r in manifest['buildings'] if r['owner'] is None],
            'limitations':['No automatic import into the existing 629-building prototype.',
                           'No duplicate calibration actors per district. A shared world layer must supply calibration once.',
                           'World Partition, continuous collision and live runtime traffic are not configured by this build.']}
    (root/'city-build.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',required=True);parser.add_argument('--districts',required=True)
    parser.add_argument('--origin-scene',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--fallback-terrain',help='GLO-30 .npz; the same file given to the surface build, so buildings outside the model ground sit on the same ground as the roads')
    args=parser.parse_args()
    origin=json.loads(Path(args.origin_scene).read_text())['coordinates']['origin_utm_m']
    fallback=GroundModel.load(args.fallback_terrain) if args.fallback_terrain else None
    fallback_hash=hashlib.sha256(Path(args.fallback_terrain).read_bytes()).hexdigest() if fallback else None
    build_packages(Beirut001Pack.load(args.pack),json.loads(Path(args.districts).read_text()),origin,args.output,fallback,fallback_hash)


if __name__=='__main__':main()
