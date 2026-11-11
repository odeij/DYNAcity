# Beirut city world

The private Unreal project contains a separate `/Game/Maps/BeirutCityBatched`
World Partition map. The existing `BeirutPrototype` traffic/capture pilot remains.

Use **Tools > Explore Beirut > Open full-city geometry** in Unreal.
**Go to area** contains the 12 named production areas. **City overview** frames
the source city bounds; **Street view near selection** uses the nearest source
road. These are editor camera controls, not driving physics.
**Inspect selected source object** reports the selected batch's area, object
count, example IDs and companion index. Individual source-object selection
from Unreal collision faces is not verified.

The city imports 17,397 source buildings and 5,697 whole roads, plus ground and
original transport-structure triangles. Buildings and roads use 72 meshes.
Four calibration actors bring the source actor count to 78. Materials, facade
windows, lane-derived widths and unmatched elevated-road lifts are illustrative.
Buildings with missing ground, unassigned buildings, 5,057 roads without source
ground coverage and 63 underground roads remain explicit export exclusions.

The importer detaches static mesh actors and removes Interchange scene roots
so importer references do not bind entire hierarchies together. All 74 source
surfaces have spatial loading and complex-as-simple static collision. Large
shared ground/transport meshes still have broad streaming bounds; efficient
nearby detail needs finer physical subdivision. No runtime frame-rate or vehicle
physics claim follows from these settings.

World Partition external actors can be unloaded after reopening the map.
`city_world.load_editor_geometry()` loads saved actors for viewing; the city
and area controls call it as needed. This affects editor loading, not runtime
streaming policy. Point clouds remain optional and load on demand through
**Tools > Point clouds**. StreetScape was verified visible/hidden in the city
map with its original 4,800,668 points; other configured previews remain on demand.

## Reproduce

Keep generated files private. Prepare [named areas](beirut-engine-districts.md),
[shared surfaces](beirut-shared-world.md) and [compact batches](beirut-city-batches.md).
Buildings outside Beirut 001 are a separate [context layer](beirut-context-buildings.md).

## Beirut only

The Unreal city is kept to municipal Beirut, the union of the 12 production
areas; the web app keeps the wider map. `dynacity.engine_city_clip` writes the
boundary as a 5 m inside/outside mask in Unreal centimetres, and
`city_clip.clip(mask, report, dry=False)` applies it in the editor (dry run first):

- source building batches stay whole; the context building layer is built
  with `--boundary` instead (2,246 gap-fill buildings inside Beirut);
- street instance groups keep only instances located inside;
- meshes fully outside are deleted; meshes crossing the line lose the triangles
  whose centre is outside, so cut edges follow triangles (up to about 10 m on
  the terrain), not the exact line.

The pass saves each cut mesh as it goes and runs the largest meshes last.
Traffic shown in Unreal is a 350 m window around the camera or driver; near the
edge a few simulated cars can still appear on roads just outside Beirut.
Create the separate world once in Unreal's Python console:

    import city_world
    city_world.create(r"PRIVATE/city-batches.json", r"PRIVATE/shared-world.json")

Advance serial imports from the repository with the local official MCP enabled:

    .venv/Scripts/python.exe scripts/import_unreal_city.py --output PRIVATE/unreal-import-progress.json

`city_tools.CityTools.advance` finalizes one completed package and starts the
next. A checkpoint under `Saved/DynaCity` records pending/completed jobs. It
refuses duplicate labels and concurrent imports. After interruption,
`resume_empty_pending()` allows retry only when none of its expected actors
exist. Partial imports require inspection. Completed maps are not automatically
replaced on source updates.

Finish with `city_collision.configure()`, save map/assets and run
`verify_city.verify()`. These meshes stay static; vehicle bodies need separate
convex collision and physics. Facade styling is optional:
`style_buildings.apply(include_batches=True)`.

## Verified on 25 October 2026

- All 26 packages imported and saved; 23,094 source objects retained in 72
  building/road meshes. Full source GLB geometry audit passed.
- 78 source actors, 74 detached spatial surfaces and exactly four basis actors;
  100 cm per source metre on all axes.
- Static collision configured on all 74 surfaces; simple visibility traces hit
  blocking surfaces at 21 sampled source-road locations.
- Reopening initially loaded four basis actors. Explicit editor loading restored
  all 74 surfaces, with collision checks passing again.
- Named-area focus, city street view, source inspection and StreetScape toggling
  exercised in the running editor.
- Toggling StreetScape after reopen loaded the identical saved actor path;
  unloaded cloud and exposure descriptors are reused rather than duplicated.
- Earlier per-road import reached approximately 12.3 GB of editor private
  memory before further growth. Following a fresh start, the complete compact
  city was observed around 7-8.2 GB during import and 7 GB after reload. These
  are snapshots, not controlled peak-memory or FPS benchmarks.
- 21 focused geometry, batching, cache, capture and traffic tests passed.

Controllable driving, road-transition validation, live SUMO, photographic
facades, capture alignment, water and detailed street context remain future work.

The [driving preparation](unreal-driving.md) now includes twelve source starts,
a runtime controller awaiting compilation, and a collision-accuracy repair.
Automatic Nanite fallback collision removed road strips and distorted terrain.
Explicit full fallback triangles plus a physics rebuild and saved-map reload
now pass wheel-support and body-clearance checks at all twelve starts.
