"""Bake Microsoft Rocketbox people (MIT) into small walking meshes for the 3D viewer.

Run inside Blender (tested with 4.5):

    blender -b --factory-startup --python scripts/prepare_people_models.py -- <rocketbox_dir>

`<rocketbox_dir>` holds what was downloaded from github.com/microsoft/Microsoft-Rocketbox
(kept outside the repository; the textures alone are ~25 MB per person):

    avatars/<Name>/<Name>.fbx                         Assets/Avatars/<group>/<Name>/Export/<Name>.fbx
    avatars/<Name>/<code>_{body,head,opacity}_color.tga   .../<Name>/Textures/
    animations/{m,f}_walk_neutral.fbx                Assets/Animations/all_animations_max_motextr_xy/*.max.fbx

For each person the script binds the in-place ("motion extracted") neutral walk to the
person's skeleton, simplifies the mesh once in rest pose (so every pose has the same
triangles), and records FRAMES poses spread over one walk cycle. Colours are the person's
own textures averaged over each triangle (hair cards keep only their opaque triangles).
Output: src/dynacity/assets/people/meshes.json, a triangle soup per person:
  colors  base64 uint8 RGB per vertex (sRGB, shared by every pose)
  frames  base64 int16 xyz per vertex in millimetres, +x forward, +z up, feet at z = 0
The viewer computes flat normals itself, so no normals are stored.
"""
import base64
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'src/dynacity/assets/people/meshes.json'
SRC = Path(sys.argv[sys.argv.index('--') + 1]) if '--' in sys.argv else Path(r'C:\private\dynacity-data\rocketbox')

# Everyday clothes from Rocketbox "Adults" (name, walk clip by sex).
PEOPLE = [
    ('Female_Adult_01', 'f'), ('Female_Adult_04', 'f'), ('Female_Adult_07', 'f'),
    ('Female_Adult_09', 'f'), ('Female_Adult_14', 'f'), ('Female_Adult_15', 'f'),
    ('Male_Adult_01', 'm'), ('Male_Adult_03', 'm'), ('Male_Adult_04', 'm'),
    ('Male_Adult_08', 'm'), ('Male_Adult_12', 'm'), ('Male_Adult_14', 'm'),
]
FRAMES = 8          # poses per walk cycle
TARGET_TRIS = 1500  # per person, after simplification
MIN_HAIR_ALPHA = .45


def log(*a):
    print('[people]', *a, flush=True)


def texture(path):
    img = bpy.data.images.load(str(path), check_existing=True)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4), w, h


def sample(tex, uv):
    px, w, h = tex
    u = np.mod(uv[:, 0], 1.0); v = np.mod(uv[:, 1], 1.0)
    x = np.clip((u * (w - 1)).astype(int), 0, w - 1); y = np.clip((v * (h - 1)).astype(int), 0, h - 1)
    return px[y, x]


def walk_rig(clip):
    """Import the walk clip's own skeleton (it plays the clip) and return it with the clip's action."""
    before = set(bpy.data.actions); objs = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(SRC / 'animations' / f'{clip}.fbx'))
    new_objs = [o for o in bpy.data.objects if o not in objs]
    rig = next(o for o in new_objs if o.type == 'ARMATURE')
    for o in new_objs:
        if o is not rig: bpy.data.objects.remove(o, do_unlink=True)
    act = rig.animation_data.action
    return rig, act


