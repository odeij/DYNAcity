"""Fit all eligible walls of exactly the previously installed pilot buildings."""
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from dynacity.engine_city_batches import Glb
from dynacity.facade_coverage import admit_buildings

start=time.perf_counter()
old=Path('C:/private/dynacity-data/engine/facade-modules-20261029')
root=Path('C:/private/dynacity-data/engine/facade-coverage-20261030');root.mkdir(exist_ok=True)
paths=[old/'charles-malek-placements.json',old/'camera-facing/charles-malek-placements.json']
plans=[json.loads(p.read_text()) for p in paths]
ids={p['source_id'] for plan in plans for p in plan['placements']}
assert len(ids)==16
base=plans[0];asset=Path(base['source_asset']);index=asset.with_suffix('.objects.json')
source=json.loads(index.read_text());glb=Glb(asset)
primitives={node['name']:(positions,faces) for node,primitive,positions,normals,faces in glb.objects()}
buildings=[]
for record in source['objects']:
    if record['source_id'] not in ids:continue
    positions,faces=primitives[record['batch']]
    triangles=faces[record['first_triangle']:record['first_triangle']+record['triangle_count']]
    used,inverse=np.unique(triangles,return_inverse=True)
    canonical=positions[used][:,[0,2,1]].copy();canonical[:,1]*=-1
    buildings.append({'id':record['source_id'],'vertices':canonical,'triangles':inverse.reshape(-1,3)})
placements,coverage,deferred=admit_buildings(buildings,20000)
result={**base,'schema':'dynacity-building-facade-modules/2','placements':placements,
    'selected_buildings':len(coverage),'wall_coverage':coverage,'omissions':deferred,
    'selection':{'source_ids':sorted(ids),'mode':'all eligible walls; complete building admission'},
    'budget':{'max_instances':20000,'tile_size_m':250,'view_distance_m':180},
    'fitting':{'positive_scale_bounds':[.70,1.40],'source_panel_m':3},
    'superseded_manifests':[str(p) for p in paths],
    'source_sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),
    'source_index_sha256':hashlib.sha256(index.read_bytes()).hexdigest(),
    'generation_seconds':time.perf_counter()-start,
    'limits':['Generic estimated architecture, not photo-confirmed facades.',
              'Positive bounded library-piece fitting; original triangles and UVs retained.',
              'Source geometry and collision unchanged; short wall facets retain the base material.']}
target=root/'placements.json';target.write_text(json.dumps(result,indent=2))
print(json.dumps({'manifest':str(target),'buildings':len(coverage),'instances':len(placements),
                  'walls':sum(len(c['wall_coverage']) for c in coverage),'deferred':len(deferred),
                  'seconds':result['generation_seconds']}))
