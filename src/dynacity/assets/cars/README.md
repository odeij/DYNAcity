# Imported traffic cars

Sedan and SUV from Kenney's **Car Kit 3.1**, CC0.
Creator/source: https://kenney.nl/assets/car-kit
Downloaded 2026-10-08 from the creator's official ZIP:
https://kenney.nl/media/pages/assets/car-kit/1a312ec241-1775131960/kenney_car-kit.zip
See LICENSE.txt for the supplied license.

The original GLBs and their shared colormap are retained here. `meshes.json`
contains derived indexed geometry: node translations baked, axes converted to
+x forward / +z up, dimensions scaled to 4.4 × 1.85 × 1.55 m (sedan) and 4.6 × 1.95 × 1.85 m (SUV), texture
palette baked into vertex colors, and body/wheels merged per vehicle type.
Rebuild using `python scripts/prepare_car_models.py` (numpy and Pillow).
The bake also identifies the source paint swatches, allowing stable per-car
body colours without recolouring glass, tyres, rims or light lenses.
The viewer embeds the derived geometry, uses instanced layers per type/paint,
and retains the simpler procedural car below zoom 16.5 or beyond 450 m from
the view center. Detail changes preserve car identity, position and count.
Close-up cars have contact shadows and rear brake lights. Brake lights follow
recorded deceleration or a stopped vehicle; they do not invent braking events
or alter the simulation. Cars outside the viewport plus a 100 m margin are
culled from drawing only; the full fleet remains in replay and accounting.

Local diagnostics: append `?trafficStats=1` before the URL fragment. Read
`#scene` data-traffic-fps, data-traffic-draw-ms, data-traffic-cars and
data-traffic-detailed and data-traffic-visible-cars attributes. Add `&trafficDetail=simple` for a comparison
using only the procedural mesh. FPS includes the whole viewer; draw-ms is
CPU submission time, not GPU time. These are development diagnostics.
