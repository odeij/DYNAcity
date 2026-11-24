# Walking people

Twelve everyday adults (six women, six men) from the **Microsoft Rocketbox Avatar
Library**, MIT licence (see LICENSE.txt), © 2020 Microsoft.
Source: https://github.com/microsoft/Microsoft-Rocketbox
(`Assets/Avatars/Adults/<Name>/Export/<Name>.fbx` and `Textures/*_color.tga`; walk clips
`Assets/Animations/all_animations_max_motextr_xy/{f,m}_walk_neutral.max.fbx`).
Downloaded 2026-11-21. The originals (~40 MB per person, mostly textures) are kept outside
the repository in the private data root (`rocketbox/`).

`meshes.json` holds derived geometry only. For each person:

- the neutral walk (women's clip for women, men's for men) drives the person's own skeleton,
  each bone following the clip bone's world orientation;
- the mesh is simplified once in rest pose from ~7,400 to ~1,500 triangles, so every pose
  shares its triangles;
- 8 poses spread over one walk cycle (32 frames at 30 fps, 1.07 s) are stored as int16
  millimetres, +x forward, +z up, feet at z = 0, hips held in place;
- each triangle gets the person's texture colour averaged over it; hair cards keep only
  their mostly opaque triangles. Normals are computed in the viewer (flat per triangle).

The viewer picks a stable person per walker and the pose from the distance walked (one cycle
per walking speed × 1.07 s), so legs keep pace with the 1.3 m/s pavement playback. Only
pages with pedestrian data embed this file.

Rebuild (needs Blender 4.5 and the downloaded originals):

    blender -b --factory-startup --python scripts/prepare_people_models.py -- <rocketbox_dir>
