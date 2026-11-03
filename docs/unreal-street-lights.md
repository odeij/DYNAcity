# Beirut street lights

## Current design — original yellow lattice poles

On 31 October 2026, the user confirmed a daylight reference showing yellow
steel lattice uprights, diagonal braces, a concrete foot and a separate road
light. They explicitly chose an original model over a paid library asset.
This replaces the rejected ornate Poly Haven lamp; the earlier yellow glow
change was reversed, and painting the ornate lamp was superseded.

`scripts/build_lebanese_lattice_lamp.py` creates the original Blender/FBX/GLB
asset outside Git. The model has four angle-steel uprights, open diagonal and
horizontal bracing, a capped mast, bolted base plate, concrete foot, bent tubular
arm and separate slim graphite fixture with two recessed, unlit optic shapes.
It is 8.05 m tall, has an overall bounding size of 1.95 × 0.60 × 8.05 m
(including the arm), and 11,916 triangles. Dimensions, taper, mounting reach
and fixture details are illustrative; the photo is not a dimensional survey.
No electrical wires, insulators or present operating condition are inferred.

[Reference photo, Rachaf, published 12 August 2025](https://amalbaladi.org.lb/details/2708/).
Capture date unknown; no reuse license established. Source imagery stays
private with [catalog/provenance](C:/private/dynacity-data/engine/street-lamps-20261031/photo-catalog.md).
The photograph is not used as a texture. This reference comes from outside
Beirut and does not establish exact equipment at every modeled location.

## Installation and placement

`engine/unreal/Content/Python/lattice_street_lamps.py` imports the hash-checked
FBX, checks Unreal centimetre bounds, creates five explicit material slots,
and replaces the two existing lamp groups. The private active library manifest
now points at the original model. Source IDs are preserved: nine illustrative
reference lamps on Charles Malek Avenue and one mapped OSM node 8358370837.
No citywide placement layer was added. The cache is not a complete inventory.

One illustrative pole (`illustrative/lamp/-48`) was moved 2 m along the same
street because the taller mast intersected a building. Its light moved with it.
All other bases, including the mapped node, were preserved. The reference
builder retains this clearance adjustment along with the earlier adjustments.
The fixture arms face the nearby carriageway. The reference and mapped-light
builders read the active model's local light anchor `[130, 0, 735]` cm rather
than assuming the old lamp's 3.45 m mounting height.

Actor packages and active-model metadata are saved. Before-state records remain
under the private project's `Saved/DynaCity/lattice-lamp-before-20261031.json`.
The old source mesh remains available; no original library model was deleted.
The older `street_light_appearance.apply()` paint helper refuses the new model.
A full library reimport can reset active records; reinstall the approved original
model after such a deliberate reimport rather than silently regenerating props.

## Light behavior and verification

The previous light color is retained: linear RGB `(1, .72, .38)`, stored as
sRGB `(255, 221, 166)`. Existing native tags switch intensity between zero by
day and 1600 by night. Attenuation stays 14 m with shadows disabled. The mesh
optics have no independent emissive glow; only tagged point lights illuminate.

Checks completed:

- Blender export and Unreal centimetre-bound comparison passed.
- Ten mesh instances and all ten source IDs survived a requested partition
  unload/load; the ten saved lights retained their color and daytime zero.
  This is not a cold editor-process restart.
- A Play session displayed the lattice model and confirmed zero/1600 switching
  on the nine nearby streamed lights. The separate mapped lamp was not loaded
  in that Play view. This check preceded the final 2 m clearance adjustment.
- Five vertical collision probes per mast found no loaded obstruction after
  the adjustment. This does not establish a complete footprint or arm collision
  audit, or visibility of every nearby building's collision.
- Python syntax checks passed. No whole-city GPU benchmark was taken.

[Daylight Play capture](C:/private/dynacity-data/engine/street-lamps-20261031/lattice-pole-play.png),
[viewport capture](C:/private/dynacity-data/engine/street-lamps-20261031/lattice-pole-viewport.png)
and [verification record](C:/private/dynacity-data/engine/street-lamps-20261031/report.json).
