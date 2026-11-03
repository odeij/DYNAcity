"""Convert generated GLB to FBX without moving its shared geographic origin."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    source, output = Path(args.source), Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1
    bpy.ops.import_scene.gltf(filepath=str(source))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    bpy.ops.object.select_all(action='DESELECT')
    triangles = 0
    for obj in objects:
        obj.select_set(True)
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
    bpy.context.view_layer.objects.active = objects[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(filepath=str(output), use_selection=True, object_types={'MESH'},
        axis_forward='X', axis_up='Z', apply_unit_scale=True,
        add_leaf_bones=False, bake_anim=False, use_mesh_modifiers=False)
    report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'fbx_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'triangles': triangles, 'origin_preserved': True,
              'operations': ['glTF axis import', 'FBX export with metric scale and X-forward/Z-up']}
    output.with_suffix('.conversion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
