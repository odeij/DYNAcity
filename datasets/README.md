# DynaCITY datasets

The data the forecasting engine, the 3D viewer and the traffic model run on, for central Beirut. Everything here comes from open sources or was generated from them by this repository's own commands. Each file keeps its source's licence and attribution (below).

Large and binary files are stored with [Git LFS](https://git-lfs.com/). Install it before cloning, or run `git lfs pull` afterwards:

```bash
git lfs install
git clone https://github.com/odeij/DYNAcity.git
```

## Layout

| Folder | File | Size | What it is | Made by |
| --- | --- | --- | --- | --- |
| `buildings/` | `bbed-2024.geojson` | 7.0 MB | 3,349 BBED building footprints with status 2018 / 2022 / 2024, floors, height, use; allowlisted fields only (no names, contacts or owners). EPSG:32636 | `dynacity fetch-bbed` |
| | `bbed-2024.geojson.manifest.json` | 1 KB | Source URL, retrieval time, field list, SHA-256 and licence of the GeoJSON | `dynacity fetch-bbed` |
| | `snapshot-2024-located.json` | 2.5 MB | Canonical 2024 urban-state snapshot (state, floors, height, use, centroid, ground elevation per building) | `dynacity build-snapshot` + `fetch-terrain` |
| | `bbed-transition-panel.csv` | 1.9 MB | Leakage-safe transition panel, 2018→2022 and 2022→2024 rows plus the descriptive 2018→2024 interval | `dynacity build-panel` |
| | `bbed-transitions-2018-2024.json` | 18 KB | Descriptive 2018→2024 transition summary | `dynacity summarize-transitions` |
| | `context-buildings-web.json` | 7.3 MB | 64,846 OSM outlines and 3,871 Microsoft footprints around the BBED area, with estimated heights | `python -m dynacity.engine_context_buildings` |
| `beirut001/` | `beirut001-pack.npz` (LFS) | 2.6 MB | Beirut 001 model reduced to building outlines, heights above model ground, BBED links, bridge levels and a ground grid | `dynacity pack-beirut001` |
| | `beirut001-structures-pack.npz` (LFS) | 7.0 MB | Same model with bridge and tunnel structures, used for Unreal drive routes and context-building heights | `dynacity pack-beirut001` |
| `traffic/` | `beirut-traffic-20261024.json` | 4.2 MB | Citywide road model: 10,818 OSM road pieces with lanes, signals, crossings, turn bans, bridge levels, Tari'ak hourly speeds and CDR peak-hour volumes. The default for `serve --traffic` | `dynacity build-traffic` |
| | `osm-bus-routes.json` | 113 KB | 30 OSM bus route relations (Overpass JSON) | Overpass API |
| `traffic/tariak/` | `velocities.csv` (LFS) | 540 MB | Raw Lebanon Traffic Dataset: 6.0 million crowd-sourced speed readings, 2014–2019, map-matched to OSM ways | Upstream, unchanged |
| | `streets.geojson` (LFS) | 14 MB | The dataset's OSM street geometry | Upstream, unchanged |
| | `datapackage.json`, `LICENSE.md` | | Upstream metadata and ODbL licence text | Upstream, unchanged |
| `pedestrians/` | `pedestrian-walkers.json` | 1.0 MB | Modelled PM pedestrian flows on 2,307 road-side segments (UNA betweenness with Sevtsuk et al. 2024 coefficients, proxy inputs). **Modelled, not observed counts** | `scripts/run_pedestrian_una.py`, `scripts/prepare_pedestrian_walkers.py` |
| `forecast/` | `forecast-request-2024-2030.json` | 2.4 MB | Business-as-usual forecast request (snapshot + scenario) | `ForecastRequest` contract |
| | `forecast-2024-2030.json` | 1.7 MB | Forecast result: first-step transitions and p05/p50/p95 KPI bands for 2026 / 2028 / 2030 | `dynacity forecast` |
| | `forecast-2018-2024.metrics.json` | 1 KB | Temporal benchmark metrics of the selected model | `dynacity train` |
| `terrain/` | `terrain-glo30.npz` (LFS) | 1.5 MB | Ground-elevation grid over the snapshot extent | `dynacity fetch-terrain` |
| `corridor/` | `masar-akhdar.geojson` | 7 KB | Al Masar al Akhdar centre line along the old railway | Hand-drawn; see `docs/green-corridor.md` |

The trained model (`*.joblib`) is not included; rebuild it with `dynacity train --panel datasets/buildings/bbed-transition-panel.csv --output <model>.joblib`. AUB point clouds, the Beirut 001 source model and the raw OSM landscape extracts are not included either (see below).

## Run the viewer from these files

```bash
D=datasets
dynacity train --panel $D/buildings/bbed-transition-panel.csv --output models/forecast.joblib
dynacity serve --model models/forecast.joblib \
  --snapshot $D/buildings/snapshot-2024-located.json \
  --bbed $D/buildings/bbed-2024.geojson \
  --forecast $D/forecast/forecast-2024-2030.json \
  --traffic $D/traffic/beirut-traffic-20261024.json \
  --beirut001 $D/beirut001/beirut001-pack.npz \
  --context-buildings $D/buildings/context-buildings-web.json \
  --host 127.0.0.1 --port 8012
```

Add `--corridor $D/corridor/masar-akhdar.geojson` for the green-corridor view.

## Sources, licences and attribution

| Data | Source | Licence | Attribution to keep |
| --- | --- | --- | --- |
| BBED buildings and everything derived from them | [Beirut Built Environment Database](https://beiruturbanlab.com/en/Details/845), Beirut Urban Lab, AUB | ODbL 1.0 | "Beirut Built Environment Database, Beirut Urban Lab (AUB)" |
| Traffic speeds (`traffic/tariak/`) | [Lebanon Traffic Dataset](https://github.com/ramikay/lebanon-traffic-dataset) (Tari'ak app) | ODbL 1.0 (`traffic/tariak/LICENSE.md`) | "Traffic speeds: Lebanon Traffic Dataset (Tari'ak), ODbL" |
| Roads, signals, crossings, bus routes | [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass | ODbL 1.0 | "© OpenStreetMap contributors" |
| Context buildings | [Overture Maps](https://overturemaps.org/): OpenStreetMap and Microsoft footprints | ODbL 1.0 | "Overture Maps Foundation, OpenStreetMap contributors, Microsoft" |
| Traffic volumes | [CDR / ELARD BRT study, 2017](https://documents1.worldbank.org/curated/en/648821508851766209/pdf/SFG3708-EA-P160224-Box405306B-PUBLIC-Disclosed-10-24-2017.pdf), Table 4-4 | Public World Bank disclosure | Cite the study |
| Elevation | [Copernicus DEM GLO-30](https://registry.opendata.aws/copernicus-dem/) | Copernicus DEM licence (free use with attribution) | "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA" |
| Beirut 001 packs | [Beirut 001 by Salim al-Kadi](https://www.salimalkadi.com/07_beirut-001), free Rhino download, January 2020 | **No named licence.** Derived geometry only; the source model is not redistributed here | "Beirut 001 3D model, Salim al-Kadi (2020)" |
| Pedestrian flows | Computed here on the OSM network; coefficients from Sevtsuk et al. (2024) | ODbL 1.0 (network) | "© OpenStreetMap contributors" |

Derived databases (panel, snapshot, traffic model, forecast) are shared under the same ODbL terms as their inputs. Forecasts and pedestrian flows are model outputs, not observations; see `docs/forecasting-engine.md` for limits.

## Not included

- **AUB point clouds** (`.las` / COPC) and the morphology features derived from them: AUB project data without a confirmed redistribution permission.
- **Beirut 001 source model** (`beirut001.3dm`, 732 MB): download it from the author's page and rebuild the packs with `dynacity pack-beirut001`.
- **Raw OSM landscape extracts** (`osm-beirut-context-*.json`, about 50 MB each): rebuild them from Overpass.
