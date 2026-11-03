"""Measure source-network, exported-road and municipal terrain coverage separately."""
import argparse
import json
from pathlib import Path
import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, box, shape
from shapely.ops import transform, unary_union
from dynacity.terrain import GroundModel


def terrain_footprint(ground):
    z=ground.ground
    good=np.isfinite(z[:-1,:-1])&np.isfinite(z[1:,:-1])&np.isfinite(z[:-1,1:])&np.isfinite(z[1:,1:])
    rectangles=[];cell=ground.cell_m
    for r,row in enumerate(good):
        changes=np.diff(np.r_[False,row,False].astype(int));starts=np.flatnonzero(changes==1);ends=np.flatnonzero(changes==-1)
        for first,last in zip(starts,ends):
            rectangles.append(box(ground.x0+(first+.5)*cell,ground.y0-(r+1.5)*cell,
                                  ground.x0+(last+.5)*cell,ground.y0-(r+.5)*cell))
    return unary_union(rectangles)


def report(traffic,surface,districts,ground):
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    areas=[(f['properties']['name'],transform(project,shape(f['geometry']))) for f in districts['groups']['features']]
    municipal=unary_union([p for _,p in areas]);terrain=terrain_footprint(ground)
    ids={r['id'] for r in surface['roads']}
    lines=[LineString(r['xy']) for r in traffic['roads']]
    lengths=[line.length for line in lines]
    municipal_length=sum(line.intersection(municipal).length for line in lines)
    exported_municipal=sum(line.intersection(municipal).length for i,line in enumerate(lines) if i in ids)
    return {'schema':'dynacity-coverage-audit/1','source_road_segments':len(lines),'exported_road_segments':len(ids),
        'exported_segment_percent':len(ids)/len(lines)*100,
        'source_network_km':sum(lengths)/1000,'exported_network_km':sum(lengths[i] for i in ids)/1000,
        'municipal_reference_km2':municipal.area/1e6,'source_terrain_km2':terrain.area/1e6,
        'source_terrain_inside_municipal_km2':terrain.intersection(municipal).area/1e6,
        'source_terrain_municipal_percent':terrain.intersection(municipal).area/municipal.area*100,
        'source_roads_inside_municipal_km':municipal_length/1000,
        'exported_roads_inside_municipal_km':exported_municipal/1000,
        'municipal_network_length_percent':exported_municipal/municipal_length*100,
        'districts':[{'name':name,'reference_km2':p.area/1e6,
            'source_terrain_percent':terrain.intersection(p).area/p.area*100} for name,p in areas],
        'limits':['Municipal reference is the union of the 12 cached source-labelled production areas; boundary currency unverified.',
                  'Source terrain only. Later approximate DEM ground extensions are excluded from this terrain metric.',
                  'Road-segment and centreline-length ratios are not land-area coverage or proof of continuous driveability.',
                  'Counts refer to available historical source data, not a complete street survey or live traffic.']}


def main():
    parser=argparse.ArgumentParser()
    for key in ('traffic','surface','districts','ground','output'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args()
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    result=report(read(args.traffic),read(args.surface),read(args.districts),GroundModel.load(args.ground))
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('districts','limits')}))


if __name__=='__main__':main()
