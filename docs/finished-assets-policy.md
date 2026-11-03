# Finished assets and left-hand driving

The current visual policy is to use finished, licensed library models. New
decorative models and car interiors must not be authored procedurally. The old
sedan, cockpit and street-prop builders are retired. Geospatial road, terrain
and building source geometry remains the map's data layer.

The vehicle is the existing **Ferrari 458 Italia by vicent091036**, distributed
with the [three.js car example](https://threejs.org/examples/webgl_materials_car.html)
under CC BY 4.0. The cached source is pinned to three.js revision
`6eec560494b0492cb72b6120ea2405bf4e4fe9c7`. Attribution and the original model URL
are recorded with the asset and shown in the controls. The model has 358,788
authored triangles, an interior and a left-side steering assembly. A previously
considered centre-steering concept model was rejected and is not installed.

The conversion keeps the source mesh triangles and UVs, combines source pieces
for Unreal, and converts the wheel-defined forward/left/up basis once. It
records the steering position and driver eye in centimetres. The wheel is
approximately 34.5 cm left of centre; the driver's eye uses the same lateral
position. Cabin scale stays positive on every axis. Mirroring is prohibited.

Street replacements use these finished Poly Haven models with their original
geometry and 4K PBR maps:

- [Modular Street Seating](https://polyhaven.com/a/modular_street_seating), Stuart Attenborrow, CC0.
- [Metal Trash Can](https://polyhaven.com/a/metal_trash_can), CC0.
- [Tree Small 02](https://polyhaven.com/a/tree_small_02), Rico Cilliers, CC0.
- [Street Lamp 01](https://polyhaven.com/a/street_lamp_01), Josh Dean, CC0.

Powered by Poly Haven. The tree's approximately 2.06 million
source triangles use Nanite. Foliage alpha is adapted to masked rendering and
OpenGL normal maps have their green channel flipped for Unreal. Source material
names that repeat are disambiguated before conversion; material-index, texture
and UV assignments remain intact. The car's source glass is adapted for a
transparent Unreal windshield.

Handmade pavement/curb cubes are removed from the reference dressing. Other
unsupported procedural street props are hidden until a suitable finished model
is available. Their source records remain intact. Old aggregate pole/trunk
collision is disabled to avoid collisions with hidden placeholders. These
decorative library replacements do not establish surveyed hardware positions
or calibrated vehicle physics.

The full car geometry is retained for the hero vehicle and nearby traffic.
The quality profile limits displayed traffic to 150 cars within 250 m, with a
350 m response-filter radius. This is a rendering budget; it does not reduce
the underlying SUMO network or demand.

Import pipeline: `fetch_quality_assets.py`, `prepare_library_asset_blender.py`,
`library_asset_core.py`, `library_assets.py`. Prepared manifests retain source
URLs, licences, hashes, bounds, authored materials and conversion operations.
Import rejects changed source/export bytes or an unapproved licence. Vehicle
validation rejects centre/right steering, a mirrored cabin, a passenger-side
eye point, non-finite coordinates or lateral eye/wheel misalignment.

Source downloads, converted models, textures and installed Unreal assets stay
outside Git. The private active manifest is
`Saved/DynaCity/library-assets-active.json`; backups and replacement reports
are retained beside it. These are a visual preview and adaptation of existing
assets, not a claim that Ferrari traffic or the particular furniture models
represent surveyed Beirut streets.

## Explicit exception: Lebanese lattice lamp — 31 October 2026

The user explicitly chose an original model from the approved daylight photo
instead of acquiring the paid finished model. This scoped exception authorizes
`scripts/build_lebanese_lattice_lamp.py` and the original lattice-lamp asset.
It does not reopen procedural authoring for other decorative props or vehicles.
The photo is used as a visual shape reference, not copied into textures. The
model's 8.05 m overall height and fixture mounting are illustrative. All other
finished-asset requirements remain in effect.
