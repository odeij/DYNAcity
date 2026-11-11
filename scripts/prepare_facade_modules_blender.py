"""Retain authored Poly Haven geometry/UVs, normalize and export two modules."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import bpy
from mathutils import Vector


def prepare(source_manifest, output):
    manifest=json.loads(Path(source_manifest).read_text())
    if manifest['license'] != 'CC0 1.0':raise ValueError('Unexpected facade licence')
    for record in manifest['files']:
        if hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Changed source bytes')
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.context.scene.unit_settings.system='METRIC'
    bpy.context.scene.unit_settings.scale_length=1
    bpy.ops.import_scene.gltf(filepath=manifest['gltf'])
    records=[]
    for size in ('small','large'):
        names=[f'wall_window_centered_{size}_01',f'window_centered_{size}_01']
        source=[]
        for name in names:
            parent=bpy.data.objects.get(name)
            if parent is None:raise ValueError('Missing authored module '+name)
            source.extend([o for o in [parent,*parent.children_recursive] if o.type=='MESH'])
        meshes=[]
        # glTF positions are module-local, but nodes are arranged in a showroom.
        # Wall origin is its right bottom corner; move to its centre, rotate
        # 180 degrees about Z so the authored outside points toward local +Y.
        reference=bpy.data.objects[names[0]].matrix_world.translation.copy()
        for original in source:
            item=original.copy();item.data=original.data.copy();bpy.context.collection.objects.link(item)
            for vertex in item.data.vertices:
                world=original.matrix_world @ vertex.co
                relative=world-reference
                vertex.co=Vector((-relative.x-1.5,-relative.y,relative.z))
            item.matrix_world.identity();meshes.append(item)
        bpy.ops.object.select_all(action='DESELECT')
        for item in meshes:item.select_set(True)
        bpy.context.view_layer.objects.active=meshes[0]
        bpy.ops.object.join();obj=bpy.context.object;obj.name='Facade_window_'+size
        obj.data.calc_loop_triangles();triangles=len(obj.data.loop_triangles)
        bounds=[[min(v.co[i] for v in obj.data.vertices) for i in range(3)],
                [max(v.co[i] for v in obj.data.vertices) for i in range(3)]]
        # Preserve source glass; separate translucent material import is audited
        # by the editor owner before use. Export uses original material names.
        target=root/('facade_window_'+size+'.fbx')
        bpy.ops.export_scene.fbx(filepath=str(target),use_selection=True,object_types={'MESH'},
            axis_forward='X',axis_up='Z',apply_unit_scale=True,add_leaf_bones=False,
            bake_anim=False,use_mesh_modifiers=False,path_mode='COPY',embed_textures=False)
        glb=root/('facade_window_'+size+'.glb')
        bpy.ops.export_scene.gltf(filepath=str(glb),export_format='GLB',use_selection=True,
            export_apply=False,export_yup=True)
        records.append({'id':'window_'+size,'fbx':str(target),'glb':str(glb),
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'triangles':triangles,
            'bounds_m':bounds,'authored_objects':names,
            'materials':[slot.material.name for slot in obj.material_slots if slot.material]})
        bpy.data.objects.remove(obj,do_unlink=True)
    result={'schema':'dynacity-prepared-facade-modules/1','source':manifest,
        'modules':records,'basis':'X along wall; Y outward; Z up; metres',
        'panel_width_m':3.,'panel_height_m':3.,'wall_offset_m':.28,
        'operations':['Authored meshes joined, source UVs/material slots retained',
            'Showroom translation removed; proper 180-degree rotation about up; no mirroring',
            'No topology or decorative mesh generation'],
        'limits':['Generic authored urban facade kit; not surveyed Beirut architecture.',
            '28cm outward offset clears the existing source wall behind recessed glass.']}
    (root/'prepared-modules.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);prepare(a.manifest,a.output)
