"""Build cached street inventories, proposals, instance manifests and exception reports.

Uses active city metadata, original building triangles, cached OSM and optional
Overture segments. Overture buildings never enter the pipeline.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import transform
from pyproj import Transformer

from dynacity.engine_city_batches import Glb
from dynacity.engine_street_context import TerrainSurface
from dynacity.terrain import GroundModel
from dynacity.street_detail import TYPES, VERSION, digest, plan, validate


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def building_footprints(checkpoint, region):
    """Reuse active source triangles only, excluding all Overture/context buildings."""
    rows = []
    for package in checkpoint['packages']:
        if not package['key'].startswith('batch-buildings-'): continue
        index = Path(package['asset']).with_suffix('.objects.json')
        items = []
        for item in read(index)['objects']:
            low, high = item['local_bounds_m']
            from shapely.geometry import box
            if box(low[0],low[1],high[0],high[1]).intersects(region): items.append(item)
        if not items: continue
        nodes = {node['name']: (np.column_stack((pos[:,0],-pos[:,2],pos[:,1])), faces)
                 for node, _, pos, _, faces in Glb(Path(package['asset'])).objects()}
        for item in items:
            vertices, faces = nodes[item['batch']]
            triangles = vertices[faces[item['first_triangle']:item['first_triangle']+item['triangle_count']]]
            polygons = [Polygon(triangle[:,:2]) for triangle in triangles]
            polygon = shapely.union_all([p for p in polygons if p.area>1e-8])
            if not polygon.is_empty:
                rows.append({'id': str(item['source_id']), 'geometry': mapping(polygon)})
    return rows


def overture_segments(path, origin, region):
    if not path: return []
    data = read(path)
    if data.get('overture_type') not in (None,'segment'):
        raise ValueError('Only Overture transportation segments may enrich roads')
    project = Transformer.from_crs(4326,32636,always_xy=True).transform
    rows = []
    for feature in data.get('features', []):
        if feature['geometry']['type'] != 'LineString': continue
        g = transform(project,shape(feature['geometry']))
        g = shapely.affinity.translate(g,-origin[0],-origin[1])
        if not g.intersects(region): continue
        props=feature['properties']; sources=props.get('sources',[]) or []
        ids=[]
        for source in sources:
            record=str(source.get('record_id',''))
            if 'openstreetmap' in str(source).lower() and '/way/' in record:
                ids.append(record.split('/way/')[-1].split('/')[0])
        # Keep only unambiguous scalar measurements; range-qualified widths are
        # recorded as available attributes, never collapsed to invented metres.
        attrs={key:props[key] for key in ('width','surface','lanes') if isinstance(props.get(key),(str,int,float))}
        rows.append({'id': props['id'], 'type': 'segment', 'osm_way_ids': ids,
                     'attributes': attrs, 'geometry': mapping(g),
                     'available_fields': sorted(props)})
    return rows


def run(config_path):
    started=time.perf_counter(); config=read(config_path); output=Path(config['output'])
    output.mkdir(parents=True,exist_ok=True)
    checkpoint=read(config['city_checkpoint']); metadata=read(checkpoint['roads_metadata'])
    origin=checkpoint['origin_utm_m']
    if metadata['coordinates']['origin_utm_m'] != origin: raise ValueError('City origin mismatch')
    osm={e['id']:e.get('tags',{}) for e in read(config['osm_roads'])['elements'] if e['type']=='way'}
    roads=[dict(r,tags=osm.get(r['source_way'],{})) for r in metadata['roads']]
    surface=TerrainSurface(GroundModel.load(config['ground']))
    def sample(x,y):
        z=surface.sample(x+origin[0],y+origin[1])
        return None if z is None else z-origin[2]
    overrides=read(config['overrides']) if Path(config['overrides']).exists() else {'streets':{},'features':{}}
    rules=config.get('rules',TYPES); results=[]; changed=[]; cache_hits=[]
    if len({s['id'] for s in config['streets']})!=len(config['streets']): raise ValueError('Duplicate street ID')
    batch_roads={};batch_buildings={};batch_features=[]
    old=read(output/'manifest.json') if (output/'manifest.json').exists() else {'streets':[]}
    previous={r['street_id']:r for r in old['streets']}
    # Code participates in cache identity; edits cannot reuse stale proposals.
    from dynacity import street_detail
    code=digest([Path(__file__).read_text(),Path(street_detail.__file__).read_text()])
    context=read(config['osm_context']) if config.get('osm_context') else {'elements':[]}
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    mapped=[]
    for element in context.get('elements',[]):
        if element.get('type')!='node' or 'lon' not in element: continue
        x,y=project(element['lon'],element['lat']);tags=element.get('tags',{})
        mapped.append({'id':'node/'+str(element['id']),'position':[x-origin[0],y-origin[1]],'tags':tags})
    for street in config['streets']:
        selected=[r for r in roads if r['id'] in street['road_ids']]
        region=shapely.union_all([LineString(r['path']).buffer(15) for r in selected])
        nearby=[r for r in roads if LineString(r['path']).intersects(region)]
        batch_roads.update({str(r['id']):r for r in nearby})
        local_mapped=[r for r in mapped if region.intersects(Point(r['position']))]
        entrances=[r['position'] for r in local_mapped if r['tags'].get('entrance') not in (None,'no')]
        # Only the relevant terrain window invalidates this street's cache.
        g=surface.ground;lo_x,lo_y,hi_x,hi_y=region.bounds
        c0=max(0,int((lo_x+origin[0]-g.x0)/g.cell_m)-2);c1=min(g.ground.shape[1],int((hi_x+origin[0]-g.x0)/g.cell_m)+3)
        r0=max(0,int((g.y0-hi_y-origin[1])/g.cell_m)-2);r1=min(g.ground.shape[0],int((g.y0-lo_y-origin[1])/g.cell_m)+3)
        values=g.ground[r0:r1,c0:c1]
        ground_hash=__import__('hashlib').sha256(np.nan_to_num(values).tobytes()+np.isfinite(values).tobytes()).hexdigest()
        ground_key=[ground_hash,g.x0,g.y0,g.cell_m,g.crs,r0,c0,origin]
        # Cache source building projections by input bytes and region, separately
        # from the cheaper layout cache.
        indexes=[p for p in checkpoint['packages'] if p['key'].startswith('batch-buildings-')]
        projection_key=digest([region.wkt,[(p['asset'],__import__('hashlib').sha256(Path(p['asset']).read_bytes()).hexdigest(),
                              __import__('hashlib').sha256(Path(p['asset']).with_suffix('.objects.json').read_bytes()).hexdigest()) for p in indexes]])
        projection_file=output/'cache'/('buildings-'+projection_key+'.json')
        if projection_file.exists(): buildings=read(projection_file)
        else:
            buildings=building_footprints(checkpoint,region);write(projection_file,buildings)
        batch_buildings.update({b['id']:b for b in buildings})
        overture=overture_segments(config.get('overture_segments'),origin,region)
        specific={'streets':{k:v for k,v in overrides.get('streets',{}).items() if k.split('/')[0] in {str(r['id']) for r in selected}},
                  'features':{k:v for k,v in overrides.get('features',{}).items() if k.startswith(street['id']+'/')}}
        key=digest([VERSION,code,street,nearby,buildings,ground_key,rules[street['type']],specific,overture,local_mapped])
        cached=output/'cache'/(street['id']+'-'+key+'.json')
        if cached.exists(): result=read(cached);cache_hits.append(street['id'])
        else:
            result=plan(street,nearby,buildings,sample,rules=rules,overrides=specific,overture=overture,entrances=entrances)
            result['overture_available_records']=len(overture)
            result['original_buildings_checked']=len(buildings)
            result['mapped_street_assets']=local_mapped
            result['entrance_nodes']=len(entrances)
            write(cached,result)
        if previous.get(street['id'],{}).get('signature')!=key: changed.append(street['id'])
        write(output/(street['id']+'.json'),result)
        batch_features.extend(result['features'])
        results.append({'street_id':street['id'],'signature':key,'file':str((output/(street['id']+'.json')).resolve()),
                        'counts':result['counts'],'exceptions':len(result['exceptions']),'failures':len(result['checks']['failures'])})
    batch_checks=validate(batch_features,
        shapely.union_all([LineString(r['path']).buffer(r['width_m']/2,cap_style=2) for r in batch_roads.values() if r.get('level',0)==0]),
        shapely.union_all([shape(b['geometry']) for b in batch_buildings.values()]),sample)
    manifest={'schema':VERSION,'origin_utm_m':origin,'streets':results,'changed_streets':changed,'batch_checks':batch_checks,
              'removed_streets':sorted(set(previous)-{s['street_id'] for s in results}),
              'cache_hits':cache_hits,'elapsed_s':time.perf_counter()-started,
              'source_checkpoint':config['city_checkpoint'],'assets':config.get('assets',{}),
              'surface_finishes':config.get('surface_finishes'),
              'overrides_path':config['overrides'],'overture_buildings':'excluded',
              'photo_comparison':'pending; no dated street photographs supplied',
              'performance':{'editor_time_s':None,'frame_ms':None,'status':'requires native before/after capture'}}
    write(output/'manifest.json',manifest)
    counts={}
    for row in results:
        for k,v in row['counts'].items(): counts[k]=counts.get(k,0)+v
    report=['# Street detail pilot','',f"Generated proposal instances: {counts}",
            f"Changed streets: {changed}; cached streets: {cache_hits}",
            '', 'Original road, terrain and building geometry retained. Overture buildings excluded.',
            'All missing widths use explicitly labeled defaults. Layouts are proposals, not surveys.',
            '','| Street | Instances | Exceptions | Failed checks |','|---|---:|---:|---:|']
    for row in results:
        data=read(row['file']);report.append(f"| {data['street']['name']} | {sum(row['counts'].values())} | {row['exceptions']} | {row['failures']} |")
    for row in results:
        data=read(row['file']);report.extend(['',data['street']['name']+': '+str(dict(__import__('collections').Counter(e['reason'] for e in data['exceptions']))),''])
    report.append('Batch overlap/ground validation failures: '+str(len(batch_checks['failures'])))
    report.extend(['Photograph comparison and native save/reload/performance remain pending until recorded.',
                   'Intersection continuity exceptions prevent district expansion.'])
    (output/'exceptions.md').write_text('\n'.join(report),encoding='utf8')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True)
    print(json.dumps(run(p.parse_args().config),indent=2))
