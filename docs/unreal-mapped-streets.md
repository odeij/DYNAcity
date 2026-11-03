# Mapped Beirut streets

The city scene uses a source-identifiable street layer from the available local OpenStreetMap extraction. Point clouds are excluded from this pass. The first driving reference is Charles Malek Avenue, source road8416/OSM way584626701; see [reference selection](beirut-street-reference.md).

`src/dynacity/engine_street_context.py` exports points, draped cover and provenance lines. It preserves OSM IDs/tags, extraction dates, inferred fixture placement and named production areas. Ground cover is clipped against the exact existing terrain triangles, preserving holes. Green areas exclude derived ground-road buffers. Unknown elevations are reported rather than assigned a sea-level default. Bridge references remain provenance; original Beirut001 structures already provide the visible mesh.

`src/dynacity/engine_street_assets.py` batches cover by named area and kind, preserving source-to-triangle ranges. `scripts/import_unreal_street_covers.py` resumes one asynchronous import at a time using the editor's local MCP server.

`scripts/build_beirut_street_props.py` authors shared street assets. `street_context.py` imports them into `/Game/Beirut/Streets` and places native hierarchical instances grouped by area and asset. `DynaStreetInstances` retains a source ID for every instance. Narrow hidden cylinders provide illustrative trunk/pole collision without blocking whole tree canopies. Other furniture is decorative. Signal lenses have no inferred live phase. Directional variants require explicit direction tags. Unmarked crossings receive no invented zebra stripes.

Use **Tools → Explore Beirut → Charles Malek Avenue — street view**. The street driving start becomes the preferred start after its actual mesh wheel/body clearance checks pass. Existing named-area starts remain available.

World Partition external actors need `modify()` and dirty-package saving when changing component materials. This pass repairs that persistence issue; the existing geometry and collision are retained.

Updating an instance layer must first load its World Partition actor descriptors. Duplicate generated groups from earlier partial loads are removed by actor identity after replacement; loaded group counts must agree with the saved source index.

The broadleaf asset uses photographic leaf/bark maps from [Poly Haven Tree Small 02](https://polyhaven.com/a/tree_small_02), by Rico Cilliers, under [CC0](https://polyhaven.com/license). Leaf cards use masked, two-sided foliage shading with explicit transmission; opacity and roughness textures require matching Masks samplers. The current extraction has no tagged palm placements, so every placed tree uses the broadleaf default. Species and crown shape are illustrative. A tree has 8,080 triangles; HISM groups and distance culling share its mesh. No frame-rate improvement is claimed without a comparable foreground performance capture.

The reference divider follows the audited two-lane, one-way road chain. Divider width and dash spacing are illustrative. Marked-crossing paint styles are also illustrative unless the source explicitly specifies zebra markings. No continuous raised sidewalk is added to this corridor because local sidewalk geometry is absent.

Driving is blocked during an active or incomplete surface import. A drive check overlapping an earlier import triggered a Chaos physics crash; imports were recovered from checkpoints, the editor was restarted, and the completed scene subsequently passed a separate spawn/acceleration/brake/stop smoke check.

## Verification

Exporter tests cover triangle-linear ground interpolation, holes, missing-ground rejection, source tags/directions and curb placement. Cover tests verify GLB material, source ranges and unchanged triangulation. Native prop dimensions are checked in centimetres against the authored bounds. Runtime import reports are saved privately in the project's `Saved/DynaCity` directory. These checks do not establish that OSM is complete or that illustrative hardware/facades match the real street photographically.
