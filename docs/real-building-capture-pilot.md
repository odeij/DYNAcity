# Real building capture pilot: Ac_726

The visible placeholder at source ID
`beirut001:163e027c-d254-482c-9c91-58de29e1f1bd` is replaced by photographed
building geometry from the user's resident `Ac_726.obj`, MTL and JPG. This is
near Saint Nicholas Garden on the western Charles Malek corridor, roughly
600 m west of the original short street-detail pilot. The remaining city has
not been converted into photographic buildings.

## Identity and placement evidence

The local mesh's arches, shutters, collapsed right-hand facade and roof match
the Ac_726 photographs in the February 2023
[UN-Habitat rehabilitation document](https://unhabitat.org/sites/default/files/2023/03/230313_esmp_batch1_unhrevised_clean.pdf),
pages 15 and 18. The document describes Ac_726 on a secondary street parallel
to Charles Malek. This identifies the building; it does not date the local scan.

The public [BBED parcel layer](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/BaseMap_BBED2024/FeatureServer/9)
returns `ParcelID=Ac_726`, with an EPSG:32636 parcel footprint. Neighboring
footprints from [Municipal Beirut Buildings](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/BASEMAP/FeatureServer/5)
identify the main footprint (13809) and the adjoining captured facade (13808).
These source responses are retained in the private reference folder.

Four roof-eave controls were identified in an unchanged orthographic render
of the capture and paired with the mapped main footprint's corners. A rigid
XY fit, without scaling or reflection, gives RMS control residual 0.626 m.
This is estimated placement: roof eaves and ground footprints are different
features, and these are not surveyed control points. Vertical translation uses
a low street-contact patch against the existing terrain, with p90 residual
deviation 0.147 m. The original capture datum remains unknown.

The colored LAS area previously tested near the original pilot does not cover
this parcel. Unconstrained geometric fits were weak and were not used to place
this building. The other three local OBJ scans have not been installed here.
In particular, RM_1_312 has a visibly different local orientation, so a Z-up
matching assumption cannot be accepted for that source.

## Actual geometry and appearance

The preparation retains 316,812 original source triangles, with their UVs and
photographic texture. Faces outside the selected mapped footprint buffer and
below the terrain are omitted; no faces are generated, holes filled or damaged
parts reconstructed. The photographed damage is intentionally still visible.

The native asset uses full source LOD0 and precise UVs. Nanite's automatic
simplification gave unacceptable faceting in this capture, so Nanite is disabled
for this asset. The source JPEG remains unchanged; Unreal uses a resident 4K
atlas for the single pilot building. Streaming the atlas at extremely low mips
made the photograph look like colored blobs, which was corrected and checked
in the actual viewport. This fixed pilot budget must be reviewed before scaling
to many photographic buildings.

The inspection material displays original RGB with its baked photographic
lighting. This is a captured-condition daytime preview, not a calibrated PBR
material or validated night rendering. Scan date and present-day restoration
appearance are unverified. A later World Bank rehabilitation progress document
also discusses restoration milestones; the old damaged capture must not be
presented as a verified 2026 condition.

## Reversible source replacement

The original batch asset stays untouched and remains hidden for its existing
collision. A new visual copy omits only the 20 indexed placeholder triangles;
all 17,188 remaining triangles retain their original position, normal and
material buffers. The photographed capture supplies the visible replacement.
This preserves the existing driving collision reference while changing the
building's rendered shape. Collision does not yet follow every scanned opening.

The photographic actor and patched visual batch explicitly disable both actor
collision and component mesh-default collision, and use the `NoCollision`
profile. Setting only the component's current collision mode proved
insufficient: a PIE wheel probe hit the patched district's broad simple
collision box at the trace origin, 4.20 m above the expected source surface.
The native driving elevation guard correctly refused that support. The
installation and reload checks now enforce the full visual-only collision
policy while leaving the hidden original source batch as collision authority.

Two generated actors use the `DYNACITY_CAPTURED_BUILDING` and `capture:Ac_726`
tags, in `DynaCity/Real building replacements/Ac_726`. The original visibility
and mesh path are backed up in `Saved/DynaCity/captured-building-backup-Ac_726.json`.
Regeneration reuses the same two actor paths. Save/reload verification also
reloads the original hidden batch and checks its mesh, material binding and
native capture triangle count.

## Repeat the workflow

Private controls and references are under
`C:/private/dynacity-data/engine/real-buildings/`. Preparation:

```powershell
.venv/Scripts/python.exe scripts/prepare_capture_building.py --control C:/private/dynacity-data/engine/real-buildings/Ac_726-control.json --checkpoint C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/Saved/DynaCity/city-import-BeirutCityBatched.json --ground C:/private/dynacity-data/engine/terrain-gap-repair-v3/repaired-visual-ground.npz --polygons C:/private/dynacity-data/engine/real-buildings/reference/Ac_726-neighbor-buildings.geojson
```

Export the prepared OBJ with `scripts/export_capture_building_blender.py` in
the existing Blender runtime, passing `--manifest` with the prepared
`capture-manifest.json`. Native `CapturedBuildingTools.prepare`,
`install_capture`, and `verify_capture` accept that manifest path. Run native
updates outside Play, sequentially, and inspect the actual viewport afterward.
`preview_capture(source_id="Ac_726")` focuses the source in the editor or safely
parks near it and opens the existing free-flight view in Play. F returns to
the parked car. Private source assets and textures are not committed or uploaded.

## How I did it

Rendered the original photographic models to identify usable source content,
matched Ac_726 to primary reference photographs and parcel data, fitted rigid
placement controls, retained the observed mesh/UVs, and omitted exactly one
placeholder from a derived visual batch. Tests cover rigid fitting, held-out
registration checks and exact preservation of unrelated source geometry. Native
checks cover scale/axes, source triangle count, saved material and actor reload.
