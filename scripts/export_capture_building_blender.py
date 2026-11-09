"""Export only the aligned photographic capture's existing faces and texture UVs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True)
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:]);path=Path(args.manifest);data=json.loads(path.read_text())
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.wm.obj_import(filepath=data['aligned_obj'],forward_axis='Y',up_axis='Z')
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    triangles=0
    for obj in meshes:
        obj.data.calc_loop_triangles();triangles+=len(obj.data.loop_triangles)
        for material in obj.data.materials:
            if material and material.use_nodes:
                bsdf=material.node_tree.nodes.get('Principled BSDF')
                if bsdf:bsdf.inputs['Roughness'].default_value=.9
    if triangles!=data['retained_faces']:raise ValueError('Original source faces changed during OBJ import')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:obj.select_set(True)
    bpy.context.view_layer.objects.active=meshes[0]
    if len(meshes)>1:bpy.ops.object.join()
    bpy.context.object.name='Captured_'+data['source_id']
    bpy.ops.export_scene.gltf(filepath=data['asset'],export_format='GLB',export_image_format='AUTO',
                              export_yup=True,export_texcoords=True,export_normals=True)
    data['asset_sha256']=hashlib.sha256(Path(data['asset']).read_bytes()).hexdigest()
    data['export_triangles']=triangles
    path.write_text(json.dumps(data,indent=2),encoding='utf8')
    print(json.dumps({'triangles':triangles,'asset':data['asset']}))


if __name__=='__main__':main()
