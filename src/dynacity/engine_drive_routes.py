"""Prepare grounded, source-identifiable starts for the Unreal driving prototype.

Geometric screening is against the private source model, not a street survey or
proof of legal driveability. No traffic direction or missing height is invented.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform
from pyproj import Transformer

from .beirut001_pack import Beirut001Pack
from .traffic import TrafficModel


def unreal_position(local):
    """Source-relative east/north/up metres to Unreal east/south/up centimetres."""
    x,y,z=local
    return [round(float(x)*100,3),round(-float(y)*100,3),round(float(z)*100,3)]


def _lane_path(line, distances, offset):
    """Sample a right-offset trajectory in the oriented source-road frame."""
    xy=shapely.get_coordinates(shapely.line_interpolate_point(line,distances))[:,:2]
    behind=shapely.get_coordinates(shapely.line_interpolate_point(line,np.maximum(0,distances-.5)))[:,:2]
    ahead=shapely.get_coordinates(shapely.line_interpolate_point(line,np.minimum(line.length,distances+.5)))[:,:2]
    tangent=ahead-behind
    norm=np.linalg.norm(tangent,axis=1)
    if np.any(norm<1e-6):raise ValueError('degenerate road heading')
    tangent/=norm[:,None]
    right=np.column_stack([tangent[:,1],-tangent[:,0]])
    return xy+right*offset,tangent


def select_starts(pack, traffic, roads, ownership, areas, origin, *,
                  spawn_height_m=1.2, vehicle_width_m=2., margin_m=.5,
                  preview_length_m=30., max_grade=.10):
    """Choose one screened start in each source area, or report it unavailable.

    `roads` are exported paths, `ownership` maps road IDs to named areas, and
    `areas` contains the original projected source-sector polygons. Starts are
    strictly within their named polygon; whole-road ownership alone is not enough.
    """
    origin=np.asarray(origin,dtype=float)
    if origin.shape!=(3,) or not np.isfinite(origin).all():raise ValueError('Finite common origin required')
    if pack.ground is None:raise ValueError('Source ground required')
    if traffic.crs!='EPSG:32636' or pack.meta['crs']!='EPSG:32636':raise ValueError('Matching source CRS required')
    if not (0<vehicle_width_m and 0<=margin_m and 0<preview_length_m and 0<max_grade and spawn_height_m>0):
        raise ValueError('Positive vehicle and screening parameters required')
    footprints=[p for p in pack.buildings if p is not None and not p.is_empty]
    tree=shapely.STRtree(footprints)
    failures=Counter();accepted=Counter();best={};road_checks=[]
    clearance=vehicle_width_m/2+margin_m
    for road in roads:
        identifier=int(road['id']);area=ownership.get(identifier)
        if area not in areas:failures['unassigned road']+=1;continue
        if int(road.get('level',0))!=0 or road.get('height_source')!='source ground':
            failures['non-ground road']+=1;continue
        if identifier<0 or identifier>=len(traffic.geometry):raise ValueError('Road ID outside source traffic model')
        if int(road['source_way'])!=int(traffic.way_id[identifier]):
            raise ValueError('Exported road ID does not match source OSM way')
        source_direction=int(traffic.oneway[identifier])
        if source_direction not in (-1,0,1):raise ValueError('Unknown source road direction')
        if traffic.highway[identifier] in ('motorway','motorway_link','trunk','trunk_link'):
            failures['high-speed road class']+=1;continue
        width=float(road['width_m'])
        lane_width=width/2 if source_direction==0 else min(width,3.2)
        if lane_width<clearance*2:
            failures['insufficient modeled lane width']+=1;continue
        local_path=np.asarray(road['path'],dtype=float)
        if local_path.ndim!=2 or local_path.shape[1]!=3 or not np.isfinite(local_path).all():
            failures['invalid exported path']+=1;continue
        xy=local_path[:,:2]+origin[:2]
        source_xy=np.asarray(traffic.geometry[identifier],dtype=float)[:,:2]
        if np.linalg.norm(xy[0]-source_xy[0])>.02 or np.linalg.norm(xy[-1]-source_xy[-1])>.02:
            raise ValueError('Exported road endpoints differ from source traffic geometry')
        if source_direction==-1:xy=xy[::-1]
        line=LineString(xy)
        # Keep the full 30 m preview and a vehicle-length buffer away from the
        # source graph endpoints, where intersections and turn choices occur.
        lower=12.;upper=line.length-preview_length_m-8.
        if upper<lower:failures['road too short for clear approach']+=1;continue
        distances=np.unique(np.r_[np.linspace(lower,upper,min(9,max(1,int((upper-lower)/15)+1))),
                                  (lower+upper)/2])
        # Prefer the assumed right lane on two-way roads. On wider one-way
        # roads, use the rightmost modeled 3.2 m lane. Width is not a curb survey.
        offset=width/4 if source_direction==0 else max(0.,width/2-lane_width/2)
        rejected=Counter();road_accepted=0
        for distance in distances:
            sample_d=np.r_[distance-3.,np.linspace(distance,distance+preview_length_m,14)]
            try:trajectory,tangents=_lane_path(line,sample_d,offset)
            except ValueError:rejected['degenerate road heading']+=1;continue
            spawn_xy=trajectory[1]
            if not areas[area].contains(Point(spawn_xy)):
                rejected['candidate outside named area']+=1;continue
            corridor=LineString(trajectory).buffer(clearance,cap_style=2)
            if not line.buffer(width/2+.02,cap_style=2).covers(corridor):
                rejected['lane corridor leaves modeled road']+=1;continue
            if len(tree.query(corridor,predicate='intersects')):
                rejected['building footprint conflict']+=1;continue
            z=pack.ground.sample(trajectory[:,0],trajectory[:,1])
            right=np.column_stack([tangents[:,1],-tangents[:,0]])*clearance
            edge_xy=np.concatenate([trajectory+right,trajectory-right])
            edge_z=pack.ground.sample(edge_xy[:,0],edge_xy[:,1])
            if not np.isfinite(z).all() or not np.isfinite(edge_z).all():
                rejected['missing source ground']+=1;continue
            steps=np.linalg.norm(np.diff(trajectory,axis=0),axis=1)
            grades=np.abs(np.diff(z))/np.maximum(steps,1e-6)
            grade=float(grades.max())
            if grade>max_grade:
                rejected['steep or discontinuous source grade']+=1;continue
            # The shared road exporter places ground-level asphalt 0.12 m
            # above source ground. Keep that source convention for runtime snapping.
            surface_z=float(z[1]+.12-origin[2])
            local=[float(spawn_xy[0]-origin[0]),float(spawn_xy[1]-origin[1]),surface_z+spawn_height_m]
            tangent=tangents[1]
            heading=math.degrees(math.atan2(-float(tangent[1]),float(tangent[0])))
            preview=np.column_stack([trajectory[1:]-origin[:2],z[1:]+.12-origin[2]])
            candidate={'area':area,'road_id':identifier,'road_name':str(road.get('name','')),
                'source_way':int(road['source_way']),'source_oneway':source_direction,
                'source_direction':'reverse drawn geometry' if source_direction==-1 else 'drawn geometry',
                'location_cm':unreal_position(local),'yaw_degrees':round(heading,6),
                'local_position_m':[round(v,6) for v in local],
                'surface_local_z_m':round(surface_z,6),'spawn_height_m':spawn_height_m,
                'preview_path_cm':[unreal_position(p) for p in preview],
                'checks':{'preview_length_m':preview_length_m,'behind_clearance_m':3.,
                    'vehicle_width_m':vehicle_width_m,'lateral_margin_m':margin_m,
                    'modeled_road_width_m':width,'lane_offset_right_m':offset,
                    'max_preview_grade':round(grade,6),'building_conflict':False,
                    'inside_source_area':True,'ground_samples_finite':True,
                    'distance_from_drawn_start_m':round(float(distance),3)}}
            # Named streets, gentle grades and longer approaches are preferable.
            # Source IDs and distances make equal-score selection reproducible.
            score=(not bool(candidate['road_name']),round(grade,5),-min(line.length,300),identifier,float(distance))
            if area not in best or score<best[area][0]:best[area]=(score,candidate)
            accepted[area]+=1;road_accepted+=1
        failures.update(rejected)
        road_checks.append({'road_id':identifier,'area':area,'accepted_candidates':road_accepted,
                            'rejected_candidates':dict(rejected)})
    starts=[best[name][1] for name in sorted(best)]
    return {'schema':'dynacity-drive-starts/1','origin_utm_m':origin.tolist(),
        'coordinates':{'source_crs':'EPSG:32636','local_axes':'X east, Y north, Z up; metres',
            'runtime_axes':'X east, Y south, Z up; centimetres','vertical_datum':'source datum unverified'},
        'starts':starts,'unavailable_areas':[{'area':name,'reason':'No candidate passed source geometry checks'}
                                          for name in sorted(areas) if name not in best],
        'report':{'named_areas':len(areas),'available_areas':len(starts),
                  'screened_exported_roads':len(roads),'accepted_candidates':dict(accepted),
                  'exclusions':dict(failures),'road_checks':road_checks},
        'limitations':['Geometric screening against inferred-alignment source data is not a street survey or safety certification.',
            'Direction follows source traffic one-way tags; right-hand lane placement and lane widths remain modeled assumptions.',
            'Source ground and 0.12 m road lift are used; the runtime must raycast and validate the actual collision surface.',
            'Buildings are screened in 2D. Trees, parked vehicles, signs and other unmapped obstacles are not screened.',
            'The 30 m preview is a source-grounded approach, not an autonomous route or verified junction crossing.']}


def prepare_starts(batches_path,surface_path,districts_path,pack_path,traffic_path,output):
    batches=json.loads(Path(batches_path).read_text());surface=json.loads(Path(surface_path).read_text())
    if batches['origin_utm_m']!=surface['origin_utm_m']:raise ValueError('Mismatched city origins')
    districts=json.loads(Path(districts_path).read_text())
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    areas={feature['properties']['name']:shapely.make_valid(transform(project,shape(feature['geometry'])))
           for feature in districts['groups']['features']}
    ownership={}
    for job in batches['jobs']:
        if job['kind']!='roads':continue
        for record in json.loads(Path(job['metadata']).read_text())['objects']:
            identifier=int(record['metadata']['road_id'])
            if identifier in ownership:raise ValueError('Road appears in two authoring groups')
            ownership[identifier]=job['production_group']
    roads=json.loads(Path(surface['roads_metadata']).read_text())['roads']
    result=select_starts(Beirut001Pack.load(pack_path),TrafficModel.load(traffic_path),roads,ownership,areas,batches['origin_utm_m'])
    result['sources']={'city_batches':str(batches_path),'surface_manifest':str(surface_path),
                       'area_manifest':str(districts_path),'source_pack':str(pack_path),'traffic':str(traffic_path)}
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.partial');temporary.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('batches','surface','districts','pack','traffic','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=prepare_starts(args.batches,args.surface,args.districts,args.pack,args.traffic,args.output)
    print(json.dumps({'starts':[{k:v for k,v in s.items() if k in ('area','road_id','road_name','location_cm','yaw_degrees')} for s in result['starts']],
                      'unavailable_areas':result['unavailable_areas'],'output':args.output},indent=2))


if __name__=='__main__':main()
