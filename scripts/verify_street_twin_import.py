"""Import one generated tile into an isolated Unreal content folder and inspect it.

No actors are created or maps changed. Only imported assets and their material
references inside the pilot folder are saved. Requires the local Unreal MCP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from unreal_mcp_client import call


def invoke(url, toolset, name, arguments):
    response = call(url, 'tools/call', {'name': 'call_tool', 'arguments': {
        'toolset_name': f'editor_toolset.toolsets.{toolset}',
        'tool_name': name, 'arguments': arguments}}, timeout=120)
    if response.get('error') or response.get('result', {}).get('isError'):
        raise RuntimeError(response)
    result = response['result']
    if 'structuredContent' in result:
        return result['structuredContent']['returnValue']
    for item in result.get('content', []):
        if item['type'] == 'text':
            value = json.loads(item['text'])
            if 'returnValue' in value:
                return value['returnValue']
            if 'error' in value:
                raise RuntimeError(value)
    raise RuntimeError(f'Unexpected Unreal tool response: {response}')


def glb_geometry(path):
    with Path(path).open('rb') as stream:
        header = stream.read(20)
        if header[:4] != b'glTF' or header[16:20] != b'JSON':
            raise ValueError('Expected binary glTF JSON header')
        doc = json.loads(stream.read(struct.unpack_from('<I', header, 12)[0]))
    triangle_count = 0
    minima, maxima = [], []
    for node in doc['nodes']:
        if 'mesh' not in node:
            continue
        for primitive in doc['meshes'][node['mesh']]['primitives']:
            accessor = doc['accessors'][primitive['attributes']['POSITION']]
            minima.append(accessor['min'])
            maxima.append(accessor['max'])
            triangle_count += doc['accessors'][primitive['indices']]['count'] // 3
    # glTF (east,up,south) metres -> Unreal (east,south,up) centimetres.
    spans = [100 * (max(p[k] for p in maxima) - min(p[k] for p in minima)) for k in (0, 2, 1)]
    bounds = {'min': {axis: 100 * min(p[k] for p in minima) for axis,k in zip(('x','y','z'),(0,2,1))},
              'max': {axis: 100 * max(p[k] for p in maxima) for axis,k in zip(('x','y','z'),(0,2,1))}}
    return triangle_count, spans, bounds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8016/mcp')
    parser.add_argument('--tile-id')
    parser.add_argument('--blender', required=True, help='Installed Blender executable for GLB to FBX conversion')
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    tiles = [t for t in manifest['tiles'] if t['asset'] and (not args.tile_id or t['tile_id'] == args.tile_id)]
    if not tiles:
        raise ValueError('No generated tile selected')
    tile = max(tiles, key=lambda t: t.get('lane_count', t.get('asphalt_lane_count', 0)))
    source = manifest_path.parent / tile['asset']
    if hashlib.sha256(source.read_bytes()).hexdigest() != tile['sha256']:
        raise ValueError('Generated tile checksum mismatch')
    expected_triangles, expected_spans, expected_bounds = glb_geometry(source)
    # The generic MCP mesh tool uses FbxFactory, which accepts FBX/OBJ only.
    # Convert using the installed Blender runtime; retain the geographic origin.
    converted = Path(args.output).resolve().parent / ('import-' + tile['content_hash']) / 'street.fbx'
    if not converted.exists():
        subprocess.run([args.blender, '--background', '--python',
                        str(Path(__file__).with_name('convert_street_twin_blender.py')), '--',
                        '--source', str(source), '--output', str(converted)],
                       check=True, capture_output=True, text=True, timeout=120)
    conversion = json.loads(converted.with_suffix('.conversion.json').read_text())
    if (conversion['source_sha256'] != tile['sha256'] or
            conversion['fbx_sha256'] != hashlib.sha256(converted.read_bytes()).hexdigest() or
            conversion['triangles'] != expected_triangles):
        raise ValueError('Converted FBX provenance or triangle count mismatch')
    asset_name = 'StreetTwin_' + tile['content_hash'][:12]
    package_path = '/Game/DynaCity/StreetTwinPilot/' + asset_name
    exists = invoke(args.url, 'asset.AssetTools', 'exists', {'path': package_path})
    assets = ([{'refPath': package_path + '.' + asset_name}] if exists else
              invoke(args.url, 'static_mesh.StaticMeshTools', 'import_file', {
                'folder_path': '/Game/DynaCity/StreetTwinPilot', 'asset_name': asset_name,
                'source_file': str(converted), 'import_materials': True,
                'import_textures': False, 'combine_meshes': True}))
    if not assets:
        raise RuntimeError('Import returned no assets')
    report = {'tile_id': tile['tile_id'], 'source_asset': str(source),
              'source_sha256': tile['sha256'], 'assets': assets,
              'conversion': conversion, 'fbx_asset': str(converted),
              'reused_existing_asset': exists,
              'expected_triangles': expected_triangles, 'expected_spans_cm': expected_spans,
              'expected_bounds_cm': expected_bounds,
              'twin_content_hash': manifest['twin_content_hash'],
              'map_modified': False, 'collision_verified': False,
              'runtime_performance_verified': False, 'meshes': []}
    # is_dirty uses AssetRegistry package names, not UObject soft paths.
    paths = {a['refPath'].split('.')[0] for a in assets}
    for asset in assets:
        if invoke(args.url, 'asset.AssetTools', 'get_asset_class', {'asset_path': asset['refPath']}) != 'StaticMesh':
            continue
        mesh = {'refPath': asset['refPath']}
        triangles = invoke(args.url, 'static_mesh.StaticMeshTools', 'get_triangle_count', {'mesh': mesh})
        bounds = invoke(args.url, 'static_mesh.StaticMeshTools', 'get_bounds', {'mesh': mesh})
        materials = invoke(args.url, 'static_mesh.StaticMeshTools', 'get_material_slots', {'mesh': mesh})
        material_refs = [invoke(args.url, 'static_mesh.StaticMeshTools', 'get_material',
                                {'mesh': mesh, 'slot_name': name}) for name in materials]
        paths.update(material['refPath'].split('.')[0] for material in material_refs
                     if material['refPath'].startswith('/Game/DynaCity/StreetTwinPilot/'))
        spans = [bounds['max'][k] - bounds['min'][k] for k in ('x', 'y', 'z')]
        report['meshes'].append({'asset': asset['refPath'], 'triangles': triangles, 'bounds': bounds,
                                 'spans_cm': spans, 'material_slots': materials, 'materials': material_refs})
    report['saved_packages'] = sorted(paths)
    report['save_succeeded'] = invoke(args.url, 'asset.AssetTools', 'save_assets', {'asset_paths': sorted(paths)})
    report['unsaved_assets'] = [p for p in paths if invoke(args.url, 'asset.AssetTools', 'is_dirty', {'asset_path': p})]
    report['triangle_match'] = sum(m['triangles'] for m in report['meshes']) == expected_triangles
    report['bounds_match'] = (len(report['meshes']) == 1 and
        all(abs(a-b) <= .5 for a,b in zip(report['meshes'][0]['spans_cm'], expected_spans)) and
        all(abs(report['meshes'][0]['bounds'][side][axis] - expected_bounds[side][axis]) <= .5
            for side in ('min', 'max') for axis in ('x', 'y', 'z')))
    report['status'] = ('pass' if report['triangle_match'] and report['bounds_match'] and
                        report['save_succeeded'] and not report['unsaved_assets'] else 'fail')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if report['status'] != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
