# Map daylight

The map uses a softer neutral ambient fill, a warm directional sun and a restrained cool sky fill. During traffic playback, the selected hour moves the sun from east through south to west, with warmer low-angle light. Outside traffic mode, the view uses neutral midday light. Lighting effects are cached until hour, theme or night level changes.

The daylight arc is illustrative. It does not claim a date, astronomical sun position or measured weather. Existing bridge and vehicle contact-shadow geometry remains an inexpensive approximation. The vehicle and bridge contact shadows stay as they are.

Verification: `node --test tests/viewer_lighting.test.cjs` covers directional consistency, warm morning colour, daylight-hour updates, cached effects and nighttime sun suppression.

## Sun shadows

"Sun shadows" in the tool menu (on by default; `#shadows=off` starts with them off) adds one deck.gl shadow map from the same sun. Buildings (BBED, Beirut 001, OSM gap, basemap context), bridges and mapped trees cast. Only a fully transparent ground plane (`shadow-ground`, 0.1 m up, drawn last) receives: deck's shadow module turns just the shadowed fragments of that plane into translucent shade, so the basemap and satellite imagery show through everywhere else. Buildings never receive a shadow, because deck's depth bias made every extruded building darken itself. The sky fill light does not cast. Shadow strength fades with the night level and shadows are dropped at night. The shadows are cast with the illustrative daylight arc above, so they are not dated or surveyed.

The shadow map is the size of the screen, so shadow edges are stair-stepped, most visibly at citywide zoom. If the median gap between animation frames is over 50 ms across 45 moving frames, shadows switch themselves off (the menu button un-presses). `#shadows=force` disables that fallback.
