# Canopy heights over the green corridor

Meta and WRI **High Resolution Canopy Height Maps v1** over Al Masar al Akhdar's 250 m buffer: tree candidates, heights at
OSM-mapped trees and the share of each street with canopy beside it. It builds on the pilot in `canopy_review.py` (38 crown
peaks for a small extent, see `docs/web-appearance-workflow.md`) and keeps its meaning: a candidate is a model estimate of a
crown top, not an observed trunk and not an approved placement.

## Source

- Meta and World Resources Institute (WRI), 2024, *High Resolution Canopy Height Maps (CHM)*, v1;
  registry <https://registry.opendata.aws/dataforgood-fb-forests/>, bucket `dataforgood-fb-data`, prefix
  `forests/v1/alsgedi_global_v6_float/` (public, no credentials).
- Licence **CC BY 4.0**. Attribution: "Meta and World Resources Institute (WRI), 2024. High Resolution Canopy Height Maps (CHM).
  Source imagery for CHM (c) Maxar." The imagery itself is not fetched or redistributed here.
- Tile `122112210` (zoom-9 quadkey; 188 MB, uint8, EPSG:3857, 1.19 m cells = 0.99 m on the ground at Beirut) covers the whole
  corridor and all its traffic roads. Its footprint file `metadata/122112210.geojson` (16 MB) holds capture dates.
- Raw files and their URL, size, SHA256 and retrieval time are in `<open-sources>/sources.json`; processed products in
  `<open-sources>/canopy/`. None of it is in Git.

## Commands

```powershell
dynacity fetch-canopy --corridor $env:DYNACITY_DATA_ROOT\corridor\masar-akhdar.geojson `
  --sources-root $env:DYNACITY_DATA_ROOT\open-sources-20261120
dynacity build-canopy --corridor ...\masar-akhdar.geojson --sources-root ...\open-sources-20261120 --tile 122112210 `
  --traffic ...\traffic\beirut-traffic-20261024.json --osm ...\traffic\osm-beirut-context-infrastructure-20261027.json `
  --footprints ...\overture\overture-buildings.geojson --output ...\open-sources-20261120\canopy
python scripts\plot_canopy_corridor.py --canopy-dir ...\canopy --corridor ...\masar-akhdar.geojson --output canopy-corridor.png
python scripts\check_canopy_registration.py --chm ...\raw\meta-wri-chm\122112210.tif --footprints overture-osm ...\overture-buildings.geojson --output registration.json
```

`fetch-canopy` picks the tile(s) from the corridor's box (plus 0.02 degrees), refuses any file over `--max-bytes`, and reads each
as parallel HTTP range requests (one connection to the bucket gave ~20 KB/s; 24 in parallel took 15 minutes for the tile).
`--footprints` is optional.

## Products (`canopy/`)

| File | Contents |
| --- | --- |
| `corridor-canopy-candidates.geojson` | Points: local maxima of the height model >= 3 m with >= 4 m spacing. Properties: `id`, `canopy_height_at_peak_m` (as in the pilot), `kind = model_estimate_not_observed_trunk`, `acq_date`, `nearest_mapped_tree_m`, `nearest_mapped_tree_row_m`, `likely_unmapped` (> 5 m from every mapped tree node), `on_building_footprint` |
| `corridor-osm-tree-heights.json` | The OSM `natural=tree` nodes inside the buffer with the maximum model height within 3 m |
| `corridor-road-canopy-cover.json` | One record per road of the corridor traffic subset: `canopy_cover` = share of centreline samples (every 1 m) with canopy >= 3 m within 8 m, `valid_fraction`, `length_m`, `intersects_buffer` |
| `chm-corridor-clip.tif` | The height model clipped to the buffer (NaN outside) |
| `canopy-summary.json` | Parameters, counts, distributions, limits |

**Road keys.** The planner numbers roads by position in the traffic model it serves. `city_road_id` is the index in the citywide
model (the planner without `--corridor`); `corridor_road_id` is the position in the whole-road subset that `serve --corridor`
builds with `pilot_network` (1,426 of 10,818 roads). Both match `planner.planner_roads` feature ids, names and geometry for every
record. `intersects_buffer` is true for 542 of them; the rest touch the buffer's box only.

## Results for the corridor

- **2,224 candidates** (10.5 per hectare, 212.6 ha). Heights are whole metres: median 5, p05-p95 3-14, max 24 m. At least a quarter sit at the
  3 m floor, and the count depends on the floor and spacing (3 / 4 / 6 / 8 m spacing gives 3,712 / 2,224 / 1,375 / 1,151).
  424 lie on a building footprint (Overture/OSM); 1,800 do not.
- **OSM trees.** Only 21 tree nodes are mapped in the buffer (one tree row), 19 of them in one double line near the southern end.
  The model shows >= 3 m canopy within 3 m of just one; the 19 show none within 12 m, so they are young or recent plantings
  or the model misses them. None carries a `height` tag. As a consequence 2,223 of 2,224 candidates are more than 5 m from any
  mapped tree: OSM here is far from complete, and "likely unmapped" says almost nothing about individual trees.
- **Roads.** Mean canopy cover 0.20 over the 1,426 roads (median 0.03; 49 % have none, 16 % have at least half); streets with
  the most: Rue Beni Qahtane, Rue Khodr, Observatoire, Ibrahim Pacha.

## Observation date

The raster has no per-pixel date. The footprint file gives one: the whole corridor lies in one footprint with
`acq_date` **2019-06-03**, so every candidate carries that date. A footprint can still be a mosaic of scenes, and the date
is that of the imagery, not of today's trees: seven years of felling, planting and growth (and the 2020 port blast) are not in it.
No claim about present-day conditions follows.

## Limits

- Model estimates from satellite imagery; heights are quantised to 1 m, biased low for tall trees, uncertain by metres.
- Trunk position, species, ground contact and clearance are unverified; candidates are crown tops.
- **Registration.** Against Overture/OSM building footprints (4,884 in the box) the canopy overlaps footprints least when it is
  sampled ~9 m east and ~6 m south of them (overlap 17 % against 22 % as given; canopy covers 7 % of the box, footprints 25 %).
  Part of this may be the footprints or crown-top parallax in off-nadir imagery; nothing is corrected. Treat positions and the 8 m
  road cover as approximate to about 10 m, not lot-accurate. `scripts/check_canopy_registration.py` reproduces the test.
- About a fifth of the canopy lies on building footprints (roof edges, parallax of tall buildings); filter with
  `on_building_footprint` before using candidates as trees.
- Heights are not height above the walkway: ground level, terraces and structures are not modelled.
