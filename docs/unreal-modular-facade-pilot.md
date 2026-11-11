# Source-bound modular facade pilot

The EfaSim-style architectural pass uses **finished library modules**, preserving
the city's existing source buildings. It places authored wall/window assemblies
instead of generating decorative geometry or replacing Beirut footprints with
generic houses.

The source is [Modular Urban Apartments Facade](https://polyhaven.com/a/modular_urban_apartments_facade)
by James Ray Cock, licensed [CC0](https://polyhaven.com/license). Powered by Poly
Haven. The provider's public API supplies file sizes and MD5 values; intake
checks both and records SHA256 hashes. Source downloads and exports remain in
private storage outside Git.

`scripts/fetch_facade_library.py` fetches the 1K glTF kit and its exact declared
dependencies. `scripts/prepare_facade_modules_blender.py` joins each authored
wall panel with its matching authored window. It retains mesh topology, UVs and
materials, removes the source showroom placement and applies a proper rotation
into a consistent local basis. It creates no new window, balcony or cornice
geometry. Prepared modules are 3 m wide and 3 m high, approximately 4,400 source
triangles each.

`src/dynacity/building_facade_modules.py` recovers vertical source wall edges
and lays out whole, unstretched modules with corner and roof clearances. Each
instance retains the building source ID, a unique facade ID, a 250 m ownership
tile and explicit assumed evidence. There is a hard instance budget and a
180 m distance cull. Floors, window arrangement and the library's appearance
are presentation assumptions. They are not observed Beirut facade records.

`scripts/prepare_building_facade_pilot.py` selects buildings around the Charles
Malek pilot (local metres X 2300–2545, Y 1450–1530). It prioritizes walls facing
the pilot point [2423,1491]. The initial output selects 16 source buildings and
caps additions at 1,000 module instances; capped buildings can be partly dressed.
The original building source has an undeclared vertical datum, which remains
explicit in the placement manifest.

A second camera-facing scope keeps the same source buildings and selection
bounds, faces the camera point [2310,1488.5], and excludes every identity from
the first manifest. It contains 200 additional placements with zero identity
overlap. Scope hashes include source bytes, selection bounds, focus, budget and
module configuration. Different scopes reuse the same imported meshes; a
repeated loaded scope or already loaded placement identity is rejected.

`engine/unreal/Content/Python/install_facade_modules.py` imports the two
modules, checks dimensions and source hashes, uses the existing authored PBR
material adapter, and groups instances by tile and module in
`DynaStreetInstances`. Collision is disabled for additions. A 28 cm outward
offset keeps the module's recessed window glass in front of the retained source
wall. Original building geometry, transforms and collision are not edited.
Only newly created actor packages and imported assets are saved.

The installer must run through the serialized editor owner. It reports saved
state separately from reload verification. An Unreal viewport inspection is
still required to confirm importer handedness, recess direction, materials,
spatial loading, overlap and visual suitability. An export or passing placement
test does not establish that the pilot is installed or visually accepted.

The installer determines the imported module's outward Y sign from its signed,
asymmetric recess bounds. Unreal's importer reverses Y in this conversion, so
the current native bounds are approximately [-15,+24] cm. Rotations use named
`pitch`, `yaw` and `roll` arguments; a diagnosed positional-constructor issue
was repaired for the first 1,000 instances without changing verification
tolerances. Native readback measured maximum yaw error 0.000004825 degrees and
zero position error. Both scopes subsequently passed verification in a fresh
editor process: all 1,200 IDs, transforms and meshes matched, and the original
building signatures remained unchanged. See the cold-load report referenced by
[the integrated pilot](efasim-street-pilot.md).

Prepared pilot files are under
`C:/private/dynacity-data/engine/facade-modules-20261029/`:

- `prepared-modules.json`: source provenance, normalized modules and bounds.
- `charles-malek-placements.json`: source-linked placement plan and budget.
- `facade-install.json`: produced by the editor installer after scoped saving.
- `camera-facing/charles-malek-placements.json`: 200 additional camera-facing
  modules; installed through `run_camera_facade_pilot.py`.

Verification covers vertical-edge extraction, source winding reversal, proper
positive instance scales, roof/selection bounds, deterministic IDs and hard
instance limits. It does not establish architectural or traffic calibration.
