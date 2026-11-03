"""Convert the cached CC0 lamp to the project's existing FBX import path."""
import argparse
from pathlib import Path
import sys
import bpy

parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(Path(args.source).resolve()))
objects=[o for o in bpy.context.scene.objects if o.type=='MESH']
bpy.ops.object.select_all(action='DESELECT')
for o in objects:o.select_set(True)
bpy.context.view_layer.objects.active=objects[0];bpy.ops.object.join()
obj=bpy.context.object
bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
bottom=min((obj.matrix_world@v.co).z for v in obj.data.vertices)
obj.location.z-=bottom
bpy.context.scene.cursor.location=(0,0,0);bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
bpy.ops.export_scene.fbx(filepath=str(Path(args.output).resolve()),use_selection=True,object_types={'MESH'},
    axis_forward='X',axis_up='Z',apply_unit_scale=True,add_leaf_bones=False,bake_anim=False,
    path_mode='COPY',embed_textures=True)
