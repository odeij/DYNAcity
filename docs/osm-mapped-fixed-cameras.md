# Mapped fixed surveillance cameras

The infrastructure extract includes 27 nodes tagged as fixed cameras mounted
on poles. The source-linked manifest selects 26 that also have numeric
`camera:direction` bearings; one has no numeric direction and is left out.
The exporter retains source IDs, tags, timestamps and approximate ground
positions. No assumption about operation or camera feeds is made.

The candidate model is the finished [CCTV camera pole by 3DAssets.dev](https://3dassets.dev/assets/recycling-and-waste-transfer-yard-recycling-and-waste--cbc2cecf),
CC0 1.0. It has 256 triangles and a 4.42 m height. The model's size, support
design and appearance are illustrative, not observations of the mapped
cameras. The original GLB and exported FBX hashes are stored in the private
asset manifest. Its pole base is at the model origin; its authored front is
glTF +Z / Blender -Y.

`scripts/prepare_mapped_fixed_cameras.py` produces the private manifest.
Placement in Unreal requires native import and separate verification.
