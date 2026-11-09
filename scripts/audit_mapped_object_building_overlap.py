"""Flag mapped point placements intersecting valid mapped OSM building outlines.

This compares source geometry only; it is not an Unreal mesh collision test.
"""
import argparse
import json
from pathlib import Path
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--manifests',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('Keep generated OSM data outside the repository')
    payload=json.loads(args.context.read_text(encoding='utf8'));polygons=[];identities=[];rejected=[]
    for feature in payload.get('features',[]):
        props=feature.get('properties',{});geometry=feature.get('geometry',{})
        if props.get('context_group')!='buildings' or props.get('kind')!='osm_building' or geometry.get('type') not in ('Polygon','MultiPolygon'):
            continue
        try: polygon=shape(geometry)
        except (TypeError,ValueError,KeyError):rejected.append(props.get('id'));continue
        if polygon.is_empty or not polygon.is_valid:rejected.append(props.get('id'));continue
        polygons.append(polygon);identities.append(props['id'])
    index=STRtree(polygons);summaries=[]
    for path in args.manifests:
        manifest=json.loads(path.read_text(encoding='utf8'));matches=[];unknown=[]
        for row in manifest.get('placements',[]):
            position=row.get('source_position_lonlat')
            if not isinstance(position,list) or len(position)!=2:
                unknown.append(row.get('source_id'));continue
            point=Point(position)
            hits=[identities[i] for i in index.query(point,predicate='intersects')]
            if hits:matches.append({'source_id':row['source_id'],'building_ids':hits})
        summaries.append({'manifest':str(path.resolve()),'total':len(manifest.get('placements',[])),
                          'overlap_count':len(matches),'overlaps':matches,'unknown_positions':unknown})
    result={'schema':'dynacity-osm-building-overlap-audit/1','context':str(args.context.resolve()),
            'building_footprints_indexed':len(polygons),'invalid_or_missing_building_geometry':len(rejected),
            'scope':'OSM source outlines only; not a test against the active Unreal building meshes',
            'manifests':summaries}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({s['manifest']:s['overlap_count'] for s in summaries}))

if __name__=='__main__':main()
