"""Render existing photographic OBJ captures unchanged for identity inspection."""
import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def main():
    p=argparse.ArgumentParser();p.add_argument('--inventory',required=True);p.add_argument('--output',required=True)
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:]);output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    records=json.loads(Path(args.inventory).read_text(encoding='utf8'))['records'];report=[]
    for row in records:
        if row['kind']!='obj' or row['id']=='Me_1_127':continue
        source=Path(row['path'])
        if getattr(source.stat(),'st_file_attributes',0)&(0x1000|0x400000):continue
        bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
        bpy.ops.wm.obj_import(filepath=str(source),forward_axis='Y',up_axis='Z')
        objects=[o for o in bpy.context.scene.objects if o.type=='MESH']
        points=[o.matrix_world@Vector(v) for o in objects for v in o.bound_box]
        low=Vector([min(v[i] for v in points) for i in range(3)]);high=Vector([max(v[i] for v in points) for i in range(3)])
        centre=(low+high)/2;diameter=(high-low).length
        # Source textures already contain photographic lighting. Display their
        # colour without adding an invented sun/shadow interpretation.
        for mat in {m for o in objects for m in o.data.materials if m}:
            if not mat.use_nodes:continue
            nodes=mat.node_tree.nodes;principled=next((n for n in nodes if n.type=='BSDF_PRINCIPLED'),None)
            output_node=next((n for n in nodes if n.type=='OUTPUT_MATERIAL'),None)
            if not principled or not output_node:continue
            colour_input=principled.inputs['Base Color'];links=list(colour_input.links)
            emission=nodes.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=.85
            if links:mat.node_tree.links.new(links[0].from_socket,emission.inputs['Color'])
            else:emission.inputs['Color'].default_value=colour_input.default_value
            mat.node_tree.links.new(emission.outputs[0],output_node.inputs['Surface'])
        scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=8
        scene.cycles.device='CPU';scene.world.color=(.14,.15,.17)
        scene.render.resolution_x=960;scene.render.resolution_y=800;scene.render.resolution_percentage=100
        scene.view_settings.view_transform='Standard';scene.render.image_settings.file_format='PNG'
        camera_data=bpy.data.cameras.new('InspectionCamera');camera=bpy.data.objects.new('InspectionCamera',camera_data)
        scene.collection.objects.link(camera);scene.camera=camera;camera.data.lens=48
        views=[]
        directions=[('a',Vector((1,-1,.65))),('b',Vector((-1,1,.5)))]
        if row['id']=='Ac_726':directions.append(('top',Vector((0,0,1))))
        for suffix,direction in directions:
            target=output/(row['id']+'-'+suffix+'.png')
            if target.exists():views.append(str(target));continue
            camera.data.type='ORTHO' if suffix=='top' else 'PERSP'
            if suffix=='top':camera.data.ortho_scale=max(high.x-low.x,(high.y-low.y)*960/800)*1.1
            camera.location=centre+direction.normalized()*diameter*1.35
            camera.rotation_euler=(centre-camera.location).to_track_quat('-Z','Y').to_euler()
            scene.render.filepath=str(target)
            bpy.ops.render.render(write_still=True);views.append(str(target))
        report.append({'id':row['id'],'source':str(source),'bounds':[list(low),list(high)],'views':views,
                       'geometry':'original source faces and UVs; no reconstruction','placement':'isolated inspection, not matched city geometry'})
    (output/'previews.json').write_text(json.dumps(report,indent=2),encoding='utf8')


if __name__=='__main__':main()
