"""Cache one reusable CC0 Poly Haven lamp, with hashes and attribution, privately."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'DynaCITY local research prototype','Referer':'https://polyhaven.com/'})
    with urllib.request.urlopen(req,timeout=45) as response:return response.read()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    asset='street_lamp_01'
    data=json.loads(fetch('https://api.polyhaven.com/files/'+asset))
    record=data['gltf']['1k']['gltf']
    entries={asset+'.gltf':record,**record['include']};files=[]
    for name,source in entries.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()):raise ValueError('Invalid asset path')
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_bytes(fetch(source['url']))
        content=path.read_bytes()
        if hashlib.md5(content).hexdigest()!=source['md5']:raise ValueError('Asset digest mismatch: '+name)
        files.append({'path':name,'url':source['url'],'sha256':hashlib.sha256(content).hexdigest()})
    (root/'manifest.json').write_text(json.dumps({'id':asset,'source':'https://polyhaven.com/a/'+asset,
        'license':'CC0 1.0','license_url':'https://polyhaven.com/license','api_credit':'Powered by Poly Haven',
        'placement':'Illustrative reference-street dressing, not surveyed hardware','files':files},indent=2),encoding='utf-8')
    print(root/('street_lamp_01.gltf'))


if __name__=='__main__':main()
