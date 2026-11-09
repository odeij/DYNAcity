# Unreal exploration controls

`engine/unreal/Content/Python/explore_city.py` supplies an editor-only
**Tools > Explore Beirut** menu. It does not require another plugin and does not
change the source geometry, traffic positions or point-cloud visibility.

Copy the module into the generated project's `Content/Python` directory and add
these lines to `init_unreal.py`, after existing tool registration:

```python
import explore_city
explore_city.register_menu()
```

To activate without restarting, run the same lines in the Unreal Python console.
Menu registration is repeatable. Before reloading the module, its existing
follow callback is removed so multiple camera controllers do not accumulate.

## Controls

| Action | Behavior |
| --- | --- |
| Play / resume traffic replay | Starts the recorded replay or resumes its paused simulation clock. |
| Pause traffic replay | Holds the current actors in place and stops following; resuming excludes the paused wall-clock time. |
| Restart traffic replay | Clears the old actor pool/callback and starts the recording again. |
| City overview | Stops following and frames the source neighborhood using its calibrated axes. Does not save the map. |
| Focus selected object | Frames exactly one selected actor. |
| Street view near selection | Moves to 1.7 m above an exported road segment near a selected source road/building. With no selection it uses a road near the source origin. For a selected replay car it views ahead from outside the opaque illustrative cabin. |
| Follow a traffic vehicle | Selects a replay vehicle near the source origin and starts a chase camera. |
| Follow selected vehicle | Follows the selected vehicle's simulation identity, rather than its reusable actor, so a pooled actor cannot silently switch the camera to another vehicle. |
| Stop following | Removes only this module's camera callback. Traffic continues. |
| Inspect selected source object | Shows a native readable dialog and saves private text/JSON reports under `Saved/DynaCity/last-inspection.*`. |

Following automatically stops when the selected vehicle leaves the active replay.
The camera callback is limited to 30 updates per second. It moves only the editor
camera; it does not control a vehicle, include collision avoidance or implement
driving physics. The current traffic data is recorded SUMO playback, not a live
observed feed. Playback must already be running to follow a vehicle.

## Source-aware inspection

Inspection recognizes `beirut001_…` buildings and `road_<id>` roads, and matches
vehicles against the current replay registry. Tagged captures report their
original versus viewing point counts, sampled/full representation, colour source
and unverified acquisition date. It does not guess properties for
other actors. Explicitly configure `scene_metadata` in `prototype_config.json`
with the private exported `scene.json` path. For the current export layout only,
the module falls back to `scene_glb`'s parent-parent directory plus `scene.json`.
Source metadata is cached and reloaded when that file changes.

The report distinguishes model identity, model height, source date/alignment,
OSM way and road elevation source from illustrative facade treatment, road width
and car appearance. A BBED object reference is marked provisional and omitted
when no match exists. It never substitutes rendered building bounds for a missing
source height, and does not claim that a model date establishes current conditions.
Local positions are east/north/up metres; the vertical datum remains unverified.

## Verification and integration

The module can be compiled outside Unreal with `python -m py_compile`, but its
camera, selection, menu and modal dialog functions require Unreal. Integration
checks on 25 October 2026 verified street-height placement, following vehicle
1001 with changing camera positions, and source-building inspection including
model identity, January 2020 source and illustrative facade note. Pause retained
actors and held sampled vehicle positions over successive MCP status calls;
resume restarted playback. For automated inspection checks, select a source object and
call `explore_city.inspect_selected(show_dialog=False)` to avoid a blocking dialog.
`explore_city.status()` reports whether a follow callback is active.

Camera controls expect a Perspective viewport. If an orthographic view prevents
placement, the camera helper reports this rather than claiming successful motion.
Following enables viewport realtime. Calibration is cached per editor world so
street-view segment searches do not repeatedly scan every actor. Capture
navigation stops following before changing the camera.
