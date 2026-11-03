# Repeatable street detail

The first batch proposes sidewalks and curbs along five existing Charles Malek
segments (10086, 8415, 8416, 10167, 10168) and narrow Al-Hikma School Street
(3186), in Achrafieh. The carriageways remain the original modeled 6.4 m and
3.2 m respectively; these widths are lane-derived, not surveyed measurements.

Original simulation roads, terrain and buildings are read-only inputs.
Overture buildings are excluded from enrichment, placement and building checks.
Existing unrelated scenery is untouched. No new façade assets are generated.

## Run the pilot

The private configuration is
`C:/private/dynacity-data/engine/street-detail/pilot-config.json`.
Prepared source assets and derived geometry remain outside Git.

From the repository in PowerShell, produce inventory and proposals first:

```powershell
.venv/Scripts/python.exe scripts/prepare_street_detail.py --config C:/private/dynacity-data/engine/street-detail/pilot-config.json
```

Inspect `pilot/exceptions.md`, each street JSON's `inventory`, `layouts`,
`junctions`, `checks`, and `exceptions`. Apply and verify the generated layer:

```powershell
.venv/Scripts/python.exe scripts/run_street_detail.py --config C:/private/dynacity-data/engine/street-detail/pilot-config.json --install
```

The existing editor must have `BeirutCityBatched` open, the latest
`engine/unreal/Content/Python/street_detail.py` and `street_tools.py` copied into
its `Content/Python`, and the local MCP endpoint running on port 8016. New tools
register at editor startup. The installer refuses Play mode and active imports.
It uses the existing compiled `DynaStreetInstances` HISM class and calibrated
city coordinate basis. The detail is decorative; collision is disabled.

## Inputs and confidence

The configuration names the active city checkpoint, cached OSM roads/context,
existing visual ground NPZ, optional cached Overture **segments**, output,
overrides, assets and a list of streets with stable IDs, source road IDs,
district and street type. Add district batches by listing their roads in a new
configuration and output directory. Use one native batch owner; do not run
concurrent imports or two configurations claiming the same generated street.

`commercial_avenue`, `residential_street`, and `major_road` profiles specify
sidewalk allowance, curb width and height. Configuration `rules` can replace
these profiles. The inventory retains missing widths, curb heights, entrance
coverage and right-of-way measurements. Unknowns remain low confidence.
Scalar OSM measurements win; a unique Overture source/spatial match may fill
an absent attribute. Ambiguous matches remain unresolved. Transportation
geometry is never imported. Current cached Overture matches do not supply a
usable scalar width for these streets.

Building clearance uses projected original building triangles from active
`batch-buildings-*` packages, with 10 cm clearance. It excludes context-building
packages. Road clearance preserves neighboring ground carriageways even when
they fall outside the requested batch. Terrain sampling uses the existing
triangle-linear `TerrainSurface`, rejecting holes and excessive module warping.
Mapped entrances omit curbs in their clearance zone, when entrance nodes exist
in the cached extract. Absence of mapped entrances is not proof of no entrances.

## Finished assets and continuity

The pilot reuses **Kenney City Kit Roads 2.2**, CC0, from the already cached
archive. `tile-low.glb` and `tile-high.glb` are converted with the existing
`prepare_library_asset_blender.py`, preserving source triangles, UVs and texture.
`library_asset_core.read_prepared` checks source/export hashes and license;
`library_assets.import_prepared` imports and verifies centimetre bounds and PBR
materials. Each proposal references a finished shared tile, scaled as a
sidewalk or curb module. Appearance and scaling are illustrative.

The `surface_finishes` configuration points to the existing licensed ambientCG
cache. `street_detail_finish.py` replaces the kit's plain white material on
generated groups with Concrete030 colour/roughness, staggered paving joints
and a separate curb finish. Materials use world coordinates so scaling a module
does not stretch its surface detail. Joints and colour remain illustrative.
Overrides are saved only on generated actors and reapplied during regeneration.
End Play before saving this finish; verify it again in the restarted driving view.

