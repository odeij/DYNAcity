# Cesium Beirut evaluation

This is an isolated experiment, not an integration into the existing city.
The private project is `C:/private/dynacity-data/engine/cesium-pilot/project/`
and the production city map is unchanged by the experiment.

## What was tested

- Cesium for Unreal 2.30.0, using the official UE 5.8 Windows plugin files;
  the plugin's BuildId matched the installed engine and it loaded successfully.
- Re:Earth Terrain quantized-mesh elevation, streamed at runtime in the pilot.
  Its cache recorded successful terrain requests. Credits: Re:Earth Terrain;
  Mapterhorn (CC BY 4.0); EGM2008 (NGA); and, where used, Protomaps and
  OpenStreetMap contributors for the water mask.
- EOxCloudless Sentinel-2 2024 imagery. This is a low-resolution satellite
  mosaic draped over terrain, not photogrammetric 3D buildings. The 2024
  mosaic is CC BY-NC-SA 4.0; the trial is for noncommercial evaluation only,
  and commercial use requires appropriate permission. Attribution: Sentinel-2
  cloudless 2024 - https://s2maps.eu by EOX IT Services GmbH (Contains modified
  Copernicus Sentinel data 2024). The URL-template overlay does not establish
  that these credits appear in the running application: add and verify visible
  attribution before allowing the pilot to be distributed or generally used.
- Copies of the existing Achrafieh building and road GLBs, imported as seven
  tagged groups into the separate map. They were saved and counted after a game
  reload. They use source-batch materials, not the complete production-city
  visual treatment. The pilot copies have collision disabled.

Sources: [Cesium for Unreal releases](https://github.com/CesiumGS/cesium-unreal/releases),
[Re:Earth Terrain](https://terrain.reearth.land/),
[EOX usage and services](https://cloudless.eox.at/documentation/usage), and
[EOX 2024 licence and attribution](https://new.eox.at/2025/03/sentinel-2-cloudless-2024/).

## Alignment and comparison

The city source uses EPSG:32636 with origin easting 730784.7583 m, northing
3751551.1590 m and source height 51.1058 m. The pilot places the Cesium origin
at 35.495250 E, 33.879080 N and ellipsoid height zero. The local UTM-to-ENU
approximation uses yaw +1.391572 degrees and horizontal scale 0.99974058.
Across 12 selected neighborhood coordinates, the approximation differs by at
most 0.021 m from the mathematical coordinate conversion. This checks the
transform mathematics only; it does not prove the source meshes or imagery
are registered correctly.

The Re:Earth EGM2008 geoid model reports roughly 22.6-22.8 m separation in the
sampled area. An experimental height shift was applied to the seven pilot
groups, on the hypothesis that their source heights are orthometric. More of
the buildings became visible above the terrain. **The city source's vertical
datum has not been established**, and this does not prove the height shift or
resolve road-terrain overlap. Do not copy the shift into the production map
without independent control points and a source-datum check.

At 12 approximate pixel samples near the pilot, the mean absolute difference
between the service's sea-level elevation raster and existing point elevations
was 2.23 m. This is not an accuracy estimate: the samples are not independent
survey measurements, the existing heights and datum are uncertain, raster
resolution is coarse, and terrain interpolation differs.

Game-mode screenshots and diagnostic reports are stored in the private pilot
directory, including `beirut-terrain-eox.png`, `beirut-hybrid-geoid.png`,
`hybrid-geoid-capture.json`, `terrain-comparison.json`, and
`hybrid-horizontal-transform-check.json`. The original-city screenshot is a
separate view and is not a pixel-registered before/after comparison.

## Current decision

Keep the existing city as the primary interactive scene. This tested Cesium
combination can supply broad geographic context, but the imagery is too coarse
for street-level or building-façade realism, and the pilot still has unresolved
height and road/terrain overlaps. A separate Google Photorealistic 3D Tiles
pilot now authenticates and streams in game mode: 69 Google responses in the
private cache had HTTP 200. Its first oblique Beirut view appeared very blurry;
this single viewpoint does not establish street-level coverage or rule out a
better viewpoint. The capture did not show the required attribution, so it is
internal evidence only and must not be shared. `Show Credits On Screen` is
configured, but actual on-screen attribution and the Google logo still need
verification; Unreal HighResShot can omit UI. Google billing and API quotas
apply. The Google map is separate from the existing city, its key is supplied
only at runtime, and the local cache is access-restricted. Google response
cache rules and attribution requirements must be followed before further use.
No streamed imagery should be mistaken for measured building detail.

## Work continued on the existing city

The legacy bollard groups contained eight objects at the same source IDs as
eight already-visible finished bollards. The duplicate placeholders were hidden
after validating the one-to-one ID match; the finished bollards were checked
visible after reloading their external actors. This does not remove the mapped
bollards. Private evidence: `Saved/DynaCity/bollard-duplicate-cleanup-20261028.json`.

The 42-road paint pilot was audited against the active saved road batch source
geometry using `scripts/audit_road_paint_heights.py`. Of 3,261 sampled paint
triangles, 3,232 were inside their corresponding source-road triangles; none of
those were below the road, and their median clearance was about 2 cm. The other
29 samples were checked separately: all lay over a different active road at
approximately the same clearance, consistent with junction ownership. These
are sampled source-geometry checks, not complete rendered-city verification.
Evidence is in the private `road-visual-pilot-20261027` directory as
`paint-height-audit-20261028.json` and `unmatched-paint-neighbor-check.json`.
