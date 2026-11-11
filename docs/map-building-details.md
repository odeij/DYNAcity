# Close-view building details

The observed map adds muted facade windows and narrow roof coping at zoom 18.5
and closer, within roughly 250 metres of the camera centre. Detail follows the
active BBED or Beirut 001 footprints and displayed heights. Window spacing, opening dimensions and
coping are illustrative: they are not surveyed facade or roof records. No tanks,
air conditioners or other unobserved roof equipment are added.

The detail is hidden for other analytical lenses, selections, highlights,
scenario results and satellite imagery. This keeps
state colours, visibility and source geometry readable. Empty, demolished and
destroyed records receive no decoration. Geometry is cached, recomputed after
40 metres of movement, and bounded to 120 footprint rings and 6,000 windows.

`viewer_building_details.js` is inlined by `render_viewer`; exported HTML remains
self-contained. `buildingDetailLayers()` is called after building layers.
