"""Original photo-referenced lattice lamp, authorized by the user on 2026-10-31.

Run in Blender. Dimensions are illustrative, not a survey. Private outputs only.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def material(name, color, roughness, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = (*color, 1)
    shader.inputs['Roughness'].default_value = roughness
    shader.inputs['Metallic'].default_value = metallic
    return mat


def box(name, center, size, mat, bevel=.003):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new('Rounded manufactured edges', 'BEVEL')
        mod.width = bevel
        mod.segments = 2
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def beam(name, start, end, width, depth, mat):
    start, end = Vector(start), Vector(end)
    obj = box(name, (start+end)/2, (width, depth, (end-start).length), mat, .0015)
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = (end-start).to_track_quat('Z', 'Y')
    return obj


def bolt(center, mat, radius=.012, depth=.024):
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=radius, depth=depth, location=center)
    obj = bpy.context.object
    obj.name = 'Anchor nut'
    obj.data.materials.append(mat)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    output = args.output.resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('Keep generated models outside the repository')
    output.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.context.scene.unit_settings.system = 'METRIC'
    paint = material('LatticeYellowPaint', (.64, .40, .025), .68)
    concrete = material('FootingConcrete', (.33, .32, .28), .94)
    steel = material('FixtureGraphite', (.045, .05, .055), .53, .25)
    lens = material('FixtureLens', (.72, .73, .64), .30)
    zinc = material('FastenerZinc', (.32, .34, .35), .38, .8)
    box('Square cast concrete footing', (0, 0, .24), (.68, .60, .48), concrete, .018)
    box('Yellow steel base plate', (0, 0, .495), (.48, .40, .03), paint, .004)
    for x in (-.19, .19):
        for y in (-.15, .15):
            bolt((x, y, .524), zinc)
    # Four angle-section uprights: two narrow flanges, with open air between.
    bottom, top, panels = .52, 8.02, 10
    def half(z):
        t = (z-bottom)/(top-bottom)
        return .17-.045*t, .13-.025*t
    for sx in (-1, 1):
        for sy in (-1, 1):
            hx, hy = half(bottom); tx, ty = half(top)
            for ox, oy, w, d in ((-sx*.016, 0, .043, .007), (0, -sy*.016, .007, .043)):
                beam('Angle steel upright', (sx*hx+ox, sy*hy+oy, bottom),
                     (sx*tx+ox, sy*ty+oy, top), w, d, paint)
    for i in range(panels):
        za = bottom+i*(top-bottom)/panels
        zb = bottom+(i+1)*(top-bottom)/panels
        ax, ay = half(za); bx, by = half(zb)
        sign = -1 if i % 2 else 1
        for side in (-1, 1):
            beam('Front back diagonal flat brace', (-sign*ax, side*ay, za),
                 (sign*bx, side*by, zb), .030, .007, paint)
            beam('Side diagonal flat brace', (side*ax, -sign*ay, za),
                 (side*bx, sign*by, zb), .007, .030, paint)
            beam('Front back horizontal tie', (-ax, side*ay, za),
                 (ax, side*ay, za), .025, .008, paint)
            beam('Side horizontal tie', (side*ax, -ay, za),
                 (side*ax, ay, za), .008, .025, paint)
    box('Mast top cap', (0, 0, 8.035), (.29, .25, .03), paint, .003)
    # Separate road-light fixture: attachment is illustrative; photograph shows
    # the head before installation, not a measurable installed arm assembly.
    for z in (7.05, 7.34):
        box('Fixture clamp plate', (.15, 0, z), (.025, .30, .065), zinc, .003)
    curve = bpy.data.curves.new('Bent tubular lamp arm', 'CURVE')
    curve.dimensions = '3D'; curve.resolution_u = 8
    curve.bevel_depth = .022; curve.bevel_resolution = 3
    spline = curve.splines.new('BEZIER'); spline.bezier_points.add(3)
    for point, co in zip(spline.bezier_points, ((.12, 0, 7.15), (.45, 0, 7.37), (.85, 0, 7.48), (1.12, 0, 7.48))):
        point.co = co; point.handle_left_type = point.handle_right_type = 'AUTO'
    arm = bpy.data.objects.new('Separate graphite fixture arm', curve)
    bpy.context.collection.objects.link(arm); arm.data.materials.append(steel)
    box('Slim road light housing', (1.25, 0, 7.475), (.72, .245, .105), steel, .049)
    box('Optic underside gasket', (1.30, 0, 7.418), (.52, .18, .018), zinc, .018)
    for x in (1.17, 1.41):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1, location=(x, 0, 7.405))
        obj = bpy.context.object; obj.name = 'Recessed unlit optic'
        obj.scale = (.084, .074, .015); obj.data.materials.append(lens)
    for x in (1.04, 1.12, 1.20, 1.28, 1.36, 1.44):
        box('Housing heat sink fin', (x, 0, 7.535), (.013, .16, .026), steel, .002)
    objects = [o for o in bpy.context.scene.objects if o.type in ('MESH', 'CURVE')]
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects: obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.convert(target='MESH'); bpy.ops.object.join()
    obj = bpy.context.object; obj.name = 'LebaneseLatticeLamp'
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    obj.data.calc_loop_triangles()
    points = [obj.matrix_world @ v.co for v in obj.data.vertices]
    bounds = [[min(v[i] for v in points) for i in range(3)], [max(v[i] for v in points) for i in range(3)]]
    assert abs(bounds[0][2]) < .001 and 7.9 < bounds[1][2] < 8.2
    fbx = output/'lebanese-lattice-lamp.fbx'
    bpy.ops.export_scene.fbx(filepath=str(fbx), use_selection=True, object_types={'MESH'}, axis_forward='X', axis_up='Z', apply_unit_scale=True, add_leaf_bones=False, bake_anim=False)
    bpy.ops.export_scene.gltf(filepath=str(output/'lebanese-lattice-lamp.glb'), export_format='GLB', use_selection=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'lebanese-lattice-lamp.blend'))
    record = {'fbx':str(fbx), 'sha256_fbx':hashlib.sha256(fbx.read_bytes()).hexdigest(), 'bounds_m':bounds,
              'dimensions_cm':[(bounds[1][i]-bounds[0][i])*100 for i in range(3)],
              'light_anchor_cm':[130, 0, 735], 'facing':'+X points toward carriageway',
              'triangles':len(obj.data.loop_triangles), 'materials':{m.name:{'color':list(m.diffuse_color[:3]), 'roughness':m.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value, 'metallic':m.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value} for m in obj.data.materials},
              'authorship':'Original DYNAcity photo-referenced model; user-authorized exception on 2026-10-31',
              'reference':'https://amalbaladi.org.lb/details/2708/',
              'limitations':['Illustrative 8 m mast and fixture assembly; no measured dimensions.', 'Source photograph used for visual study, not as a texture.', 'No electrical wires or current operating condition inferred.']}
    (output/'model.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps({'fbx':str(fbx), 'triangles':record['triangles'], 'dimensions_cm':record['dimensions_cm']}))


if __name__ == '__main__':
    main()
