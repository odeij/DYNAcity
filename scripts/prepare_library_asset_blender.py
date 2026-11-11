"""Convert an existing library model for Unreal, preserving authored geometry/UVs.

No primitives, decorations or replacement materials are created. The vehicle
adapter rotates its existing wheel-defined basis and records the authored left
steering position so Unreal need not guess or mirror the cabin.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys
import bpy
import bmesh
from mathutils import Matrix,Vector


def document(path):
    raw=path.read_bytes()
    if path.suffix=='.glb':
        length=struct.unpack_from('<I',raw,12)[0]
        return json.loads(raw[20:20+length]),raw[28+length:]
    return json.loads(raw),None


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--vehicle',action='store_true')
    parser.add_argument('--traffic-forward', choices=['+x','-x','+y','-y'],
                        help='Explicit Blender source axis toward the front for a background vehicle')
    parser.add_argument('--source-scale', type=float, default=1.0,
                        help='Uniform reviewed source-unit conversion to metres')
    parser.add_argument('--exclude-object', action='append', default=[],
                        help='Explicitly reviewed non-vehicle scene object to omit')
    # Optional rigid clip of scanned captures that include a ground plinth: xmin xmax ymin ymax zmin zmax in source metres.
    # Triangles are only cut/removed, never added to or re-textured; the origin moves to the clip centre.
    parser.add_argument('--crop',nargs=6,type=float)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    import math
    if not math.isfinite(args.source_scale) or args.source_scale<=0:
        parser.error('--source-scale must be finite and positive')
    if args.traffic_forward and (args.vehicle or args.crop):
        parser.error('--traffic-forward cannot be combined with --vehicle or --crop')
    source=Path(args.source).resolve();output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    doc,binary=document(source)
    names=Counter(m.get('name','') for m in doc.get('materials',[]))
    import_path=source;temporary=None
    if source.suffix.lower() in ('.gltf','.glb') and any(count>1 for count in names.values()):
        # Unique index-based names preserve each primitive's original material
        # index, including duplicate names with different texture coordinate sets.
        for i,m in enumerate(doc.get('materials',[])):
            m['name']='dynacity_source_material_'+str(i)
        temporary=source.with_name('.dynacity-import-'+hashlib.sha256(source.read_bytes()).hexdigest()[:12]+source.suffix)
        if temporary.exists():raise FileExistsError(temporary)
        if source.suffix.lower()=='.glb':
            raw=source.read_bytes(); old_json_length=struct.unpack_from('<I',raw,12)[0]
            payload=json.dumps(doc,separators=(',',':')).encode('utf-8')
            payload+=b' '*((-len(payload))%4)
            remaining=raw[20+old_json_length:]
            temporary.write_bytes(struct.pack('<4sII',b'glTF',2,20+len(payload)+len(remaining))+
                                  struct.pack('<I4s',len(payload),b'JSON')+payload+remaining)
        else:
            temporary.write_text(json.dumps(doc),encoding='utf-8')
        import_path=temporary
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    try:bpy.ops.import_scene.gltf(filepath=str(import_path))
    finally:
        if temporary:temporary.unlink()
    bpy.context.view_layer.update()
    for name in args.exclude_object:
        obj=bpy.data.objects.get(name)
        if obj is None:raise ValueError('Reviewed excluded object not found: '+name)
        bpy.data.objects.remove(obj,do_unlink=True)
    objects=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if args.source_scale!=1:
        # Scale world transforms together without scaling nested parents twice.
        transforms={o:o.matrix_world.copy() for o in objects}
        for o in objects:o.parent=None
        for o in objects:o.matrix_world=Matrix.Scale(args.source_scale,4)@transforms[o]
        bpy.context.view_layer.update()
    crop_centre=None
    if args.crop:
        x0,x1,y0,y1,z0,z1=args.crop;crop_centre=((x0+x1)/2,(y0+y1)/2)
        for o in objects:
            bm=bmesh.new();bm.from_mesh(o.data);bm.transform(o.matrix_world)
            for co,no,inner in (((x0,0,0),(1,0,0),True),((x1,0,0),(1,0,0),False),((0,y0,0),(0,1,0),True),((0,y1,0),(0,1,0),False),((0,0,z0),(0,0,1),True),((0,0,z1),(0,0,1),False)):
                geom=bm.verts[:]+bm.edges[:]+bm.faces[:]
                r=bmesh.ops.bisect_plane(bm,geom=geom,plane_co=co,plane_no=no,clear_inner=inner,clear_outer=not inner)
            bm.transform(o.matrix_world.inverted());bm.to_mesh(o.data);bm.free()
        objects=[o for o in objects if len(o.data.polygons)>0]
        for o in [o for o in bpy.context.scene.objects if o.type=='MESH' and len(o.data.polygons)==0]:bpy.data.objects.remove(o)
        bpy.context.view_layer.update()
    original_triangles=sum(len(o.data.loop_triangles) for o in objects)
    for o in objects:o.data.calc_loop_triangles()
    original_triangles=sum(len(o.data.loop_triangles) for o in objects)
    points=[o.matrix_world@v.co for o in objects for v in o.data.vertices] if args.crop else [o.matrix_world@Vector(p) for o in objects for p in o.bound_box]
    vehicle=None
    if args.vehicle:
        def pivot(name):return bpy.data.objects[name].matrix_world.translation
        forward=((pivot('wheel_fl')+pivot('wheel_fr'))-(pivot('wheel_rl')+pivot('wheel_rr'))).normalized()
        forward.z=0;forward.normalize();left=Vector((0,0,1)).cross(forward).normalized()
        if left.dot(pivot('wheel_fl')-pivot('wheel_fr'))<0:raise ValueError('Authored wheel labels contradict the vehicle basis.')
        rotation=Matrix((forward,left,Vector((0,0,1)))).to_4x4()
        rotated=[rotation@p for p in points]
        centre=Vector(((max(p.x for p in rotated)+min(p.x for p in rotated))/2,
                       (max(p.y for p in rotated)+min(p.y for p in rotated))/2,min(p.z for p in rotated)))
        basis=Matrix.Translation(-centre)@rotation
        steering=[basis@(o.matrix_world@Vector(p)) for o in objects if o.name.startswith('steering_') for p in o.bound_box]
        wheel=Vector(tuple((min(p[i] for p in steering)+max(p[i] for p in steering))/2 for i in range(3)))
        if wheel.y<.15:raise ValueError('The authored steering wheel is not on the left; reject this asset.')
        eye=wheel+Vector((-.48,0,.30))
        # FBX X-forward/Z-up converts Blender's positive-left Y to Unreal's -Y.
        vehicle={'handedness':'left','steering_ground_cm':[wheel.x*100,-wheel.y*100,wheel.z*100],
                 'eye_chassis_cm':[eye.x*100,-eye.y*100,eye.z*100-75],
                 'mirror_cabin':False,'basis':'X forward, Unreal Y right, Z up; centimetres'}
    elif args.traffic_forward:
        # Background vehicle axis is explicitly inspected from source landmarks.
        axes={'+x':Vector((1,0,0)),'-x':Vector((-1,0,0)),
              '+y':Vector((0,1,0)),'-y':Vector((0,-1,0))}
        forward=axes[args.traffic_forward];left=Vector((0,0,1)).cross(forward)
        rotation=Matrix((forward,left,Vector((0,0,1)))).to_4x4()
        rotated=[rotation@p for p in points]
        centre=Vector(((max(p.x for p in rotated)+min(p.x for p in rotated))/2,
                       (max(p.y for p in rotated)+min(p.y for p in rotated))/2,min(p.z for p in rotated)))
        basis=Matrix.Translation(-centre)@rotation
    else:
        # Keep the authored horizontal origin. Only put its existing base at zero.
        basis=Matrix.Translation((-crop_centre[0],-crop_centre[1],-min(p.z for p in points)) if crop_centre else (0,0,-min(p.z for p in points)))
    transforms={o:o.matrix_world.copy() for o in objects}
    for o in objects:o.matrix_world=basis@transforms[o]
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT')
    for o in objects:o.select_set(True)
    bpy.context.view_layer.objects.active=objects[0]
    if len(objects)>1:bpy.ops.object.join()
    obj=bpy.context.object;obj.name='LibraryAsset'
    obj.data.calc_loop_triangles()
    if len(obj.data.loop_triangles)!=original_triangles:raise ValueError('The source triangle count changed during conversion.')
    bpy.context.scene.cursor.location=(0,0,0);bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
    bounds=[obj.matrix_world@v.co for v in obj.data.vertices] if args.crop else [obj.matrix_world@Vector(p) for p in obj.bound_box]
    minimum=[min(p[i] for p in bounds)*100 for i in range(3)]
    maximum=[max(p[i] for p in bounds)*100 for i in range(3)]
    exported=output/'asset.fbx'
    bpy.ops.export_scene.fbx(filepath=str(exported),use_selection=True,object_types={'MESH'},
        axis_forward='X',axis_up='Z',apply_unit_scale=True,add_leaf_bones=False,bake_anim=False,
        path_mode='COPY',embed_textures=True)
    # Retain source PBR definitions. FBX's material subset is replaced with these
    # exact authored factors/textures on import, including normal/roughness maps.
    images=[]
    for i,entry in enumerate(doc.get('images',[])):
        if 'uri' in entry:
            p=(source.parent/entry['uri']).resolve()
            if not p.is_relative_to(source.parent):raise ValueError('External texture path escaped its source folder.')
        else:
            view=doc['bufferViews'][entry['bufferView']];offset=view.get('byteOffset',0)
            ext='.png' if entry.get('mimeType')=='image/png' else '.jpg'
            p=output/('image_'+str(i)+ext);p.write_bytes(binary[offset:offset+view['byteLength']])
        images.append(str(p))
    result={'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'fbx':str(exported),'fbx_sha256':hashlib.sha256(exported.read_bytes()).hexdigest(),
        'triangles':original_triangles,'bounds_blender_cm':{'min':minimum,'max':maximum},
        'material_names':[m.name for m in obj.data.materials],
        'materials':doc.get('materials',[]),'textures':doc.get('textures',[]),'images':images,
        'vehicle':vehicle,'traffic_source_forward':args.traffic_forward,'crop_source_m':args.crop,
        'source_scale_to_metres':args.source_scale,'excluded_scene_objects':args.exclude_object,
        'operations':['source import']+(['rigid plane clip of ground plinth (no triangles added)'] if args.crop else [])+['rigid basis/base conversion','mesh combination','FBX export']}
    (output/'prepared.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'triangles':original_triangles,'dimensions_cm':[b-a for a,b in zip(minimum,maximum)],'vehicle':vehicle}))


if __name__=='__main__':main()
