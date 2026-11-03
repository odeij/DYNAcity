"""Build one raised-patch FBX per mapped traffic island from manifest footprints plus traced heights (agent 2).

Run with: blender --background --python scripts/agent2_build_island_fbx_blender.py -- --islands F --heights F --output DIR
Geometry is derived only from the mapped OSM footprint (open lines were buffered to an inferred width) and heights
traced on the existing Unreal roads. Top is 12 cm above the traced surface (inferred); a 3 cm skirt hides the seam.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import bmesh
import bpy

TOP_M = 0.12
SKIRT_M = 0.03


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--islands', required=True)
    p.add_argument('--heights', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args(sys.argv[sys.argv.index('--') + 1:])
    islands = json.loads(Path(a.islands).read_text(encoding='utf8'))['islands']
    heights = json.loads(Path(a.heights).read_text(encoding='utf8'))
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for isl in islands:
        rows = heights[isl['source_id']]
        known = [r['z_cm'] for r in rows if r['z_cm'] is not None]
        if len(known) < max(2, len(rows) // 2) or max(known) - min(known) > 300:
            manifest.append({'source_id': isl['source_id'], 'skipped': 'insufficient or inconsistent traced surface', 'hits': len(known), 'vertices': len(rows)})
            continue
        mean_z = sum(known) / len(known)
        zmap = {(round(r['xy'][0], 4), round(r['xy'][1], 4)): (r['z_cm'] if r['z_cm'] is not None else mean_z) for r in rows}
        wmap = {(round(r['xy'][0], 4), round(r['xy'][1], 4)): r['world_cm'] for r in rows}
        keys = list(zmap)
        cx = sum(k[0] for k in keys) / len(keys)
        cy = sum(k[1] for k in keys) / len(keys)
        wcx = sum(wmap[k][0] for k in keys) / len(keys)
        wcy = sum(wmap[k][1] for k in keys) / len(keys)
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete()
        bm = bmesh.new()
        top = {k: bm.verts.new((k[0] - cx, k[1] - cy, (zmap[k] - mean_z) / 100 + TOP_M)) for k in keys}
        edges = {}
        for tri in isl['triangles_local_xy_m']:
            ks = [(round(x, 4), round(y, 4)) for x, y in tri]
            vs = [top[k] for k in ks]
            try:
                f = bm.faces.new(vs)
            except ValueError:
                continue
            if f.normal.z < 0:
                f.normal_flip()
            for i in range(3):
                e = tuple(sorted((ks[i], ks[(i + 1) % 3])))
                edges[e] = edges.get(e, 0) + 1
        for (k1, k2), n in edges.items():
            if n != 1:
                continue
            b1 = bm.verts.new((k1[0] - cx, k1[1] - cy, top[k1].co.z - TOP_M - SKIRT_M))
            b2 = bm.verts.new((k2[0] - cx, k2[1] - cy, top[k2].co.z - TOP_M - SKIRT_M))
            try:
                f = bm.faces.new((top[k1], top[k2], b2, b1))
            except ValueError:
                continue
            mx = (k1[0] + k2[0]) / 2 - cx
            my = (k1[1] + k2[1]) / 2 - cy
            if f.normal.x * mx + f.normal.y * my < 0:
                f.normal_flip()
        mesh = bpy.data.meshes.new('island')
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new('TrafficIsland_' + isl['source_id'].replace('/', '_'), mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        path = out / ('island_' + isl['source_id'].replace('/', '_') + '.fbx')
        bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, object_types={'MESH'}, axis_forward='X', axis_up='Z',
            apply_unit_scale=True, add_leaf_bones=False, bake_anim=False, path_mode='COPY')
        manifest.append({'source_id': isl['source_id'], 'fbx': str(path), 'fbx_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'centre_world_cm_xy': [wcx, wcy], 'centre_z_cm': mean_z, 'triangles': len(mesh.polygons), 'surface_hits': len(known),
            'vertices': len(keys), 'geometry_kind': isl['geometry_kind'], 'tags': isl['tags'], 'top_above_surface_m_inferred': TOP_M,
            'surface_actors': sorted({r['hit'] for r in rows if r['z_cm'] is not None})})
    (out / 'islands-fbx.json').write_text(json.dumps(manifest, indent=1), encoding='utf8')
    print(json.dumps({'built': sum(1 for m in manifest if 'fbx' in m), 'skipped': [m for m in manifest if 'skipped' in m]}))


main()
