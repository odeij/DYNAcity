"""Export mapped OSM street details against the unchanged Beirut source terrain.

Coordinates are east/north/up metres about the common city origin. OSM nodes
are mapping abstractions, not surveys of poles; inferred curb offsets and tree
row spacing are labelled. Nothing here represents a live signal phase.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point, Polygon, LineString, shape
from shapely.ops import transform
from pyproj import Transformer

from .beirut001_pack import Beirut001Pack
from .landscape import landscape_payload
from .traffic import TrafficModel, road_widths
from .greenery import AREA_KINDS, POINT_KINDS

POINTS={'tree','traffic_signals','street_lamp','crossing','bus_stop','bench','waste_basket',
        'drinking_water','fountain','shelter','bicycle_parking','bollard','gate','lift_gate','block',
        'fire_hydrant'}
POINTS |= POINT_KINDS
COVERS={'wood','scrub','grass','park','garden','sand','pitch','playground','parking',
        'water','cemetery','orchard','allotments','farmland','brownfield','construction'}
COVERS |= AREA_KINDS | {'hedge'}
PATHS={'footway','pedestrian','path','steps','cycleway'}
LINES=PATHS|{'wall','fence','retaining_wall','embankment','pier','breakwater','waterway','railway','chain','jersey_barrier'}
LINES |= {'hedge'}
GREEN={'wood','scrub','grass','park','garden','orchard','allotments','farmland'}
GREEN |= AREA_KINDS - {'green_roof','planter','greenfield','greenhouse_horticulture'}


class TerrainSurface:
    """Exactly the two planar triangles used by engine_city_surface.terrain_mesh."""
    def __init__(self,ground):self.ground=ground

    def sample(self,x,y):
        g=self.ground;fc=(x-g.x0)/g.cell_m-.5;fr=(g.y0-y)/g.cell_m-.5
        c,r=math.floor(fc),math.floor(fr)
        if r<0 or c<0 or r>=g.ground.shape[0]-1 or c>=g.ground.shape[1]-1:return None
        a,b,e,d=map(float,(g.ground[r,c],g.ground[r,c+1],g.ground[r+1,c],g.ground[r+1,c+1]))
        if not all(math.isfinite(z) for z in (a,b,e,d)):return None
        u,v=fc-c,fr-r
        return a+(b-a)*u+(e-a)*v if u+v<=1 else d+(e-d)*(1-u)+(b-d)*(1-v)

    def drape(self,polygon,origin,lift=.03):
        """Clip each source terrain triangle, then triangulate its polygon pieces."""
        g=self.ground;lo_x,lo_y,hi_x,hi_y=polygon.bounds;rows,cols=g.ground.shape
        c0=max(0,math.floor((lo_x-g.x0)/g.cell_m-.5));c1=min(cols-2,math.floor((hi_x-g.x0)/g.cell_m-.5))
        r0=max(0,math.floor((g.y0-hi_y)/g.cell_m-.5));r1=min(rows-2,math.floor((g.y0-lo_y)/g.cell_m-.5))
        vertices=[];faces=[];missing=0
        for r in range(r0,r1+1):
            for c in range(c0,c1+1):
                z=[float(g.ground[r,c]),float(g.ground[r,c+1]),float(g.ground[r+1,c]),float(g.ground[r+1,c+1])]
                if not all(math.isfinite(v) for v in z):missing+=1;continue
                x=g.x0+(c+.5)*g.cell_m;y=g.y0-(r+.5)*g.cell_m;s=g.cell_m
                xy=[(x,y),(x+s,y),(x,y-s),(x+s,y-s)]
                for indices in ((0,2,1),(1,2,3)):
                    triangle=Polygon([xy[i] for i in indices])
                    if not triangle.intersects(polygon):continue
                    clipped=triangle.intersection(polygon)
                    if clipped.is_empty:continue
                    pieces=list(clipped.geoms) if hasattr(clipped,'geoms') else [clipped]
                    coords=np.array([xy[i] for i in indices]);heights=np.array([z[i] for i in indices])
                    matrix=np.column_stack([coords,np.ones(3)])
                    plane=np.linalg.solve(matrix,heights)
                    for piece in pieces:
                        if not isinstance(piece,Polygon) or piece.area<1e-6:continue
                        for tri in shapely.constrained_delaunay_triangles(piece).geoms:
                            points=list(tri.exterior.coords)[:3];start=len(vertices)
                            vertices.extend([[round(px-origin[0],4),round(py-origin[1],4),
                                round(plane[0]*px+plane[1]*py+plane[2]+lift-origin[2],4)] for px,py in points])
                            cross=(points[1][0]-points[0][0])*(points[2][1]-points[0][1])-(points[1][1]-points[0][1])*(points[2][0]-points[0][0])
                            faces.append([start,start+1,start+2] if cross>0 else [start,start+2,start+1])
        return vertices,faces,missing


def parse_heading(value):
    """OSM geographic bearing (north=0 clockwise) to local east=0 north=90."""
    cardinals={'N':0,'NE':45,'E':90,'SE':135,'S':180,'SW':225,'W':270,'NW':315}
    raw=str(value or '').strip().upper()
    try:bearing=float(cardinals[raw] if raw in cardinals else raw)
    except ValueError:return None
    return (90-bearing)%360 if math.isfinite(bearing) else None


def arrow_tag(tags):
    """Only explicit signal-arrow tags; generic lane turns are not signal heads."""
    values=[]
    for key in ('traffic_signals:arrow','traffic_signals:arrows','traffic_signals:direction','traffic_signals:turn'):
        values.extend(str(tags.get(key,'')).lower().replace('|',';').split(';'))
    left='left' in values;right='right' in values
    return 'left_right' if left and right else 'left' if left else 'right' if right else None


def build_street_context(context,pack,traffic,origin,districts=None,visual_ground=None):
    if pack.ground is None:raise ValueError('Source terrain required; no flat fallback')
    origin=np.asarray(origin,dtype=float)
    if origin.shape!=(3,) or not np.isfinite(origin).all():raise ValueError('Invalid shared city origin')
    payload=context if context.get('type')=='FeatureCollection' else landscape_payload(context)
    original_surface=TerrainSurface(pack.ground)
    surface=TerrainSurface(visual_ground or pack.ground);project=Transformer.from_crs(4326,32636,always_xy=True).transform
    road_lines=[LineString(coords) for coords in traffic.geometry]
    road_tree=shapely.STRtree(road_lines);widths=road_widths(traffic)
    ground_indices=[i for i in range(len(road_lines)) if int(traffic.level[i])==0]
    ground_tree=shapely.STRtree([road_lines[i] for i in ground_indices])
    # Ground cover must not paint over the existing carriageway. Widths are lane-derived.
    road_buffers=[line.buffer(float(width)/2+.2,cap_style=2) if int(traffic.level[i])==0 else Polygon()
                  for i,(line,width) in enumerate(zip(road_lines,widths))]
    buffer_tree=shapely.STRtree(road_buffers)
    owners=[]
    if districts:
        owners=[(f['properties']['name'],shapely.make_valid(transform(project,shape(f['geometry']))))
                for f in districts['groups']['features']]
    def owner(geometry):
        centre=geometry.representative_point()
        matches=[name for name,p in owners if p.covers(centre)]
        if matches:return {'production_area':sorted(matches)[0],'ownership_source':'inside named source sector'}
        if owners:return {'production_area':min(owners,key=lambda item:(item[1].distance(centre),item[0]))[0],
                          'ownership_source':'nearest named source sector; outside source polygon'}
        return {'production_area':'Unassigned','ownership_source':'no source sectors supplied'}
    points=[];areas=[];lines=[];unplaced_points=[];feature_audit=[];infrastructure_references=[];skipped=Counter();source_counts=Counter();fixture_counts=Counter()
    for feature in payload['features']:
        props=feature['properties'];kind=props['kind'];tags=props.get('tags',{})
        bridge=tags.get('bridge') not in (None,'no','false','0')
        infrastructure=props.get('context_group')=='infrastructure'
        if kind not in POINTS|COVERS|LINES and not bridge and not infrastructure:continue
        audit={'id':props['id'],'instance_id':props.get('instance_id',props['id']),'kind':kind,
               'status':'skipped','reason':'unsupported_geometry'}
        feature_audit.append(audit)
        source_counts[kind]+=1
        if bridge:source_counts['bridge_tagged_features']+=1
        geometry=shapely.make_valid(transform(project,shape(feature['geometry'])))
        if geometry.is_empty:skipped[kind+':empty_geometry']+=1;continue
        base={'id':props['id'],'kind':kind,'name':props.get('name',kind),'tags':tags,
              'source':'OpenStreetMap contributors','as_of':props.get('as_of',payload.get('as_of')),
              'retrieved_on':props.get('retrieved_on',payload.get('retrieved_on')),**owner(geometry)}
        if infrastructure:
            # Keep the actual OSM geometry and tags even when there is no
            # finished model or it cannot be draped to the available terrain.
            # Non-point geometries are references, never fabricated fixtures.
            infrastructure_references.append({**base,'geometry_lonlat':feature['geometry'],
                'representation':'source reference; a model and native import are not implied'})
        if geometry.geom_type=='Point' and (kind in POINTS or infrastructure):
            x,y=geometry.x,geometry.y
            nearest=int(road_tree.nearest(geometry)) if road_lines else None
            if nearest is not None and kind in {'traffic_signals','street_lamp','crossing'} and int(traffic.level[nearest])!=0:
                ground_nearest=ground_indices[int(ground_tree.nearest(geometry))] if ground_indices else None
                if ground_nearest is not None and road_lines[ground_nearest].distance(geometry)<=20:nearest=ground_nearest
            row={**base,'instance_id':props.get('instance_id',props['id']),
                 'source_position_lonlat':feature['geometry']['coordinates'],
                 'position_source':props.get('position_source') or 'OSM mapped node; physical fixture position unverified',
                 'height_m':props.get('height_m') or (6 if kind=='tree' else 4.5 if kind=='street_lamp' else 3.8 if kind=='traffic_signals' else 1),
                 'height_source':props.get('height_source') or 'illustrative default',
                 'species':props.get('species') or tags.get('species'),
                 'crown_diameter_m':props.get('crown_diameter_m'),
                 'leaf_type':props.get('leaf_type') or tags.get('leaf_type'),
                 'heading_degrees':0.,'heading_source':'illustrative default; no orientation evidence'}
            if nearest is not None:
                road=road_lines[nearest];d=road.project(geometry);a=road.interpolate(max(0,d-.5));b=road.interpolate(min(road.length,d+.5))
                dx,dy=b.x-a.x,b.y-a.y;norm=math.hypot(dx,dy)
                heading=math.degrees(math.atan2(dy,dx))%360
                row.update(road_id=nearest,road_name=traffic.name[nearest] if getattr(traffic,'name',None) else '',
                           road_width_m=float(widths[nearest]),road_heading_degrees=heading,
                           heading_degrees=heading,heading_source='nearest source road tangent; inferred',
                           road_level=int(traffic.level[nearest]),road_lift_source='No bridge lift inferred for mapped point')
                if int(traffic.level[nearest])!=0 and kind in {'traffic_signals','crossing'}:
                    skipped[kind+':elevated_or_underground_nearest_road']+=1;continue
                if kind in {'traffic_signals','street_lamp'} and norm>0 and road.distance(geometry)<float(widths[nearest])/2+.3:
                    at=road.interpolate(d);side=float(widths[nearest])/2+.65
                    x,y=at.x+dy/norm*side,at.y-dx/norm*side
                    row['position_source']='Inferred right curb of nearest source road; OSM source node retained separately'
                    row['curb_offset_m']=side
                if kind=='traffic_signals':row['heading_degrees']=(heading+180)%360;row['heading_source']='Upstream-facing nearest road tangent; inferred'
            tagged=parse_heading(tags.get('traffic_signals:direction') or tags.get('direction'))
            if tagged is not None:row['heading_degrees']=tagged;row['heading_source']='OSM numeric/cardinal direction tag; unverified'
            elif tags.get('traffic_signals:direction') in {'forward','backward'} and nearest is not None:
                row['heading_degrees']=(heading+(180 if tags['traffic_signals:direction']=='forward' else 0))%360
                row['heading_source']='OSM relative direction interpreted against inferred nearest road orientation'
            if kind=='traffic_signals':
                row.update(arrow=arrow_tag(tags),signal_phase='unknown; no live signal data',
                           pole_geometry_source='illustrative fixture; node is not a measured pole survey')
            if kind=='crossing':
                row['crossing_style']=tags.get('crossing:markings') or tags.get('crossing') or 'unknown'
                row['paint_markings']=(row['crossing_style'] in {'zebra','marked','uncontrolled','yes','traffic_signals'}
                    and tags.get('crossing:markings')!='no' and tags.get('crossing') not in {'no','unmarked','informal'})
            z=surface.sample(x,y)
            if z is None:
                skipped[kind+':missing_source_ground']+=1
                unplaced_points.append({**row,'reason':'Missing source ground; no 3D placement generated'})
                audit['reason']='missing_ground_after_extension'
                continue
            row['position_local_m']=[round(x-origin[0],4),round(y-origin[1],4),round(z-origin[2],4)]
            row['elevation_source']=('Source terrain triangle interpolation; source vertical datum unverified'
                if original_surface.sample(x,y) is not None else
                'Approximate Copernicus ground extension; filtered DSM and inferred alignment, not surveyed')
            audit.update(status='exported',reason=None,elevation_source=row['elevation_source'])
            points.append(row);fixture_counts[kind]+=1
        elif infrastructure and kind not in COVERS|LINES:
            audit.update(status='reference_only',reason='No supported physical geometry or finished asset')
        elif geometry.geom_type in ('Polygon','MultiPolygon') and kind in COVERS|PATHS:
            source_area=geometry.area
            if kind=='green_roof':
                # A green roof is above the ground. Use an overlapping existing model roof.
                matches=[(building.intersection(geometry).area,i) for i,building in enumerate(pack.buildings)
                         if building.intersects(geometry)]
                if not matches:
                    audit['reason']='no_matching_source_roof';skipped[kind+':no_matching_source_roof']+=1;continue
                overlap,i=max(matches)
                if overlap < .5*geometry.area:
                    audit['reason']='ambiguous_source_roof';skipped[kind+':ambiguous_source_roof']+=1;continue
                roof=geometry.intersection(pack.buildings[i]);vertices=[];triangles=[]
                for tri in shapely.constrained_delaunay_triangles(roof).geoms:
                    start=len(vertices)
                    vertices.extend([[x-origin[0],y-origin[1],float(pack.top_z[i])+.04-origin[2]] for x,y in list(tri.exterior.coords)[:3]])
                    triangles.append([start,start+1,start+2])
                areas.append({**base,'vertices_local_m':vertices,'triangles':triangles,'lift_m':.04,
                    'source_area_m2':source_area,'rendered_area_m2':roof.area,
                    'drape_source':'Matched existing source building roof; OSM footprint alignment unverified'})
                audit.update(status='exported',reason=None,source_area_m2=source_area,rendered_area_m2=roof.area)
                continue
            if kind in GREEN:
                indices=buffer_tree.query(geometry,predicate='intersects')
                if len(indices):geometry=geometry.difference(shapely.union_all([road_buffers[int(i)] for i in indices]))
            if geometry.is_empty:
                skipped[kind+':entirely_masked_by_roads']+=1;audit['reason']='entirely_masked_by_inferred_road_width';continue
            vertices,triangles,missing=surface.drape(geometry,origin)
            if not triangles:
                skipped[kind+':missing_source_ground']+=1;audit['reason']='missing_ground_after_extension';continue
            vv=np.asarray(vertices);ff=np.asarray(triangles)
            edge_a=vv[ff[:,1],:2]-vv[ff[:,0],:2];edge_b=vv[ff[:,2],:2]-vv[ff[:,0],:2]
            rendered_area=float(np.abs(edge_a[:,0]*edge_b[:,1]-edge_a[:,1]*edge_b[:,0]).sum()/2)
            audit.update(status='exported',reason=None,source_area_m2=source_area,
                road_masked_area_m2=source_area-geometry.area,rendered_area_m2=rendered_area,
                unrendered_ground_area_m2=max(0,geometry.area-rendered_area))
            areas.append({**base,'vertices_local_m':vertices,'triangles':triangles,'lift_m':.03,
                'drape_source':'Clipped source terrain triangles; no cross-cell interpolation',
                'rendering_mask':'Lane-derived ground road buffers subtracted' if kind in GREEN else 'mapped footprint',
                'missing_terrain_cells':missing,'source_area_m2':round(geometry.area,3),
                'rendered_area_m2':round(rendered_area,3),
                'elevation_source':'Source terrain with explicitly approximate extension where source is absent' if visual_ground else 'Source terrain'})
        elif geometry.geom_type in ('LineString','MultiLineString') and (kind in LINES or bridge):
            parts=list(geometry.geoms) if hasattr(geometry,'geoms') else [geometry]
            for i,line in enumerate(parts):
                sampled=[];missing=False
                for distance in np.linspace(0,line.length,max(2,math.ceil(line.length/3)+1)):
                    point=line.interpolate(distance);z=surface.sample(point.x,point.y)
                    if z is None:missing=True;break
                    sampled.append([round(point.x-origin[0],4),round(point.y-origin[1],4),round(z-origin[2],4)])
                if missing:
                    skipped[kind+':line_missing_source_ground']+=1
                    audit['reason']='line_missing_ground_after_extension'
                    if bridge:
                        lines.append({**base,'instance_id':props['id']+'#'+str(i),'points_local_m':[],
                            'source_points_local_xy_m':[[round(x-origin[0],4),round(y-origin[1],4)] for x,y in line.coords],
                            'bridge_tagged':True,'render_geometry':False,
                            'elevation_source':'Source ground unavailable; bridge reference retained without invented deck elevation'})
                    continue
                lines.append({**base,'instance_id':props['id']+'#'+str(i),'points_local_m':sampled,
                    'width_m':props.get('width_m') or (1.8 if kind in PATHS else .2),
                    'width_source':props.get('width_source') or 'illustrative fallback',
                    'bridge_tagged':bridge,'render_geometry':not bridge and kind!='steps',
                    'elevation_source':'Source terrain; bridge-tagged lines are provenance only, not deck elevations'})
                audit.update(status='exported',reason=None)
                if kind in PATHS-{'steps'} and not bridge:
                    width=props.get('width_m') or 1.8
                    strip=line.buffer(width/2,cap_style=2)
                    vertices,triangles,missing_cells=surface.drape(strip,origin)
                    if triangles:
                        areas.append({**base,'instance_id':props['id']+'#strip'+str(i),
                            'vertices_local_m':vertices,'triangles':triangles,'lift_m':.03,'width_m':width,
                            'width_source':props.get('width_source') or 'illustrative fallback',
                            'drape_source':'Clipped source terrain triangles; source line buffered with tagged/default width',
                            'rendering_mask':'mapped path buffered to display width','missing_terrain_cells':missing_cells,
                            'source_area_m2':round(strip.area,3)})
    signal_ids={p['id'] for p in points if p['kind']=='traffic_signals'}
    references=[]
    for row in points:
        if row['kind']!='traffic_signals':continue
        centre=np.array(row['position_local_m'][:2]);near=[p for p in points if np.linalg.norm(np.array(p['position_local_m'][:2])-centre)<250]
        counts=Counter(p['kind'] for p in near)
        score=counts['traffic_signals']*4+counts['tree']+counts['bus_stop']*2
        references.append({'signal_source_id':row['id'],'production_area':row['production_area'],
            'position_local_m':row['position_local_m'],'nearby_mapped_counts':dict(counts),'score':score})
    reference=max(references,key=lambda r:(r['score'],r['signal_source_id'])) if references else None
    # Prefer the named street/garden pair selected for the first detailed street.
    garden=next((area for area in areas if area['id']=='way/264735045'),None)
    if garden is not None:
        midpoint=Point(*project(35.5218280509,33.8919873454));z=surface.sample(midpoint.x,midpoint.y)
        if z is not None:
            index=int(road_tree.nearest(midpoint));line=road_lines[index];d=line.project(midpoint)
            a=line.interpolate(max(0,d-.5));b=line.interpolate(min(line.length,d+.5))
            reference={'name':'Charles Malek Avenue reference corridor','secondary_garden_source_id':garden['id'],
                'source_position_lonlat':[35.5218280509,33.8919873454],
                'position_local_m':[midpoint.x-origin[0],midpoint.y-origin[1],z-origin[2]],
                'road_id':index,'road_name':traffic.name[index] if getattr(traffic,'name',None) else '',
                'heading_degrees':math.degrees(math.atan2(b.y-a.y,b.x-a.x))%360,**owner(midpoint),
                'selection_source':'Named source street beside mapped garden; selected reference, not a new survey'}
    return {'schema':'dynacity-engine-street-context/1','origin_utm_m':origin.tolist(),
        'coordinates':'metres; X east, Y north, Z up; consumer performs Unreal conversion once',
        'points':points,'unplaced_points':unplaced_points,'areas':areas,'lines':lines,'source_counts':dict(source_counts),
        'infrastructure_references':infrastructure_references,
        'feature_audit':feature_audit,'greenery_inventory':payload.get('greenery_inventory',[]),
        'source_rows':payload.get('source_rows',[]),
        'counts':{'points':dict(fixture_counts),'area_features':len(areas),'line_features':len(lines),
                  'source_signal_nodes':source_counts['traffic_signals'],'represented_signal_nodes':len(signal_ids),
                  'source_tree_nodes':payload.get('tree_nodes'),'source_tree_rows':payload.get('tree_rows')},
        'skipped':dict(skipped),'reference_street':reference,
        'limitations':['Only features in the available OSM extraction; real-world completeness is unknown.',
            'No point clouds used. Ground and source model alignment remain inferred, not surveyed.',
            'Poles, signal orientation, dimensions and tree-row spacing include explicit illustrative inference.',
            'Green areas do not imply individually mapped trees. No live signal phase is inferred.',
            'Bridge-tagged lines are provenance only; existing source bridge structures must not be duplicated.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('context','pack','traffic','city','districts','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--visual-ground',help='Explicit approximate elevation extension; source pack remains unchanged')
    parser.add_argument('--infrastructure-only',action='store_true',
        help='Export classified infrastructure from a display FeatureCollection as a supplemental manifest')
    args=parser.parse_args()
    read=lambda path:json.loads(Path(path).read_text(encoding='utf-8'))
    from .terrain import GroundModel
    context=read(args.context)
    if args.infrastructure_only:
        if context.get('type')!='FeatureCollection':parser.error('--infrastructure-only needs a display FeatureCollection')
        context={**context,'features':[f for f in context['features'] if f.get('properties',{}).get('context_group')=='infrastructure'],
                 'tree_nodes':None,'tree_rows':None,'greenery_inventory':[],'source_rows':[]}
    result=build_street_context(context,Beirut001Pack.load(args.pack),TrafficModel.load(args.traffic),
        read(args.city)['origin_utm_m'],read(args.districts),
        GroundModel.load(args.visual_ground) if args.visual_ground else None)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    result['inputs']={name:str(Path(getattr(args,name)).resolve()) for name in ('context','pack','traffic','city','districts')}
    if args.visual_ground:result['inputs']['visual_ground']=str(Path(args.visual_ground).resolve())
    if args.infrastructure_only:result['inputs']['scope']='classified infrastructure only; supplemental export'
    temporary=output.with_suffix('.partial')
    temporary.write_text(json.dumps(result,separators=(',',':'),allow_nan=False),encoding='utf-8')
    temporary.replace(output)
    print(json.dumps({'output':str(output),'counts':result['counts'],'skipped':result['skipped'],'reference_street':result['reference_street']},indent=2))


if __name__=='__main__':main()
