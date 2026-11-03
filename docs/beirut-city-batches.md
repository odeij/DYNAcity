# Compact city imports for Unreal

`python -m dynacity.engine_city_batches` batches the prepared source GLBs into
the existing 12 production areas. It concatenates whole objects per material:
five illustrative building material meshes and two road meshes per area
(asphalt, and pavements with their kerbs). A pavement goes to its road's area.
No building or road is cut at an area boundary. A road belongs to the area with
the largest source-sector overlap; roads outside the named sector polygons use
the nearest sector, explicitly marked in their object metadata.

This reduces the current prepared building/road import from 23,094 source
objects to 72 GLB meshes in 24 import jobs, totaling 47,391,124 bytes.
The 17,397 building objects and 5,697 road objects keep their original vertex
positions, normals, triangle order within each object and materials. Existing
terrain and source transport structures remain shared layers, referenced in
the resulting `city-batches.json`; they are not duplicated in district batches.

Each job has a unique `name`, a `production_group`, a `kind`, its GLB `asset`,
companion `metadata` path, `mesh_count`, `object_count` and `bytes`. The common
`origin_utm_m` matches the pilot, building packages and shared road layer.
Node names begin `buildings_` or `roads_` and identify the material batch.
No district batch creates additional calibration markers.

Each companion `.objects.json` records source IDs, source attributes, local
bounds, vertex ranges and triangle ranges within its named GLB batch mesh.
Bounds use metres relative to the shared origin, X east, Y north, Z up.
Triangle ranges describe the source GLB only: Unreal's importer may reorder
triangles, so they must not be assumed to match Unreal collision face indices.
An inspector can use source IDs and bounds until an imported face mapping is
verified. Whole-batch collision and rendering are engine integration work.

Production areas are authoring groups. This import optimization does not
establish a runtime streaming-cell size or require merging the whole city
into one mesh. Per-building selection and independent facade changes require
source metadata or a separate detailed nearby representation.

Verification compares every one of the 23,094 real output objects with its
source GLB: positions, normals, indices and materials match exactly; source IDs
appear once. All 24 job names are unique. Of the 5,697 roads, 208 use the
explicit nearest-sector ownership fallback. The private
`geometry-verification.json` records these results. Unit tests additionally
reject transformed inputs and duplicate IDs instead of silently corrupting
the batch. Unreal memory and frame-time improvements need engine measurement;
asset-count reduction alone is not an engine performance benchmark.
