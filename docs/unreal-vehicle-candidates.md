# Unreal traffic vehicle candidates

Candidate review: 27 October 2026. Candidate listings are not installed or
approved visual matches for Beirut; verify source files and licence metadata
again at download time. Fleet frequencies need local observations.

## Generic passenger car pack

- [Generic passenger car pack](https://sketchfab.com/3d-models/generic-passenger-car-pack-20f9af9b8a404d5cb022ac6fe87f21f5),
  by Comrade1280, is listed as downloadable under CC BY 4.0. Attribution is
  required. Sketchfab's public model API reports 69,312 triangles for the pack.
- The creator lists ten body types including sedan, hatchback, wagon, minivan,
  pickup and SUV, each below 8,000 triangles, with six colour textures. These
  are listing claims until the downloaded files are independently inspected.
- This is a candidate for background traffic, not an assertion that the models
  represent specific Beirut vehicles. Assess shape, textures, origin, forward
  axis, dimensions, materials, licences and performance before use. The
  renderer has optional mesh groups and stable per-vehicle assignment in the
  compiled plugin, but the mixed fleet has not been configured or visually
  verified in the city. Appropriate vehicle meshes and type mappings are still
  needed.
- The unauthenticated Sketchfab download API returned HTTP 401. No asset was
  downloaded or imported; use the normal authorized download flow when
  available. Do not bypass access controls or claim uninspected asset quality.

The existing Kenney Car Kit has CC0 sedan and SUV assets already used by the
browser viewer, but its stylized appearance needs comparison with the current
Unreal finished-asset visual direction before adopting it there.

## Quaternius low-poly cars, 2016

The creator's [OpenGameArt listing](https://opengameart.org/content/low-poly-cars-0)
offers a CC0 archive without a login. A local copy and source/hash manifest
were saved privately under `asset-quality-pass/library/quaternius-cars/`.
The archive integrity check passed. Its OBJ files include `SimpleCar`, `Truck`,
and `RaceCar`; the low-poly versions have approximately 940, 796 and 836
triangles respectively after polygon triangulation. The appearance and scale
have not been inspected in Unreal, so these are potential lightweight
background assets, not established Beirut matches or approved replacements.
The archive is the 2016 pack, distinct from the creator's later Cars Pack.
The low-poly `SimpleCar.obj` vertex bounds span approximately 4.55 × 2.24 ×
2.25 source units, but the unit scale and up axis have not been established.
The included MTL defines two plain grey materials without texture references;
the archive also contains separate texture images, so do not assume that the
OBJ/MTL alone reproduces the creator's intended appearance. A second untextured
diagnostic using a provisional Y-up interpretation shows distinctly angular,
stylized shapes. It is enough to flag a likely mismatch with the current
realistic finished-asset direction, but does not establish final material
quality, dimensions or Unreal rendering. Keep this pack uninstalled unless
the visual direction explicitly accommodates it.

The [RGS Dev low-poly vehicles pack](https://opengameart.org/content/free-low-poly-vehicles-pack)
includes buses, vans, taxis and other vehicle types and is listed as CC0. Its
creator's preview is visibly stylized, so it is not a strong match for the
current realistic visual direction. It was not downloaded or imported.

## Additional CC0 candidates (27 October 2026)

- [byzmod3d low-poly car pack](https://opengameart.org/content/low-poly-car-3d)
  lists CC0. Its downloaded archive passed ZIP integrity validation and includes
  FBX files and a Blender file. Its included previews show stylized cars, a van
  and a pickup; they are a weak match for the current realistic vehicle. No
  Unreal import or dimensions/performance validation has been performed.
- [Creomoto generic car](https://opengameart.org/content/car-0) lists CC0. The
  source preview depicts a boxy generic vehicle. Its separately downloaded OBJ
  contains 19,318 vertices and 19,280 polygon faces, references a `car.mtl`
  not supplied with that standalone OBJ, and has unverified units and axes.
  The listing says its materials are in the separate Blender file; it has not
  been inspected or imported and must not be used with assumed materials.

Downloads, listing URLs, declared licences and SHA-256 hashes are recorded in
private `engine/asset-quality-pass/library/additional-car-candidates-20261027/source.json`.
Neither candidate establishes a representative Beirut fleet.

## Everyday sedan and delivery van pilot (27 October 2026)

[Innerscene sedan](https://www.innerscene.com/tools/library/3d-parts/sedan-car-42109739)
and [delivery van](https://www.innerscene.com/tools/library/3d-parts/delivery-van-131990b3)
are source-listed CC0 original unbranded models. Their private source records
include URLs and hashes. Blender inspection found 16,300 and 16,280 triangles,
respectively; a workbench view shows simple angular forms, so the appearance is
illustrative and still needs city comparison. Their named front tires, headlights
and rear lights establish source -Y as the forward direction. The converter now
supports an explicit background-vehicle forward axis and recentres the base;
it does not invent an interior or alter the full-detail driven car.

Both converted assets passed source/hash/licence and material validation and
imported in a separate Unreal 5.8 null-renderer host. Unreal measured the sedan
at 469.60 × 232.00 × 148.00 cm and the van at 544.60 × 248.00 × 242.00 cm,
with 9 and 8 material slots. Imports warned about missing FBX smoothing groups;
shading, actual facing direction and wheel contact must be checked visually in
Unreal. The converted files are private under `engine/asset-quality-pass/prepared`,
and native results are in `traffic-lod-native-host-20261027/everyday-vehicles-result.json`.
These assets are not installed or configured in the city, and their existence
alone does not establish locally representative fleet proportions.

## Additional bus and taxi pilots (27 October 2026)

[Innerscene low-floor bus](https://www.innerscene.com/tools/library/3d-parts/low-floor-city-bus-9e7ce899)
and [generic taxi](https://www.innerscene.com/tools/library/3d-parts/generic-city-taxi-with-taxi-roof-sign-394afe2b)
are listed as CC0. Source URLs and hashes are recorded alongside the downloads.
The bus has 18,780 source triangles; native Unreal import measured approximately
1,198.5 × 320 × 311 cm, including its protrusions. The taxi has 126,666 source
triangles; its successful import measured approximately 459.25 × 210.98 ×
176.80 cm and resolved 13 material slots. These are geometry checks in an
isolated null-renderer host, not city visual or performance results. The bus is
a full-size generic city bus, not evidence of Beirut vehicle sizes or service.

The first taxi import was deliberately rejected because source materials share
names but differ in UV coordinate selection. The converter now assigns unique
index-based material names in a temporary GLB while preserving primitive material
indices, textures, UV channels and source bytes. The converted taxi passed the
existing provenance/material checks and native import. The changes also support
GLTF duplicate names without guessing. Shader appearance and exported UV channel
use still need visual checking in Unreal. Full details and native results are
private under `engine/asset-quality-pass`; none of these vehicles is installed
or mapped in the city. The heavier taxi needs a performance evaluation before
use at scale.

An isolated Unreal 5.8 automation test then loaded the imported sedan, van,
taxi and bus meshes into the native traffic renderer. It supplied one typed
vehicle per group and verified each group received one instance and that the
front-point offset used each mesh's actual length. `DynaCity.Traffic.ImportedVehicleTypes`
passed in the null-renderer host; its private report is under
`engine/asset-quality-pass/traffic-lod-native-host-20261027/fleet-test-report`.
This checks native grouping and placement arithmetic with those assets, not
visible wheel contact, in-city driving, a calibrated fleet mix or performance.

## City installation and runtime check (29 October 2026)

The earlier preparation-only status above is superseded for the Innerscene
sedan, delivery van, unique-material taxi and full-size bus. All four were
imported into the working city project and configured as SUMO traffic groups.
The player retains its existing Ferrari; background passenger cars now use the
16,300-triangle sedan rather than the 358,788-triangle player model. Material
assets explicitly enable instanced-static-mesh usage to avoid grey fallback
shading. A rendered preview shows the blue sedan, white van and bus, and yellow
taxi. A city screenshot confirms coloured passenger cars in the real scene.

The backend runs the citywide road model with the existing historical 2015
mobility scenario. Passenger, taxi, delivery/light-goods and bus type IDs map
to the corresponding visual groups. Minibuses and trucks have no approved
matching visual assets and are omitted from rendering; they remain in SUMO.
These four models do not constitute a complete or currently calibrated Beirut
fleet. At one live sample the component counts were 54 sedans, two vans and
three taxis; the bus group was loaded with zero instances in that sample.

`traffic_fleet.install(folder)` reproduces the installation using the existing
validated prepared assets. Reinstalling the street library now preserves the
traffic configuration. Private evidence is under
`engine/traffic-fleet-fix-20261029/`, including import metadata,
`fleet-preview.png`, `city-fleet.png`, and `live-fleet-groups.json`.

## Real-model replacement and paint (29 October 2026)

The next reviewed candidates are Martin Trafas's BMW E36 M3 and PHAC's
Toyota Corolla KE80, redistributed under CC BY 4.0 with primary Sketchfab
licence metadata checked. Credits and modifications are recorded in
`unreal-traffic-vehicle-credits.md`. Prepared Blender geometry has 38,913
and 55,563 triangles respectively. The BMW's separate display plinth is
excluded; both use explicit front-axis and ground alignment. The Mercedes
C180 candidate failed its rendered preview and is excluded from installation.

`traffic_fleet.install_real(folder)` imports the two reviewed cars, generates
three distance detail levels and maps passenger-car IDs to stable model
choices. Equal BMW/Toyota model sampling is illustrative, not a measured
Beirut make/model distribution. Generic commercial vehicles remain as recorded
above. The player Ferrari is preserved.

`traffic-paint-palette.json` records Axalta's global 2025 new-production colour
percentages: white 29, black 23, gray 22, silver 7, blue 6, red 3, green 3,
yellow/gold 3, brown/beige 2, others 2. This is a provisional proxy for visual
variety, not a count of Lebanon's in-use fleet. RGB swatches are illustrative;
the "others" category uses one dark purple example. Finite positive weights
and bounded linear RGB values are validated. Stable independent ID hashes
choose model and paint, preserving trajectories and colours across frames.

The native renderer carries four custom floats per vehicle instance (RGB plus
an override mask). Only explicitly marked body materials use this data;
glass, tyres, lights and interiors retain their authored materials. Colour
variation does not add a separate mesh or material per car. A 20,000-ID check
placed every category within one percentage point of its target.

Both models are imported and saved with three LODs. A native Unreal screenshot
shows both models in six distinct body colours with separate trim and glass;
the transient preview actors and lights were then removed. Material API and
vertex-interpolator input naming mismatches found during that check are fixed.
The native FleetLifecycle automation test passes, including per-instance RGB,
override masks, stable updates and rejection of invalid paint data. Fifty-nine
focused Python checks pass. In-city SUMO verification remains pending: the local
backend logged a SUMO `bad allocation` failure on one startup, and the latest
request waits in TraCI `getVersion` for the SUMO startup handshake. No moving
city traffic has been verified with these assets. Evidence is recorded in the
private `real-vehicle-fleet-20261029` import and live-verification reports.
