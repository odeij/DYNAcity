# Shared street model for SUMO and Unreal

`scripts/prepare_street_twin.py` compiles the existing private traffic model into
an evidence-labelled lane package and tiled GLB assets. This is a foundation for
an accurate digital twin. Actual accuracy still requires dated geometry and
traffic observations, calibration, independent validation and runtime checks.

## Run against existing inputs

Use the installed project Python environment and an output directory outside
the repository. No downloads are required. `--elevation-metadata` accepts the
road metadata referenced by the existing `shared-world.json`, with projected
metre coordinates, `source_way`, and local XYZ paths.

```powershell
.\.venv\Scripts\python.exe scripts\prepare_street_twin.py `
  --traffic C:\private\dynacity-data\traffic\beirut-traffic-20261024.json `
  --elevation-metadata <absolute-path-to-shared-road-metadata.json> `
  --bounds-utm 732900 3753250 733900 3753900 `
  --output C:\private\dynacity-data\engine\street-twin-20261029\marmikhael
```

Omit `--bounds-utm` to compile the complete road model. Bounds select whole roads
for the Unreal package; the converted SUMO network remains citywide so corridor
edges do not become artificial simulation boundaries. Compilation does not run
demand, calibrate a simulation, or measure frame rate.

## Data and geometry contract

- `street_twin.py`: canonical road and lane identities, exact runtime SUMO ID
  bindings, field-level evidence and deterministic owner tiles. IDs use OSM way
  plus source endpoints, surviving array reorder and subset operations. Source
  resplitting or changed endpoints can change identity.
- `street_twin_pipeline.py`: binds elevations by OSM way and full XY geometry,
  accepting only unique matches within 0.25 metres. This is a correspondence
  tolerance, not a claim of surveyed accuracy. Original traffic XY is retained.
  Source grade breaks and samples no farther than five metres apart survive
  interpolation. Per-road height source, level and vertical datum are retained.
- `street_twin_unreal.py`: reads actual netconvert lane paths, removes SUMO's
  coordinate offset, and generates graded ribbons using the same horizontal
  lane geometry. It preserves lane IDs and geometry ranges in GLB node extras
  and the manifest. Missing elevation, missing lanes or incompatible widths are
  recorded as omissions. There is no flat-ground fallback.
- `street_twin_validation.py`: independently checks network identities,
  reference geometry, exported lane metadata and held-out observation metrics.

Coordinates use EPSG:32636 and a common local origin. Canonical coordinates are
X east, Y north, Z up in metres. GLB uses X east, Y up, Z south in metres; the
Unreal importer performs its engine axis/unit conversion. Validate the imported
basis and bounds before placing assets beside the existing city.

## Outputs and acceptance

`build-report.json` records build completion separately from accuracy. A completed
command may produce partial geometry and failed/blocked validation results.
Rebuilds first mark this report `building`; exceptions mark it `failed`, preventing
an old successful report from being mistaken for the current build. JSON reports
are published by atomic replacement.

The output contains the canonical package, actual SUMO XML, network consistency,
lane alignment, observation validation, and `unreal/street-twin-unreal.json` with
content-addressed GLBs. Repeated builds verify existing asset checksums and reuse
unchanged files; changed tiles generate new assets. Old assets are retained.
Whole roads have one owner tile and may extend beyond it. These are build tiles;
World Partition runtime cells and HLODs still require installation and profiling.

Keep these checks separate:

| Check | Meaning |
| --- | --- |
| SUMO identity | Expected road/node/lane bindings and widths agree |
| Source reference geometry | netconvert reference lines agree with the source within 0.1 m |
| Lane alignment | Exported lane metadata follows actual SUMO lane shapes within 0.02 m |
| Observed accuracy | Supplied held-out measurements pass declared error tolerances |
| Live Unreal verification | Imported assets, placement, collision and runtime performance have been checked in Unreal |

Netconvert can shift reference endpoints to avoid junction overlaps. These shifts
remain failed source-geometry checks even when visual lanes agree with the actual
simulation. Missing converted edges are reported and omitted. Identity/width
disagreements fail the build; missing-edge omissions remain visible in a partial
package. Unknown elevations block complete lane coverage. Offline metadata
agreement does not prove rendered mesh placement or physical driving behavior.

## Observation package

