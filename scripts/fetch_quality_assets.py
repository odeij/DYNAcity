"""Cache finished library assets with provenance. No geometry is authored here."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'DynaCITY asset integration',
                                              'Referer':'https://polyhaven.com/'})
    with urllib.request.urlopen(request,timeout=60) as response:return response.read()


def polyhaven(root,key,resolution='4k'):
    if not re.fullmatch(r'[a-z0-9_]+',key):raise ValueError('Invalid Poly Haven asset ID')
    folder=root/key;folder.mkdir(parents=True,exist_ok=True)
    record=json.loads(fetch('https://api.polyhaven.com/files/'+key))['gltf'][resolution]['gltf']
    files=[]
    for name,entry in {key+'.gltf':record,**record['include']}.items():
        target=(folder/name).resolve()
        if not target.is_relative_to(folder.resolve()):raise ValueError('Invalid source asset path')
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():target.write_bytes(fetch(entry['url']))
        content=target.read_bytes()
        if hashlib.md5(content).hexdigest()!=entry['md5']:raise ValueError('Source digest mismatch: '+name)
        files.append({'path':name,'url':entry['url'],'sha256':hashlib.sha256(content).hexdigest()})
    source={'id':key,'source':'https://polyhaven.com/a/'+key,'license':'CC0 1.0',
            'credit':'Powered by Poly Haven','resolution':resolution,'files':files}
    (folder/'source.json').write_text(json.dumps(source,indent=2),encoding='utf-8')
    return source


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--only',nargs='+',help='Fetch these Poly Haven asset IDs instead of the standard set')
    parser.add_argument('--resolution',default='4k',choices=('1k','2k','4k','8k'))
    args=parser.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    assets=[]
    for key in args.only or ('modular_street_seating','metal_trash_can','tree_small_02','street_lamp_01'):
        print('Fetching finished asset:',key,flush=True)
        assets.append(polyhaven(root,key,args.resolution))
    # A selective download must not replace the main library inventory.
    inventory='library-sources-selective.json' if args.only else 'library-sources.json'
    (root/inventory).write_text(json.dumps(assets,indent=2),encoding='utf-8')
    print('Library assets cached with original meshes, textures and source hashes.')


if __name__=='__main__':main()
