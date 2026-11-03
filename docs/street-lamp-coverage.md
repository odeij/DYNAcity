# Street-lamp location coverage audit — 31 October 2026

The current ten lights are not ten surveyed locations: nine are illustrative
placements on the Charles Malek reference section, and only one is an explicit
OSM node (`node/8358370837`). The saved mapped instance index contains that one
lamp. This is a pilot coverage limitation, not evidence that Beirut has ten
poles and not a renderer instance limit.

## Fresh source check

`scripts/fetch_street_lamp_inventory.py` queries the full existing simulation
extent `[35.4693, 33.8288, 35.5720, 33.9076]` for `highway=street_lamp`,
`man_made=street_lamp`, `light_source`, `tower:type=lighting`, `lamp_mount`,
`light:mount` and highway ways with `lit` tags. The live query returned one
explicit lamp, no additional lighting candidates, and 388 road segments with
lighting tags: 384 `lit=yes`, 4 `lit=no`. These are road segments, not unique
streets or pole locations. Data is © OpenStreetMap contributors, ODbL. Tags do
not establish current operation or inventory completeness.

Private artifacts: [query/report](C:/private/dynacity-data/engine/street-lamp-inventory-20261031/report.json),
[GeoJSON](C:/private/dynacity-data/engine/street-lamp-inventory-20261031/lighting-inventory.geojson)
and the raw response beside them, including exact query, endpoint, object
versions/timestamps and retrieval time. No scene objects were changed.

## Fast expansion options

- For rapid visual coverage, batch estimated placements along suitable road
  edges, with road-width-aware offsets, intersection clearances, ground-height
  checks and building/bridge clearance checks. Keep separate source IDs and
  estimated-placement metadata. Do not claim regular spacing is a real survey.
- For actual coordinates, seek a municipal/maintenance GIS inventory or use
  geotagged street imagery to detect poles, match detections across adjacent
  frames, estimate coordinates from calibrated camera poses/depth and review
  uncertain matches. An image GPS point alone is the camera location, not the
  pole location. Existing private street imagery covers only a sample.
- [Rebirth Beirut's lighting program](https://rebirthbeirut.org/lets-light-up-our-community/)
  lists relit streets and avenues, including Charles Malek. It is useful as a
  street-level priority reference but does not publish individual pole
  coordinates on that page.
- [Beirut Urban Lab's published basemap](https://www.beiruturbanlab.com/en/Details/666)
  describes buildings, parcels, roads, contours and zones. That description
  does not establish a downloadable pole inventory.

A fresh OSM download alone cannot recover poles that nobody has mapped. The
fast visual route is a labeled estimated layer; full real-location coverage
requires an inventory or a broader imagery survey. No inferred placements were
installed during this source audit.