Modules follow the existing road path and terrain slope. Curbs and walking
surface occupy separate footprints. The walking surface keeps at least 0.9 m
of modeled clear space. A constrained profile tries a 1.2 m total allowance;
sections that cannot fit are omitted and reported. Parking, trees, lamps,
bins and signs remain deferred until layout review passes; existing furniture
is retained. Parking or vegetation never consumes modeled traffic space.

At installation, every module is traced against the actual existing source
surface. A module on bare terrain extends from that surface up to the proposed
curb elevation. Where raised pavement already exists, it receives a thin 2 cm
finished cap instead of a second slab. Support adjustments are recorded in
`Saved/DynaCity/street-detail-placement-index.json`; source meshes are unchanged.

Source-split junctions attempt short connectors using the same finished
modules, subject to road, building, module and terrain checks. Connected split
segments are distinguished from branch junctions requiring crossing/corner
review. No bridge across a traffic lane or inferred zebra marking is invented.
Disconnected sidewalk components and unresolved junctions block expansion.

## Regeneration and overrides

Instance IDs contain street, source segment, side, metre station and kind.
Generated actors carry only `DYNACITY_STREET_DETAIL_V1` and live in
`DynaCity/Generated street detail/<district>/<street>` in the Outliner. This is
an isolated tagged actor/folder layer, not an Unreal Data Layer asset.

The external `overrides.json` is never overwritten by the generator:

```json
{
  "streets": {
    "8416/left": {"width_m": 1.2, "reason": "Building clearance; needs measurement"}
  },
  "features": {
    "charles-malek/8416/left/00012": {"disabled": true, "reason": "Entrance review"}
  }
}
```

Width overrides affect the specified segment side. Station suppression removes
its sidewalk/curb pair. Record edits here rather than moving generated actors
by hand. Station IDs assume the existing source segment orientation and start;
source re-segmentation requires manual override review. Unused override keys
must be reviewed against each street's `used_overrides` list.

Per-street cache signatures include code, profile, selected/neighboring roads,
local terrain window, projected buildings, local mapped features, Overture
matches and relevant overrides. A distant override does not invalidate another
street. The native installer compares persistent signatures and actor counts;
it builds replacements before retiring only its own prior groups. Removed
streets are cleaned up in this namespace. Asset hashes participate in native
signatures. Reruns load World Partition descriptors before deciding what exists.

## Verification and limits

`run-report.json` records cache changes, native changes and reload results.
The reload check compares the full stable-ID multiset, including duplicate
detection, saved transforms, and a native support trace for every module. Native
reports also live in the project's `Saved/DynaCity` folder.
The test suite covers determinism, unchanged source input, override persistence,
selective cache invalidation, holes, building/traffic clearance, curb/walk
separation, OSM precedence, Overture ambiguity, and invalid inputs.

Plan checks cover projected overlaps, terrain-relative floating modules and
sidewalk components. These are not a native full-mesh collision test or an
accessibility certification. Photographic comparison requires suitable dated
street photographs; existing Unreal screenshots are not photographs. Source
hardware positions and façade textures are unverified.

Keep district expansion blocked until the exception report is reviewed,
corner/crossing continuity is resolved, and comparable fixed-camera before/after
frame performance is recorded. Editor generation time and instance counts are
recorded now; they are not GPU frame-rate measurements.

`StreetTools.profile_detail(samples=90)` starts a nonblocking hidden/visible
comparison, with 30 warmup frames per phase. `detail_profile_status` reports its
progress, and `Saved/DynaCity/street-detail-frame-profile.json` retains medians
and p95 editor Slate tick intervals. Original temporary visibility is restored.
This measures editor cadence only; a representative foreground GPU/game frame
capture is still required before scaling up.

## How I did it

Reused `TerrainSurface`, active city/road metadata, original building GLB triangle
indexes and the licensed library importer. `dynacity.street_detail` owns layout
rules; the preparation script owns source adapters and caches; Unreal's new
detail module owns isolated HISM updates. Verified the two-street pilot through
automated tests, a clean repeated generation/import, a segment-specific width
override, and World Partition unload/reload ID checks.
