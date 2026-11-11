# Beirut neighbourhood engine prototype

The separate [city-wide Unreal world](unreal-city-world.md) now contains all
prepared named-area buildings and roads. This page describes the original pilot.

The first export covers a 700 by 700 metre square around Sea Highway
(35.49525 E, 33.87908 N). It prepares assets for Unreal without a Cesium
dependency. It does not replace the browser application or move SUMO into Unreal.

The subsequent [named city-area build](beirut-engine-districts.md) prepares twelve
building packages in the same coordinate frame. [Capture coverage](beirut-capture-coverage.md)
records real local inputs and labelled previews. [Exploration controls](unreal-exploration-controls.md)
add playback, camera and source inspection to the existing editor prototype.

## Current implementation and verified scope

The crop is centred on 33.87908 N, 35.49525 E in the Mousaitbeh/UNESCO area.
"Sea Highway" is the traffic dataset's road label; the export includes the
surrounding blocks. The southwest replay close-up is near source OSM ways
1018275075 (Moussa Ben Nousseir) and 5051252 (Habib Abi Chahla). This is a
700-metre neighbourhood prototype, not coverage of all Beirut.

- `engine_scene.py` reads the existing Beirut 001 structure pack, its ground grid,
  traffic model and processed OSM context. It keeps source UUIDs, original road
  indices, OSM way IDs, provisional BBED matches, roof heights and tree metadata.
- Coordinates are local EPSG:32636 metres, east/north/up, with the exact origin
  recorded. Terrain is the existing source grid. Missing ground is skipped and
  counted; no zero-elevation replacement is invented.
- Buildings are complete source footprints with courtyard holes. Roof steps
  already flattened in the source pack remain identified in metadata. No new
  facade measurements are inferred. Source alignment remains provisional.
- Roads follow sampled ground and a shared-node illustrative ramp profile. Some
  bridge lifts match model decks; other lifts use a labelled level assumption.
  Concrete support placement is illustrative. These first-pass export profiles
  are not yet proven equivalent to every browser bridge profile.
- `engine_asset.py` writes a standard GLB with material groups, source metadata,
  shared tree meshes, unit normals, and four coordinate calibration nodes.
- `engine_traffic.py` uses a separate existing API session to record SUMO frames,
  releases it afterwards, and converts positions to local metres with the
  corresponding exported road grade. Simulation metrics still cover the full
  fleet. Vehicles without a nearby exported road are omitted from display only.
- `engine/preview/index.html` loads the actual GLB and recorded traffic for
  inspection. This is a browser asset viewer, not evidence of Unreal performance.

The initial private export contains 629 buildings, 244 roads, 44 illustrative
support objects, 14 mapped trees, and one terrain mesh. Ten intersecting roads
were skipped because their sampled paths reach missing source ground. A
20-second recording has 21 frames and up to 687 displayable vehicles and 257
signal movements. Neither fleet size nor browser performance predicts Unreal FPS.

## Build the assets

