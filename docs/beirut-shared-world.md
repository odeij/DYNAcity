# Shared Beirut ground and road assets

`python -m dynacity.engine_city_surface` prepares shared city geometry in the
same EPSG:32636 local origin as the district building packages. Source-derived
assets remain private. It does not change the existing Unreal pilot map.

The exporter creates three GLBs, a road-path metadata file and
`shared-world.json`. The terrain GLB contains coordinate calibration once;
the roads and source transport structures contain none. Roads retain stable
traffic model IDs and OSM way IDs and cross production-area boundaries without
being cut or duplicated. Named production groups remain useful for authoring,
while this shared layer preserves the connected network.

Terrain keeps the source model's original grid elevations. Cells with any
missing corner are omitted, preserving coverage gaps rather than adding a
flat surface. Missing elevation on either edge of a road (checked at no more
than 5 metres spacing) excludes the entire road and records its reason.


With `--fallback-terrain <terrain-glo30.npz>` (from `dynacity fetch-terrain`,
`shared-city-surface/4`) the model ground is merged with the Copernicus GLO-30
estimate (`terrain.merge_ground`) before terrain, roads and kerbs are built.
Model ground samples are kept exactly. Where the model has none, GLO-30 is used
plus an offset: at the seam it equals the local model-minus-GLO-30 difference
(no step), fading over 60 m into a regional offset fitted along the seam. Sea
stays a gap, and cells up to 400 m beyond both extents take the nearest known
value. GLO-30 is a filtered surface model (about 5 m RMSE against the model
ground), so those roads and the terrain they sit on are **lower accuracy**.
Each road records `ground_source` (`pack`, `glo30`, or `blended` for a road
crossing the seam) and the manifest has `ground_source_counts`. Bridge deck
heights still come from the model ground only. Without the flag nothing
changes. Underground roads are listed as excluded; tunnel mouths and interiors require
a separate treatment.

Street-level roads share one carriageway. Each road's outline (round ends,
so junction corners are filled) is cut against the roads before it, widest
first, so every patch of asphalt belongs to exactly one road and no two
surfaces stack. The pieces are clipped to the exact terrain triangles and sit
12 cm above them, so the ground cannot show through. Roads other than
motorway/trunk classes get a pavement (1.6 m on residential and living
streets, 2.4 m otherwise, as in the browser street view) that stays off every
carriageway and stands one 15 cm kerb higher, with vertical kerb faces on all
of its edges. Each road's pavement is a separate `pavement:<id>` object with
the Concrete material, so road batches hold an asphalt and a pavement mesh.
Pavement widths and kerb height are illustrative, not a sidewalk survey.

Bridge roads and their ramps keep a graded ribbon and use the existing source deck matching and shared graph-node
ramp elevation convention. Unmatched elevated roads use the explicitly
illustrative 5.5 metres per level convention. The transport-structure asset
retains original Beirut 001 mesh triangles, including its underground source
geometry; it adds no invented piers. Those source structures are separate from
the illustrative asphalt ribbons and may require per-location alignment
review before the layers are combined for driving.

Widths come from the existing lane-count model, not surveyed curb positions.
Materials are illustrative. Source model alignment and vertical datum remain
unverified. A road surface alone is not proof of collision, legal driveability,
or a validated traffic simulation.

The content-addressed cache includes input file content, exporter code and
common origin. Existing valid assets are reused. `shared-world.json` reports
coverage, excluded roads, coordinate origin, sizes and geometry layer paths.
Building assets are not duplicated in these shared GLBs. Unreal World Partition,
runtime streaming, collision and vehicle controls are separate integration
work, not functions performed by this command.

Verification covers original elevations and missing cells, upward terrain
normals, continuous graded ribbon geometry, unstacked junction surfaces,
outward kerb faces, pavements kept off the carriageway, whole-road IDs, underground
exclusions and exact source transport triangles.
