"""Append an extra city-batch layer to the Unreal city import checkpoint.

Use for layers built after the city world was created (for example
`dynacity.engine_context_buildings`). Packages already queued are not added
again. Then run `import_unreal_city.py` to import them one at a time.
"""
import argparse
import json
from pathlib import Path


def queue(checkpoint,manifest):
    data=json.loads(Path(checkpoint).read_text());layer=json.loads(Path(manifest).read_text())
    if data.get('pending'):raise RuntimeError('Finalize the pending import first')
    if layer['origin_utm_m']!=data['origin_utm_m']:raise RuntimeError('Layer uses a different coordinate origin')
    keys={p['key'] for p in data['packages']}
    new=[{'key':'batch-'+job['name'],**job} for job in layer['jobs'] if 'batch-'+job['name'] not in keys]
    data['packages'].extend(new)
    temporary=Path(checkpoint).with_suffix('.partial');temporary.write_text(json.dumps(data,indent=2))
    temporary.replace(checkpoint)
    return {'queued':[p['key'] for p in new],'total_packages':len(data['packages'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',required=True,help='Saved/DynaCity/city-import-BeirutCityBatched.json')
    parser.add_argument('--layer',required=True,help='Layer manifest with jobs, e.g. context-buildings.json')
    args=parser.parse_args();result=queue(args.checkpoint,args.layer)
    print(json.dumps({'queued':len(result['queued']),'total_packages':result['total_packages']}))


if __name__=='__main__':main()
