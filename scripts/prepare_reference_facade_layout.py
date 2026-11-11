"""Fit authored library details to the unchanged reference building bounds."""
import hashlib
import json
import math
from pathlib import Path
import numpy as np

ROOT=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
source=json.loads((ROOT/'source-building.json').read_text())
prepared=json.loads((ROOT/'prepared-reference.json').read_text())
observation=json.loads(Path('C:/private/dynacity-data/engine/appearance-evidence-20261030/reviewed-observations.json').read_text())['observations'][0]
assert source['record']['source_id']==observation['source_id']
sizes={r['id']:[b-a for a,b in zip(*r['bounds_m'])] for r in prepared['assets']}
low,high=np.asarray(source['record']['local_bounds_m'])
height=float(high[2]-low[2]);floors=max(1,round(height/3.2));floor_height=height/floors
focus=np.array([2525.139,1492.925]);placements=[];coverage=[]
for wall_index,wall in enumerate(source['walls']):
    a,b=np.array(wall['a']),np.array(wall['b']);normal=np.array(wall['normal'])[:2]
    if np.dot(normal,focus-(a[:2]+b[:2])/2)<=0:continue
    tangent=np.array([normal[1],-normal[0]])
    if np.dot(b[:2]-a[:2],tangent)<0:a,b=b,a
    length=float(np.linalg.norm(b[:2]-a[:2]));columns=max(1,round(length/3.2));bay=length/columns
    yaw=math.degrees(math.atan2(tangent[1],tangent[0]))
    for row in range(1,floors):
        z=float(low[2]+row*floor_height)
        for column in range(columns):
            midpoint=a[:2]+tangent*((column+.5)*bay)
            for kind,width,depth,outward,vertical in [('cornice',bay,1.2,.61,.16),('glass',bay*.97,.05,1.19,1.05)]:
                point=midpoint+normal*outward
                placements.append({'id':f'{observation["source_id"]}/reference/{wall_index}/{row}/{column}/{kind}',
                    'module':kind,'position_m':[float(point[0]),float(point[1]),z+(0 if kind=='cornice' else .16)],
                    'yaw_deg':yaw,'scale':[width/sizes[kind][0],depth/max(sizes[kind][1],.01),vertical/sizes[kind][2]]})
    coverage.append({'wall_index':wall_index,'length_m':length,'columns':columns,'bay_width_m':bay,
                     'floor_rows':floors,'floor_height_m':floor_height})
result={'schema':'dynacity-reference-facade/1','source':source,'prepared':prepared,
    'source_sha256':hashlib.sha256(Path(source['source_asset']).read_bytes()).hexdigest(),
    'source_index_sha256':hashlib.sha256(Path(source['source_index']).read_bytes()).hexdigest(),
    'observation':observation,'floor_count_assumed':floors,'floor_height_m':floor_height,
    'wall_coverage':coverage,'placements':placements,
    'limits':['Historical color families and visible glazing supported by photograph',
              'Floor count, spacing, recesses, balcony depth and hidden elevations are estimated',
              'Source height/footprint retained; this is a reference-based visual approximation']}
(ROOT/'reference-layout.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'assumed_floors':floors,'floor_height_m':floor_height,'visible_walls':len(coverage),'instances':len(placements)}))
