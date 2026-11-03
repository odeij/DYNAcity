# Sea, Corniche and street life (Unreal city)

Added 1 November 2026. All placements are illustrative, not surveyed or observed.

- **Prepare** (no Unreal): `python scripts/prepare_sea_street_life.py --roads <shared-city-surface roads JSON> --coastline <Overpass coastline/breakwater/pier export> --output C:/private/dynacity-data/engine/sea-coast`. Writes `water-normal.png` (generated tileable wave normals), `corniche.json`, `parked-cars.json`.
- **Install** (editor Python, Play stopped): `import sea_street_life; sea_street_life.install_all()`. Actors are tagged `DYNACITY_SEA_STREET_LIFE` in folder `DynaCity/Sea and street life`; materials and texture live in `/Game/DynaCity/SeaStreetLife`. Re-running rebuilds only these.

## What it adds

- **Sea:** one 60 km plane at mean sea level, local z −51.4 m. The Beirut 001 ground matches Copernicus GLO-30 (heights above mean sea level) to a median of +0.34 m on the coastal strip, so model z is effectively height above sea level. The first install used an estimate of −51.0 m, about 40 cm high; re-running `install_all()` applies −51.4. The water material uses world-space UVs, two panning normal layers, a Fresnel deep-to-light colour and a faint self-lit tint, because the sky light has no lower hemisphere and unlit water rendered black. Land covers the plane except at sea and in existing terrain gaps, where water now shows instead of void.
- **Corniche:** concrete seawall, two rails and posts every 2.5 m along the OSM coastline between Ain el-Mreisseh and Raouche, only where a modeled road lies within 70 m: 143 wall pieces, 286 rails, 1,318 posts. The OSM coastline and the model's terrain edge do not always agree, so some wall pieces stand slightly offshore.
- **Parked cars:** 20,934 kerbside slots on ground-level streets 5.5–11 m wide (not highways), 6 m apart with 45 % fill. Slots are dropped within 12 m of either street end, on any other carriageway, and within 15 m of a drive start. Models are the installed BMW E36, Toyota KE80 and delivery van, in six existing paints with an illustrative mix. No collision; drawn within about 90–160 m of the camera.
- **Built-in traffic** (`traffic_local.py`, started from `mobility_runtime.tick`): about 70 vehicles near the player follow road centrelines in the right-hand lane and turn onto connected roads, through the same renderer as SUMO. Vehicles carry `type_id`s (`car:bmw`, `car:toyota`, `taxi`, `van`) because mixed traffic draws only typed vehicles. Status reads "Ambient traffic (built-in, not SUMO) · illustrative"; connecting SUMO replaces it. There is no demand model, signal timing or collision avoidance.

## Checked

Editor captures of the coast (sea visible to the horizon, wall and railing in place) and of a parked street (both kerbs, mixed paints). In Play: 19 typed vehicles visible within the 250 m draw radius, one seen moving on Charles Malek Avenue. Only this feature's 22 actor packages were saved.

## Not done

Pedestrians (see `docs/pedestrian-flow-model.md`) and surf or foam at the shoreline. The corrected sea level is not yet reinstalled in the editor.
