"""Private, source-identifiable neighbourhood assets for Blender and game engines.

The interchange frame is metres, X east, Y north, Z up, relative to a recorded
UTM origin. This is not the Unreal (centimetres, left-handed) coordinate frame.
The asset exporter performs the format conversion; consumers must not repeat it.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import transform

from .beirut001_pack import Beirut001Pack, bridge_decks
from .traffic import TrafficModel, road_widths
from .viewer import lonlat_to_utm, utm_to_lonlat


def polygon_mesh(geometry, elevation, *, bottom=None):
    """Triangulate holes correctly, optionally extruding all boundary rings."""
    vertices, faces = [], []
    geometry = shapely.make_valid(geometry)
    parts = list(geometry.geoms) if hasattr(geometry,'geoms') else [geometry]
    for part in parts:
        if part.is_empty or not isinstance(part, Polygon):
            continue
        part = shapely.orient_polygons(part)
        for triangle in shapely.constrained_delaunay_triangles(part).geoms:
            coords = list(triangle.exterior.coords)[:3]
            start = len(vertices)
            vertices.extend([[x, y, float(elevation(x, y))] for x, y in coords])
            a,b=np.array(coords[1])-coords[0],np.array(coords[2])-coords[0]
            cross = a[0]*b[1]-a[1]*b[0]
            faces.append([start, start+1, start+2] if cross > 0 else [start+2, start+1, start])
            if bottom is not None:
                start = len(vertices)
                vertices.extend([[x, y, float(bottom(x, y))] for x, y in coords])
                faces.append([start+2, start+1, start] if cross > 0 else [start, start+1, start+2])
        if bottom is not None:
            for ring in [part.exterior, *part.interiors]:
                for a, b in zip(ring.coords, list(ring.coords)[1:]):
                    start = len(vertices)
                    vertices.extend([[*a[:2], float(bottom(*a[:2]))], [*b[:2], float(bottom(*b[:2]))],
                                     [*b[:2], float(elevation(*b[:2]))], [*a[:2], float(elevation(*a[:2]))]])
                    faces.extend([[start, start+1, start+2], [start, start+2, start+3]])
    return vertices, faces


def build_scene(pack, traffic, context, *, longitude, latitude, radius_m=350):
    if pack.meta['crs'] != 'EPSG:32636' or traffic.crs != 'EPSG:32636':
        raise ValueError('Prototype requires matching EPSG:32636 source coordinates')
    if pack.ground is None:
        raise ValueError('A model ground grid is required; no flat invented ground fallback')
    if not 50 <= radius_m <= 1500:
        raise ValueError('Prototype half-width must be between 50 and 1500 metres')
    cx, cy = lonlat_to_utm(longitude, latitude, 36, True)
    z0 = float(pack.ground.sample([cx], [cy])[0])
    if not math.isfinite(z0):
        raise ValueError('Selected origin has no source ground elevation')
    extent = box(cx-radius_m, cy-radius_m, cx+radius_m, cy+radius_m)
    origin = np.array([cx, cy, z0])
    meshes, trees, roads, skipped = [], [], [], Counter()

    def ground(x, y):
        z = float(pack.ground.sample([x], [y])[0])
        if not math.isfinite(z):
            raise ValueError('Missing terrain sample')
        return z

    def add(identifier, kind, geometry, top, bottom=None, **metadata):
        try:
            vertices, triangles = polygon_mesh(geometry, top, bottom=bottom)
        except (ValueError,shapely.errors.GEOSException) as exc:
            skipped[kind+('_invalid_geometry' if isinstance(exc,shapely.errors.GEOSException) else '_missing_ground')] += 1
            return
        if not triangles:
            return
        local = np.asarray(vertices)-origin
        meshes.append({'id':identifier, 'kind':kind, 'vertices':local.round(4).tolist(),
                       'triangles':triangles, 'metadata':metadata})

    # Keep entire source buildings at the boundary, rather than slicing their walls.
    for i, footprint in enumerate(pack.buildings):
        if not footprint.intersects(extent):
            continue
        base = float(pack.ground_z[i])
        if not math.isfinite(base):
            skipped['building_missing_ground'] += 1
            continue
        add('beirut001:'+str(pack.building_ids[i]), 'building', footprint,
            lambda x,y,i=i:float(pack.top_z[i]), lambda x,y,b=base:b,
            source_index=i, bbed_objectid=int(pack.bbed_objectid[i]),
            alignment='inferred; not surveyed', roof_levels=int(pack.roof_levels[i]),
            height_m=float(pack.heights()[i]), source='Beirut 001, January 2020')

    # Retain the source grid, never extrapolate the sea or gaps as zero ground.
    xs, ys, zs = pack.ground.centres(*extent.bounds)
    terrain_vertices, terrain_faces = [], []
    if xs.size:
        terrain_vertices = (np.stack([xs,ys,np.nan_to_num(zs,nan=z0)],axis=-1).reshape(-1,3)-origin).tolist()
        rows, cols = zs.shape
        for row in range(rows-1):
            for col in range(cols-1):
                if np.isfinite(zs[row:row+2,col:col+2]).all():
                    a=row*cols+col; b=a+1; c=a+cols; d=c+1
                    terrain_faces.extend([[a,c,b],[b,c,d]])
        meshes.append({'id':'source-ground','kind':'terrain','vertices':terrain_vertices,
                       'triangles':terrain_faces,'metadata':{'source':pack.ground.source,'cell_m':pack.ground.cell_m}})

    widths = road_widths(traffic)
    decks = bridge_decks(pack, traffic)
    # One height per shared graph node avoids disconnected ramp endpoints.
    node_lift = {}
    for i, line in enumerate(traffic.geometry):
        if traffic.level is not None and traffic.level[i] > 0:
            lift = decks.get(i, 5.5*int(traffic.level[i]))
            for node in (int(traffic.u[i]), int(traffic.v[i])):
                node_lift[node] = max(node_lift.get(node,0),lift)
    for i, coords in enumerate(traffic.geometry):
        line = LineString(coords)
        if not line.intersects(extent):
            continue
        level = int(traffic.level[i]) if traffic.level is not None else 0
        if level < 0:
            skipped['underground_road'] += 1
            continue
        start = node_lift.get(int(traffic.u[i]),0)
        end = node_lift.get(int(traffic.v[i]),0)

        def road_z(x,y,line=line,start=start,end=end):
            t=line.project(Point(x,y))/max(line.length,1e-6)
            return ground(x,y)+start+(end-start)*t+0.12

        path=[]
        # Sample both road geometry and terrain at <= 5 m spacing.
        for distance in np.linspace(0,line.length,max(2,math.ceil(line.length/5)+1)):
            p=line.interpolate(distance)
            try:
                path.append((np.array([p.x,p.y,road_z(p.x,p.y)])-origin).tolist())
            except ValueError:
                path=[];break
        if not path:
            skipped['road_missing_ground']+=1;continue
        roads.append({'id':i,'source_way':int(traffic.way_id[i]),'path':path,
                      'width_m':float(widths[i]),'level':level,'name':traffic.name[i] if traffic.name else '',
                      'height_source':'matched model deck' if i in decks else 'illustrative ramp/level offset' if start or end else 'source ground'})
        # Narrow strips follow sampled grades rather than triangulating across a long bend.
        for part,(a,b) in enumerate(zip(path,path[1:])):
            a,b=np.array(a)+origin,np.array(b)+origin
            strip=LineString([a[:2],b[:2]]).buffer(float(widths[i])/2,cap_style=2).intersection(extent)
            add(f'road:{i}:{part}','road',strip,road_z,
                (lambda x,y,f=road_z:f(x,y)-0.65) if start or end else None,
                road_id=i,source_way=int(traffic.way_id[i]),height_source=roads[-1]['height_source'])
        # Simple concrete support forms are explicitly illustrative, not surveyed piers.
        if level>0:
            for n,distance in enumerate(np.arange(12,line.length,28)):
                p=line.interpolate(distance)
                if not extent.contains(p):continue
                if road_z(p.x,p.y)-ground(p.x,p.y)<2:continue
                add(f'pier:{i}:{n}','concrete',p.buffer(0.8,quad_segs=4),
                    lambda x,y,f=road_z:f(x,y)-0.65,ground,source='illustrative support placement')

    def project(x,y,z=None):
        return lonlat_to_utm(x,y,36,True)
    for feature in context.get('features',[]):
        props=feature.get('properties',{}); geometry=feature.get('geometry')
        if not geometry or props.get('kind') not in ('tree','tree_row'):
            continue
        if geometry['type']!='Point':continue
        p=transform(project,shape(geometry))
        if extent.contains(p):
            try:z=ground(p.x,p.y)
            except ValueError:continue
            trees.append({'id':props['id'],'position':(np.array([p.x,p.y,z])-origin).tolist(),
                          'height_m':props.get('height_m') or 6,'metadata':props})
    west,south=utm_to_lonlat(cx-radius_m,cy-radius_m,36,True)
    east,north=utm_to_lonlat(cx+radius_m,cy+radius_m,36,True)
    return {'schema':'dynacity-engine-scene/1','coordinates':{'crs':'EPSG:32636','origin_utm_m':origin.tolist(),
            'origin_lonlat':[longitude,latitude],'axes':'X east, Y north, Z up','units':'metres',
            'unreal_axes':'X east, Y south, Z up; centimetres','vertical_datum':'source model datum undeclared'},
            'bounds_lonlat':[west,south,east,north],'half_width_m':radius_m,'meshes':meshes,'trees':trees,'roads':roads,
            'counts':dict(Counter(m['kind'] for m in meshes)), 'skipped':dict(skipped),
            'limitations':['Private Beirut 001 source; no redistribution rights assumed.',
                          'Inferred alignment; source dates differ; not a surveyed digital twin.',
                          'Road width, ramp offsets, support forms and tree forms are illustrative.',
                          'Initial geometry prototype; no surveyed facade materials or driving physics.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',required=True);parser.add_argument('--traffic',required=True)
    parser.add_argument('--context',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--longitude',type=float,default=35.49525)
    parser.add_argument('--latitude',type=float,default=33.87908)
    parser.add_argument('--radius-m',type=float,default=350)
    args=parser.parse_args()
    path=Path(args.output)
    if path.exists():raise FileExistsError(f'Refusing to replace existing export: {path}')
    scene=build_scene(Beirut001Pack.load(args.pack),TrafficModel.load(args.traffic),
                      json.loads(Path(args.context).read_text(encoding='utf-8')),
                      longitude=args.longitude,latitude=args.latitude,radius_m=args.radius_m)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(scene,allow_nan=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'output':str(path),'counts':scene['counts'],'trees':len(scene['trees']),
                      'roads':len(scene['roads']),'skipped':scene['skipped']}))


if __name__=='__main__':main()
