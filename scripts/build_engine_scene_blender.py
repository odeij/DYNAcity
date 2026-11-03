"""Build a private .blend and portable GLB from engine_scene JSON.

Run with Blender --background --python this_file -- --scene ... --output ...
All geometry is in local east/north/up metres. GLB uses its standard Y-up frame;
Unreal's glTF importer handles the axis and centimetre conversion.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def material(name, colour, roughness=0.8):
    mat=bpy.data.materials.new(name)
    mat.diffuse_color=(*colour,1)
    mat.use_nodes=True
    bsdf=mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value=(*colour,1)
    bsdf.inputs['Roughness'].default_value=roughness
    return mat


def mesh_object(name, vertices, triangles, mat, properties=None):
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata(vertices,[],triangles)
    mesh.update()
    obj=bpy.data.objects.new(name,mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    for key,value in (properties or {}).items():
        obj[key]=value if isinstance(value,(str,int,float,bool)) else json.dumps(value)
    return obj


def joined_objects(name, items, mat):
    vertices,triangles=[],[]
    for item in items:
        offset=len(vertices)
        vertices.extend(item['vertices'])
        triangles.extend([[v+offset for v in face] for face in item['triangles']])
    return mesh_object(name,vertices,triangles,mat,items[0]['metadata'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scene',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--render',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    if (output/'neighbourhood.blend').exists():
        raise FileExistsError('Use a new output folder for a new Blender build')
    scene_data=json.loads(Path(args.scene).read_text(encoding='utf-8'))
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    scene=bpy.context.scene
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1.0
    mats={
        'terrain':material('Ground — provisional model surface',(0.38,0.35,0.29)),
        'road':material('Asphalt',(0.055,0.065,0.075)),
        'concrete':material('Illustrative concrete',(0.56,0.55,0.50)),
        'trunk':material('Illustrative tree bark',(0.19,0.12,0.065)),
        'leaves':material('Illustrative foliage',(0.12,0.25,0.10)),
        'marking':material('Illustrative lane paint',(0.83,0.81,0.68)),
    }
    palettes=[material(f'Illustrative facade {i}',colour) for i,colour in enumerate([
        (0.70,0.65,0.54),(0.62,0.60,0.54),(0.77,0.73,0.65),(0.62,0.56,0.46),(0.73,0.69,0.61)])]
    road_parts=defaultdict(list)
    for item in scene_data['meshes']:
        if item['kind']=='road':
            road_parts[item['metadata']['road_id']].append(item)
            continue
        mat=mats.get(item['kind'])
        if item['kind']=='building':
            colour_index=int.from_bytes(hashlib.sha256(item['id'].encode()).digest()[:2],'little')%len(palettes)
            mat=palettes[colour_index]
        obj=mesh_object(item['id'].replace(':','_'),item['vertices'],item['triangles'],mat,
                        {'source_id':item['id'],'kind':item['kind'],**item['metadata']})
        if item['kind']=='building':
            # Materials and edge treatment are presentation choices, not surveyed details.
            modifier=obj.modifiers.new('Small presentation edge bevel','BEVEL')
            modifier.width=0.06;modifier.segments=1
    for identifier,items in road_parts.items():
        obj=joined_objects(f'road_{identifier}',items,mats['road'])
        obj['source_id']=f'road:{identifier}';obj['kind']='road'
    print(f'Built {len(road_parts)} road meshes and source buildings',flush=True)

    for tree in scene_data['trees']:
        x,y,z=tree['position'];height=tree['height_m']
        bpy.ops.mesh.primitive_cylinder_add(vertices=8,radius=0.16,depth=height*0.55,
                                            location=(x,y,z+height*0.275))
        trunk=bpy.context.object;trunk.name=tree['id'].replace('/','_')+'_trunk'
        trunk.data.materials.append(mats['trunk']);trunk['source_id']=tree['id']
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2,radius=1,
                                              location=(x,y,z+height*0.7))
        crown=bpy.context.object;crown.name=tree['id'].replace('/','_')+'_crown'
        crown.scale=(height*0.30,height*0.30,height*0.34)
        crown.data.materials.append(mats['leaves']);crown['source_id']=tree['id']
        crown['position_source']=tree['metadata'].get('position_source','OSM')
        crown['shape_source']='illustrative'

    # Paint is illustrative, never a replacement for SUMO lane connectivity.
    paint_vertices,paint_faces=[],[]
    box_vertices=[(-.5,-.5,-.5),(.5,-.5,-.5),(.5,.5,-.5),(-.5,.5,-.5),
                  (-.5,-.5,.5),(.5,-.5,.5),(.5,.5,.5),(-.5,.5,.5)]
    box_faces=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
               (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    for road in scene_data['roads']:
        if road['width_m']<5:continue
        distance=0
        for a,b in zip(road['path'],road['path'][1:]):
            a,b=Vector(a),Vector(b); length=(b-a).length
            if length<0.1:continue
            if (int(distance/5)%2==0 and
                max(abs((a.x+b.x)/2),abs((a.y+b.y)/2))<=scene_data['half_width_m']-3):
                midpoint=(a+b)/2;midpoint.z+=0.025
                rotation=(b-a).to_track_quat('X','Z')
                offset=len(paint_vertices)
                paint_vertices.extend([list(midpoint+rotation@Vector((x*min(length,3),y*.10,z*.015)))
                                       for x,y,z in box_vertices])
                paint_faces.extend([[offset+i for i in f] for f in box_faces])
            distance+=length
    mesh_object('Illustrative lane paint',paint_vertices,paint_faces,mats['marking'],
                {'kind':'illustrative lane paint','source':'Presentation; not surveyed markings'})
    for name,position in [('ORIGIN',(0,0,0)),('EAST',(10,0,0)),('NORTH',(0,10,0)),('UP',(0,0,10))]:
        vertices=[[v*.3 for v in vertex] for vertex in box_vertices]
        marker=mesh_object('DYNACITY_BASIS_'+name,vertices,box_faces,mats['leaves'],{'kind':'coordinate calibration'})
        marker.location=position
    print('Built trees and batched road paint',flush=True)

    # Export only scene assets; studio lighting and camera are preview-only.
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(filepath=str(output/'neighbourhood.glb'),export_format='GLB',
                              use_selection=True,export_extras=True,export_apply=True,
                              export_materials='EXPORT',export_cameras=False,export_lights=False)
    scene['coordinates']=json.dumps(scene_data['coordinates'])
    scene['source_scene']=str(Path(args.scene).resolve())
    scene['limitations']=json.dumps(scene_data['limitations'])
    bpy.ops.object.light_add(type='SUN',location=(0,0,350))
    sun=bpy.context.object;sun.name='Preview sun';sun.rotation_euler=(math.radians(27),math.radians(-25),math.radians(-35))
    sun.data.energy=3;sun.data.angle=math.radians(12)
    scene.world.color=(0.35,0.4,0.5)
    bpy.ops.object.camera_add(location=(520,-650,570))
    camera=bpy.context.object;camera.name='Preview camera'
    camera.rotation_euler=(Vector((0,0,15))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type='ORTHO';camera.data.ortho_scale=1020;camera.data.clip_end=5000
    scene.camera=camera
    scene.render.engine='CYCLES';scene.cycles.samples=24
    scene.render.resolution_x=1600;scene.render.resolution_y=1000;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.filepath=str(output/'preview.png')
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'neighbourhood.blend'))
    if args.render:bpy.ops.render.render(write_still=True)
    print('DYNACITY_BUILD_COMPLETE '+str(output))


if __name__=='__main__':main()
