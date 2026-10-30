# Map daylight

The map uses a softer neutral ambient fill, a warm directional sun and a restrained cool sky fill. During traffic playback, the selected hour moves the sun from east through south to west, with warmer low-angle light. Outside traffic mode, the view uses neutral midday light. Lighting effects are cached until hour, theme or night level changes.

The daylight arc is illustrative. It does not claim a date, astronomical sun position or measured weather. Existing bridge and vehicle contact-shadow geometry remains an inexpensive approximation; this change does not add expensive real-time shadow maps or imply physically accurate cast shadows.

Verification: `node --test tests/viewer_lighting.test.cjs` covers directional consistency, warm morning colour, daylight-hour updates, cached effects and nighttime sun suppression.