`--observation-package <json>` accepts `observations`, `simulations` and
`tolerances`. Every metric row requires `location_id`, `metric`, `unit`,
`interval_start`, `interval_end` and `value`. Timestamps require explicit time-zone
offsets. Observation rows additionally require `source_id` and `split`, either
`calibration` or `validation`. Use distinct metric/location identifiers for
vehicle classes and turning movements. Resolve source IDs to dated survey records
in the evidence inventory before accepting results.

Tolerances map metric names to `{unit, absolute, relative}`; allowed error is
`absolute + relative * abs(observed)`. Define these before tuning. Calibration and
validation intervals at the same location and metric may not overlap. Missing
held-out observations, matching simulation rows or tolerances remain blocked.
Passing results apply only to the supplied locations, periods and metrics.

## Verified on 29 October 2026

The current private citywide road input compiled 10,818 roads into 594 owner
tiles. One full pipeline run took 46.60 seconds, including conversion and GLB
export; this is a build timing on the current machine, not a runtime benchmark.
It generated 329 populated tile assets containing 8,289 of 17,937 expected
directional lanes and 501,922 triangles. Elevation was uniquely bound for 5,202
of 10,818 roads. Missing elevations excluded 9,637 lanes. Another 11 lane ribbons
need an endpoint join/remeshing treatment because their short terminal segments
fold the current miter geometry. SUMO omitted two directed edges belonging to
one road and shifted 118 reference edge geometries beyond the 0.1 m tolerance;
these results remain failed source-consistency checks.

The selected Mar Mikhael area contains 217 whole roads, with 293 of 346 expected
lanes exported in 20 populated tiles. All 293 known-height lane paths pass the
independent SUMO metadata alignment check. The remaining 53 lanes have unknown
elevation; two reference edges shift during conversion. A repeat build produced
an identical manifest and left all 20 current GLB files unchanged.

One corridor tile was imported into the running Unreal project under
`/Game/DynaCity/StreetTwinPilot`. It retained all 3,020 triangles, signed bounds,
axis orientation and centimetre scale within the verifier's 0.5 cm tolerance,
and its Asphalt material reference. Only the candidate mesh and its in-folder
material were saved; package dirty checks returned false. This verifies an
isolated asset import, not a corridor installed in the world or a reload test.

Reproduce the asset check with:

```powershell
.\.venv\Scripts\python.exe scripts\verify_street_twin_import.py `
  --manifest <private-output>\unreal\street-twin-unreal.json `
  --output <private-output>\unreal-import-verification.json `
  --blender C:\private\dynacity-tools\blender-4.5.9-windows-x64\blender.exe
```

The generic MCP StaticMesh importer uses FbxFactory, so the verifier converts
GLB to FBX with the existing Blender installation while retaining its geographic
origin. Conversion hashes and triangle counts are checked before import. Queries
and saves are serialized through the local MCP server. Package paths, rather
than UObject soft paths, are used for registry-backed dirty checks. Referenced
materials inside the isolated pilot folder are saved explicitly. The verifier
checks triangle totals and signed bounds against the original GLB; no level
actors are created.

No dated held-out traffic observations were supplied to either build. Observed
traffic accuracy remains blocked. The subsequent [street and mobility pilot](efasim-street-pilot.md)
installed licensed PBR finishes, draped lane paint and modular facades in the
existing map. A clean editor restart verified the saved actors, and native
Play verified sustained moving traffic. Citywide streaming, sustained driving
frame rate and photographic Beirut appearance remain unverified.

## Remaining work

1. Acquire representative street surveys and traffic observations across road
   types, districts and periods; resolve elevation and conversion gaps.
2. Generate and validate junction internal-link surfaces, sidewalks, crossings,
   curb activity, operational signals and transit behavior from accepted evidence.
3. Expand the verified pilot in bounded scopes; check lane and vehicle placement,
   collision, materials and representative street photographs. The first saved
   actor/finish and native moving-traffic checks are complete.
4. Configure streaming/HLODs and profile sustained driving with citywide demand
   on specified hardware. Report frame time and simulation throughput separately.

Preserve existing city buildings, roads and terrain while validating candidate
assets. Procedural content should use accepted geometry and a reusable local
asset/material library. Landmark detail can be authored separately. This command
does not install candidate streets into the existing map.
