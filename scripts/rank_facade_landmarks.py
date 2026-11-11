"""Prioritize photo candidates with uniquely nearby named map landmarks."""
import json
from pathlib import Path
from collections import defaultdict
from shapely.geometry import Point,shape
from shapely.strtree import STRtree
from pyproj import Transformer

base=Path('C:/private/dynacity-data/engine')
root=base/'facade-batch-evidence-20261030'
collection=Path(json.loads((base/'appearance-citywide-20261030/latest-run.json').read_text())['run_directory'])
records=json.loads((collection/'inventory.json').read_text(encoding='utf-8'))['records']
polygons=[shape(r['footprint']) for r in records];tree=STRtree(polygons)
osm=Path('C:/private/dynacity-data/traffic/osm-beirut-context-sources/places.json')
nodes=json.loads(osm.read_text(encoding='utf-8'))['elements'];transform=Transformer.from_crs('EPSG:4326','EPSG:32636',always_xy=True)
anchors=defaultdict(list)
for node in nodes:
    tags=node.get('tags',{});name=tags.get('name:en') or tags.get('name:fr') or tags.get('name')
    if node.get('type')!='node' or 'lon' not in node or not name:continue
    point=Point(transform.transform(node['lon'],node['lat']))
    near=sorted((polygons[int(i)].distance(point),int(i)) for i in tree.query(point.buffer(25)))
    if len(near)<2 or near[0][0]>5 or near[1][0]-near[0][0]<8:continue
    anchors[records[near[0][1]]['source_id']].append({'name':name,'osm_node':node['id'],'distance_m':round(near[0][0],2),'next_building_m':round(near[1][0],2)})
reviewed={p['photo_id'] for p in json.loads((root/'review-decisions.json').read_text(encoding='utf-8'))['photos']}
results=[]
for file in sorted((root/'all-batches').glob('pass-*.json')):
    data=json.loads(file.read_text(encoding='utf-8'))
    for record in data['records']:
        for candidate in record['candidates']:
            if candidate['photo_cached'] and candidate['photo_id'] not in reviewed and record['source_id'] in anchors:
                results.append({'source_id':record['source_id'],'district':record['district'],'photo_id':candidate['photo_id'],
                                'rank_cost':candidate['rank_cost'],'anchors':anchors[record['source_id']],
                                'status':'landmark_candidate_requires_visual_confirmation'})
results.sort(key=lambda r:(r['rank_cost'],r['photo_id'],r['source_id']))
out=root/'reviewed'/'landmark-priority.json'
out.write_text(json.dumps({'candidates':results,'osm_source':str(osm),'limits':['Named point near a building does not prove that its sign appears in the photograph.','Historical photos and map timestamps may differ.']},indent=2),encoding='utf-8')
print(json.dumps({'candidate_pairs':len(results),'photos':len({r['photo_id'] for r in results}),'output':str(out)}))