def bake(name, sex):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    folder = SRC / 'avatars' / name
    bpy.ops.import_scene.fbx(filepath=str(folder / f'{name}.fbx'))
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    body = next(o for o in bpy.data.objects if o.type == 'MESH')
    for o in [o for o in bpy.data.objects if o not in (arm, body)]: bpy.data.objects.remove(o, do_unlink=True)
    rig, act = walk_rig(f'{sex}_walk_neutral')
    # Copying the clip's local bone rotations onto another Biped leaves the arms up (the two
    # skeletons' rest poses differ); following each clip bone's world orientation does not.
    for pb_ in arm.pose.bones:
        if pb_.name not in rig.pose.bones: continue
        c = pb_.constraints.new('COPY_ROTATION'); c.target = rig; c.subtarget = pb_.name
        c.target_space = c.owner_space = 'WORLD'
        if pb_.name == 'Bip01 Pelvis':
            c = pb_.constraints.new('COPY_LOCATION'); c.target = rig; c.subtarget = pb_.name
            c.target_space = c.owner_space = 'WORLD'
    start, end = (int(round(f)) for f in act.frame_range)
    cycle = end - start  # last frame repeats the first

    # Facing direction in rest: from the head bone towards the eyes.
    bpy.context.scene.frame_set(start)
    mw = arm.matrix_world
    pb = arm.pose.bones
    head = mw @ pb['Bip01 Head'].head
    eyes = (mw @ pb['Bip01 LEye'].head + mw @ pb['Bip01 REye'].head) / 2
    fwd = Vector((eyes.x - head.x, eyes.y - head.y, 0)).normalized()
    turn = Matrix.Rotation(-math.atan2(fwd.y, fwd.x), 4, 'Z')

    # Simplify once, before the armature deforms it, so every pose shares its triangles.
    tris = sum(len(p.vertices) - 2 for p in body.data.polygons)
    dec = body.modifiers.new('decimate', 'DECIMATE')
    dec.ratio = min(1.0, TARGET_TRIS / tris); dec.use_collapse_triangulate = True
    while body.modifiers[0] != dec: body.modifiers.move(body.modifiers.find(dec.name), 0)

    tex = {}
    for m in body.data.materials:
        part = 'opacity' if 'opacity' in m.name else 'head' if 'head' in m.name else 'body'
        tga = next(folder.glob(f'*_{part}_color.tga'), None)
        # Some people have no hair-card texture: their (unused) cards are dropped below.
        tex[m.name] = texture(tga) if tga else None
    mats = [m.name for m in body.data.materials]

    poses, hips, colours, keep = [], [], None, None
    dg = bpy.context.evaluated_depsgraph_get()
    for k in range(FRAMES):
        bpy.context.scene.frame_set(start + round(k * cycle / FRAMES))
        dg.update()
        ev = body.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        n = len(me.loop_triangles)
        loops = np.empty(n * 3, dtype=np.int32); me.loop_triangles.foreach_get('loops', loops)
        verts = np.empty(n * 3, dtype=np.int32); me.loop_triangles.foreach_get('vertices', verts)
        co = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)[verts]
        m4 = np.array(turn @ ev.matrix_world, dtype=np.float64)
        world = co @ m4[:3, :3].T + m4[:3, 3]
        if colours is None:
            uv = np.empty(len(me.loops) * 2, dtype=np.float32); me.uv_layers.active.data.foreach_get('uv', uv)
            uv = uv.reshape(-1, 2)[loops].reshape(n, 3, 2)
            mi = np.empty(n, dtype=np.int32); me.loop_triangles.foreach_get('material_index', mi)
            # Each triangle's colour: the texture averaged at its corners, edge midpoints and centre.
            w = np.array([[1,0,0],[0,1,0],[0,0,1],[.5,.5,0],[0,.5,.5],[.5,0,.5],[1/3,1/3,1/3]])
            rgba = np.zeros((n, 4))
            for idx, name_ in enumerate(mats):
                sel = mi == idx
                if not sel.any(): continue
                if tex[name_] is None: rgba[sel] = 0; continue  # alpha 0: dropped as hair below
                pts = np.einsum('sk,nkd->nsd', w, uv[sel]).reshape(-1, 2)
                rgba[sel] = sample(tex[name_], pts).reshape(-1, len(w), 4).mean(1)
            hair = np.array([('opacity' in mats[i]) for i in mi])
            keep = ~hair | (rgba[:, 3] >= MIN_HAIR_ALPHA)
            srgb = np.clip(rgba[:, :3], 0, 1)  # Blender holds 8-bit image pixels as stored (sRGB)
            colours = np.repeat((srgb[keep] * 255 + .5).astype(np.uint8), 3, axis=0)
        poses.append(world.reshape(n, 3, 3)[keep].reshape(-1, 3))
        # Walk in place: the hips' horizontal position this frame becomes the origin.
        hips.append(np.array(turn @ (arm.matrix_world @ arm.pose.bones['Bip01 Pelvis'].head))[:2])
        ev.to_mesh_clear()

    floor = min(p[:, 2].min() for p in poses)
    out = []
    for p, h in zip(poses, hips):
        q = p - [h[0], h[1], floor]
        out.append(base64.b64encode(np.round(q * 1000).astype('<i2').tobytes()).decode())
    height = max(p[:, 2].max() for p in poses) - floor
    log(name, f'{tris} -> {len(colours) // 3} triangles, {height:.2f} m tall, cycle {cycle} frames')
    return {'name': name, 'sex': sex, 'vertices': len(colours), 'height_m': round(float(height), 3),
            'cycle_s': round(cycle / bpy.context.scene.render.fps, 4),
            'colors': base64.b64encode(colours.tobytes()).decode(), 'frames': out}, cycle


def main():
    people, cycles = [], []
    only = sys.argv[sys.argv.index('--') + 2:] if '--' in sys.argv else []
    for name, sex in [p for p in PEOPLE if not only or p[0] in only]:
        person, cycle = bake(name, sex)
        people.append(person); cycles.append(cycle)
    fps = bpy.context.scene.render.fps
    doc = {'source': 'Microsoft Rocketbox Avatar Library (MIT), github.com/microsoft/Microsoft-Rocketbox',
           'animation': '{m,f}_walk_neutral, motion extracted (in place)', 'frames_per_cycle': FRAMES,
           'cycle_s': round(sum(cycles) / len(cycles) / fps, 4), 'people': people}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(',', ':')), encoding='utf-8')
    log('wrote', OUT, f'{OUT.stat().st_size / 1e6:.2f} MB')


main()
