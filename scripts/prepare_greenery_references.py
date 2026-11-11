"""Represent point-only parks and unclosed greenery as explicit data reference geometry."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import shape,Polygon,box,LineString
from shapely.ops import transform
from pyproj import Transformer
from dynacity.landscape import landscape_payload
from dynacity.engine_street_context import TerrainSurface
from dynacity.terrain import GroundModel
from dynacity.engine_asset import export_glb
from dynacity.engine_street_assets import _colour_glb


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('raw','ground','manifest','output'):p.add_argument('--'+k,required=True)
    args=p.parse_args();read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    raw=read(args.raw);manifest=read(args.manifest);payload=landscape_payload(raw)
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    surface=TerrainSurface(GroundModel.load(args.ground));origin=manifest['origin_utm_m']
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    domain=transform(project,box(*raw['query_bbox']))
    candidates=[f for f in payload['features'] if f['properties'].get('kind')=='greenery_reference' or
                (f['geometry']['type']=='Point' and f['properties'].get('kind') in ('park','wood','garden','scrub'))]
    vertices=[];faces=[];objects=[];records=[]
    for f in candidates:
        props=f['properties'];geometry=transform(project,shape(f['geometry']))
        if geometry.geom_type=='Point':
            # Cartographic ring: does not imply vegetation extent or a tree.
            outline=geometry.buffer(1.2).difference(geometry.buffer(.95))
            representation='Point-only greenery location; ring is a data marker, not a mapped extent'
        else:
            outline=geometry.intersection(domain).buffer(.10,cap_style=2)
            representation='Unclosed source outline; thin reference stroke, no inferred enclosed area'
        if outline.is_empty:continue
        v,t,missing=surface.drape(outline,origin,lift=.08)
        start=len(faces);offset=len(vertices);vertices.extend(v);faces.extend([[i+offset for i in tri] for tri in t])
        if t:
            objects.append({'source_id':props['id'],'triangle_start':start,'triangle_count':len(t),
                            'representation':representation})
        records.append({'id':props['id'],'kind':props.get('reference_kind',props['kind']),
                        'status':'reference_only' if t else 'skipped','representation':representation,
                        'missing_terrain_cells':missing,'tags':props['tags']})
    name='StreetCover_GreeneryReferences'
    path=output/(name+'.glb')
    export_glb({'coordinates':{'origin_utm_m':origin},'meshes':[{'id':name,'kind':'terrain',
        'vertices':vertices,'triangles':faces,'metadata':{'representation':'Cartographic data references'}}],
        'trees':[],'roads':[],'bounds_lonlat':raw['query_bbox'],'limitations':['Reference geometry only.']},path,include_calibration=False)
    _colour_glb(path,(.22,.48,.20))
    package={'key':'greenery_references','name':name,'kind':'greenery_reference','production_area':'Data references',
             'asset':str(path),'objects':objects,'triangles':len(faces)}
    (output/'reference-package.json').write_text(json.dumps({'package':package,'records':records},indent=2),encoding='utf-8')
    print(json.dumps({'references':len(objects),'triangles':len(faces)}))


if __name__=='__main__':main()