Use the repository virtual environment with the `engine` extra (Shapely 2.1
supplies constrained triangulation). All generated source-derived assets must
remain outside Git because Beirut 001 redistribution rights are not established.

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m dynacity.engine_scene --pack <private-pack.npz> --traffic <traffic.json> --context <processed-osm-context.json> --output <private-output>/scene.json
python -m dynacity.engine_asset --scene <private-output>/scene.json --output <private-output>/assets/neighbourhood.glb
python -m dynacity.engine_traffic --scene <private-output>/scene.json --url http://127.0.0.1:8014 --seconds 20 --output <private-output>/assets/traffic-replay.json
python scripts/prepare_unreal_project.py --assets <private-output>/assets --output <private-output>/UnrealPrototype
```

Commands refuse to replace existing exports/projects. Use a new output location
for a revised build. The source templates are in `engine/unreal`; generated
projects reference private GLB/replay paths through `prototype_config.json`.

Optional native Blender build (Blender 4.5 LTS):

```powershell
blender --background --python scripts/build_engine_scene_blender.py -- --scene <private-output>/scene.json --output <private-output>/blender --render
```

This builds native mesh objects with source metadata and writes `.blend`, `.glb`
and a preview PNG. Its optional lane paint, material palette and small bevels are
illustrative presentation additions. It does not use BLOSM to replace our
existing building geometry.

## Unreal editor control through the built-in MCP

The project enables Epic's experimental `ModelContextProtocol`, `EditorToolset`
and `SlateInspectorToolset` plugins. It binds only to `127.0.0.1:8016/mcp`.
There is no third-party Unreal server and no change to global agent settings.
`scripts/unreal_mcp_client.py` communicates with the standard HTTP MCP endpoint;
it initializes a session and makes tool calls sequentially on the editor thread.

The `dynacity_tools.DynaCityTools` toolset provides `status`, `import_scene`,
`save_and_frame`, `start_replay`, `stop_replay`, and `capture_viewport`. Discover
schemas with MCP `tools/list`, then `list_toolsets` and `describe_toolset`.
Calls use the `call_tool` dispatcher. Import is asynchronous: check `status`
until it finishes, then save, then start replay. These operations do not run
from a Slate startup callback because saving can re-enter Slate ticks.

The first preview caps mesh distance fields at 32 voxels per axis to bound
initial lighting-data compilation. This quality setting changes no source
geometry and can be raised later when profiling visual quality and GPU memory.

## Manual editor fallback

Open the generated `DynaCityBeirut.uproject` after installation. In the Python
console run `import import_neighbourhood; import_neighbourhood.build_from_config()`.
Wait for the asynchronous Interchange scene import, then save all assets and the
map. Confirm source geometry and scale before claiming successful engine import.

Run `import preview_traffic; preview_traffic.start_from_config()` outside Play mode
for the editor replay proof. It checks four imported calibration nodes and
requires 100 engine units per metre. Source render-frame rate does not determine
the simulation clock. Vehicles are pooled, transient actors. The optional sedan
asset replaces the cube fallback with bodywork, opaque tinted windows, wheels,
mirrors, front/rear lamps and six deterministic paint colours. These are
illustrative generic cars; no vehicle make or observed fleet composition is
inferred. Wheels do not rotate and there is no vehicle physics. Signal
locations are placeholder spheres with emissive red, amber, green and off
materials driven by recorded states; full poles and direction arrows remain an
asset step.
Use `preview_traffic.stop()` to remove only its preview actors and tick callback.

### Optional illustrative car asset

Generate the sedan outside Git using Blender:

```powershell
& <blender.exe> --background --python scripts/build_engine_vehicle_blender.py -- --output <private-output>/vehicles/Sedan.fbx
```

Copy `import_vehicle.py` and `preview_traffic.py` into the generated project's
`Content/Python` directory. In the Unreal command console run:

```text
py import import_vehicle; import_vehicle.build('<private-output>/vehicles/Sedan.fbx')
```

Wait until import completes, then run `py import_vehicle.activate()`. The asset
is imported to `/Game/Beirut/Prototype/Vehicles/Sedan`. Replay checks X-forward
bounds in centimetres before using unit actor scale and the road-contact pivot.
The generated sedan measures approximately 4.41 by 1.88 by 1.47 metres. Imported
geometry, paint-slot overrides and moving cars were checked in Unreal via MCP
on 25 October 2026. Reused actors receive the colour associated with their new
vehicle ID. Source traffic positions and IDs determine motion; paint is cosmetic.

The Python replay is editor-only. A live packaged runtime client, driver physics,
interactive scenario controls, facade detail, collision validation and equal-scene
browser-versus-Unreal benchmarks are not implemented or verified by this scaffold.

## Toggleable native point clouds

The editor has **Tools > Point clouds > Show point clouds**, a checked visibility
toggle independent of city geometry. **Go to selected capture** enables and frames
the capture, while **Return to city overview** moves back without saving or
changing geometry. Capture entries select, show and frame one capture at a time.
This is the current editor interface; a packaged game HUD remains future work.

The project enables Epic's `LidarPointCloud` plugin. Private configuration adds:

```json
{
  "point_cloud_origin_utm_m": ["origin easting", "origin northing", "origin height"],
  "point_clouds": [{"id": "TheStreetScape", "path": "<private-source>/TheStreetScape.las"}]
}
```

Use actual numeric coordinates from `scene.json`'s `coordinates.origin_utm_m`.
The plugin converts metres to centimetres and flips Y. The display offset
subtracts that common origin; no independent minimum-Z normalization or surface
warping is applied. This is coordinate conversion, not verified survey alignment:
the source vertical datum remains unverified. Original LAS files are untouched.

Imports are lazy and cached under `/Game/Beirut/PointClouds`. Source points and
RGB are retained, including duplicate samples. Import completes synchronously
before the offset is applied and the final asset is saved, avoiding a partial
background import or offset reset. Subsequent toggles change only visibility;
they do not reimport or rewrite the point-cloud asset. Online-only OneDrive files
are refused rather than silently downloading multi-GB inputs.

The current private project lists StreetScape, NestledVillage and HeritageLandscape.
StreetScape retains all 4,800,668 source points. NestledVillage and HeritageLandscape
now have explicitly labelled viewing subsets (1,851,087 and 1,966,153 points).
All three imported native counts were checked. MCP on/off/on checks and the
actual menu toggle passed; the 629 source buildings remain intact. Captures are
outside the Sea Highway crop, so use their navigation controls. Bounded
photographic inspection exposure now reduces washout without changing the whole
city's exposure. Capture/model alignment and source dates still require review.

## Verification

On 25 October 2026, Unreal 5.8.4 imported and saved all 629 source buildings.
The saved map was reopened successfully. Imported calibration nodes measured
100 cm per source metre: east = +X, north = -Y, up = +Z. The built-in MCP
toolset started recorded SUMO replay, and consecutive status calls showed
vehicle positions changing. DX12 Shader Model 6 is enabled for Nanite assets.
The eight focused geometry, asset and replay tests pass.

The scene now uses movable sun/sky lighting and histogram exposure with a
controlled preview intensity. A fresh MCP viewport capture shows terrain,
roads, shadows and placeholder traffic. No final visual-quality or FPS
comparison is claimed. If a later editor restart presents Restore Packages,
dismiss that startup dialog before attempting MCP operations. The Python replay
must be started explicitly after reopening the editor. For a fresh rendered
capture, use the built-in EditorAppToolset CaptureViewport operation; a scheduled
high-resolution screenshot can remain pending while the editor is not drawing.

Focused tests cover courtyard triangulation, winding, source heights and IDs,
missing ground handling, GLB buffer layout and axes, calibration nodes, road-grade
selection at overlapping bridges, signal/light state preservation and session
cleanup after a failed API request. The browser GLTFLoader must load the actual
generated asset without errors; picking must expose source IDs; replay pause and
camera reset must operate visibly. Unreal MCP calls require a running editor
whose startup dialogs have been dismissed.

References: [Interchange import](https://dev.epicgames.com/documentation/unreal-engine/importing-assets-using-interchange-in-unreal-engine),
[Interchange Python manager](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/InterchangeManager?application_version=5.5),
[SUMO TraCI](https://sumo.dlr.de/docs/TraCI/).

## Illustrative facade materials

`Content/Python/style_buildings.py` applies reversible material overrides only
to actors labelled `beirut001_`. The six concrete/stone paint variants use a
stable hash of the source identity. A world-space shader adds opaque window
patterns on walls, subtle tonal variation and sill shading. Roofs do not
receive window patterns. Building geometry, IDs and source heights are retained.
Window spacing (3.0 m horizontally, 3.2 m vertically), paint and grime are
illustrative assumptions, not measured floor counts or facade records.

Run `py import style_buildings; style_buildings.apply()` in the Unreal console
after copying the script to the generated project. Use
`py style_buildings.restore()` to return to imported materials, then explicitly
save the map. The pass was applied to 629 source buildings, compiled and visually
checked via MCP on 25 October 2026. It establishes visual detail, not surveyed
street-level resemblance. Exact balconies, shopfronts, Arabic signage and
landmarks still require local evidence and source attribution.
