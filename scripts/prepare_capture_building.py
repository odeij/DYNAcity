"""Prepare a photo-identified capture and exact source-batch omission, privately."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import shapely
from shapely.geometry import shape

from dynacity.capture_building import fit_xy,omit_source_triangles
from dynacity.engine_capture_inventory import is_resident
from dynacity.engine_street_context import TerrainSurface
from dynacity.terrain import GroundModel


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def prepare(control,checkpoint,ground,polygons):
    config=read(control);cp=read(checkpoint);origin=np.asarray(cp['origin_utm_m']);source=Path(config['source'])
    output=Path(config['output']);output.mkdir(parents=True,exist_ok=True)
    if not is_resident(source):raise ValueError('Capture must already be resident')
    rotation,translation,errors=fit_xy(config['source_ties_xy_m'],config['target_ties_utm_m'])
    if np.sqrt(np.mean(errors**2))>1.5:raise ValueError('Control residual too large for a placement preview')
    vertices=np.array([list(map(float,line.split()[1:4])) for line in source.open() if line.startswith('v ')])
    xy=vertices[:,:2]@rotation.T+translation
    surface=TerrainSurface(GroundModel.load(ground))
    x0,x1,y0,y1=config['ground_patch_source_xy']
    patch=(vertices[:,0]>=x0)&(vertices[:,0]<=x1)&(vertices[:,1]>=y0)&(vertices[:,1]<=y1)
    patch&=vertices[:,2]<=np.quantile(vertices[patch,2],.25)
    indices=np.flatnonzero(patch)[::5]
    offsets=[surface.sample(*xy[i])-vertices[i,2]+.03 for i in indices if surface.sample(*xy[i]) is not None]
    if len(offsets)<20:raise ValueError('Insufficient ground-patch support')
    z_offset=float(np.median(offsets));world=np.column_stack([xy,vertices[:,2]+z_offset]);local=world-origin
    rows=read(polygons)['features'];wanted=set(config['municipal_building_ids'])
    selected=[shape(f['geometry']) for f in rows if f['properties']['OBJECTID'] in wanted]
    if len(selected)!=len(wanted):raise ValueError('Missing mapped building control polygons')
    allowed=shapely.union_all(selected).buffer(.6)
    # Keep only original source faces; no reconstruction, hole fill or UV edits.
    target=output/source.name;faces_total=faces_kept=0;positions_written=0;mtl_names=[];used=np.zeros(len(vertices),dtype=bool)
    with source.open(encoding='utf8') as inp,target.open('w',encoding='utf8') as out:
        for line in inp:
            fields=line.split()
            if not fields:out.write(line);continue
            if fields[0]=='v':
                out.write('v %.7f %.7f %.7f\n'%tuple(local[positions_written]));positions_written+=1
            elif fields[0]=='vn':
                n=np.asarray(list(map(float,fields[1:4])));n[:2]=n[:2]@rotation.T
                out.write('vn %.9f %.9f %.9f\n'%tuple(n))
            elif fields[0]=='f':
                faces_total+=1;ids=[int(v.split('/')[0])-1 for v in fields[1:]]
                centre=world[ids].mean(axis=0)
                ground_z=surface.sample(*centre[:2])
                if not shapely.intersects_xy(allowed,*centre[:2]) or ground_z is None or centre[2]<ground_z-.5:continue
                out.write(line);faces_kept+=1;used[ids]=True
            else:
                out.write(line)
                if fields[0]=='mtllib':mtl_names.extend(fields[1:])
    dependencies=[]
    for name in mtl_names:
        material=source.parent/name
        if not is_resident(material):raise ValueError('Material must be resident')
        shutil.copy2(material,output/name)
        for line in material.read_text().splitlines():
            if line.startswith(('map_Kd ','map_Ka ','map_bump ','bump ')):
                texture=source.parent/line.split(maxsplit=1)[1].strip()
                if not texture.resolve().is_relative_to(source.parent.resolve()) or not is_resident(texture):raise ValueError('Invalid texture dependency')
                shutil.copy2(texture,output/texture.name)
                dependencies.append({'source':str(texture),'sha256':hashlib.sha256(texture.read_bytes()).hexdigest()})
    if not 1000<faces_kept<=faces_total:raise ValueError('Unexpected captured face coverage')
    patches=[]
    for package in cp['packages']:
        if not package['key'].startswith('batch-buildings-'):continue
        index=Path(package['asset']).with_suffix('.objects.json');objects=read(index)['objects']
        matched=[r['source_id'] for r in objects if r['source_id'] in config['replace_source_ids']]
        if not matched:continue
        patches.append(omit_source_triangles(package['asset'],index,matched,output/'source-batch-omission.glb'))
    if sum(len(p['removed']) for p in patches)!=len(config['replace_source_ids']):raise ValueError('Original placeholder identity is unresolved')
    matrix=np.eye(4);matrix[:2,:2]=rotation;matrix[:2,3]=translation;matrix[2,3]=z_offset
    result={**config,'schema':'dynacity-captured-building/1','origin_utm_m':origin.tolist(),
            'aligned_obj':str(target),'asset':str(output/'capture.glb'),'patches':patches,
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'textures':dependencies,
            'source_faces':faces_total,'retained_faces':faces_kept,
            'source_to_utm_matrix':matrix.tolist(),'control_errors_m':errors.tolist(),
            'control_rms_m':float(np.sqrt(np.mean(errors**2))),'ground_fit_samples':len(offsets),
            'ground_fit_p90_deviation_m':float(np.quantile(np.abs(np.asarray(offsets)-z_offset),.9)),
            'expected_local_bounds_m':[local[used].min(0).tolist(),local[used].max(0).tolist()],
            'identity_confidence':'Photo and parcel ID match; eave-based placement estimated, not surveyed',
            'status':'captured-condition alignment preview, not verified current-day restoration',
            'source_originals_modified':False,'public_redistribution_rights':'unverified; private working copy only'}
    (output/'capture-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({k:result[k] for k in ('retained_faces','control_rms_m','ground_fit_p90_deviation_m','aligned_obj')},indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--control',required=True);p.add_argument('--checkpoint',required=True)
    p.add_argument('--ground',required=True);p.add_argument('--polygons',required=True);args=p.parse_args()
    prepare(args.control,args.checkpoint,args.ground,args.polygons)
