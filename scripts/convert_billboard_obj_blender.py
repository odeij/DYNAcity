"""Re-export the finished CC0 GGBotNet billboard (OBJ + textures) as GLB without changing its mesh."""
import sys
from pathlib import Path
import bpy
src, out = (Path(a) for a in sys.argv[sys.argv.index('--') + 1:][:2])
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.wm.obj_import(filepath=str(src))
meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
print('MESHES', [(o.name, tuple(round(x, 2) for x in o.dimensions), len(o.data.polygons)) for o in meshes])
bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLB', export_apply=False)
