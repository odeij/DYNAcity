# Mapped dome surveillance cameras

Five OSM camera nodes explicitly tagged `camera:type=dome` and
`camera:mount=pole` have ground-positioned source records and a prepared
placement manifest at
`C:/private/dynacity-data/engine/asset-quality-pass/camera-placements.json`.
Source IDs and mapped `camera:direction` bearings are preserved. The node
location is mapped, not surveyed; terrain elevation is sampled. The generic
model's 2.84 m pole height, appearance and actual operation are unverified.

The finished [Innerscene camera-on-pole model](https://www.innerscene.com/tools/library/3d-parts/pan-tilt-zoom-camera-on-pole-726d211e)
is CC0. Source and converted hashes and the licence are recorded in its private
asset manifest. The camera lens faces Blender negative Y, verified from the
source mesh bounds; Unreal's imported basis must be checked before applying
bearings. Nodes that only say surveillance or specify another mounting/type
are not assigned this model. A staging manifest does not imply Unreal import.

Run `scripts/prepare_mapped_cameras.py` against the supplemental context and
the prepared asset; `tests/test_engine_mapped_cameras.py` checks eligibility,
source identity, bearing handling and nonfinite positions.
