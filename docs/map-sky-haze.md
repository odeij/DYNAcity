# Map sky, haze and horizon

`applySky()` in `src/dynacity/viewer_template.html` sets the MapLibre sky and
fog (`map.setSky`, style-spec 5.24: sky-color, horizon-color, fog-color,
sky-horizon-blend, horizon-fog-blend, fog-ground-blend, atmosphere-blend) from
the theme, `nightLevel()` and, while the traffic clock runs, a warm dawn/dusk
band (peaks at 06:30 and 18:30). It runs from `applyNight()` and on style load.

MapLibre fog does not reach deck.gl layers (deck draws over the map, not
interleaved). `HazeExtension`, a `deck.LayerExtension`, therefore blends the
colour of every extruded building layer (BBED, Beirut 001, context MVT
buildings, OSM gap buildings) toward the same fog colour by distance from the
camera. The distance start/end scale with the camera-to-centre distance, so
the haze reads the same at any zoom and is nearly absent in a top-down view.
At night the fog colour carries the same brightness/saturation the map canvas
gets from its CSS dimming, so map and deck meet at the horizon.

Water: light theme keeps the one OSM-teal water colour; dark theme keeps the
basemap style's own water colour. A coast "shallow water" band was tried and
dropped: OpenFreeMap ocean polygons are clipped at tile edges, so outlining
them draws a visible tile grid.

The dark theme is not reachable from the page today (the document is always
`data-theme="light"`); the dark branch was checked by loading the dark style
into a running page.
