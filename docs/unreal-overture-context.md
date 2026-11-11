# Non-building Overture context in Unreal

The 21 October 2026 release supplements the existing city without replacing its
roads, buildings, terrain, traffic network or existing mapped scenery.

`scripts/fetch_overture_context.py` uses the release's STAC index, Parquet
row-group bounding boxes and HTTPS byte ranges to retrieve the map extent
(35.4693, 33.8288, 35.5720, 33.9076). Nine types are retained: places, water,
land use, land cover, infrastructure, transportation segments and connectors,
division areas, and land. Selected source attributes and per-feature licenses
are retained. Place enrichment adds categories, confidence, operating status
and addresses from the current schema.

`scripts/prepare_overture_unreal.py` clips reference geometry to the map extent.
It exports native Unreal label catalogs as CSV files with GERS/source IDs,
geometry and source properties in metadata. They import as StringTable assets
under `/Game/Beirut/Overture/OT_*`; they are geographic references, not spawned
3D objects or calibrated simulation inputs.

Named places supplement the existing `Saved/DynaCity/location-index.json`.
The original index is backed up; repeated merges rebuild only the Overture
portion. Matching normalized names within about 50 m are skipped. Existing
roads and places remain authoritative. Source coordinates are representative
positions, not surveyed entrances. Closed places are retained in the source
catalog but excluded from location search when explicitly marked closed.

For scenery, an existing OSM identity takes precedence. Remaining land cover
is restricted to municipal Beirut and has existing OSM polygons, buildings,
lane-width road buffers and previously accepted patches subtracted. Ground
patches use the exact existing terrain triangles with a 3 cm visual lift.
Terrain holes are skipped; no sea or water elevation is invented. Materials
are illustrative. The patches have no collision and cast no shadows.

The shared city import checkpoint serializes new GLB imports after previous
work finishes. `engine/unreal/Content/Python/overture_context.py` imports and
verifies the label catalogs, resets the location-search cache, finishes only
the new scenery actors, and verifies saved actors after map reload. A baseline
records original actor transforms, mesh references and materials so unintended
changes can be detected.

Private outputs live under `C:/private/dynacity-data/overture/nonbuildings/`
and `C:/private/dynacity-data/engine/overture-context/`. The latter contains
`overture-context.json`, `catalog-import.json`, and `unreal-verification.json`.
Only a successful verification report establishes the completed saved import.

The completed import contains 62,070 catalog records across the nine types,
19,253 additional searchable places (414 nearby-name duplicates skipped), and
ten woodland/shrub ground patches totaling 4,755 triangles. Existing source
identity eliminated all 570 supported land-use scenery candidates. After map
reload, all ten patches were visible with collision disabled, all catalog
counts matched, and all 904 baseline actors retained their transforms, mesh
references and materials.

Tests in `tests/test_overture_context.py` compare the optimized terrain drape
against the original exporter, including holes and heights, and verify source
identity plus repeatable location merging and backup preservation.

Source documentation: [Overture catalog](https://docs.overturemaps.org/getting-data/cloud-sources/),
[base theme](https://docs.overturemaps.org/guides/base/),
[place schema](https://docs.overturemaps.org/schema/reference/places/place/).
