# Recovering street elevations from the original LAS

`las-ground-review` now extracts candidate street/open-ground observations using explicit, externally supplied polygons. It excludes buildings and vehicles before aggregation and rejects candidate cells that cross an exclusion. It reads every original point, retains counts and observed elevation quantiles, records source hashes, and leaves unavailable areas as NaN. There is no interpolation, smoothing, display alignment or implicit CRS conversion.

This prepares a ground review, **not an accepted flood DTM**. An annotation says where a road appears in plan view; it does not establish whether a point is pavement, a bridge, a barrier or an outlier. Neither a small within-cell spread nor dense observations establish vertical accuracy. Elevated roads and underpasses need separate connectivity and surveyed elevations.

The footprint file is a planar GeoJSON FeatureCollection with `horizontal_crs`, a nonempty `evidence` description and valid 2D Polygon/MultiPolygon features. Every feature explicitly declares `properties.role` as `include` or `exclude`. Include polygons identify draft road/open-ground review areas; exclusions take precedence. Invalid geometry is refused without repair. Footprints must already share the cloud's asserted projected metre CRS.

Run with every screening choice declared:

```sh
.venv/bin/python -m dynacity.cli las-ground-review /private/original.las \
  --footprints /private/review-footprints.geojson \
  --output /private/new-ground-review.npz \
  --cell-m 0.5 --crs EPSG:32636 \
  --crs-evidence 'Document how this horizontal CRS was established' \
  --exclusion-buffer-m 0.3 --min-points 5 --max-spread-m 0.2
```

These example values are **review settings**, not measured Beirut hydraulic parameters or claimed survey accuracy. A 0.5 m aggregation does not establish 0.5 m source accuracy. `max-spread-m` screens observed q90 minus q10 within each cell. Quantiles select original elevations: lower ranks for q10/q50, upper ranks for q90. Empty, sparse, rough and partially excluded cells remain unavailable in `candidate_z`; raw selected-point counts and quantiles are retained for inspection. No resulting file has TerrainGrid's schema or is promoted to solver input.

## Actual local extraction, 19 October 2026

Private output folder:

`/Users/johnkofdarelli/dynacity-private/data/ground-review-20261019/`

The original `AlMasarAlAkhdar_TILE_1-1.las` was processed in full: 1,629,609 points, SHA256 `08da54427763c84640b892cbc4362805b49b895647163d8e09e9edfd869fa19e`. Its class labels are all zero. The existing September project's draft `annotations.json` supplied five road polygons, 17 building exclusions and 14 vehicle exclusions. Their image-to-world transform was reproduced from that project's `segment.py`, without changing source coordinates. The original annotation hash and transform are retained in `tile-1-1-footprints.geojson`.

At the explicitly selected review settings above, 179,920 original observations fall in the selected review area. Of 12,515 fully permitted 0.5 m cells, 8,962 pass the support/spread screen: 2,240.5 m² of candidates. The remaining permitted cells have insufficient observations or too much vertical spread. This is incomplete coverage; disconnected patches must not be joined by invented terrain or treated as isolated closed catchments.

`ground-review.png` compares the source RGB top view, supported candidates and observed elevation spread. The cloud still has no embedded CRS or vertical datum, and external BBED overlap is not independent survey verification. Candidate heights retain the original Z, including possible raised structures/outliers. Map display offsets are never applied to hydraulic preparation.

Before using any candidates in a real model, reconcile the footprints and vertical structures in 3D, establish ground elevations with independent checkpoints, tie drainage rim/invert levels into the same reference, and survey gaps/breaklines and contributing upstream/downstream connections. Rainfall, drainage and independent event observations remain separate acquisition requirements in [data-inventory.md](data-inventory.md).
