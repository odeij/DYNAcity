# Unreal clouds, lighting and vehicle signals

The Play-world presentation has volumetric clouds (with an animated dome fallback), morning,
afternoon, sunset and night lighting presets, and emissive vehicle fixtures.
The browser simulation is unchanged. Atmosphere controls are in **Explore
Beirut → City atmosphere & traffic**; **L** still toggles day/night.

For the driven car, **Z** toggles left, **X** toggles right and **H** toggles
hazards. Press the same key again to cancel. Indicators are manual, including
cancellation; steering does not invent a turn intention. **Space** and opposite
throttle illuminate the brake lamps. Front and rear running lamps follow night
mode. The existing two player headlights remain the only vehicle spotlights.

SUMO traffic uses the authoritative signal bitmask already emitted by the API:
right=1, left=2, hazards=4, brakes=8, front lights=16. The Unreal adapter previously
dropped this field. See [SUMO vehicle signaling](https://sumo.dlr.de/docs/TraCI/Vehicle_Signaling.html).
These states reflect synthetic driving decisions, not observations of Beirut
drivers. Missing signal data stays unknown, while an explicit zero switches
signals off. There is no acceleration fallback when the source lacks signals.

Traffic lights use the last source sample until the next sample's time; they
do not anticipate a future turn by interpolating discrete bits. Amber flashing
uses simulation time (0.4 seconds on, 0.4 off), so it freezes with starved
playback. Clearing/restarting a session clears lamp instances. No yaw-based
inference, independent traffic clock or alteration to SUMO demand is introduced.

## Cost and limits

- Four instanced lamp batches per renderer: dim red, brake red, white and amber.
  Lamps are emissive meshes without point lights, collision or shadows, and
  share the traffic visibility/vehicle limits. They are indicative fixtures
  fitted to each prepared model's bounds, not surveyed/model-specific lenses.
- The native sky uses Unreal's stock volumetric material through a private
  dynamic instance: layer base 2 km, thickness 1.8 km (stock was 5–15 km),
  coverage −0.12, density 0.012, bottom occlusion 0.25 and view samples 1.5×.
  Its world scale is independent of the 8000× fallback sphere. The Python
  adapter applies these settings on every Play start, including existing
  native plugin builds. Engine assets are not edited.
- When the stock volume material is unavailable, the dome uses version 7
  smooth procedural noise with a perspective cloud-plane projection and a
  horizon fade. This replaces the harsh ground macro-texture pattern.
  Both paths are illustrative weather, not a meteorological model. Increased
  volume samples have not been performance-benchmarked across the whole city.
- Material creation, metadata checks and shader compilation run outside Play.
  During Play, the adapter loads the five prepared material assets directly;
  missing assets produce a preparation error instead of attempting an editor
  rebuild. The standard driving entry point prepares them before starting Play.
- Lighting hours are illustrative presets, not a solar ephemeris, automatic
  daily cycle, live weather feed or synchronization with SUMO's demand hour.
  Tagged streetlights and player headlights follow the selected night state.
- Materials reuse bundled Unreal assets and are generated once under
  `/Game/DynaCity/AmbientV1`. No images are downloaded and no source-photo
  catalog entries are added. Sky/lamp actors are confined to the Play world;
  the saved city geometry and appearance evidence remain separate.

## Implementation and verification

Native code: `DynaAmbientSky`, `DynaVehicleLights`, `DynaDrivePawn`, and
`DynaMobilityWorld` in `engine/unreal/Plugins/DynaCityDrive/Source/DynaCityDrive`.
Editor integration: `ambient_effects.py`, `traffic_client.py`,
`mobility_runtime.py`, `control_panel.py` and the bundled controls UI.

Python adapter tests cover source-mask preservation, explicit zero, missing
legacy data and invalid masks. Native automation `DynaCity.Traffic.VehicleLights`
covers left/right placement, hazard and brake coexistence, flash timing,
night lamps, clearing and player controls. Build/render results are recorded
in the dated Beirut work-log entry and private verification report.

Seven regression tests in `tests/test_unreal_ambient_effects.py` cover the
PIE-only loading path, each missing asset, and reuse of cached editor assets.
