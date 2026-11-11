"""Header-only private capture coverage; never hydrate online-only cloud files."""
from __future__ import annotations
import argparse
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
import struct
from pyproj import Transformer
from .las import LASHeader

ONLINE_ONLY=0x1000|0x400000


def is_resident(path):return not bool(getattr(Path(path).stat(),'st_file_attributes',0)&ONLINE_ONLY)


def las_record(path,*,crs='EPSG:32636',crs_evidence='Externally asserted from project BBED alignment; not embedded',resident=None):
    path=Path(path);local=is_resident(path) if resident is None else resident
    record={'id':path.stem,'path':str(path.resolve()),'kind':'photogrammetric_point_cloud',
            'bytes':path.stat().st_size,'availability':'resident' if local else 'online_only',
            'acquisition_date':None,'acquisition_date_status':'unknown; LAS creation fields are not survey dates'}
    if not local:return record,None
    header=LASHeader.read(path)
    creation=None
    if header.creation_year and 1<=header.creation_day_of_year<=366:
        creation=(date(header.creation_year,1,1)+timedelta(days=header.creation_day_of_year-1)).isoformat()
    west,east,south,north,low,high=header.bounds
    project=Transformer.from_crs(crs,4326,always_xy=True)
    ring=[project.transform(x,y) for x,y in [(west,south),(east,south),(east,north),(west,north),(west,south)]]
    record.update(point_count=header.point_count,source_rgb=header.has_rgb,bounds_utm_m=list(header.bounds),
                  las_creation_date=creation,source_crs=crs,crs_evidence=crs_evidence,
                  embedded_crs=header.as_dict()['embedded_crs'],vertical_datum='unverified',
                  footprint_kind='header bounding rectangle; not a guarantee of dense coverage inside')
    feature={'type':'Feature','properties':{k:v for k,v in record.items() if k!='path'},
             'geometry':{'type':'Polygon','coordinates':[ring]}}
    return record,feature


def glb_record(path):
    path=Path(path)
    record={'id':path.stem,'path':str(path.resolve()),'kind':'glb_mesh','bytes':path.stat().st_size,
            'availability':'resident' if is_resident(path) else 'online_only','acquisition_date':None}
    if record['availability']!='resident':return record
    with path.open('rb') as stream:
        magic,version,length=struct.unpack('<4sII',stream.read(12))
        if magic!=b'glTF' or version!=2:raise ValueError('Expected GLB 2')
        count,kind=struct.unpack('<I4s',stream.read(8))
        if kind!=b'JSON' or count>32*1024*1024:raise ValueError('Invalid/oversized GLB JSON chunk')
        document=json.loads(stream.read(count))
    primitives=[p for mesh in document.get('meshes',[]) for p in mesh.get('primitives',[])]
    accessors=document.get('accessors',[])
    positions=[accessors[p['attributes']['POSITION']] for p in primitives if 'POSITION' in p.get('attributes',{})]
    record.update(generator=document.get('asset',{}).get('generator'),mesh_count=len(document.get('meshes',[])),
                  node_count=len(document.get('nodes',[])),image_count=len(document.get('images',[])),
                  material_count=len(document.get('materials',[])),
                  positions=sum(p['count'] for p in positions),
                  triangles=sum(accessors[p['indices']]['count']//3 for p in primitives if 'indices' in p and p.get('mode',4)==4),
                  vertex_colours=any('COLOR_0' in p.get('attributes',{}) for p in primitives),
                  texture_coordinates=any('TEXCOORD_0' in p.get('attributes',{}) for p in primitives),
                  position_bounds=[{'min':p.get('min'),'max':p.get('max')} for p in positions],
                  root_nodes=[document['nodes'][i] for s in document.get('scenes',[]) for i in s.get('nodes',[])],
                  external_images=[i['uri'] for i in document.get('images',[]) if 'uri' in i and not i['uri'].startswith('data:')],
                  source_crs='unconfirmed; accessor coordinates and transforms require alignment verification')
    return record


def inventory(root):
    root=Path(root);records=[];features=[]
    for path in sorted(root.glob('*.las')):
        record,feature=las_record(path);records.append(record)
        if feature:features.append(feature)
    for path in sorted(root.glob('*.glb')):records.append(glb_record(path))
    mesh_root=root/'BeirutMeshes'
    if mesh_root.is_dir():
        for path in sorted(mesh_root.rglob('*')):
            if path.suffix.lower() not in {'.obj','.mtl','.jpg','.jpeg','.png'}:continue
            record={'id':path.stem,'path':str(path.resolve()),'kind':path.suffix.lower().lstrip('.'),
                    'bytes':path.stat().st_size,'availability':'resident' if is_resident(path) else 'online_only',
                    'acquisition_date':None,'source_crs':'not verified'}
            if path.suffix.lower()=='.mtl' and is_resident(path):
                dependencies=[line.split(maxsplit=1)[1].strip() for line in path.read_text(errors='replace').splitlines() if line.startswith('map_Kd ')]
                record['diffuse_textures']=[{'name':name,'exists':(path.parent/name).is_file(),
                    'resident':is_resident(path.parent/name) if (path.parent/name).is_file() else False} for name in dependencies]
            records.append(record)
    return {'schema':'dynacity-capture-inventory/1','generated_at':datetime.now(timezone.utc).isoformat(),
            'records':records,'footprints':{'type':'FeatureCollection','features':features},
            'limitations':['Headers and dependency metadata only; no dense-coverage quality audit.',
                           'LAS export creation dates are not acquisition dates. No automatic hydration.',
                           'Footprints assume project EPSG:32636; vertical datum and mesh alignment remain unverified.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();data=inventory(args.root);path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    path.with_suffix('.geojson').write_text(json.dumps(data['footprints']),encoding='utf-8')
    print(json.dumps({'records':len(data['records']),'resident_cloud_footprints':len(data['footprints']['features']),
                      'online_only':sum(r['availability']=='online_only' for r in data['records'])}))


if __name__=='__main__':main()
