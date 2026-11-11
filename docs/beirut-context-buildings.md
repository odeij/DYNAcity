# Context buildings around Beirut 001

The Unreal city's 3D buildings come from Beirut 001, which covers the 12
municipal areas (about 20 km²). The terrain extension reaches about 10 × 10 km,
into the southern suburbs and east of the Beirut River, so before this layer
that area showed ground and OSM ground cover with no buildings.

`dynacity.engine_context_buildings` fills it with mapped footprints from
Overture Maps (OSM outlines plus Microsoft machine-learned footprints, ODbL).
Beirut 001 stays authoritative: any footprint sharing at least 10% of its own
area, or of the Beirut 001 building's area, is dropped, never merged or moved.

## Build of 28 October 2026

Overture release 2026-09 over the traffic-map extent
(35.4693, 33.8288, 35.5720, 33.9076):

| | Buildings |
| --- | ---: |
| Overture candidates | 68,719 |
| Dropped: overlap a Beirut 001 building | 15,193 |
| Dropped: footprint under 8 m² / no terrain | 211 / 176 |
| Exported | 53,139 |
| — outside the 12 municipal areas | 50,892 |
| — gaps inside the municipal areas | 2,247 |
| Source: OSM / Microsoft ML | 50,058 / 3,257 (of 53,315 kept) |

Only 159 footprints have a mapped height and 111 a floor count. The other
52,980 use the median Beirut 001 height for the same footprint-area bin
(8.5 m under 100 m², 17 m for 100–200 m², about 25 m for 200–800 m²). These
are estimates, labelled per building as `height_source` and `height_kind`; they
are not BBED records and carry no status or forecast data. Floor counts use the
fitted 3.0 m per floor; the order is in [building-heights.md](building-heights.md).

Footprints sit on the same extended visual ground as the terrain extension:
the base is 1 m below the lowest ground point under the outline, and the roof
is the median ground plus the height. They are grouped into 1.5 km tiles and
batched per facade material (42 packages, 202 meshes, 78 MB). Mesh labels start
with `buildings_context_`, so the existing editor loading and styling include them.

## Unreal is Beirut only

The Unreal city now keeps only municipal Beirut (see
[unreal-city-world.md](unreal-city-world.md#beirut-only)). Its context layer is
built with `--boundary PRIVATE/beirut-districts.json`: 2,246 gap-fill buildings
inside the 12 areas (1,224 OSM, 1,023 Microsoft; 18 packages). The full
53,139-building build above is what the web app uses.

## Web app

`--web-output` also writes a browser companion (7 MB): estimated heights for
64,637 OSM outlines, keyed by OSM ID, and the 3,871 Microsoft footprints as
features. Serve it with the OSM context:

```powershell
dynacity serve ... --landscape PRIVATE/osm-beirut-context-infrastructure-20261027.json --context-buildings PRIVATE/context-buildings-web.json
```

OSM outlines keep their own mapped heights; only the flat 6 m fallback is
replaced. Every feature carries `height_kind` and `estimated`; the legend counts them and the viewer draws estimates lighter. The viewer still hides buildings covered by BBED or Beirut 001,
depending on the building mode, and draws the rest across the whole map at
every zoom as one array built once per mode. Hover shows the source and how each
height was set. Unreal drops Microsoft footprints overlapping Beirut 001 at build
time; the browser does it at view time, so BBED-only mode can still show them
inside the city.

## Why the browser viewer showed fewer before

The browser viewer uses the same OSM data, but shows far fewer buildings at once:

- Its standard `serve` command passed no `--landscape`, so it drew no OSM
  context buildings, only BBED's 3,349 records (or Beirut 001's) plus CARTO
  basemap buildings. The run command now passes both files.
- `osm-landscape.json` (Sanayeh, 23 October) holds 762 OSM buildings.
- `osm-beirut-context-display.json` holds 64,828 for the whole map extent, the
  same set Overture's OSM share comes from. With that file the browser used to
  draw only buildings inside the current view, only from zoom 16, at a flat 6 m.

## Reproduce

```powershell
python -m pip install -e '.[overture]'
overturemaps download --bbox=35.4693,33.8288,35.572,33.9076 -f geojson -t building -o PRIVATE/overture-buildings.geojson
python -m dynacity.engine_context_buildings --overture PRIVATE/overture-buildings.geojson --pack PRIVATE/beirut001-structures-pack.npz --ground PRIVATE/extended-visual-ground.npz --city PRIVATE/city-batches.json --output PRIVATE/context-buildings --web-output PRIVATE/context-buildings-web.json
python scripts/queue_unreal_city_layer.py --checkpoint PROJECT/Saved/DynaCity/city-import-BeirutCityBatched.json --layer PRIVATE/context-buildings/context-buildings.json
python scripts/import_unreal_city.py --output PRIVATE/context-buildings/unreal-import-progress.json
```

`--osm` with an Overpass extract can replace `--overture`; Overture already
includes OSM, so the two are not combined.

## Limits

- Footprints are not aligned to Beirut 001; offsets of a few metres are possible
  where the two meet. Some machine-learned footprints inside the city may be
  slightly shifted copies of Beirut 001 buildings with under 10% overlap.
- Flat roofs and illustrative facades only.
- OSM and the machine-learned footprints may be out of date or incomplete.
- Attribution: Overture Maps Foundation, OpenStreetMap contributors, Microsoft (ODbL-1.0).
