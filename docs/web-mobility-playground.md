# Web mobility playground

Current planning controls are documented in [Street planning](web-street-planning.md). The street-intervention workflow replaces the original walking-speed control.

Added 2 November 2026. Open [the local viewer](http://127.0.0.1:8012/?traffic=1#lat=33.895&lon=35.485&zoom=18&pitch=50) after starting:

```powershell
./scripts/start_web_mobility.ps1
```

The launcher serves local private data on loopback. It uses the citywide 24 October road model, Beirut 001 buildings, Overture context buildings, the newer greenery/public-space inventory, utilities/waterfront inventory, lighting inventory, and **the same `pedestrian-walkers.json` used by Unreal**. Missing optional overlays are skipped. Required base data must exist under `C:/private/dynacity-data` (or `-DataRoot`). No Unreal scene is modified.

## Try it

- Open Traffic. Increase incoming traffic to 2× or reduce it to zero. SUMO starts a new run with the same initial fleet and changes both boundary arrivals and trips starting inside. Existing normal/closure comparisons use the same demand in both runs.
- Select a street section to pedestrianise it, cap motor speed or reallocate a vehicle lane to pavements. Pedestrian demand (0–3×) and the flow overlay are in optional assumptions/display. The PM baseline is independent of the traffic hour; animation uses a fixed illustrative walking speed.
- Select **Close / reopen pavement**, then click a green walking segment. Its walkers disappear and the affected modelled occupancy is reported. Click again to reopen. No pedestrian detour is inferred.
- Use **Make car-free** or **Crash** for SUMO road experiments. Making a road car-free leaves its pavements available. Existing traffic metrics report stopped/diverted vehicles and paired trip delays.
- Pause/resume playback, or reset mobility settings and road/pavement edits. Reset leaves separately selected weather scenarios in place. Controls persist for the current page only.

## Data and rendering

The shared pedestrian export contains 2,307 pavement segments and about 732 modelled occupants at baseline. The adapter converts its east/north/up origin in UTM 36N to longitude/latitude without changing the private source. It associates each segment with the nearest web ground road for display elevation; these associations are inferred, not shared numeric indexes or survey matches. Web ground roads are flat, so the browser uses its own road plane plus the pavement lift instead of Unreal's absolute terrain elevations.

Additional OSM inventories merge by `(type, id)`; later geometry and tags take precedence while untagged dependency nodes cannot erase existing tags. Per-element capture/retrieval metadata is retained. With the currently available local inputs, the combined payload contains 1,498 tree features, 1,867 surface features, 1,408 paths, 37 furniture features, 349 infrastructure references, 346 boundaries, 22 water features and 22 transport features, alongside buildings and places. These are source feature counts, not verified Unreal actor counts or live assets.

The browser retains its existing generic furniture, vegetation, infrastructure references and building rendering. Unreal marketplace meshes, City Sample characters, source terrain meshes, photo facade pilots and licensed real-car meshes are **not converted by this change**. The source inventories and pedestrian paths are shared; renderers differ. Infrastructure references do not supply operational simulation inputs.

## Limits and verification

Pedestrian flows come from UNA with proxy home/job inputs and published regression coefficients. They are not observed or locally calibrated counts. Occupancy removes the regression constant; animated directions, fractional walker allocation and character appearance are illustrative. Animation uses a fixed illustrative walking speed; the planning panel changes street rules and space, not UNA trip generation. Walkers loop within each segment; there is no destination routing, crossing solver, collision avoidance, pedestrian–vehicle coupling or hazard response. Closing a pavement removes its occupants without redistributing demand. Citywide occupancy totals are separate from the viewport's drawing cap of 2,400 people. Reduced-motion mode holds their positions.

Traffic uses the existing SUMO solver and its historical/synthetic assumptions. The incoming multiplier ranges from 0–2× and does not modify the initial fleet. Demand changes restart sessions; road closures within a run retain previous effects. External planner prose still requires the existing language-model configuration; the mobility controls work without it.

Verification: 33 Python viewer/context tests, 38 SUMO integration tests, and 106 browser-logic tests passed (two existing flood tests skipped). Tests cover coordinate conversion, input rejection, overlay deduplication/provenance, demand scaling down to zero in SUMO, unchanged replay behavior, pavement offsets, closure/reopening, occupancy accounting, pause and reduced motion. Live browser checks confirmed 1×→3× occupancy (732→2,197), reset to baseline, pause/resume, actual citywide SUMO comparison metrics and no console errors. Private report: [verification.json](C:/private/dynacity-data/web-mobility-20261102/verification.json).

Main implementation: `viewer_pedestrians.py`, `viewer_pedestrians.js`, `viewer_template.html`, `cli.py`, `traffic_sumo.py`, and `scripts/start_web_mobility.ps1`. Both `serve` and `export-viewer` accept `--pedestrians` and repeatable `--context-overlay` inputs; the JavaScript module is embedded in portable exports.
