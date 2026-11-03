"""Search resident AUB building captures against resident coloured LAS previews."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from dynacity.capture_registration import coarse_candidates,refine,voxel_sample
from dynacity.engine_capture_inventory import is_resident
from dynacity.las import iter_point_chunks


def obj_vertices(path):
    values=[]
    for line in Path(path).open(encoding='utf8'):
        if line.startswith('v '):values.append(list(map(float,line.split()[1:4])))
    return np.asarray(values)


def run(inventory,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    records=json.loads(Path(inventory).read_text(encoding='utf8'))['records']
    origin=np.array([733200.,3753200.,80.])
    targets=[];sources=[]
    for row in records:
        if row['id'] in ('TheStreetScape','TheNestledVillage','TheHeritageLandscape') and row['kind']=='photogrammetric_point_cloud':
            original=Path(row['path'])
            if row['id']=='TheStreetScape':path=original;stride=4
            else:
                # Existing exact-record viewing copies avoid reading 100M points.
                copies=list(Path('C:/private/dynacity-data/engine').rglob('*'+row['id']+'*.las'))
                copies=[p for p in copies if is_resident(p)]
                path=min(copies,key=lambda p:p.stat().st_size) if copies else None;stride=2
                if path is None:
                    print('No resident bounded preview for '+row['id'],flush=True);continue
            if not is_resident(path):continue
            cache=output/(row['id']+'-sample.npy')
            if cache.exists():points=np.load(cache)
            else:
                arrays=[]
                for chunk in iter_point_chunks(path):
                    arrays.append(np.column_stack([chunk.x[::stride],chunk.y[::stride],chunk.z[::stride]])-origin)
                points=np.concatenate(arrays);points=points[voxel_sample(points,.35)];np.save(cache,points)
            targets.append(points);sources.append({'id':row['id'],'path':str(path),'sample_points':len(points)})
            print(row['id'],len(points),'sampled points',flush=True)
    if not targets:raise ValueError('No resident target scans')
    target=np.concatenate(targets);target=target[voxel_sample(target,.4)]
    coarse_target=target[voxel_sample(target,.7)]
    report={'schema':'dynacity-building-capture-matches/1','origin_utm_m':origin.tolist(),'sources':sources,
            'assumptions':['Target LAS CRS assumed EPSG:32636; vertical datum unverified.',
                           'Local OBJs assumed metres and Z up. Search does not deform meshes.',
                           'A geometric fit is a candidate, not verified identity or approved replacement.'],
            'matches':[]}
    for row in records:
        if row['kind']!='obj' or row['id']=='Me_1_127':continue
        path=Path(row['path'])
        if not is_resident(path):continue
        result_file=output/(row['id']+'-matches.json')
        if result_file.exists():
            result=json.loads(result_file.read_text());report['matches'].append(result);continue
        started=time.perf_counter();vertices=obj_vertices(path)
        source=vertices[voxel_sample(vertices,.4)]
        print('Searching',row['id'],len(source),'source voxels',flush=True)
        candidates=coarse_candidates(source,coarse_target,keep=24)
        refined=[]
        for i,seed in enumerate(candidates):
            item=refine(source,target,seed);refined.append(item)
            print(row['id'],i,round(item['median_m'],3),round(item['within_05m'],3),flush=True)
        refined.sort(key=lambda r:(-r['within_05m'],r['median_m']))
        for item in refined:
            matrix=np.asarray(item['transform']);centre=source.mean(axis=0)@matrix[:3,:3].T+matrix[:3,3]+origin
            item['centre_utm_m']=centre.tolist()
            item['distance_to_charles_m']=float(np.linalg.norm(centre[:2]-np.array([733208.4,3753042.85])))
        result={'id':row['id'],'source':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'source_vertices':len(vertices),'sample_vertices':len(source),'elapsed_s':time.perf_counter()-started,
                'candidates':refined[:8],'replacement_applied':False}
        result_file.write_text(json.dumps(result,indent=2),encoding='utf8');report['matches'].append(result)
    (output/'matches.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();run(args.inventory,args.output)
