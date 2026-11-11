# Terrain loading repair

The city reopened with only 23 of 715 actors loaded. The GUI welcome flow did
not load the city, and the city loader omitted street cover meshes, including
the terrain extension. Startup, overview, location navigation and driving now
load those layers together. Runtime spatial streaming remains enabled.

The extension and the small gap repair use the original terrain's material.
Camera navigation enables realtime rendering and invalidates the viewport so
the displayed frame follows the stored camera position.

A grid audit found 21 enclosed nodata regions. The visual repair fills 19 with
local inverse-distance interpolation from eight nearby finite samples. The
furthest repaired sample is 31.63 m from known ground. Two regions intersect
mapped water and remain absent. Mapped waterway buffers are also protected;
boundary-connected sea and off-grid regions are never filled. All existing
finite source elevations remain unchanged. The separate patch has 1,290
triangles and no collision; it does not claim surveyed accuracy or driveability.

Private artifacts:

- `C:/private/dynacity-data/engine/ground-loading-verification.json`
- `C:/private/dynacity-data/engine/terrain-gap-repair-v2/terrain-gap-repair.json`
- `C:/private/dynacity-data/engine/terrain-gap-repair-v3/terrain-gap-repair.json`

The v3 export adds the waterway check and has the same SHA256 as the installed
v2 mesh. The patch is installed by `terrain_repair.start()` / `finish()`.
`prepare_unreal_terrain_gap_repair.py` regenerates it from the private ground
grid and street context. Keep generated data outside Git.

Verification: 24 focused loading, terrain repair, location and route tests pass.
Native editor inspection after reopening confirmed all 664 expected visible
layers loaded before the new patch, with matching terrain materials. This does
not certify every city mesh, coastal edge or runtime streaming transition.
The editor's "No Loaded Regions" notice describes editor region selections;
loading actors individually can leave that notice visible.
