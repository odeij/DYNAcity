"""Build original illustrative street props for mapped Beirut placements.

Run Blender --background --python this_file -- --output PRIVATE/props.
Original meshes; optional CC0 photographic foliage maps. Metres, +X facing, +Z up, ground
origin. Signal lenses are unlit: these models do not represent a live phase.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sys

import bpy
from mathutils import Vector

TEXTURED_MATERIALS = {}


def foliage_material(root, slot, leaf=False):
    """Explicit map records let Unreal rebuild role-aware materials without FBX heuristics."""
    prefix='leaves' if leaf else 'branch'
    maps={role:str(root/'textures'/('tree_small_02_'+prefix+'_'+suffix+'_1k.'+ext))
          for role,suffix,ext in [('base_color','diff','jpg'),('normal','nor_dx','jpg'),
                                  ('roughness','rough','jpg')]}
    if leaf:maps['opacity']=str(root/'textures/tree_small_02_leaves_alpha_1k.png')
    for path in maps.values():
        if not Path(path).is_file() or Path(path).stat().st_size<1000:raise ValueError('Missing map '+path)
    record=dict(slot_name=slot,**maps,masked=leaf,two_sided=leaf)
    TEXTURED_MATERIALS[slot]=record
    mat=material(slot,(.25,.38,.12) if leaf else (.25,.17,.09))
    nodes=mat.node_tree.nodes;links=mat.node_tree.links;bsdf=nodes.get('Principled BSDF')
    for role,input_name in [('base_color','Base Color'),('roughness','Roughness'),('opacity','Alpha')]:
        if role not in maps:continue
        tex=nodes.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(maps[role],check_existing=True)
        if role!='base_color':tex.image.colorspace_settings.name='Non-Color'
        links.new(tex.outputs['Color'],bsdf.inputs[input_name])
    # Blender uses OpenGL normals; Unreal receives the DirectX file recorded above.
    normal_path=maps['normal'].replace('nor_dx','nor_gl')
    if Path(normal_path).is_file():
        tex=nodes.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(normal_path,check_existing=True)
        tex.image.colorspace_settings.name='Non-Color';normal=nodes.new('ShaderNodeNormalMap')
        links.new(tex.outputs['Color'],normal.inputs['Color']);links.new(normal.outputs['Normal'],bsdf.inputs['Normal'])
    if leaf:
        mat.surface_render_method='DITHERED';bsdf.inputs['Subsurface Weight'].default_value=.12
    return mat


def leaf_cards(name, cards, mat, full_atlas=False):
    """Individual photographed leaves; narrow bounding UVs avoid atlas neighbours."""
    verts=[];faces=[];uvs=[]
    # Pixel boxes in the official 1024 atlas (top-origin image coordinates).
    crops=[(29,319,216,442),(241,291,371,371),(251,425,420,517),(578,544,708,619)]
    if full_atlas:crops=[(0,0,1024,1024)]
    for centre,axis,side,length,width,index in cards:
        centre=Vector(centre);axis=Vector(axis).normalized();side=Vector(side).normalized()
        normal=axis.cross(side).normalized();n=len(verts)
        verts.extend([tuple(centre-axis*length/2-side*width/2),tuple(centre-axis*length/2+side*width/2),
                      tuple(centre+axis*length/2+side*width/2),tuple(centre+axis*length/2-side*width/2)])
        faces.append((n,n+1,n+2,n+3))
        x0,y0,x1,y1=crops[index%len(crops)]
        uvs.extend([(x0/1024,1-y1/1024),(x0/1024,1-y0/1024),
                    (x1/1024,1-y0/1024),(x1/1024,1-y1/1024)])
    obj=polygon(name,verts,faces,mat);layer=obj.data.uv_layers.new(name='UVMap')
    for loop in obj.data.loops:layer.data[loop.index].uv=uvs[loop.vertex_index]
    for face in obj.data.polygons:face.use_smooth=True
    return obj


def textured_broadleaf(mats):
    random.seed(7301);bark=mats['photo_bark'];leaves=mats['photo_leaves']
    rod('Root flare',(0,0,0),(.02,0,.32),.25,bark,12,tip=.17)
    rod('Curved lower trunk',(.02,0,.25),(.12,-.07,2.5),.17,bark,12,tip=.11)
    rod('Crown leader',(.12,-.07,2.5),(.26,.04,4.75),.11,bark,10,tip=.008)
    cards=[]
    for i in range(17):
        angle=i*2.39996;radius=random.uniform(1.2,1.95)*(1-.18*(i/17))
        base=Vector((.12,-.07,2.5+i*.105));tip=Vector((math.cos(angle)*radius,math.sin(angle)*radius,3.6+i*.10))
        mid=base.lerp(tip,.5)+Vector((0,0,.25))
        rod('Scaffold branch',base,mid,.055,bark,8,tip=.032);rod('Secondary branch',mid,tip,.032,bark,7,tip=.009)
        for j in range(8):
            t=.35+j*.09;start=base.lerp(tip,t)
            turn=angle+(-1 if j%2 else 1)*random.uniform(.45,1.2)
            end=start+Vector((math.cos(turn)*random.uniform(.3,.65),math.sin(turn)*random.uniform(.3,.65),random.uniform(.12,.45)))
            rod('Fine twig',start,end,.014,bark,5,tip=.003)
            for k in range(18):
                p=start.lerp(end,k/17)+Vector((random.uniform(-.22,.22),random.uniform(-.22,.22),random.uniform(-.20,.24)))
                axis=Vector((math.cos(turn+random.uniform(-1.1,1.1)),math.sin(turn+random.uniform(-1.1,1.1)),random.uniform(-.3,.7)))
                side=Vector((-axis.y,axis.x,random.uniform(-.8,.8)))
                length=random.uniform(.20,.34);cards.append((p,axis,side,length,length*.62,k+i))
    leaf_cards('Photographic individual leaf cards',cards,leaves)


def textured_conifer(mats):
    """Generic needle-leaved form; no species claim without a source species tag."""
    random.seed(9182);bark=mats.get('pine_bark',mats['bark']);leaves=mats.get('pine_leaves',mats['leaf0'])
    rod('Conifer trunk',(0,0,0),(.08,.03,6),.18,bark,12,tip=.015)
    cards=[]
    for tier in range(8):
        z=1.8+tier*.48;radius=1.75*(1-tier/10)
        for branch in range(7):
            angle=branch*math.tau/7+tier*1.7
            start=Vector((.04,0,z));end=Vector((math.cos(angle)*radius,math.sin(angle)*radius,z+.15))
            rod('Needle branch',start,end,.035,bark,6,tip=.006)
            for i in range(7):
                centre=start.lerp(end,.3+i*.105)+Vector((0,0,random.uniform(-.12,.15)))
                axis=Vector((math.cos(angle),math.sin(angle),random.uniform(-.25,.4)))
                side=Vector((-axis.y,axis.x,.2))
                cards.append((centre,axis,side,.6,.52,i))
                cards.append((centre,axis,Vector((0,0,1)),.6,.52,i))
    leaf_cards('Photographic needle sprigs',cards,leaves,full_atlas=True)


def pine_material(root,slot,leaf=False):
    prefix='twig' if leaf else 'bark'
    maps={role:str(root/'textures'/f'pine_tree_01_{prefix}_{suffix}_1k.jpg')
          for role,suffix in [('base_color','diff'),('normal','nor_dx'),('roughness','rough')]}
    if leaf:maps['opacity']=str(root/'textures/pine_tree_01_twig_alpha_1k.png')
    for path in maps.values():
        if not Path(path).is_file():raise ValueError('Missing photographic map '+path)
    TEXTURED_MATERIALS[slot]=dict(slot_name=slot,**maps,masked=leaf,two_sided=leaf)
    mat=material(slot,(.18,.28,.09) if leaf else (.27,.20,.13))
    nodes=mat.node_tree.nodes;links=mat.node_tree.links;bsdf=nodes.get('Principled BSDF')
    for role,target in [('base_color','Base Color'),('roughness','Roughness'),('opacity','Alpha')]:
        if role not in maps:continue
        tex=nodes.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(maps[role],check_existing=True)
        if role!='base_color':tex.image.colorspace_settings.name='Non-Color'
        links.new(tex.outputs['Color'],bsdf.inputs[target])
    if leaf:mat.surface_render_method='DITHERED'
    return mat


def material(name, colour, metallic=0, roughness=.6):
    result=bpy.data.materials.get(name) or bpy.data.materials.new(name)
    result.diffuse_color=(*colour,1);result.use_nodes=True
    node=result.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value=(*colour,1)
    node.inputs['Metallic'].default_value=metallic
    node.inputs['Roughness'].default_value=roughness
    return result


def box(name,position,size,mat,bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1,location=position)
    obj=bpy.context.object;obj.name=name;obj.dimensions=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    obj.data.materials.append(mat)
    if bevel:
        modifier=obj.modifiers.new('Rounded manufactured edges','BEVEL')
        modifier.width=bevel;modifier.segments=2
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def rod(name,start,end,radius,mat,vertices=10,tip=None):
    start,end=Vector(start),Vector(end);direction=end-start
    bpy.ops.mesh.primitive_cone_add(vertices=vertices,radius1=radius,
        radius2=radius if tip is None else tip,depth=direction.length,location=(start+end)/2)
    obj=bpy.context.object;obj.name=name
    obj.rotation_euler=direction.to_track_quat('Z','Y').to_euler()
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:polygon.use_smooth=True
    return obj


def ellipsoid(name,position,size,mat,subdivisions=2):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions,radius=1,location=position)
    obj=bpy.context.object;obj.name=name;obj.scale=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:polygon.use_smooth=True
    return obj


def polygon(name,points,faces,mat):
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(points,[],faces);mesh.update()
    obj=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def traffic_signal(mats,direction=None):
    metal,dark,base=mats['galvanized'],mats['black'],mats['concrete']
    rod('Concrete socket',(0,0,0),(0,0,.16),.17,base,12)
    rod('Galvanized pole',(0,0,.12),(0,0,3.55),.063,metal,12,tip=.045)
    for z in (2.63,3.32):
        rod('Rear bracket',(0,0,z),(.16,0,z),.025,dark,8)
    box('Signal backplate',(.17,0,2.99),(.09,.57,1.26),dark,.025)
    box('Three chamber housing',(.25,0,2.99),(.20,.39,1.12),dark,.04)
    for z,colour in zip((3.35,2.99,2.63),('red','amber','green')):
        rod('Lens black rim',(.35,0,z),(.38,0,z),.135,dark,20)
        if direction is None:
            ellipsoid('Unlit '+colour+' lens',(.389,0,z),(.018,.112,.112),mats[colour],3)
        else:
            ellipsoid('Unlit lens backing',(.389,0,z),(.012,.112,.112),mats['lens_back'],3)
            # +Y projects to screen-right when viewing the +X-facing head.
            sign=1 if direction=='left' else -1
            outline=[(-.085,0),(-.012,.070),(-.012,.026),(.075,.026),
                     (.075,-.026),(-.012,-.026),(-.012,-.070)]
            points=[(.408,y*sign,z+v) for y,v in outline]
            order=list(range(len(points)))
            area=sum(a[1]*b[2]-b[1]*a[2] for a,b in zip(points,points[1:]+points[:1]))
            if area<0:order.reverse()
            polygon(colour+' '+direction+' arrow',points,[order],mats[colour])
        # Tapered three-sided hood, clear of the visible lens face.
        verts=[(.36,-.145,z+.09),(.36,.145,z+.09),(.55,.13,z+.13),(.55,-.13,z+.13),
               (.36,-.145,z-.09),(.36,.145,z-.09),(.52,-.13,z-.05),(.52,.13,z-.05)]
        polygon('Sun visor',verts,[(0,1,2,3),(4,0,3,6),(1,5,7,2)],dark)


def broadleaf(mats):
    rod('Root flare',(0,0,0),(0,0,.32),.25,mats['bark'],12,tip=.17)
    rod('Trunk',(0,0,.25),(.08,-.03,3.8),.17,mats['bark'],12,tip=.085)
    random.seed(41)
    for i in range(9):
        angle=i*math.tau/9;rad=1.0 if i%3 else .65
        end=(math.cos(angle)*rad,math.sin(angle)*rad,3.7+random.uniform(-.25,.55))
        rod('Crown branch',(.04,0,2.35+random.uniform(0,.6)),end,.065,mats['bark'],8,tip=.02)
        ellipsoid('Leaf cluster',(*end[:2],end[2]+.58),
            (1.25+random.uniform(-.2,.2),1.10+random.uniform(-.2,.2),1.30),mats['leaf'+str(i%3)])
    ellipsoid('Crown centre',(.1,.05,4.9),(1.5,1.45,1.18),mats['leaf1'])


def palm(mats):
    previous=Vector((0,0,0))
    for i in range(8):
        z=(i+1)*.76;end=Vector((.18*(z/6.08)**2,0,z))
        rod('Palm trunk segment',previous,end,.19-i*.009,mats['palm_bark'],12,tip=.18-i*.009)
        previous=end
    centre=previous
    ellipsoid('Palm crown base',centre,(.35,.35,.45),mats['leaf0'])
    for i in range(12):
        a=i*math.tau/12;axis=Vector((math.cos(a),math.sin(a),0));side=Vector((-math.sin(a),math.cos(a),0))
        points=[]
        for j in range(9):
            t=j/8
            point=centre+axis*(t*2.65)+Vector((0,0,.8*math.sin(t*math.pi)-.45*t))
            points.append(point)
            if j:rod('Frond rachis',points[j-1],point,.026*(1-t)+.005,mats['leaf1'],6)
            if 0<j<8:
                width=.55*math.sin(t*math.pi)
                for sign in (-1,1):
                    tip=point+side*(width*sign)-axis*.21+Vector((0,0,-.22))
                    polygon('Palm leaflet',[tuple(point),tuple(tip),tuple(point-axis*.24)],[(0,1,2)],mats['leaf'+str(i%3)])


def street_lamp(mats):
    rod('Lamp base',(0,0,0),(0,0,.18),.18,mats['concrete'],12)
    rod('Tapered lamp pole',(0,0,.15),(0,0,7.3),.095,mats['galvanized'],12,tip=.045)
    path=[(0,0,7.1),(.20,0,7.45),(.62,0,7.62),(1.15,0,7.62),(1.55,0,7.56)]
    for a,b in zip(path,path[1:]):rod('Curved bracket',a,b,.037,mats['galvanized'],10)
    box('Luminaire',(1.6,0,7.52),(.75,.29,.13),mats['black'],.055)
    box('Unlit diffuser',(1.68,0,7.447),(.55,.20,.012),mats['white'],.02)


def bench(mats):
    for y in (-.73,.73):
        for x in (-.22,.22):rod('Bench leg',(x,y,0),(x,y,.43),.035,mats['black'],8)
        rod('Bench back support',(-.25,y,.2),(-.31,y,.91),.03,mats['black'],8)
        rod('Bench arm rest',(.22,y,.64),(-.25,y,.64),.028,mats['black'],8)
    for x in (-.23,-.115,0,.115,.23):box('Seat timber',(x,0,.46),(.09,1.8,.065),mats['wood'],.012)
    for z in (.68,.81):box('Back timber',(-.29,0,z),(.065,1.8,.10),mats['wood'],.012)


def bus_stop(mats):
    rod('Bus stop pole',(0,0,0),(0,0,2.7),.035,mats['galvanized'],10)
    box('Stop marker',(.02,0,2.43),(.07,.48,.49),mats['blue'],.025)
    # Pictogram has no fabricated operator name or route information.
    box('Bus pictogram',(.059,0,2.43),(.008,.28,.26),mats['white'],.025)
    box('Pictogram windscreen',(.065,0,2.47),(.008,.22,.09),mats['blue'],.01)
    for y in (-.08,.08):box('Pictogram wheel',(.065,y,2.29),(.008,.045,.055),mats['white'],.01)


def bollard(mats):
    rod('Bollard',(0,0,0),(0,0,.84),.065,mats['black'],12)
    rod('Reflective collar',(0,0,.66),(0,0,.73),.067,mats['white'],12)
    ellipsoid('Rounded cap',(0,0,.84),(.065,.065,.045),mats['black'])


def crossing(mats):
    for y in (-2.50,-1.50,-.50,.50,1.50,2.50):
        box('Worn off-white crossing stripe',(0,y,.004),(3,.48,.008),mats['marking'])


def gate(mats):
    for y in (-1.2,1.2):rod('Gate post',(0,y,0),(0,y,1.35),.055,mats['black'],10)
    for z in (.25,1.15):box('Gate rail',(.02,0,z),(.055,2.25,.055),mats['black'],.012)
    for i in range(9):box('Gate picket',(.02,-1.05+i*.2625,.70),(.035,.035,.90),mats['black'],.006)


def lift_gate(mats):
    box('Barrier footing',(0,-1.6,.10),(.42,.42,.20),mats['concrete'],.03)
    box('Barrier pedestal',(0,-1.6,.58),(.29,.30,.85),mats['black'],.04)
    box('Illustrative lowered arm',(.03,0,.92),(.085,3.4,.085),mats['white'],.012)
    for y in (-1.2,-.4,.4,1.2):box('Red arm reflector',(.076,y,.92),(.009,.24,.085),mats['red'])


def block(mats):
    # Bevelled concrete traffic block. Mapped point dimensions are illustrative.
    box('Concrete barrier block',(0,0,.40),(.65,1.4,.80),mats['concrete'],.09)
    for y in (-.48,.48):box('Reflector',(.33,y,.57),(.012,.17,.13),mats['white'],.015)


def waste_basket(mats):
    rod('Litter bin base',(0,0,0),(0,0,.12),.27,mats['black'],16)
    rod('Litter bin',(0,0,.10),(0,0,.86),.29,mats['bin_green'],16,tip=.31)
    rod('Opening',(0,0,.861),(0,0,.866),.245,mats['black'],16)
    for y in (-.27,.27):rod('Lid support',(0,y,.81),(0,y,1.02),.018,mats['black'],8)
    box('Rain lid',(0,0,1.025),(.66,.65,.065),mats['bin_green'],.06)


def bicycle_parking(mats):
    points=[(0,-.30,0),(0,-.30,.73)]
    for i in range(1,9):
        a=math.pi-i*math.pi/8
        points.append((0,.30*math.cos(a),.73+.30*math.sin(a)))
    points.append((0,.30,0))
    for a,b in zip(points,points[1:]):rod('Bicycle stand tube',a,b,.033,mats['galvanized'],10)
    for y in (-.30,.30):box('Anchor plate',(0,y,.025),(.16,.16,.05),mats['galvanized'],.015)


def fountain(mats):
    rod('Basin plinth',(0,0,0),(0,0,.14),1.12,mats['concrete'],40)
    rod('Basin water surface',(0,0,.141),(0,0,.15),1.0,mats['water'],40)
    bpy.ops.mesh.primitive_torus_add(major_radius=1.08,minor_radius=.10,
        major_segments=40,minor_segments=8,location=(0,0,.24))
    bpy.context.object.name='Stone basin rim';bpy.context.object.data.materials.append(mats['concrete'])
    rod('Central stone',(0,0,.14),(0,0,.5),.19,mats['concrete'],16,tip=.14)


def shelter(mats):
    for x in (-.60,.60):
        for y in (-1.2,1.2):rod('Shelter support',(x,y,0),(x,y,2.45),.035,mats['black'],10)
    box('Shelter roof',(0,0,2.48),(1.55,2.7,.11),mats['galvanized'],.04)
    # Open shelter: no fake advertising or invented route/operator signage.
    box('Shelter back rail',(-.60,0,1.1),(.04,2.4,.04),mats['black'],.008)
    for y in (-1.2,1.2):box('Shelter side rail',(0,y,1.1),(1.2,.04,.04),mats['black'],.008)


def main():
    raise SystemExit("Retired: use finished library assets through prepare_library_asset_blender.py and library_assets.install().")
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--only',help='Comma-separated asset keys; merge these into an existing manifest')
    parser.add_argument('--textured-trees',action='store_true',help='Use previously downloaded official Poly Haven leaf/bark maps')
    parser.add_argument('--preview',action='store_true',help='Render a small EEVEE broadleaf preview')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
    mats={key:material(key,colour,metal,rough) for key,colour,metal,rough in [
        ('galvanized',(.33,.35,.35),.65,.4),('black',(.025,.030,.029),.3,.52),
        ('concrete',(.39,.37,.32),0,.88),('red',(.32,.008,.004),.05,.24),
        ('amber',(.44,.19,.006),.05,.24),('green',(.008,.21,.046),.05,.24),
        ('lens_back',(.008,.012,.012),0,.3),('bark',(.19,.11,.058),0,.96),
        ('palm_bark',(.30,.23,.14),0,.95),('leaf0',(.055,.13,.022),0,.9),
        ('leaf1',(.095,.21,.035),0,.88),('leaf2',(.14,.26,.055),0,.9),
        ('wood',(.25,.13,.065),0,.78),('white',(.77,.77,.69),0,.38),
        ('blue',(.025,.15,.32),.1,.5),('marking',(.70,.68,.57),0,.95),
        ('bin_green',(.035,.095,.070),.25,.65),('water',(.045,.13,.15),.15,.24) ]}
    if args.textured_trees:
        mats['photo_bark']=foliage_material(root,'BeirutPhotoBark')
        mats['photo_leaves']=foliage_material(root,'BeirutPhotoLeaves',True)
        if not args.only or 'tree_conifer' in args.only:
            mats['pine_bark']=pine_material(root,'BeirutPineBark')
            mats['pine_leaves']=pine_material(root,'BeirutPineNeedles',True)
    builders={'traffic_signal':lambda:traffic_signal(mats),
        'traffic_signal_left':lambda:traffic_signal(mats,'left'),
        'traffic_signal_right':lambda:traffic_signal(mats,'right'),
        'tree_broadleaf':lambda:textured_broadleaf(mats) if args.textured_trees else broadleaf(mats),'tree_palm':lambda:palm(mats),
        'tree_conifer':lambda:textured_conifer(mats),
        'street_lamp':lambda:street_lamp(mats),'bench':lambda:bench(mats),
        'bus_stop':lambda:bus_stop(mats),'bollard':lambda:bollard(mats),
        'crossing_marking':lambda:crossing(mats),'gate':lambda:gate(mats),
        'lift_gate':lambda:lift_gate(mats),'block':lambda:block(mats),
        'waste_basket':lambda:waste_basket(mats),'bicycle_parking':lambda:bicycle_parking(mats),
        'fountain':lambda:fountain(mats),'shelter':lambda:shelter(mats)}
    existing=root/'manifest.json'
    records=json.loads(existing.read_text())['assets'] if args.only and existing.is_file() else {}
    wanted=set(args.only.split(',')) if args.only else set(builders)
    if wanted-set(builders):raise ValueError('Unknown asset keys: '+str(wanted-set(builders)))
    for name,build in builders.items():
        if name not in wanted:continue
        bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
        build();bpy.ops.object.select_all(action='SELECT')
        bpy.context.view_layer.objects.active=next(o for o in bpy.context.scene.objects if o.type=='MESH')
        bpy.ops.object.join();obj=bpy.context.object;obj.name=name
        bpy.context.scene.cursor.location=(0,0,0);bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
        bpy.ops.object.transform_apply(location=False,rotation=True,scale=True)
        if args.textured_trees and name=='tree_broadleaf':
            obj.scale.z=6.08/obj.dimensions.z
            bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
            ground=min(v.co.z for v in obj.data.vertices)
            for vertex in obj.data.vertices:vertex.co.z-=ground
        coords=[obj.matrix_world@Vector(p) for p in obj.bound_box]
        low=[min(p[i] for p in coords) for i in range(3)];high=[max(p[i] for p in coords) for i in range(3)]
        fbx=root/(name+'.fbx');glb=root/(name+'.glb')
        bpy.ops.export_scene.fbx(filepath=str(fbx),use_selection=True,object_types={'MESH'},axis_forward='X',axis_up='Z',apply_unit_scale=True,add_leaf_bones=False,bake_anim=False,use_mesh_modifiers=True)
        bpy.ops.export_scene.gltf(filepath=str(glb),export_format='GLB',use_selection=True,export_yup=True)
        record={'fbx':str(fbx),'glb':str(glb),'dimensions_m':[round(high[i]-low[i],5) for i in range(3)],
                'bounds_m':[[round(v,5) for v in low],[round(v,5) for v in high]],
                'facing':'+X','origin':'ground contact at local 0','units':'metres',
                'vertices':len(obj.data.vertices),'triangles':sum(len(p.vertices)-2 for p in obj.data.polygons),
                'material_slots':len(obj.data.materials),'sha256_fbx':hashlib.sha256(fbx.read_bytes()).hexdigest(),
                'illustrative':True,'live_state':False}
        if name=='tree_broadleaf':record.update(trunk_height_m=2.35,trunk_radius_m=.17)
        if name=='tree_palm':record.update(trunk_height_m=6.08,trunk_radius_m=.19)
        if name=='tree_conifer' and args.textured_trees:
            record.update(trunk_height_m=1.8,trunk_radius_m=.18,
                materials=[TEXTURED_MATERIALS[m.name] for m in obj.data.materials],
                texture_source={'url':'https://polyhaven.com/a/pine_tree_01','license':'CC0-1.0',
                                'author':'Rico Cilliers / Rob Tuytel'},
                geometry='Original generic conifer branches and photographed needle sprigs; species illustrative')
        if args.textured_trees and name=='tree_broadleaf':
            record.update(version='2-photographic-leafcards',materials=[TEXTURED_MATERIALS[m.name] for m in obj.data.materials],
                          texture_source={'asset':'Tree Small 02','author':'Rico Cilliers','url':'https://polyhaven.com/a/tree_small_02',
                                          'license':'CC0-1.0','license_url':'https://polyhaven.com/license'},
                          geometry='Original branched mesh with 2448 individually UV-mapped leaf cards; no solid canopy')
        records[name]=record
        print(name,record['dimensions_m'],record['triangles'])
        if args.preview and name=='tree_broadleaf':
            scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=12;scene.cycles.device='CPU'
            scene.render.resolution_x=700;scene.render.resolution_y=800;scene.render.resolution_percentage=100
            scene.world.color=(.35,.35,.35)
            floor=material('Preview ground',(.28,.27,.24));box('Preview floor',(0,0,-.05),(200,200,.1),floor)
            bpy.ops.object.light_add(type='AREA',location=(3,-4,8));bpy.context.object.data.energy=1200;bpy.context.object.data.shape='DISK';bpy.context.object.data.size=5
            bpy.context.object.rotation_euler=(Vector((0,0,3))-bpy.context.object.location).to_track_quat('-Z','Y').to_euler()
            bpy.ops.object.camera_add(location=(8,-10,5));camera=bpy.context.object
            camera.rotation_euler=(Vector((0,0,3))-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=7.3;scene.camera=camera
            scene.render.filepath=str(root/'tree_broadleaf-preview.png');bpy.ops.render.render(write_still=True)
    manifest={'schema':'dynacity-street-props/1','authored_frame':'+X forward, +Y left, +Z up; metres',
        'fbx_axes':'X forward, Z up; Unreal import scale must be verified',
        'glb_axes':'glTF right-handed Y up; Blender converted from authored Z up',
        'assets':records,'source':'Original procedural geometry; version 2 trees use CC0 photographic maps identified per asset',
        'limitations':['Illustrative street hardware, not measured Beirut equipment.',
                       'Tree species/size/shape only defaults unless placement source supplies tags.',
                       'Signal lenses are unlit; no live traffic phase is inferred.',
                       'Only use direction variants, palms and crossing stripes where source tags justify them.']}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__=='__main__':main()
