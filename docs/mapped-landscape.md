# Mapped vegetation and open surfaces

Pass `--landscape /private/path/osm-landscape.json` to `serve` or `export-viewer`
to load an Overpass JSON extract with geometry or OSM API map JSON with nodes. The viewer draws closed mapped
parks, gardens, grass, woods, scrub, sand, parking and sports/play areas with
different ground colours, and individual OSM tree nodes with 3D crowns nearby.
It does not populate parks with invented tree locations or infer use from a
gap between buildings. Unsupported or unclosed features remain unchanged.

OSM tree heights are retained when usable, labelled unverified; missing heights
use an illustrative 6 m tree. Crown shape/species and surface materials are
illustrative. Hover shows the OSM identity, extract date and height provenance.
Park boundaries are land-use evidence, not proof that every point is grass.
Raw extracts stay outside Git; OSM attribution remains visible in the map.

The initial local extract was retrieved on 23 October 2026 through the OSM map
API for `35.483,33.891,35.493,33.900`, around Sanayeh. It supplies 64 supported
features: 46 parking areas, 7 parks, 3 individual trees, 3 playgrounds, 3 woodland
areas, one grass area and one garden. This is local coverage, not a Beirut-wide
inventory. Retrieval date does not establish that every mapped feature is current.

Ground surfaces now distinguish tagged asphalt, concrete, paving stones, gravel,
soil and grass before using the broader land-use tint. Subtle boundary strokes
appear at close zoom, while partial opacity retains basemap paths. A park tag
still does not imply that the entire area is lawn.

Mapped trees use one reusable mesh with a round tapering trunk and an uneven,
multi-lobed crown. Deterministic rotation varies the illustrative silhouette,
without moving any source point or changing its height. The mesh is 6 m tall
before height scaling. Every tree in the map viewport is rendered in 3D from
zoom 16.5; zoomed-out views retain all trees as small green markers. There is
no 512-tree truncation. Park boundaries appear from zoom 17.

Mapped landscape stays visible in both Original BBED and Beirut 001 + BBED
building modes. Satellite imagery suppresses the illustrative surface treatment.

The expanded tree extract retrieved on 24 October 2026 covers the traffic-map
extent (west 35.4693, south 33.8288, east 35.5720, north 33.9076). It contains
1,191 individual tree nodes and 20 tree-row ways. Every individual node is
retained. Rows use illustrative spacing of at most about 8 m, suppressing display
instances within 3 m of existing tree points. This adds 307 row display instances;
they are not 307 measured trees. Hover distinguishes individual nodes from row
spacing and retains source IDs and per-feature dates. Park/wood polygons are
never used to invent individual tree locations.

Use the local merged `osm-landscape-beirut-trees.json` with `--landscape` for this
expanded coverage. It retains the original Sanayeh ground surfaces and their
earlier source dates; only the tree extraction covers the full map extent.
