# Al Masar al Akhdar corridor view

The browser viewer can be focused on one corridor: Al Masar al Akhdar, the
green route along the abandoned railway from Mar Mikhael station east to the
river and south along Corniche el Nahr.

## The line

`scripts/prepare_green_corridor.py` writes the line from a saved Overpass
extract (no network calls). It takes OpenStreetMap ways 83038929, 83038918,
85665492, 745902854, 1038301678, 1038301679, 745902853 and 1038301685, all
tagged `railway=abandoned`, and leaves out the branches to Tripoli, Naqoura
and the Damascus line inland. It follows the old rail bed. The project's own
design boundary has not been loaded, and the southern end, the former yard
near 33.876 N, should be confirmed against the project drawings.

```sh
PYTHONPATH=src python scripts/prepare_green_corridor.py \
  --osm C:/private/dynacity-data/traffic/osm-beirut-context-sources/mobility.json \
  --output C:/private/dynacity-data/corridor/masar-akhdar.geojson
```

## Serving it

Add `--corridor <line.geojson>` (and optionally `--corridor-buffer-m`,
default 250) to the usual `dynacity serve` command. Every input is cut to the
buffer at start-up:

| Input | How it is cut | At 250 m |
| --- | --- | --- |
| Snapshot and BBED | buildings whose centroid lies in the buffer | 176 of 3,349 |
| Business-as-usual forecast | recomputed for those buildings; `--forecast` is ignored | 300 draws |
| Roads and SUMO traffic | whole roads touching the buffer's box; cars enter and leave at its edge | 1,426 of 10,818 |
| Beirut 001 buildings | buildings in the buffer's box | 3,773 of 17,569 |
| OSM landscape and context buildings | elements touching the buffer's box | |
| Basemap 3D buildings | tiles near the corridor only | |

## The page

A **Corridor** button (the curved line in the left bar) opens a panel with
four stops: Mar Mikhael station, the river bend, Corniche el Nahr and the
southern end. A stop flies the camera there facing along the line; **Tour
the corridor** visits them in order, and **Whole corridor** returns to the
overview. The stops are written into the corridor file by the prepare script.

To keep the page on the corridor, these city-wide tools are hidden: the
Forecast and Change risk map tabs, the KPI table, About this forecast, saved
scenarios and the free-text scenario box. Disasters offers only the weather
that matters for a green route: storm, rain, flood and heat. Add `?full=1` to
the address to bring everything back.

Roads are selected whole, never clipped. Turn bans, crossings and bridge
levels follow the roads' new numbering. The page opens framed on the corridor
at a lower tilt, draws the buffer outline and the rail line, and names the
corridor in the header.

The corridor run is a bounded network with artificial edge demand, like the
Mar Mikhael pilot. It is not a cutout of a citywide simulation, and traffic
that would cross the edge is not represented.
