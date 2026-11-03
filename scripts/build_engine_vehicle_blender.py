"""Create an illustrative sedan, +X forward, metres, origin at road contact.

Run Blender --background --python this_file -- --output <private>/Sedan.fbx.
No external models or textures are used. Dimensions are presentation defaults.
"""
import argparse
import math
from pathlib import Path
import sys
import bpy


def material(name,colour,metal=0,roughness=.5):
    result=bpy.data.materials.new(name)
    result.diffuse_color=(*colour,1)
    result.use_nodes=True
    shader=result.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value=(*colour,1)
    shader.inputs['Metallic'].default_value=metal
    shader.inputs['Roughness'].default_value=roughness
    return result


def box(name,location,dimensions,mat,bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1,location=location)
    obj=bpy.context.object;obj.name=name;obj.dimensions=dimensions
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    obj.data.materials.append(mat)
    if bevel:
        modifier=obj.modifiers.new('Soft body edges','BEVEL')
        modifier.width=bevel;modifier.segments=2
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def main():
    raise SystemExit("Retired: use finished library assets through prepare_library_asset_blender.py and library_assets.install().")
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    paint=material('Body paint',(.16,.24,.3),.45,.32)
    glass=material('Opaque tinted glass',(.018,.037,.05),.35,.2)
    tyre=material('Rubber',(.014,.016,.018),0,.9)
    rim=material('Wheel alloy',(.38,.4,.42),.7,.28)
    front=material('Headlamp',(.88,.85,.67),.2,.25)
    rear=material('Tail lamp',(.45,.012,.008),.15,.3)
    trim=material('Dark trim',(.025,.028,.03),.2,.6)
    box('Body',(0,0,.58),(4.4,1.72,.64),paint,.13)
    # A tapered cabin, with windows inset beneath thin roof and pillars.
    verts=[(-1.15,-.78,.87),(-1.15,.78,.87),(.95,.78,.87),(.95,-.78,.87),
           (-.76,-.66,1.42),(-.76,.66,1.42),(.52,.66,1.42),(.52,-.66,1.42)]
    mesh=bpy.data.meshes.new('Cabin');mesh.from_pydata(verts,[],[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)])
    obj=bpy.data.objects.new('Glass cabin',mesh);bpy.context.collection.objects.link(obj);obj.data.materials.append(glass)
    box('Roof',(-.12,0,1.43),(1.38,1.38,.07),paint,.035)
    for y in (-.79,.79):
        box('Window pillar',(-.17,y,1.12),(.09,.045,.49),paint)
        box('Door trim',(-.1,y,.81),(2.1,.045,.045),trim)
        box('Mirror',(.75,y*1.12,1.02),(.22,.11,.12),paint,.035)
        for x in (-1.36,1.35):
            bpy.ops.mesh.primitive_cylinder_add(vertices=12,radius=.31,depth=.19,
                location=(x,y*1.04,.31),rotation=(math.pi/2,0,0))
            bpy.context.object.data.materials.append(tyre)
            bpy.ops.mesh.primitive_cylinder_add(vertices=12,radius=.19,depth=.015,
                location=(x,y*1.17,.31),rotation=(math.pi/2,0,0))
            bpy.context.object.data.materials.append(rim)
        box('Headlight',(2.18,y*.72,.68),(.045,.39,.17),front,.02)
        box('Taillight',(-2.18,y*.72,.68),(.045,.33,.17),rear,.02)
    box('Grille',(2.185,0,.48),(.045,.68,.18),trim,.02)
    bpy.ops.object.select_all(action='SELECT')
    bpy.context.view_layer.objects.active=obj
    bpy.ops.object.join();obj=bpy.context.object;obj.name='Sedan'
    bpy.context.scene.cursor.location=(0,0,0)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    # FBX's explicit basis preserves X-forward/Z-up in the Unreal importer.
    bpy.ops.export_scene.fbx(filepath=str(output),use_selection=True,object_types={'MESH'},
        axis_forward='X',axis_up='Z',apply_unit_scale=True,add_leaf_bones=False,
        bake_anim=False,use_mesh_modifiers=True)
    print('Sedan bounds in metres:',list(obj.dimensions))


if __name__=='__main__':main()
