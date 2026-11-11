"""Plan library facade instances on selected, unchanged city source triangles."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from dynacity.engine_city_batches import Glb
from dynacity.building_facade_modules import plan_modules


def prepare(asset,index,modules,output,bounds,max_instances,priority_point=None,
            focus_point=None,exclude_manifest=None):
    mesh=Glb(asset); source=json.loads(Path(index).read_text())
    primitives={node['name']:(positions,faces) for node,primitive,positions,normals,faces in mesh.objects()}
    buildings=[]
    for record in source['objects']:
        positions,faces=primitives[record['batch']]
        first=record['first_triangle'];count=record['triangle_count']
        triangles=faces[first:first+count]
        used,inverse=np.unique(triangles,return_inverse=True)
        canonical=positions[used][:,[0,2,1]].copy();canonical[:,1]*=-1
        buildings.append({'id':record['source_id'],'vertices':canonical,
            'triangles':inverse.reshape(-1,3),'metadata':record['metadata']})
    prepared=json.loads(Path(modules).read_text())
    excluded=json.loads(Path(exclude_manifest).read_text())['placements'] if exclude_manifest else []
    excluded_ids=[item['id'] for item in excluded]
    focus_point=focus_point or [2423.,1491.]
    result=plan_modules(buildings,bounds=bounds,max_instances=max_instances,
                        lift_m=prepared['wall_offset_m'],focus_point=focus_point,
                        priority_point=priority_point,exclude_ids=excluded_ids)
    result.update(module_manifest=str(Path(modules).resolve()),
        source_asset=str(Path(asset).resolve()),source_sha256=hashlib.sha256(Path(asset).read_bytes()).hexdigest(),
        coordinates=source['coordinates'],bounds_m=bounds,
        source_index_sha256=hashlib.sha256(Path(index).read_bytes()).hexdigest())
    result['selection']={'focus_point_m':focus_point,'priority_point_m':priority_point,
        'excluded_manifest':str(Path(exclude_manifest).resolve()) if exclude_manifest else None,
        'excluded_manifest_sha256':hashlib.sha256(Path(exclude_manifest).read_bytes()).hexdigest() if exclude_manifest else None,
        'excluded_placement_count':len(excluded_ids)}
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text(json.dumps(result,indent=2))
    print(json.dumps({'output':output,'selected_buildings':result['selected_buildings'],
        'instances':len(result['placements']),'omissions':len(result['omissions'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--asset',required=True);p.add_argument('--index',required=True)
    p.add_argument('--modules',required=True);p.add_argument('--output',required=True)
    p.add_argument('--bounds',type=float,nargs=4,default=[2300,1450,2545,1530])
    p.add_argument('--max-instances',type=int,default=1800)
    p.add_argument('--priority-point',type=float,nargs=2)
    p.add_argument('--focus-point',type=float,nargs=2)
    p.add_argument('--exclude-manifest')
    a=p.parse_args();prepare(a.asset,a.index,a.modules,a.output,a.bounds,a.max_instances,
        a.priority_point,a.focus_point,a.exclude_manifest)
