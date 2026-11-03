# Mapped power tower preparation

Forty OSM `power=tower` nodes in the current infrastructure supplement have
terrain-backed placement points. The source does not tag their heights, shapes,
materials or orientations. Ground elevation may come from the approximate
Copernicus visual extension and is not survey evidence.

The finished [Grab3D Power Line Tower](https://grab3d.com/models/city/power-line-tower/)
is CC0, 25,360 triangles, 20.06 m tall, and has a stylized teal finish. Its
shape, height and colour are illustrative rather than evidence of the mapped
structures. Source URL, licence and source and export SHA256 digests are kept
alongside the private prepared model. No wires are inferred.

`scripts/prepare_mapped_power_towers.py` emits the private source-linked
placement manifest; it preserves node ID and position and explicitly labels
unknown dimensions and orientation. Passing the manifest to native Unreal
requires independent placement and overlap verification. A generated manifest
is not evidence of a saved or visible Unreal actor.
