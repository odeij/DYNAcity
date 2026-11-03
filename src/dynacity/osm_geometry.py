"""Small OSM geometry reader, including assembled multipolygon holes.

Incomplete or ambiguous rings are rejected, never closed with invented edges.
"""
from __future__ import annotations
import math


def finite_point(point):
    p=[point.get('lon'),point.get('lat')]
    return p if all(isinstance(v,(float,int)) and math.isfinite(v) for v in p) else None


def way_points(element, nodes):
    raw=element.get('geometry')
    if raw is None:
        refs=element.get('nodes',[])
        if not refs or any(n not in nodes for n in refs):
            return None
        raw=[nodes[n] for n in refs]
    points=[finite_point(p) for p in raw]
    return points if len(points)>=2 and all(p is not None for p in points) else None


def inside(point, ring):
    x,y=point; result=False
    for a,b in zip(ring,ring[1:]):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            result=not result
    return result


def _rings(segments):
    segments=[list(s) for s in segments]
    rings=[]
    while segments:
        chain=segments.pop(0)
        while chain[0]!=chain[-1]:
            matches=[(i,False) for i,s in enumerate(segments) if s[0]==chain[-1]]
            matches += [(i,True) for i,s in enumerate(segments) if s[-1]==chain[-1]]
            if len(matches)!=1:
                return None
            i,reverse=matches[0];piece=segments.pop(i)
            chain.extend((piece[::-1] if reverse else piece)[1:])
        if len(chain)<4:
            return None
        rings.append(chain)
    return rings


def osm_geometry(element, nodes, ways):
    if element.get('type')=='node':
        point=finite_point(element)
        return {'type':'Point','coordinates':point} if point else None
    if element.get('type')=='way':
        points=way_points(element,nodes)
        if not points:
            return None
        return {'type':'Polygon','coordinates':[points]} if len(points)>=4 and points[0]==points[-1] else {'type':'LineString','coordinates':points}
    if element.get('tags',{}).get('type')!='multipolygon':
        return None
    segments={'outer':[],'inner':[]}
    for member in element.get('members',[]):
        if member.get('type')!='way':
            continue
        role=member.get('role') or 'outer'
        if role not in segments:
            continue
        way=member if member.get('geometry') else ways.get(member.get('ref'),{})
        points=way_points(way,nodes)
        if not points:
            return None
        segments[role].append(points)
    outer=_rings(segments['outer']);holes=_rings(segments['inner'])
    if not outer or holes is None:
        return None
    polygons=[[ring] for ring in outer]
    area=lambda r:abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(r,r[1:])))
    for hole in holes:
        containers=[p for p in polygons if inside(hole[0],p[0])]
        if not containers:
            return None
        min(containers,key=lambda p:area(p[0])).append(hole)
    return {'type':'MultiPolygon','coordinates':polygons}
