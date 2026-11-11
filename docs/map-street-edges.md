# Close-view street edges

The street view now supports raised sidewalk slabs and pale curb strips aligned to the existing mapped road centrelines. Detail starts at zoom 18, is cached within a 550 m neighbourhood, and stops before road endpoints to keep junction mouths open. Bridges, tunnels, motorway/trunk roads and short junction links receive no sidewalk extrusions. Duplicate reverse-direction geometry is suppressed.

Widths and the 10.5 cm sidewalk / 16 cm curb heights are illustrative. They use the same class-based pavement allowance as the existing map; they are not a sidewalk survey. Explicit `sidewalk=no/none/left/right` values are respected if present. The current traffic payload generally does not carry those tags. No medians, curb ramps or accessibility claims are invented. Mapped median and accessibility geometry would need a richer source layer before those features can be added responsibly.

Integration: inline `viewer_street_edges.js` in the viewer script, then append `streetEdgeDetailLayers()` after the main road surfaces and before road markings/cars. No network requests or changes to routing are introduced.
