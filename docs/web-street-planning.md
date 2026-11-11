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
