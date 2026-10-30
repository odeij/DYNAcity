# Intersection detail

At street zoom 18 and above, the viewer draws directional arrows on nearby
SUMO signal approaches. Incoming-lane positions, headings and controlled turn
directions come from the running network. Several controlled movements on the
same lane share one arrow stem. Straight, left and right combinations are
preserved; no U-turn arrows are inferred.

Paint dimensions and presence are illustrative, not observations of Beirut's
actual road markings. Arrows follow the displayed road height, including raised
approaches. A whole arrow is omitted when the road is too short or curved to
keep it inside its displayed surface. Tunnels are omitted. The layer is limited
to 300 approaches within 450 metres and caches geometry independently of light
states. Lane metadata is scanned once per replay sample, rather than once per
animation frame. Camera movement and new lane layouts refresh the geometry.
It does not invent pedestrian crossings or change traffic behaviour.

Existing road markings retain stop bars at mapped signal nodes and zebras only
at mapped crossings (`r.xing`). This module adds no duplicate stop bars.

Integration: inline `viewer_intersections.js` in the viewer script, then append
`intersectionDetailLayers()` after the road layers/other road paint.
