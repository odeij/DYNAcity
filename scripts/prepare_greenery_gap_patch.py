"""Bounded visual infill inside an explicit mapped land polygon; never infer open sea terrain."""
import argparse
import json
import math
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import box
from scipy.spatial import cKDTree
from dynacity.terrain import GroundModel
from dynacity.engine_asset import export_glb
from dynacity.engine_street_assets import _colour_glb


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('gap','ground','manifest','source-id','output'):p.add_argument('--'+k,required=True)
    args=p.parse_args();raw=json.loads(Path(args.gap).read_text())
    gap=shapely.from_geojson(raw if isinstance(raw,str) else json.dumps(raw))
    ground=GroundModel.load(args.ground);source=json.loads(Path(args.manifest).read_text());origin=source['origin_utm_m']
    rows,cols=np.nonzero(np.isfinite(ground.ground));xy=np.column_stack([ground.x0+(cols+.5)*ground.cell_m,ground.y0-(rows+.5)*ground.cell_m]);zz=ground.ground[rows,cols];tree=cKDTree(xy)
    vertices=[];faces=[];maximum=0;skipped=0
    west,south,east,north=gap.bounds
    for x in np.arange(math.floor(west/5)*5,east,5):
        for y in np.arange(math.floor(south/5)*5,north,5):
            piece=gap.intersection(box(x,y,x+5,y+5))
            for polygon in shapely.get_parts(piece):
                if polygon.geom_type!='Polygon' or polygon.area<.001:continue
                for tri in shapely.constrained_delaunay_triangles(polygon).geoms:
                    points=np.asarray(tri.exterior.coords)[:3,:2];distance,idx=tree.query(points,k=3)
                    if distance[:,0].max()>30:skipped+=1;continue
                    maximum=max(maximum,float(distance[:,0].max()))
                    weights=1/np.maximum(distance,.1)**2;z=(zz[idx]*weights).sum(1)/weights.sum(1)
                    start=len(vertices);vertices.extend([[a-origin[0],b-origin[1],c+.035-origin[2]] for (a,b),c in zip(points,z)]);faces.append([start,start+1,start+2])
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True);name='StreetCover_MappedGreeneryGap';path=output/(name+'.glb')
    export_glb({'coordinates':{'origin_utm_m':origin},'meshes':[{'id':name,'kind':'terrain','vertices':vertices,'triangles':faces,
        'metadata':{'source_id':args.source_id,'elevation_source':'Visual IDW infill within mapped scrub; nearest valid samples at most 30 m away'}}],
        'trees':[],'roads':[],'bounds_lonlat':None,'limitations':['Estimated visual surface, not surveyed elevation.']},path,include_calibration=False)
    _colour_glb(path,(.24,.28,.13))
    package={'key':'mapped_greenery_gap','name':name,'kind':'scrub','production_area':'Mapped coastal scrub',
        'asset':str(path),'objects':[{'source_id':args.source_id,'representation':'Bounded visual elevation infill','area_m2':gap.area}],
        'triangles':len(faces)}
    report={'package':package,'max_nearest_sample_distance_m':maximum,'skipped_triangles':skipped,
            'area_m2':gap.area,'source_id':args.source_id,'elevation_source':'Inverse-distance estimate from existing nearby land samples; explicit mapped polygon only'}
    (output/'gap-package.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
