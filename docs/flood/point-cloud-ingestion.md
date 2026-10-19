# Point-cloud ingestion

`dynacity las-surface` streams a local uncompressed LAS into an observed-surface
review package. Requires the `flood` optional dependencies for CRS validation.
The command requires explicit horizontal CRS evidence and aggregation spacing:

```sh
dynacity las-surface /path/to/original.las \
  --output /private/data/surface-review.npz \
  --cell-m 1 --crs EPSG:32636 \
  --crs-evidence 'Actual source of the coordinate-system assertion'
```

The NPZ contains minimum/maximum observed Z, counts and class-2-label counts per
cell. The JSON sidecar records source SHA256, header, class histogram, coverage,
CRS assertion and limitations. One metre is aggregation spacing, not an accuracy
claim. Empty cells stay NaN, including occluded ground. Point extrema do not
establish ground elevations. Even class 2 labels need provenance and validation.
The output cannot be loaded as a verified `TerrainGrid` or hydraulic input mesh.

The reader uses bounded chunks and an explicit point-count guard (10 million by
default). It rejects compressed, truncated, nonmetric, oversized and invalid
inputs. Existing outputs are not overwritten. No source points are modified.

## Local execution, 19 October 2026

The downloaded original `AlMasarAlAkhdar_TILE_1-1.las` was fully processed:
1,629,609 points, all class 0, no embedded CRS. At 1 m aggregation there are
14,324 populated cells of 16,724 (85.65%). Its approximately 112 × 147 m point
extent intersects 10 BBED footprints under the project's existing externally
asserted EPSG:32636. Overlap is not independent alignment validation.

Private artifacts are under
`/Users/johnkofdarelli/dynacity-private/data/point-cloud-review-20261019/`:
surface NPZ and provenance JSON, coverage-check JSON, source inventory, review
Markdown and a figure comparing observed extrema against BBED outlines.

Focused filesystem enumeration located 24 LAS paths: the downloaded original,
seven source candidates in AUB OneDrive and 16 edited derivative paths in an
older Documents project. All 23 other paths were cloud-only placeholders at
inspection. Derivatives include draft labels, car edits and recoloured copies,
not 16 independent surveys. Their contents were not hydrated or treated as
verified ground. Two additional ZIP archives contain derivative work.

The loaded city ground model remains separate. Flood terrain adoption requires
checked ground classification, gap boundaries, independent registration and
vertical datum/checkpoints. Missing underground connectivity and event forcing
cannot be obtained from a point cloud.

## Viewing the measured points

`dynacity export-point-cloud /path/original.las --output-dir /private/data/cloud-view
--crs EPSG:32636 --crs-evidence 'Actual coordinate assertion evidence'` exports all
source points and RGB in separate binary buffers, with checksums. No points are
interpolated or decimated. Horizontal positions are reprojected to WGS84. Source
RGB above 8-bit range uses the high byte for display. The tile minimum source Z
is subtracted only for presentation beside the flat map; no geoid conversion
or ground extraction is implied. Original Z is recoverable with the recorded
`display_z_offset`.

Start the local city server with `--point-cloud-dir /private/data/cloud-view`.
The **LAS point cloud** checkbox shows/hides the source points and a teal tile
outline; **Centre tile** frames them. Overlapping BBED and surrounding context
blocks are suppressed while points are visible, then restored when hidden.
Boundary-overlapping blocks are suppressed in full to avoid hiding the scan.
Cloud visibility and the focused camera are retained in the location link.
Nothing is fed into hydraulic terrain, transition-model features or forecasts.

This option is limited to loopback serving for private local viewing. The API
serves only the configured metadata and fixed position/RGB buffers; no caller
file paths are accepted. Buffer counts/checksums are checked on startup. If no
cloud is configured, the control remains hidden. The first live tile is stored
at `/Users/johnkofdarelli/dynacity-private/data/point-cloud-view-tile-1-1/`.

### Visual road alignment

The first tile's minimum observed point was about 7.38 m below the central
road corridor. Subtracting only that minimum left the road points floating
above the flat CARTO road plane. The local view now has a separate optional
`display_alignment` record in its metadata: `kind: visual_road_anchor`, a
finite `translation_z_m`, and `changes_source_coordinates: false`.

For this tile, 725 observed points in two 2 m radius windows on OSM ways
530756703 and 530760370 gave a combined median source Z of 81.65054. The two
window medians agreed within 0.01 source units. Relative to the original export
offset of 74.27493, the rigid display translation is **−7.37561 m**. The windows,
point counts, quantiles, source hashes and method are retained privately in
`visual-road-alignment.json`, and the original metadata was backed up.

The browser applies the translation once to its downloaded position buffer.
The LAS, exported binary positions, RGB, source hashes, horizontal coordinates
and relative 3D geometry remain unchanged. This is a visual road-corridor anchor
from unclassified points, not a surveyed checkpoint, ground classification or
vertical datum conversion. Real local slopes can still differ from a flat
basemap away from the anchor. It is not passed to the flood terrain or solver.
