"""Patch small enclosed DEM gaps; retain sea boundaries and mapped water."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import binary_fill_holes, label, distance_transform_edt
from scipy.spatial import cKDTree
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from dynacity.terrain import GroundModel
from dynacity.engine_city_surface import terrain_mesh
from dynacity.engine_asset import export_glb


def repair_grid(ground, protected=None, max_distance_m=50):
    """Interpolate bounded nodata only; finite input samples remain identical."""
    known=np.isfinite(ground.ground)
    enclosed=binary_fill_holes(known)&~known
    groups,count=label(enclosed)
    distances=distance_transform_edt(~known,sampling=ground.cell_m)
    rr,cc=np.where(known)
    tree=cKDTree(np.column_stack((rr,cc)))
    result=ground.ground.copy();records=[]
    for group in range(1,count+1):
        r,c=np.where(groups==group)
        x=ground.x0+(c+.5)*ground.cell_m;y=ground.y0-(r+.5)*ground.cell_m
        water=protected is not None and any(protected.intersects(Point(px,py).buffer(ground.cell_m)) for px,py in zip(x,y))
        if water or distances[r,c].max()>max_distance_m:
            records.append({'cells':len(r),'status':'retained water' if water else 'retained large gap'});continue
        distance,index=tree.query(np.column_stack((r,c)),k=min(8,len(rr)))
        weights=1/np.maximum(distance,.01)**2
        result[r,c]=(ground.ground[rr[index],cc[index]]*weights).sum(axis=1)/weights.sum(axis=1)
        records.append({'cells':len(r),'status':'estimated local ground','max_distance_m':float(distances[r,c].max())})
    return GroundModel(ground.crs,ground.x0,ground.y0,ground.cell_m,result,
                       ground.source+' Small enclosed nodata repaired with local IDW; approximate visual ground.'),records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ground',required=True);p.add_argument('--context',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();folder=Path(args.output).resolve()
    if folder.is_relative_to(Path(__file__).resolve().parents[1]):p.error('Keep generated assets outside Git')
    folder.mkdir(parents=True,exist_ok=True)
    context=json.loads(Path(args.context).read_text());origin=context['origin_utm_m']
    polygons=[]
    for area in context['areas']:
        if area['kind']!='water':continue
        vertices=np.asarray(area['vertices_local_m'])[:,:2]+np.asarray(origin[:2])
        polygons.extend(Polygon(vertices[face]) for face in area['triangles'])
    from shapely.geometry import LineString
    for line in context['lines']:
        if line['kind']!='waterway':continue
        points=line.get('points_local_m') or line.get('path_local_m') or line.get('vertices_local_m')
        if points and len(points)>1:
            polygons.append(LineString(np.asarray(points)[:,:2]+np.asarray(origin[:2])).buffer(15))
    protected=unary_union(polygons) if polygons else None
    ground=GroundModel.load(args.ground);fixed,records=repair_grid(ground,protected)
    valid=np.isfinite(ground.ground);old=valid[:-1,:-1]&valid[1:,:-1]&valid[:-1,1:]&valid[1:,1:]
    valid=np.isfinite(fixed.ground);new=valid[:-1,:-1]&valid[1:,:-1]&valid[:-1,1:]&valid[1:,1:]
    mesh=terrain_mesh(fixed,origin);mesh['triangles']=np.asarray(mesh['triangles'])[np.repeat(~old[new],2)].tolist()
    mesh['id']='StreetCover_TerrainGapRepair'
    path=folder/'StreetCover_TerrainGapRepair.glb'
    export_glb({'coordinates':context['coordinates'],'meshes':[mesh],'trees':[],'roads':[],
                'bounds_lonlat':[],'limitations':['Local estimated visual terrain; no surveyed accuracy or new collision.']},path,include_calibration=False)
    fixed.save(folder/'repaired-visual-ground.npz')
    report={'asset':str(path),'records':records,'triangles':len(mesh['triangles']),
            'source_ground':str(Path(args.ground).resolve()),'context':str(Path(args.context).resolve()),
            'finite_source_samples_unchanged':bool(np.array_equal(ground.ground[np.isfinite(ground.ground)],fixed.ground[np.isfinite(ground.ground)])),
            'repaired_samples':int((~np.isfinite(ground.ground)&np.isfinite(fixed.ground)).sum())}
    (folder/'terrain-gap-repair.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
