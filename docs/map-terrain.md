# Terrain in the web viewer

`dynacity serve --terrain terrain-glo30.npz` puts the city on its relief: the basemap and imagery are draped over a height model, and buildings, streets, the green corridor, cars, pedestrians, trees and signals stand on it. Without `--terrain` nothing changes (flat plane, z = 0).

## One grid, two readers

`terrain_viewer.ViewerTerrain` cuts one lon/lat height grid (1e-4 degrees, about 9 x 11 m; whole centimetres in int16) from the ground model and the page uses it twice:

- the page downloads it from `/v1/viewer/terrain.bin` (gzip, 0.73 MB for the whole of Beirut) and `groundZ(lon, lat)` samples it bilinearly for the z of every deck layer;
- MapLibre's `raster-dem` source reads Terrarium PNG tiles from `/v1/viewer/terrain/{z}/{x}/{y}.png`, cut from the same grid with the same bilinear rule, and `setTerrain` drapes the basemap over them (exaggeration 1).

**Ground used.** With `--beirut001` the Beirut 001 ground grid (finer, from the model's own ground mesh) is used where it covers and GLO-30 elsewhere, joined by `terrain.merge_ground` (the GLO-30 side is offset onto the Beirut 001 ground at the seam, then fades to a regional offset over 60 m). Without the pack it is GLO-30 alone. Sea (NaN) is 0 m; beyond the grid the nearest edge value holds, so there is no cliff at its border.

These are approximate ground heights: GLO-30 is a surface model filtered down to street level (see `terrain.py`), and the Beirut 001 ground sits on an inferred alignment. They are not a survey, and the datum is the sources' (EGM2008 geoid for GLO-30), used as height above the viewer's z = 0.

## Overlay mode is correct, so deck stays non-interleaved

deck.gl 9.4's `MapboxOverlay` already centres its camera on MapLibre's terrain (its viewport `position[2]` is the centre elevation). Measured in the page, `viewport.project([lon, lat, groundZ])` lands on `map.project([lon, lat])` (which includes the terrain) within a few pixels at pitches 55 to 72. The one limit of overlay mode: deck's canvas is drawn over MapLibre's, so terrain never hides deck geometry (a ridge does not hide a street behind it unless deck geometry, such as buildings, does).

## What stands on the ground

| Layer | z |
| --- | --- |
| Street paths (`elevateRoads`) | each path is densified to a vertex every 8 m, points are `[lon, lat, z, h]`: `z = groundZ + h`, `h` = bridge deck height above ground (0 on the ground). Pavements, edges, markings, junction paint, pedestrians' flows, cars and signal poles all read these paths |
| Cars | `.04 m` above the road; on a junction they keep their last height above ground; pitch follows the grade over 8 m |
| Sidewalk slabs, kerbs | each end at the road's own level |
| BBED, Beirut 001 and OSM buildings, basemap context buildings | footprint base at the lowest ground under the footprint; heights unchanged |
| Facade windows and roof coping | base added |
| Bridge piers | from the ground under each pier up to the deck |
| Corridor line and outline, OSM paths, rails, waterways, ground surfaces, parking, trees, furniture, places, crashes, signals | per point, with the layer's own small lift against z-fighting |

The Beirut 001 bridge mesh is in model heights, which already agree with GLO-30 (docs/beirut001-pack.md), and is left as it is. The LAS point cloud keeps its own display heights.

## Limits

- Polygons (land-use surfaces, parking) take per-vertex ground heights; their interiors are not subdivided.
- A road mapped at ground level that in reality crosses on an untagged viaduct follows the ground under it; so do tunnels (they were drawn flat before).
- Ground heights are 10 m cells: a retaining wall or river bank steeper than that is smoothed.

## Option: FABDEM V1-2 bare-earth ground (non-commercial licence)

`dynacity fetch-fabdem` writes the same `.npz` as `fetch-terrain` from
[FABDEM V1-2](https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn) (Forest And Buildings removed
Copernicus DEM, University of Bristol; Hawker et al. 2022, doi:10.1088/1748-9326/ac4d4f). It takes CRS, origin, cell size and
shape from an existing terrain file (`--like`), so `serve --terrain <fabdem.npz>` works unchanged and the default
GLO-30 file is not touched:

```powershell
dynacity fetch-fabdem --like $env:DYNACITY_DATA_ROOT\terrain-glo30-roads.npz `
  --output $env:DYNACITY_DATA_ROOT\terrain-fabdem-roads.npz `
  --sources-root $env:DYNACITY_DATA_ROOT\open-sources-20261120
```

- **Download.** Only the one 1-degree tile (`N33E035`, 18 MB) is read, by HTTP range requests into the publisher's
  1.6 GB 10-degree archive; the archive is not downloaded. The tile goes to `<sources-root>/raw/fabdem/`; its URL,
  size, SHA256, retrieval time and the licence text are appended to `<sources-root>/sources.json`.
- **No street-level filter.** GLO-30 is a surface model, so `terrain.ground_from_surface` lowers it with a 150 m
  low-percentile window. FABDEM already has buildings and trees removed in the source, so that filter is skipped and
  the height is resampled bilinearly onto the lattice. The one rule kept from the GLO-30 path is the sea rule (cells at or
  below 0 m carry no ground, land cells are at least 0.1 m), so both files share one coastline (2,128 of
  about 784,000 land cells, 0.3 %, are land in only one of the two files).
- **Licence: CC BY-NC-SA 4.0.** Non-commercial and share-alike, unlike GLO-30's licence. Credit the attribution stored in
  the file's `source` field (also in `sources.json`); commercial use needs the publisher (fabdem@fathom.global). The
  dataset page's metadata box also lists a "Non-Commercial Government Licence"; the page text and `license.txt` give
  CC BY-NC-SA 4.0, which is what is recorded here. Keep this file out of any commercial or public deployment.
- **Datum and accuracy.** Heights are metres above EGM2008, like GLO-30. FABDEM is a machine-learning correction of
  GLO-30 with vertical errors of a few metres, larger on steep slopes and in dense cities; it is not a survey.

### Measured against the served GLO-30 ground and Beirut 001

`scripts/compare_fabdem_glo30.py` gives (full numbers in the run's `fabdem-stats.json`; 10 m cells, heights in metres):

| FABDEM minus GLO-30 (served, filtered) | cells | median | p10 / p90 | max abs |
| --- | --- | --- | --- | --- |
| Inside the Beirut 001 ground coverage | 207,018 | +0.83 | -0.72 / +4.21 | 24.9 |
| Outside it | 574,890 | +0.98 | -0.84 / +14.82 | 76.8 |

FABDEM is mostly higher than the filtered GLO-30 because a low percentile over 150 m sits below the true ground on any
slope; on the hills east of the city the gap reaches tens of metres. Against the Beirut 001 ground at its cells
(bilinear, 207,802 cells): FABDEM minus Beirut 001 has median +0.30 m and absolute p10 / p90 of 0.35 / 7.63 m (max 40.1 m);
GLO-30 filtered minus Beirut 001 has median -0.37 m and absolute p10 / p90 of 0.33 / 7.92 m (max 57.1 m). FABDEM does not
agree better with Beirut 001 in the typical cell (median absolute gap 2.36 m against 2.11 m); it removes the filter's
low bias and trims the large misses.

After `merge_ground` (Beirut 001 where it covers, the other model elsewhere) the seam stays smooth with either source: the
step between a Beirut 001 cell and its neighbouring fallback cell has median 0.20 m for FABDEM and 0.18 m for GLO-30
(Beirut 001's own neighbouring cells differ by 0.20 m), and p90 1.05 m against 0.80 m. FABDEM needs a smaller shift at the
seam (first-ring median offset +0.82 m against +1.39 m) because it is not biased low. The profile in the run folder shows one
transect.
