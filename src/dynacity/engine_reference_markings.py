"""Illustrative two-lane paint along a source-audited one-way reference street."""
import hashlib
import json
import math
from pathlib import Path
from .engine_asset import export_glb
from .engine_street_assets import _colour_glb


def build(audit,manifest,output):
    source=Path(manifest);data=json.loads(source.read_text());roads=json.loads(Path(audit).read_text())['roads']
    vertices=[];faces=[];objects=[];distance=0.
    centre=data['reference_street']['position_local_m'];radius=125.
    for row in roads:
        if row['lanes']!=2 or row['oneway']!=1:raise ValueError('Expected audited two-lane one-way road')
        start=len(faces)
        for a,b in zip(row['road_export']['path'],row['road_export']['path'][1:]):
            length=math.dist(a[:2],b[:2]);offset=0.
            while offset<length-1e-6:
                phase=distance%9;step=min(length-offset,3-phase if phase<3 else 9-phase)
                if step<1e-6:step=min(length-offset,1e-5)
                if phase<3:
                    first=[a[i]+(b[i]-a[i])*offset/length for i in range(3)]
                    last=[a[i]+(b[i]-a[i])*(offset+step)/length for i in range(3)]
                    if math.dist(first[:2],centre[:2])<=radius and math.dist(last[:2],centre[:2])<=radius:
                        normal=[-(b[1]-a[1])/length*.06,(b[0]-a[0])/length*.06]
                        idx=len(vertices)
                        for p,sign in ((first,-1),(last,-1),(last,1),(first,1)):
                            vertices.append([p[0]+normal[0]*sign,p[1]+normal[1]*sign,p[2]+.025])
                        faces.extend([[idx,idx+1,idx+2],[idx,idx+2,idx+3]])
                offset+=step;distance+=step
        objects.append({'source_id':'way/'+str(row['source_way']),'road_id':row['road_id'],
                        'triangle_start':start,'triangle_count':len(faces)-start,
                        'inference':'Two lanes from OSM; 12cm divider width and 3m/6m dash spacing are illustrative.'})
    output=Path(output);output.mkdir(parents=True,exist_ok=True);name='StreetCover_reference_lane_divider'
    path=output/(name+'.glb')
    if not path.exists():
        scene={'coordinates':{'origin_utm_m':data['origin_utm_m']},'limitations':['Illustrative paint using source lane count.'],
            'bounds_lonlat':None,'trees':[],'roads':[],'meshes':[{'id':name,'kind':'terrain','vertices':vertices,
                'triangles':faces,'metadata':{'source_manifest':str(source)}}]}
        export_glb(scene,path,include_calibration=False);_colour_glb(path,(.67,.66,.59))
    result={'schema':'dynacity-street-cover-assets/1','origin_utm_m':data['origin_utm_m'],
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'packages':[{'key':'reference_lane_divider',
            'name':name,'kind':'lane_marking','production_area':'Remeil','asset':str(path),'objects':objects,'triangles':len(faces)}]}
    target=output/'reference-markings.json';target.write_text(json.dumps(result,indent=2));return target


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('audit','manifest','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();print(build(args.audit,args.manifest,args.output))
