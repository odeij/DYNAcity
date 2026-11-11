# Beirut street reference and bridge audit

Audit date: 25 October 2026. No point clouds were used. Counts below describe
the locally cached OSM coverage, not a complete survey of Beirut or confirmed
present-day hardware.

## First reference section

Use a **250 m section of Charles Malek Avenue centred on model road 8416**
(OSM way `584626701`) in Achrafieh. Its cached one-way direction is eastbound.
This short model segment anchors the reference; the detailed section extends
along adjacent source road segments rather than stretching that segment.

On 28 October 2026, a comparison with the active city building-batch triangles
found that two of the nine illustrative lamp positions overlapped modeled
buildings in plan view, with overlapping height ranges. Their meshes and light
actors were moved together along the reference road, by approximately 8 m and
6 m respectively, to candidate positions clear of the projected building
footprints. The source IDs remain `illustrative/lamp/48` and
`illustrative/lamp/96`; these are not surveyed lamp locations. The actor
packages were saved and reloaded, and a game-mode night capture confirmed all
nine loaded reference lights change from zero daytime intensity to 1600 at
night. Captures also show harsh highlights and an otherwise dark surrounding
city; short-lived softer-light trials were not applied to the saved game because
they made the road darker without sufficiently resolving the highlights.
Private reports: `reference-lamp-clearance-20261028.json` and
`night-clearance-after-20261028.json` under `Saved/DynaCity`.

A further audit of the ten illustrative reference benches and bins found five
whose origin points overlapped modeled buildings in plan view and height range.
Those five instances were moved to nearby candidate positions along the same
road. Candidate checks used approximate expanded object footprints, projected
building triangles, other reference props and nearby indexed roads. After the
actor packages were saved and reloaded, a repeat point audit found zero of the
ten origin points inside modeled buildings. A second check of the transformed
mesh bounding boxes found no projected building overlap at compatible height
ranges. These are conservative source-geometry checks, not a full collision test
or a claim that these props represent surveyed furniture. The reference builder
retains the adjustments. A game-mode daylight capture is available, although
the camera does not show every adjusted prop clearly. Private reports include
`reference-furniture-clearance-20261028.json` and
`reference-furniture-bounds-audit-20261028.json` under `Saved/DynaCity`.

- Centre: longitude `35.5218280509`, latitude `33.8919873454`.
- EPSG:32636 centre: `[733208.4, 3753042.85]` m.
- Source ground at centre: `61.7854413` m, datum unverified.
- Local east/north/up centre: `[2423.6417198, 1491.6910048, 10.6796518]` m,
  relative to city origin `[730784.7582802251,3751551.1589951646,51.10578950470706]`.
- Heading: `+0.627300987` degrees counterclockwise from local east. Convert
  through the calibrated Unreal basis; do not treat it as an Unreal yaw.
- Within a 150 m radius: **5 signal nodes, 15 crossing features, one grass
  feature and 27 displayed tree instances**. All 27 trees are illustrative
  spacing along five mapped tree rows (`478839772`–`478839776`), not 27
  individually surveyed positions. The row geometry is source data; count,
  species, crown shape and exact instance positions remain inferred.

Saint Nicholas Garden (`way/264735045`) is a useful second park view farther
west, but its local density must not be confused with avenue-wide totals.
A 150 m radius around nearby road 8423 contains 4 signals, 2 parks, a bench and
a bus stop; it has no individual tree or crossing features in the display cache.

The full named avenue, within 75 m of its cached linework, has 13 signals,
32 crossing features, 11 displayed tree instances, 3 parks and a bench.
Those are avenue-wide counts, not counts for the proposed first section.

## Available OSM detail

The merged `traffic/osm-beirut-context.json` cache contains 211 signal elements,
1,191 individual tree nodes, 20 tree rows and 198 bridge-tagged elements.
The derived display payload contains 1,498 tree instances after inferred row
spacing, 85 parks, 61 gardens, 161 grass, 134 wood and 49 scrub features,
201 crossings and 227 bus stops. Only one street lamp and eight benches are
mapped in this cache; missing objects must not be presented as mapped facts.
Display bounds are `[35.4693,33.8288,35.572,33.9076]`; retrieval is recorded as
24 October 2026, with source timestamps retained per feature where available.

## Existing bridges and coverage gaps

The current shared city layer already imports the original Beirut 001 transport
structure triangles as one mesh. The structures pack has **90 bridge outlines
and 421,423 source transport triangles**. `engine_city_surface.py` retains those
triangles and explicitly adds no invented piers. Its structure GLB is about
34 MB. The older neighbourhood exporter in `engine_scene.py` does add
illustrative support cylinders to elevated road strips; these must not be
overlaid indiscriminately on the full-city source structure mesh.

The shared surface export has 5,697 of 10,817 source model road segments:
5,057 are skipped for missing source ground and 63 for being underground
(without a fallback terrain; with `--fallback-terrain` the missing-ground roads
are drawn on offset GLO-30 ground, see `beirut-shared-world.md`).
Of the exported roads, 5,607 use source ground, 27 use matched model decks,
and 63 use illustrative ramp/level offsets. Only 34 exported segments have
positive levels (33 at level 1 and one at level 2); 27 are deck-matched.
The remaining elevated geometry and approach lifts are not surveyed bridges.

Preserve source structure geometry and improve its concrete finish first.
For unmatched elevated roads, inspect deck/terrain clearance and source tags
before adding supports. Any new inferred deck thickness, railing or pier
placement needs separate metadata and must avoid duplicating existing source
triangles, obstructing roads below, or filling source-ground gaps with flat land.

## How this was checked

Read the cached OSM raw/display JSON, the shared-world manifest and road
metadata, `beirut001-structures-pack.npz`, and the city/pilot surface exporter
code. Compared candidate streets using EPSG:32636 distances and sampled the
chosen centre from the source ground grid. This audit did not validate Unreal
runtime collisions or verify hardware against dated street photographs.
