"""Fetch one CC0 modular facade kit through Poly Haven's public API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import requests


def fetch(output):
    root = Path(output); root.mkdir(parents=True, exist_ok=True)
    asset = 'modular_urban_apartments_facade'
    headers = {'User-Agent': 'DYNAcity/1.0 (Powered by Poly Haven)'}
    api = requests.get('https://api.polyhaven.com/files/' + asset, headers=headers, timeout=60)
    api.raise_for_status(); spec = api.json()['gltf']['1k']['gltf']
    records = dict(spec['include'])
    records[asset + '_1k.gltf'] = {k: v for k, v in spec.items() if k != 'include'}
    def one(item):
        relative, record = item
        path = root / relative
        if not path.is_relative_to(root):
            raise ValueError('Unsafe provider path')
        if path.exists():
            data = path.read_bytes()
        else:
            response = requests.get(record['url'], headers=headers, timeout=120)
            response.raise_for_status(); data = response.content
        if len(data) != record['size'] or hashlib.md5(data).hexdigest() != record['md5']:
            raise ValueError('Provider size/hash mismatch: ' + relative)
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return dict(record, path=str(path), sha256=hashlib.sha256(data).hexdigest())
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(one, records.items()))
    manifest = {'schema': 'dynacity-facade-library/1', 'asset_id': asset,
        'source_url': 'https://polyhaven.com/a/' + asset, 'license': 'CC0 1.0',
        'license_url': 'https://polyhaven.com/license', 'author': 'James Ray Cock',
        'credit': 'Powered by Poly Haven', 'resolution': '1k', 'files': files,
        'gltf': str(root / (asset + '_1k.gltf'))}
    target = root / 'manifest.json'; target.write_text(json.dumps(manifest, indent=2))
    return str(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    print(fetch(parser.parse_args().output))
