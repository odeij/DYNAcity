"""Open-window interior for the existing illustrative sedan, same metre basis."""
import argparse
import math
from pathlib import Path
import sys
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_engine_vehicle_blender import box, material


def main():
    raise SystemExit("Retired: use finished library assets through prepare_library_asset_blender.py and library_assets.install().")
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    trim=material('Cockpit charcoal',(.025,.033,.039),0,.72)
    paint=material('Cockpit body',(.16,.24,.3),.45,.32)
    metal=material('Cockpit brushed alloy',(.24,.27,.28),.6,.3)
    dial=material('Instrument face',(.012,.018,.022),0,.45)
    white=material('Instrument marks',(.7,.86,.78),0,.4)
    shader=white.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Emission Color'].default_value=(.35,.75,.55,1)
    shader.inputs['Emission Strength'].default_value=.8
    box('Dashboard',(.62,0,.86),(.54,1.48,.18),trim,.04)
    box('Bonnet',(1.62,0,.78),(1.12,1.67,.12),paint,.04)
    box('Roof',(-.12,0,1.43),(1.38,1.38,.07),trim,.025)
    for y in (-.75,.75):
        box('Door',(-.1,y,.66),(1.95,.08,.40),trim,.02)
        # Sloped A pillars, with open windows so the exterior remains visible.
        pillar=box('A pillar',(.72,y*.93,1.15),(.065,.065,.64),trim,.01)
        pillar.rotation_euler[1]=math.radians(-37)
    box('Centre console',(.12,.07,.59),(1.08,.23,.34),trim,.025)
    box('Instrument binnacle',(.41,-.34,.97),(.14,.54,.23),trim,.025)
    for y in (-.47,-.23):
        bpy.ops.mesh.primitive_cylinder_add(vertices=40,radius=.085,depth=.009,location=(.33,y,.99),rotation=(0,math.pi/2,0))
        bpy.context.object.data.materials.append(dial)
        for i in range(11):
            angle=math.radians(-120+24*i)
            tick=box('Dial tick',(.321,y+math.sin(angle)*.066,.99+math.cos(angle)*.066),(.004,.008,.014),white)
            tick.rotation_euler[0]=-angle
        needle=box('Needle',(.315,y,.99),(.006,.005,.062),white)
        needle.rotation_euler[0]=.7
    bpy.ops.mesh.primitive_torus_add(major_segments=40,minor_segments=8,major_radius=.168,minor_radius=.014,
        location=(.08,-.34,.92),rotation=(0,math.pi/2-.25,0))
    bpy.context.object.data.materials.append(trim)
    box('Steering hub',(.08,-.34,.92),(.055,.12,.075),trim,.01)
    for sign in (-1,1):
        spoke=box('Wheel spoke',(.08,-.34+sign*.09,.92),(.025,.14,.025),metal)
    box('Wheel bottom spoke',(.08,-.34,.83),(.025,.025,.16),metal)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.join()
    obj=bpy.context.object;obj.name='Cockpit'
    bpy.context.scene.cursor.location=(0,0,0);bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    bpy.ops.export_scene.fbx(filepath=str(output),use_selection=True,object_types={'MESH'},axis_forward='X',axis_up='Z',
        apply_unit_scale=True,add_leaf_bones=False,bake_anim=False)


if __name__=='__main__':main()
