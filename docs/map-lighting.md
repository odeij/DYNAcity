# Map daylight

The map uses a softer neutral ambient fill, a warm directional sun and a restrained cool sky fill. During traffic playback, the selected hour moves the sun from east through south to west, with warmer low-angle light. Outside traffic mode, the view uses neutral midday light. Lighting effects are cached until hour, theme or night level changes.

The daylight arc is illustrative. It does not claim a date, astronomical sun position or measured weather. Existing bridge and vehicle contact-shadow geometry remains an inexpensive approximation. The vehicle and bridge contact shadows stay as they are.

Verification: `node --test tests/viewer_lighting.test.cjs` covers directional consistency, warm morning colour, daylight-hour updates, cached effects and nighttime sun suppression.

## Sun shadows

"Sun shadows" in the tool menu (on by default; `#shadows=off` starts with them off) adds one deck.gl shadow map from the same sun. Buildings (BBED, Beirut 001, OSM gap, basemap context), bridges and mapped trees cast. Only a fully transparent ground plane (`shadow-ground`, 0.1 m up, drawn last) receives: deck's shadow module turns just the shadowed fragments of that plane into translucent shade, so the basemap and satellite imagery show through everywhere else. Buildings never receive a shadow, because deck's depth bias made every extruded building darken itself. The sky fill light does not cast. Shadow strength fades with the night level and shadows are dropped at night. The shadows are cast with the illustrative daylight arc above, so they are not dated or surveyed.

The shadow map is the size of the screen, so shadow edges are stair-stepped, most visibly at citywide zoom. If the median gap between animation frames is over 50 ms across 45 moving frames, shadows switch themselves off (the menu button un-presses). `#shadows=force` disables that fallback.

## Night windows

When the traffic hour is between dusk and dawn (`nightLevel()` above 0), building walls carry rows of warm lit windows (`viewer_night.js`). It is a deck.gl `LayerExtension` on the extruded `SolidPolygonLayer`s: the Beirut 001 buildings, the BBED buildings, the OSM/context gap buildings and the basemap context tiles. Shader hooks used, read from the loaded deck.gl 9.4.0 build: `vs:#decl`, `vs:#main-end`, `fs:#decl` and `fs:DECKGL_FILTER_COLOR`. Storeys are about 3 m and bays about 3.6 m, rounded so each wall holds a whole number of both. Windows are added after the scene lighting, so night lighting does not darken them. Roofs are never lit. A soft warm halo around each lit window is the glow. A screen-space bloom was not used: the loaded deck.gl and luma.gl build ships no bloom module, and a full-screen pass would also be heavy over a separate basemap canvas.

The pattern is decorative. There is no occupancy data. Which windows glow, and what fraction (about 12 to 55 percent per building), come from a hash of the building id, so a building always shows the same windows. Buildings recorded as vacant or evicted, demolished, empty lot or parking, or stalled or cancelled get no windows; a Beirut 001 building takes the state of its linked BBED record, and an unlinked one counts as unknown and is lit decoratively. The Forecast and Change risk lenses use the same recorded (observed) state. In daytime the shader exits on one uniform test. About this view states this when night is on. Windows smaller than about 5 px blend to their average coverage so distant walls do not shimmer. Above zoom 18.5 the dark illustrative window boxes of the building details are hidden once the night level passes 0.5, since they would cover the lit rows.
