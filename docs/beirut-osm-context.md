# Beirut mapped city context

The OSM context import covers the same map extent as the current traffic network,
not only Sanayeh. This is an extraction boundary, not an assertion of complete
mapping or the administrative limits of Greater Beirut.

Fetch a reusable private extract from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/fetch_osm_context.py --bbox 35.4693 33.8288 35.5720 33.9076 --output C:/private/dynacity-data/traffic/osm-beirut-context.json
```

The fetcher caches five source groups (landscape, mobility, places, buildings,
infrastructure),
retains their Overpass queries, bounds, timestamps and public tags, and merges
objects by OSM type and ID. It fails rather than publishing a truncated response.
Existing cached groups allow interrupted downloads to resume. Use a new output
name or archive the cache when intentionally refreshing source data.

The infrastructure group fetches `emergency`, `power`, `utility`, `man_made`,
`traffic_calming`, `traffic_sign`, `advertising`, `kerb`, `tactile_paving`,
`parking`, `parking:lane` and `playground` tags. Use `--groups infrastructure`
with a new output path for an independent targeted refresh. These selectors
also return overlapping objects with other tags: retaining a source object does
not mean it has a visual or simulation implementation. Road-attached attributes
stay on their roads; individual signs or devices are not invented from them.
In particular a mapped traffic-calming tag does not establish height or current
condition. Nodes and other features with usable geometry are retained with
source tags and timestamps under the `infrastructure` context group; unsupported
geometry is reported rather than repaired.

Pass the resulting JSON to `serve --landscape ...` or `export-viewer --landscape
...`. Rendered HTML includes the detail modules, so exported viewers need no
additional local JavaScript requests. Raw extracts remain outside Git.

## What is drawn

- Parks, gardens, woods, grass, sports grounds, parking, water and supported
  ground-cover areas across the extraction extent. Multipolygon rings are
  assembled from source ways, with holes retained. Broken rings are rejected.
- Individual tree nodes and tree rows, with existing tree provenance rules.
- Pedestrian routes, cycleways and plazas; mapped stairs have tread marks.
  Widths use valid metric tags, with illustrative defaults otherwise.
- Mapped walls, fences, hedges, barriers, gates and street furniture nearby.
  Furniture forms, missing sizes and orientations are illustrative. Existing
  source geometry is never moved to make a visually pleasing arrangement.
- Waterways, rail alignments, mapped businesses, amenities and transport stops.
  Nearby names are thinned for readability, while their source objects remain.
- Infrastructure nodes appear as small reference markers from zoom 18, and
  mapped infrastructure lines and area outlines as strokes from zoom 17. Hover for source tags;
  these are map references, not finished 3D assets or simulation inputs.
- OSM building outlines fill gaps outside active BBED or Beirut 001 footprints.
  Source models take precedence over overlapping OSM buildings. OSM height tags,
  floor-count estimates and illustrative fallback heights remain distinguishable.
- Source way IDs connect road display surfaces and sidewalk-presence tags to
  their OSM records. These tags do not overwrite SUMO carriageway dimensions,
  historical demand, simulated controls or authoritative trajectories.
- Mapped crossing nodes are snapped to compatible existing road geometry for
  crossing paint, using the existing crossing importer. Explicitly unmarked
  crossings are excluded. This does not change simulated signal timing.

Hover mapped objects for public source identity, dates and relevant tags. Open
More tools → About this view for imported category counts and geometry gaps.
The counts describe source objects, not current inventory or operating status.

## Limits

This is a citywide mapped context layer, not a surveyed, live digital twin.
OSM data is uneven and may be stale. An unmapped feature remains unknown.
Steps have no invented vertical rise where elevations are absent. Furniture is
generic; no street-level photo textures are implied. Building parts and other
source attributes remain in the import even when they are not given a separate
3D model. Source geometry errors and source-model alignment still need review.
Analysis lenses retain their building state colours and forecasting semantics.
Satellite mode suppresses illustrative mapped-context treatments.

Camera changes refresh nearby geometry at a bounded rate. Business names and
place markers appear only from zoom 18.5 and are re-spaced against the current
camera. They use fixed pixel sizes. Wider views retain the basemap's city and
street labels. Traffic movement boards appear from zoom 18; zoom 16–18 uses
neutral junction-location dots, and city-scale views omit individual controls.
Camera zoom/pitch limits match deep links (20 / 75°). When the estimated camera
eye enters a building volume or a nearby building blocks its view ray, that building fades for visibility; its source
footprint, height and analytical record are unchanged. Camera moves also update
the URL so a problematic view can be reproduced.

References: [OSM multipolygons](https://wiki.openstreetmap.org/wiki/Relation:multipolygon),
[OSM attribution and licence](https://www.openstreetmap.org/copyright).
