"""Export unchanged geospatial triangles and adapt authored CC0 kit pieces."""
import hashlib
import json
from pathlib import Path
import bpy
import bmesh
import numpy as np
from mathutils import Vector

ROOT=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
SOURCE=Path('C:/private/dynacity-data/assets-inbox/PolyHaven/modular_urban_apartments_facade/manifest.json')
manifest=json.loads(SOURCE.read_text())
if manifest['license']!='CC0 1.0':raise ValueError('Unexpected library license')
for item in manifest['files']:
    if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
        raise ValueError('Changed licensed source')
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
data=np.load(ROOT/'source-batch.npz')
vertices=data['vertices'][:,[0,2,1]].astype(float);vertices[:,1]*=-1
mesh=bpy.data.meshes.new('SourceGeometryUnchanged');mesh.from_pydata(vertices.tolist(),[],data['faces'].tolist());mesh.update()
obj=bpy.data.objects.new('Rmeil9_ReferenceVisual',mesh);bpy.context.collection.objects.link(obj)
for name in ['SourceExisting','ReferenceFacade']:mesh.materials.append(bpy.data.materials.new(name))
for poly,index in zip(mesh.polygons,data['materials']):poly.material_index=int(index)
records=[]

def export(obj,identity):
    bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
    path=ROOT/(identity+'.fbx')
    bpy.ops.export_scene.fbx(filepath=str(path),use_selection=True,object_types={'MESH'},
        axis_forward='X',axis_up='Z',apply_unit_scale=True,add_leaf_bones=False,bake_anim=False,
        use_mesh_modifiers=False,path_mode='AUTO',embed_textures=False)
    obj.data.calc_loop_triangles()
    bounds=[[min(v.co[i] for v in obj.data.vertices) for i in range(3)],
            [max(v.co[i] for v in obj.data.vertices) for i in range(3)]]
    records.append({'id':identity,'fbx':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'triangles':len(obj.data.loop_triangles),'bounds_m':bounds,
                    'material_triangles':{str(slot.material.name):sum(1 for t in obj.data.loop_triangles if t.material_index==i)
                                          for i,slot in enumerate(obj.material_slots) if slot.material},
                    'materials':[slot.material.name for slot in obj.material_slots if slot.material]})

export(obj,'source_visual_batch')
bpy.data.objects.remove(obj,do_unlink=True)
bpy.ops.import_scene.gltf(filepath=manifest['gltf'])
for identity,name,glass_only in [('cornice','cornice_standard_standard_01',False),
                                ('glass','window_centered_large_01',True)]:
    source=bpy.data.objects.get(name)
    if source is None:raise ValueError('Missing authored source '+name)
    reference=(bpy.data.objects['wall_window_centered_large_01'].matrix_world.translation.copy()
               if glass_only else source.matrix_world.translation.copy())
    pieces=[]
    for original in [source,*source.children_recursive]:
        if original.type!='MESH':continue
        item=original.copy();item.data=original.data.copy();bpy.context.collection.objects.link(item)
        if glass_only:
            allowed={i for i,slot in enumerate(item.material_slots) if slot.material and 'glass' in slot.material.name.lower()}
            bm=bmesh.new();bm.from_mesh(item.data)
            bmesh.ops.delete(bm,geom=[f for f in bm.faces if f.material_index not in allowed],context='FACES')
            bm.to_mesh(item.data);bm.free()
            if not item.data.polygons:
                bpy.data.objects.remove(item,do_unlink=True);continue
        for vertex in item.data.vertices:
            relative=original.matrix_world@vertex.co-reference
            vertex.co=Vector((-relative.x-1.5,-relative.y,relative.z))
        item.matrix_world.identity();pieces.append(item)
    if not pieces:raise ValueError('No authored geometry for '+identity)
    bpy.ops.object.select_all(action='DESELECT')
    for item in pieces:item.select_set(True)
    bpy.context.view_layer.objects.active=pieces[0];bpy.ops.object.join();obj=bpy.context.object
    if identity=='cornice':
        # Turn the authored strip into a horizontal shelf before fitting depth;
        # this avoids magnifying its shallow vertical moulding into a large eave.
        for vertex in obj.data.vertices:
            x,y,z=vertex.co;vertex.co=Vector((x,-z,y))
    if glass_only:
        # Reuse one actual planar glass face rather than window bevel facets.
        bm=bmesh.new();bm.from_mesh(obj.data);bm.normal_update()
        largest=max(bm.faces,key=lambda face:face.calc_area())
        normal=largest.normal.copy();origin=largest.verts[0].co.copy()
        planar=[face for face in bm.faces if face.normal.dot(normal)>.99999
                and all(abs((v.co-origin).dot(normal))<.00001 for v in face.verts)]
        bmesh.ops.delete(bm,geom=[face for face in bm.faces if face not in planar],context='FACES')
        bm.to_mesh(obj.data);bm.free()
    # Move the extracted piece's bottom and outward centre to its origin;
    # placement scale later fits its original geometry to the reference test.
    low=[min(v.co[i] for v in obj.data.vertices) for i in range(3)]
    high=[max(v.co[i] for v in obj.data.vertices) for i in range(3)]
    center=Vector(((low[0]+high[0])/2,(low[1]+high[1])/2,low[2]))
    for vertex in obj.data.vertices:vertex.co-=center
    export(obj,identity);bpy.data.objects.remove(obj,do_unlink=True)
(ROOT/'prepared-reference.json').write_text(json.dumps({'source_library':manifest,'assets':records,
    'operations':['Source batch vertices and face order copied without displacement',
                  'Existing authored cornice rigidly rotated as horizontal ledge; original UV retained',
                  'Coplanar authored front glass faces extracted from window; original faces/UV retained'],
    'limits':['Balcony dimensions, floor spacing and hidden architecture are presentation estimates',
              'This reuses finished licensed geometry; it is not a surveyed reconstruction']},indent=2))
print(json.dumps(records))
