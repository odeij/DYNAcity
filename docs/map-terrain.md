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
