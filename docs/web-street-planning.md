# Street planning in the web viewer

The web mobility panel is for street-design decisions: selecting a road section, pedestrianising it, calming motor traffic and reallocating a vehicle lane to pavements. Walking animation speed is fixed and is not a planning control. Demand multipliers are optional scenario assumptions.

Start the local viewer with `scripts/start_web_mobility.ps1`, then open [Traffic](http://127.0.0.1:8012/?traffic=1). Select an exposed road on the map or search by street name. Each action affects the selected **section**, not every section of a named street.

## The planning table (2D)

`dynacity serve --traffic …` also serves a simpler 2D page at [/planner](http://127.0.0.1:8012/planner), made for planners who are not traffic modellers. It shows today's OpenStreetMap streets (OpenFreeMap tiles) inside an old-map frame: torn, scorched paper edges, a compass rose and a title scroll. A planner taps a street, or searches for it by name and can pick the whole street, then chooses one of three plain actions: close it to cars, give one lane to people, or slow it to 30 km/h. Each action is inked onto the map with a pencil sound, made with the browser's Web Audio, so no sound files are used. The red seal starts the same paired SUMO comparison as the 3D viewer at 8:00, 13:00 or 17:00. It plays the cars as ink dots, draws detours in purple and queues in red, and reports completed trips, extra time per trip, detours and cars with no way through in plain words. Edits made while it runs reach the running session. The page's only data is `/v1/planner/roads`: each road's path, name, class, motor lanes, one-way flag and free-flow speed (`planner.py`).

Citywide, a run takes about 20 s to start and then advances at roughly real time, so effects take a minute or two to spread.

### Switching between the 3D city and the 2D map

The **2D** button in the 3D viewer's left rail opens the planner on the same page, at the spot being viewed (`viewer_plan2d.js`). The viewer's panels fade, and the camera turns north-up and looks straight down at the same centre. The buildings sink into their footprints while roofs, trees, cars and bridges fade. The planner was already loaded in a frame at that centre and zoom, so the scale matches: it fades in over the flattened city, then its paper tint, torn desk edges, panel, title and compass are laid on top. **3D city** (or Esc) runs this in reverse. The paper lifts off, the 3D view takes over at the planner's current spot, and the camera tilts back to its earlier angle as the buildings rise. Planner edits are kept for the next visit; a running planner simulation is stopped on the way out.

Opened on its own, `/planner#lon=…&lat=…&zoom=…` starts at that spot and keeps its hash current, and **3D city** opens the viewer there with `rise=1`, so the city rises in place. The planner's zoom range is 11–19, so a closer 3D view settles at zoom 19. Its hours are 8:00, 13:00 and 17:00; it takes the nearest one to the 3D clock. The button appears only on a served page with a traffic model (`/planner` exists only then).

**Hiding the interface.** The eye button in the viewer's rail, or **H**, hides every panel and leaves the city, the credits line the basemap licence requires, and one eye button in the corner to bring them back. In the planner the same button (or **H**) slides away the paper panel, title and compass. The choice carries across when switching between 3D and 2D.

## Optional context layers: residents and shops (off by default)

The planning table can show who lives near a street and what is on it. Both layers are off unless `serve` is given their files; without the flags `/planner` and `/v1/planner/roads` behave exactly as before, no extra request is made, and the two context routes do not exist.

```sh
python scripts/prepare_planner_context.py population --tif lbn_pop_2025_CN_100m_R2025A_v1.tif --source-url <worldpop url> --output population-beirut.json
overturemaps download --bbox=35.4655,33.8270,35.5745,33.9085 -f geojson -t place -r 2026-11-18.0 -o places-beirut.geojson
python scripts/prepare_planner_context.py places --geojson places-beirut.geojson --release 2026-11-18.0 --output places-beirut.json
dynacity serve ... --traffic <model> --population population-beirut.json --places places-beirut.json
```

Raw downloads and the prepared files stay outside the repository (private data boundary). The prepared files hold only what the page needs; `planner_context.py` joins them to the served road model at start-up, so road indices always match the model in use, citywide or cut to a corridor.

| Layer | Source and licence | What the page says |
| --- | --- | --- |
| Residents | WorldPop Global 2015-2030, release R2025A v1, constrained top-down, 100 m, Lebanon, year 2025 ([data.worldpop.org](https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/LBN/v1/100m/constrained/lbn_pop_2025_CN_100m_R2025A_v1.tif)). CC BY 4.0, cited as Bondarenko et al. (2025), doi 10.5258/SOTON/WP00840 | On a selected street: "About N residents within 200 m". Under "Your changes": "About N residents live within 200 m of the streets you changed". Always labelled a WorldPop modelled estimate for 2025; Lebanon has had no census since 1932 |
| Shops and services | Overture Maps places, release 2026-11-18.0. Overture states a licence per provider rather than one for the theme: most are CDLA Permissive 2.0, Foursquare is Apache 2.0, AllThePlaces is CC0 1.0 (no ODbL component in Places) | On a selected street: "N shops and services on this street", the commonest categories and up to eight names. Under "Your changes": the total on the changed streets. Optional small dots at street zoom (15 and closer), with a name and category on hover; a checkbox turns them off |

The credits line under the map and a block at the end of the panel name both sources, their licences and the attribution text. On phones the map's credits line is hidden by the existing layout, so the panel block is the visible credit there.

**How the counts are made.** Each 100 m WorldPop cell is split into 4 x 4 sub-cells of about 25 m, each carrying a sixteenth of the cell's people; a road's residents are the sub-cells whose centre lies within 200 m of its line. For several streets the sub-cells are united before they are summed, so someone living near two changed streets is counted once. Places count for a road when within 25 m of its line; a place at a junction belongs to each street there and is counted once in a total. The numbers are rounded to the nearest 10 (under 100), 50 (under 1,000), 100 (under 10,000) or 500. Counting happens on the server (`POST /v1/planner/context/summary`, which takes road indices from `/v1/planner/roads`); `GET /v1/planner/context` gives the credits and the places on a street for the dots.

**Limits.**
- WorldPop is a model that spreads national totals over built-up cells with satellite-derived covariates. With no recent census to calibrate against, it cannot be checked street by street. Treat counts as orders of magnitude for comparing streets, not as headcounts. R2025A is flagged by WorldPop as a version that may change. The 2025 year is the latest release year; the 2026-2030 files are projections.
- A 200 m circle around a road is a distance, not a walking catchment: it ignores barriers such as the Beirut River, motorways and the railway.
- Overture places are a merge of provider data (mostly Meta). Some are closed, mislocated, or stacked at a single building (several can share one point). Only places with confidence 0.5 or more and a category are kept. Categories are Overture's own and include non-retail places such as schools, hospitals and places of worship, hence "shops and services" is a loose label. The list can be out of date or incomplete and is not a business census.
- Only the allowlisted place fields are read from the download (id, primary name, primary category, confidence, point). Phone numbers, emails, social links, websites, addresses and brand fields are never copied into the prepared file.
- The layers do not feed the traffic model, the SUMO comparison or any forecast; they only describe the streets a planner has selected or changed.

## Decisions and consequences

| Decision | Traffic solver | Pedestrian display and indicators |
| --- | --- | --- |
| Pedestrianise a section | Closes its motor lanes; SUMO reroutes trips where a route exists and accounts for blocked trips | Pavement walkers remain. A separate indicator estimates space if the full street width is walkable |
| Calm traffic to 30 km/h, or select another limit | Caps the selected section's speed; never raises an already lower limit | Marks the planned section; no pedestrian behaviour or safety benefit is inferred |
| Reallocate a lane to pavements | Bars a curb-side lane per direction from motor traffic using SUMO lane permissions | Draws reserved space and adjusts pavement-walker placement; reports walking area and modelled occupants per 100 m² |
| Restore or clear changes | Restores original lane permissions and speeds; earlier queues can take time to clear | Restores original pavement depiction and area indicators |

The interface offers lane reallocation only when a motor lane remains and the viewer has pavements. A single-lane section can instead be pedestrianised. The API also handles removal of every lane as a closure. Lane width is an explicit 3.2 m assumption. On a one-way section, reserved space follows its right-hand curb in the direction of travel; two-way sections reserve a curb-side lane in each direction. Existing vehicles can take time to leave a newly reserved lane.

## Comparison and evidence

The SUMO comparison forks before the first intervention. Only the scenario run receives the plan; the comparison retains the original road rules with the same fleet and incoming demand. Changes reach the existing session, preserving its traffic history. Speed caps combine with existing crash slowdowns by taking the lower limit. In both runs drivers re-plan from current road speeds on entry and every two minutes, so a slowed or narrowed street sheds traffic to parallel streets. This assumes every driver follows a live map app, which is not measured for Beirut. Lane restrictions can affect permitted turns and queues; local calibration has not been performed.

Pedestrian indicators use proxy-modelled PM occupancy from the shared UNA export and illustrative viewer pavement geometry. They keep occupancy constant before and after: a larger denominator reduces the reported density without predicting additional people, different destinations, avoidance, crossing behaviour, safety or wellbeing. Pedestrianisation's whole-width space is conditional on that width actually being usable. These are geometric scenario indicators, not a coupled pedestrian-flow forecast.

Road and lane counts come from the source model, including defaults where mapping is incomplete. Neither road widths nor the pedestrian results are local survey measurements. Web pavement overlays illustrate reserved space; they do not change the original source geometry or Unreal scene. Native character assets and the pedestrian crossing/routing solver remain outside this implementation.

## Implementation and checks

`traffic_planning.py` validates up to 1,000 unique road entries. `traffic_sumo.py` applies speed limits, permissions, closures and restoration to the scenario connection. The web adapter sends `planning: [{road, speed_limit_kmh?, lanes_removed}]` with each simulation request. `viewer_pedestrians.js` supplies section selection, actions, indicators and geometry; `viewer_template.html` supplies integration and layout.

Private implementation handoffs, test evidence and browser captures are collected under `C:/private/dynacity-data/web-planning-20261102`. The work log records final verification and inspected coverage.

Acceptance: nine new planning tests and 59 focused JavaScript tests independently passed. Live section 5847 selection, pedestrianisation, lane allocation, speed caps and restoration were checked in the populated viewer. Full-city lane-constraint inspection used an empty fleet separately, with a 0.02 km/h speed serialization tolerance. Narrow and desktop controls were inspected; visual coverage is limited to that section. See the work log and private verification record for retained evidence.
