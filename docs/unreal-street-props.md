# Reusable street props

`scripts/build_beirut_street_props.py` creates original, low polygon street
models for the mapped placement layer. Geometry and colours are illustrative
defaults, not measurements or photographs of Beirut hardware. There are no
external model dependencies. Version 2 broadleaf uses the CC0 photographic leaf
atlas and bark maps from [Tree Small 02](https://polyhaven.com/a/tree_small_02)
by Rico Cilliers, under [Poly Haven's CC0 license](https://polyhaven.com/license).

    blender --background --threads 2 --python scripts/build_beirut_street_props.py -- --output PRIVATE/props

Use `--only gate,block` to generate selected keys and merge them into an existing
manifest without overwriting other prop files.

Each key has a combined FBX mesh with multiple material slots and a GLB copy.
The JSON manifest contains dimensions, local bounds, triangle counts, material
slot counts and an FBX SHA256 hash. All meshes use metres, a ground-contact
origin and local +X facing. FBX exports X forward/Z up; GLB uses glTF's Y-up
conversion. Unreal imports must check the resulting centimetre dimensions.

Available keys are `traffic_signal`, `traffic_signal_left`,
`traffic_signal_right`, `tree_broadleaf`, `tree_palm`, `street_lamp`, `bench`,
`bus_stop`, `bollard`, `crossing_marking`, `gate`, `lift_gate`, `block`,
`waste_basket`, `bicycle_parking`, `fountain` and `shelter`.

Signal lenses are tinted and **unlit**. No live red/amber/green phase is
simulated by these meshes. Left/right arrow variants have matching arrow
shapes on each lens and must only be selected when the placement source
supplies the corresponding direction. The lift gate's lowered pose is a
presentation default, not a live barrier status.

Generic broadleaf trees have clustered crowns, visible branches and trunk
metadata for simple collision proxies. The palm model is reserved for source
tags supporting palm identification. Tree meshes must not be used to imply
observed individual trees inside a mapped park or woodland unless the layer
explicitly identifies that placement as inferred.

The upgraded `tree_broadleaf` removes solid canopy ellipsoids. It contains
2,448 separately oriented leaf cards with cropped UVs and the source alpha
mask, visible scaffold branches, and photographed bark: 8,080 triangles total.
Run `--only tree_broadleaf --textured-trees` after downloading the official maps
listed by `https://api.polyhaven.com/files/tree_small_02` into `props/textures`.
Use 1K JPG `leaves_diff`, `leaves_nor_dx`, `leaves_nor_gl`, `leaves_rough`,
`branch_diff`, `branch_nor_dx`, `branch_nor_gl`, `branch_rough`; use 1K PNG
`leaves_alpha`. Each downloaded file was checked against the API's MD5.
The manifest `materials` records identify explicit texture roles, static-mesh
slot names, two-sided rendering and alpha masking. Unreal uses DirectX normal
maps; Blender's preview uses OpenGL normal maps. `--preview` creates a small
Cycles preview using two CPU threads (no large render required).

This remains an illustrative generic tree, not a measured Beirut species or
individual crown. The palm remains the earlier mesh because the current source
placements contain no tagged palms; broadleaf leaf textures are not misapplied
to palm fronds.

Crossing stripes span 3.0 m along local X and 5.48 m along local Y. Their top
is 0.008 m above the origin. Scale local Y to the source road width and place
them slightly above asphalt; only use the marking model for crossing tags
that support a marked crossing. Fountain geometry is a static basin without
jets. Shelter geometry has no fabricated operator or advertising signage.

Generation with Blender 4.5.9 LTS completed successfully. All 17 FBX files were
then imported back into an empty Blender scene: each yielded one mesh, with
dimensions matching the manifest within 0.0001 m. The private output contains
`roundtrip-verification.json`. Native Unreal scale checks remain authoritative.
