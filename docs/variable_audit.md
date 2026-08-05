# Task 2 variable audit

This audit answers Odei's follow-up request: after matching BBED to the point cloud, document what exists, what is missing, and what segmentation can provide. Field availability can differ between BBED layers and survey years, so the generated `building_lookup.csv` remains the run-specific source of truth.

## Available from the current BBED building layer

| Category | Current fields or examples | Use after matching |
|---|---|---|
| Identity | `OBJECTID`, `BULBuildingID`, `ParcelID` | Stable lookup from a point or object to BBED records |
| Location | Footprint polygon, cadastral area, sector | Spatial organization and aggregation |
| Function | Building use, use description, ground-floor commercial use, basement/rooftop use | Urban-function queries and scene parameters |
| Form | Number of floors, typical floor height, ground-floor height, building height, apartments | 3D massing and plausibility checks |
| Development | Completion year, permit number/year, status in 2018/2022 | Change analysis and temporal filtering |
| Provenance | Data source and footprint source | Traceability and confidence review |

Heritage, parcel, zoning, titles, infrastructure, vacancy, water, and energy information may live in separate BBED layers. They should be joined only through documented building/parcel keys or an explicitly evaluated spatial join—not assumed to be fields in the building layer.

## Produced by this pipeline

| Variable | Source | Status |
|---|---|---|
| Point-to-building match ID | 2D point-in-polygon | Implemented as `bbed_id` |
| Match ambiguity | Count of overlapping BBED footprints | Implemented as `bbed_amb` |
| Points per footprint | Matched point aggregation | Implemented |
| Point density per square metre | Point count / BBED footprint area | Implemented |
| Minimum, maximum, mean Z and Z range | Matched/evaluation points | Implemented; Z range is not a robust surveyed height |
| Mean RGB | Matched/evaluation points when RGB exists | Implemented |
| Footprint detection, precision, recall, F1, and IoU | Optional independent instance polygons | Implemented when `--instances` is supplied |

## Variables segmentation can add

These values are not copied from BBED; they require the semantic/instance segmentation stage:

| Variable | Required output | Why it matters |
|---|---|---|
| Semantic class per point | Reliable SPT or replacement model labels | Separates buildings, roads, vegetation, cars, terrain, and street furniture |
| Building instance ID | Instance segmentation or post-processing | Distinguishes adjacent buildings and enables BBED instance IoU |
| Roof, façade, and ground labels | Part segmentation | Improves height, massing, façade, and roof statistics |
| Data-driven footprint | Projected building-class points or instance polygon | Measures footprint disagreement with BBED |
| Building height estimate | Ground model plus roof points | More robust than raw Z range |
| Roof type and slope | Roof-point geometry | Supports procedural 3D reconstruction |
| Vegetation coverage | Vegetation classes / RGB features | Adds environmental context absent from a building footprint alone |
| Damage or missing-scan flags | Dedicated model and confidence | Supports heritage reconstruction and scan-quality review |
| Per-label confidence | Calibrated model scores | Prevents uncertain predictions from silently becoming facts |

## Still missing or requiring confirmation

1. **CRS metadata in the LAS header.** The project CRS is reported as `EPSG:32636`, but it should be embedded in source and output files wherever possible.
2. **Independent registration controls.** A shared CRS and plausible overlay do not quantify XY accuracy. Surveyed control points or corresponding corners are required for residual error and systematic-offset estimates.
3. **Reproducible segmentation instances.** The presentation documents a 3 m / 5th-percentile ground model, excess-green filtering, a 2.5 m height threshold, and 8 m watershed seed spacing. The exact vegetation threshold, morphology settings, script, and exported polygons are still required to reproduce 52 instances, 41/45 detection, and mean IoU 0.234.
4. **Cross-layer keys and cardinality.** Building-to-parcel, heritage, zoning, titles, infrastructure, vacancy, water, and energy joins need documented key names and one-to-one/one-to-many rules.
5. **Survey date and provenance per attribute.** BBED combines several survey rounds. Each value used in forecasting or scene editing needs an effective date and source.
6. **Point acquisition metadata.** Capture date, positional accuracy, vertical datum, camera calibration, point density variation, and processing history are needed for defensible measurements.
7. **Ontology mapping.** BBED building uses and segmentation class labels need a versioned mapping, including unknown and mixed-use cases.
8. **Change policy.** The system needs explicit rules for disagreements caused by new construction, demolition, outdated footprints, occlusion, or a segmentation failure.

## Recommended next validation run

1. Run the pipeline on `TheStreetScape.las` with `--point-crs EPSG:32636`.
2. Save the exact BBED query result and generated JSON/Markdown reports with a dated output folder.
3. Export the watershed predictions as polygon instances and rerun with `--instances`.
4. Review every false positive, false negative, low-IoU match, and `bbed_amb > 1` region in GIS software.
5. Validate the same locked thresholds on `TheMixedFabric.las` and report the change from the in-sample tile.
6. Measure XY residuals at independently selected building corners before calling the layer "registered."
