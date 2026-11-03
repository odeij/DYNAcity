"""Refresh all greenery selectors with bounded POST queries; retain old non-green context separately."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import urllib.parse
import urllib.request
from fetch_osm_context import GROUPS


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();base=json.loads(Path(args.base).read_text(encoding='utf-8'))
    output=Path(args.output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):p.error('Keep raw OSM outside Git')
    output.parent.mkdir(parents=True,exist_ok=True)
    west,south,east,north=base['query_bbox'];box=f'({south},{west},{north},{east})'
    selectors=GROUPS['landscape']+['nwr["barrier"~"^(hedge|hedge_bank)$"]']
    groups=[selectors[i:i+4] for i in range(0,len(selectors),4)]
    def fetch(pair):
        i,selectors=pair;query='[out:json][timeout:40];('+''.join(s+box+';' for s in selectors)+');out body geom;'
        cache=output.parent/f'greenery-source-{i}.json'
        if cache.exists():
            data=json.loads(cache.read_text(encoding='utf-8'))
            if data.get('query')==query:return data
        errors=[]
        for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter'):
            try:
                req=urllib.request.Request(endpoint,data=urllib.parse.urlencode({'data':query}).encode(),
                    headers={'User-Agent':'DYNAcity-Beirut-greenery/1.0'})
                with urllib.request.urlopen(req,timeout=55) as response:data=json.load(response)
                if data.get('remark') or not isinstance(data.get('elements'),list):raise ValueError(data.get('remark','invalid response'))
                data.update(query=query,endpoint=endpoint,retrieved_on=datetime.now(timezone.utc).isoformat())
                cache.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                print(f'greenery group {i}: {len(data["elements"])} objects',flush=True);return data
            except Exception as error:errors.append(str(error))
        raise RuntimeError(f'Group {i} failed: {errors}')
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(fetch,enumerate(groups)))
    merged={(e['type'],e['id']):e for e in base['elements']}
    fresh={}
    for data in responses:
        for e in data['elements']:
            fresh[e['type'],e['id']]={**e,'source_as_of':data.get('osm3s',{}).get('timestamp_osm_base'),
                                     'retrieved_on':data['retrieved_on']}
    # Delete old objects covered by the refreshed selectors, including removed greenery.
    # Other thematic layers retain their original extraction dates.
    for key,e in list(merged.items()):
        t=e.get('tags',{})
        if any(k in t for k in ('natural','landcover','landuse','leisure','species','genus','taxon','leaf_type','leaf_cycle','garden:type','garden:style','vegetation','green_roof')) or t.get('barrier') in ('hedge','hedge_bank'):
            del merged[key]
    merged.update(fresh)
    base.update(elements=list(merged.values()),greenery_refresh={
        'retrieved_on':datetime.now(timezone.utc).isoformat(),'unique_fresh_objects':len(fresh),
        'sources':[{k:d.get(k) for k in ('query','endpoint','retrieved_on','osm3s')} for d in responses],
        'non_greenery_context':'Retained from the earlier extract, with original dates'})
    output.write_text(json.dumps(base,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'output':str(output),'fresh_objects':len(fresh),'merged_objects':len(merged)}),flush=True)


if __name__=='__main__':main()
