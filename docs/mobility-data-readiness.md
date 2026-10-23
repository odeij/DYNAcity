# Evidence needed for mixed traffic and street activity

Audit date: 22 October 2026. Scope: mobility only. This is a source and
implementation readiness assessment with a working corridor pilot described below.
It does not establish current calibration. Unreachable sources are skipped,
without repeated download attempts.

## Implemented corridor pilot

The reachable OSM map API returned a local extract on 22 October 2026 for
`35.522,33.895,35.530,33.900`. The offline compiler
`scripts/prepare_mobility_pilot.py` produces an evidence package containing two
bus-stop markers and 19 parking locations. No signal or crossing tags were present
in that response; missing mapping does not establish physical absence. Occupancy
is unknown. The raw extract and generated files remain in the private data folder.

The separate pilot network contains 104 whole road segments intersecting that box.
It preserves original node identities and inherits the parent model's historical
volume references. Its road indices are local; a manifest maps them back to the
parent network. This is a bounded network, with artificial boundary demand, not a
citywide impact study or a cutout of an already-running citywide simulation.

The viewer offers an **optional historical vehicle mix**, disabled by default,
using the eight reported classes and rounded shares in the 2015 paper below.
The 99.9% total is normalized. Vehicle identity, type, dimensions and replay are
retained through both baseline and scenario runs. Geometry uses SUMO's class
dimensions; driving behaviour and generic minibus dimensions remain assumptions.
Motorcycles are supported by the input schema and tested, but no motorcycle demand
is supplied because the source excludes them.

Traffic-light colours now replay actual SUMO controller states at the displayed
simulation time. Existing generated controls are labelled assumed. Survey-backed
signal programs and stopping/parking events can be imported and are tested in
SUMO, including lane blocking and closure/reopening for mixed classes. **No local
surveyed programs or stop events are loaded in this pilot.** Parked vehicles,
pickup demand and pedestrians are not fabricated from infrastructure markers.

ACTC's reachable B7 timetable describes Cola–Sidon, whereas the two local OSM stop
labels include B7. These identifiers have not been reconciled; the timetable is
therefore not used to inject buses or departures into the local pilot.

## Findings from the loaded inputs

The private traffic directory contains `osm-roads.json`, `osm-bus-routes.json`
and `beirut-traffic.json`. Raw inputs remain outside Git.

| Input | Checked content | What it supports | What it does not establish |
| --- | --- | --- | --- |
| Saved OSM roads | 5,569 ways; OSM base timestamp 3 October 2026; no node elements | Mapped road shapes and available way tags | Signal locations, crossings or absence of either: this extract contains ways only |
| Re-fetched OSM roads (23 October 2026) | 5,570 ways, 186 `highway=traffic_signals` nodes, 42 turn-restriction relations; OSM base timestamp 23 October 2026 | Mapped signal locations (138 junctions after snapping) and 26 via-node turn bans | Phase timing, whether the lights operate, unmapped signals, crossings |
| Saved OSM transit | 30 route relations; 30 routes in the built model, all labelled `bus` | Candidate route geometry and identifiers | Actual vehicle category, active service, timetable, frequency or stopping behaviour |
| Saved OSM parking/sidewalk tags | 18 ways with at least one parking-prefixed tag; two with a `sidewalk` tag | Sparse candidate infrastructure evidence | Parking space counts, occupancy, turnover, curb blocking or pedestrian demand |
| Built traffic model | Historical speed period 2014–2019; route records retain paths, not stop coordinates or schedules | Existing historical speed context | Current traffic, vehicle class shares, pedestrian counts or pickup events |

Some raw transit relations reference stop nodes, but those node elements and
their coordinates are not present in the saved relations-only response. A route
tagged `bus` may name private vans in its operator field; it cannot by itself
decide the simulated vehicle type.

The default passenger-only mode is retained. With a road file that carries signal
nodes, `export_network` puts traffic lights only where OSM maps them: 186 signal
nodes snap to 138 junctions (`traffic.SIGNAL_SNAP_M`), against 1,269 junctions the
old four-way rule would light. 100 of the 138 are four-way junctions and 38 are
three-way. Older road files without signal nodes still fall back to the four-way
guess. SUMO supplies the programs either way, so locations are mapped but timings
remain assumptions. 36 of the 42 restrictions go through a single junction and are
applied (26 distinct banned movements); the 6 via-way restrictions are left out.

Speed check, 10 simulated minutes per run in continuous mode, median of per-road
simulated over observed (Tari'ak 2014–2019) speed:

| Hour | Four-way guess (1,269 lights) | OSM lights (138) | Observed median |
| --- | --- | --- | --- |
| 08:00 | 17.5 km/h, ratio 0.86 | 24.5 km/h, ratio 1.18 | 20.6 km/h |
| 17:00 | 12.1 km/h, ratio 0.63 | 18.0 km/h, ratio 0.93 | 19.3 km/h |

The OSM lights bring the evening median close to observed and overshoot the
morning one. Per-road error stays near 13 km/h in all four runs, and about 90%
of active cars are stopped at the 10-minute mark in every run, so demand and
junction behaviour are not calibrated by this change.

The stopped cars were gridlock, not congestion: about 6,000 at 08:00 had stood
over five minutes. With the junction options and gridlock breaker described in
[traffic-sumo.md](traffic-sumo.md#gridlock), one car at 08:00 and three at 17:00
remain stuck that long, and per-road error falls to about 11 km/h. The median
ratio is now 1.28 at 08:00 and 1.21 at 17:00: simulated traffic runs too fast
because the city empties. The initial fleet finishes its trips, new trips
start only at the map edge, and those edges are congested, so active cars fall
from about 27,000 to 3,500 at 08:00 within ten minutes. Calibrating speeds
needs trips that also start inside the modelled area.

Interior trips (about 4 km, see [traffic-sumo.md](traffic-sumo.md#trips-inside-the-area))
stop the drain: after ten simulated minutes the median ratio is 1.14 at 08:00,
0.96 at 13:00 (not used for any setting) and 0.87 at 17:00. The fleet is still
rising at that point, so these are not steady-state values and the model is
not yet calibrated. The optional pilot adds typed vehicles and
controller-state replay, while pedestrian demand and parking occupancy are missing.

## Primary sources checked

### Mixed vehicles

[AUB Issam Fares Institute, Economic Impacts of Adopting a Sustainable Transport
System in Beirut, Working Paper 28, February 2015](https://scholarworks.aub.edu.lb/server/api/core/bitstreams/07cae89a-f5c1-4878-af96-b179825303b9/content)
reports historical classified peak-hour traffic counts in section 5.5, table 5.1
(printed page 14). It separates private cars, taxis, vans, minibuses, light goods
vehicles, two heavy goods classes and public service vehicles. This is a
potential historical comparison source, not a current corridor-specific input.

The paper explicitly says motorcycles were excluded from the traffic survey
(printed page 15). Its separate national vehicle registration totals must not
be substituted for a local on-road motorcycle share. The displayed percentages
are rounded, and the text and tabulated composition differ; resolve the original
survey and geographic/time scope before adopting a distribution.

Vehicle dimensions, acceleration, braking, following gaps and motorcycle lateral
movement require separate evidence. Measured class proportions do not calibrate
these parameters. SUMO type defaults would remain labelled modelling assumptions.

### Scheduled buses

[ACTC's operator page](https://actc-international.com/public-transportation/)
publishes timetable images and directs users to its ACTC PT app. The page was
reachable. It is stronger operational evidence than route geometry alone, but
the audit did not obtain a downloadable GTFS feed or reconcile its routes with
the 30 saved OSM relations. Published schedules describe planned service, not
observed arrivals or measured dwell times. Fleet passenger capacities are not
vehicle lengths.

[Bus Map Project](https://busmap.me/) provides another route inventory. This
does not establish service frequency or permission to redistribute its data.
Search results for Lebanon Transit in Pennsylvania are outside the study area
and must be excluded.

### Walking, parking and pickups

[Sevtsuk et al., Cities 149, 2024, DOI 10.1016/j.cities.2024.104927](https://doi.org/10.1016/j.cities.2024.104927)
reports pedestrian observations at 90 locations collected in 2021–2022.
[MIT's publication record](https://dspace.mit.edu/entities/publication/030f8a4c-715a-46cf-8558-5a4708cc4eed)
confirms the publication and authorship. Surveyed street counts are promising
historical calibration evidence, but raw location/time records were not obtained
in this audit. Link counts do not determine individual crosswalk movements.

[AUB's congestion studies catalogue](https://www.aub.edu.lb/Neighborhood/Documents/CongestionStudiesDatabaseCatalogue.pdf)
lists parking deficit GIS/Excel datasets and other neighbourhood surveys from
2007–2015. The catalogue was reachable; it is an inventory, not the underlying
data. Its Ras Beirut coverage cannot be assumed to include Armenia Street.
No usable pickup location, arrival-rate or dwell-time records were obtained.

### Traffic lights and crossings

No accepted local phase plan, cycle length, offset, movement mapping or current
operational-status record was obtained. An earlier bounded query for OSM signal
and crossing nodes in an approximate Mar Mikhael box timed out. The citywide road
query on 23 October 2026 did return signal nodes (see the table above); crossing
nodes were not requested. Missing mapping is still not evidence of absence.

Even a successful OSM signal-location query would not establish present-day
operation or the phase timing. Each accepted junction needs dated movement-level
evidence, including conflicting turns, pedestrian phases and whether the lights
were actually operating during the survey period.

## Implementation order and acceptance

1. Select a corridor and reference period that match the available observations.
   Record source URL/file, measurement dates, time zone, location, units,
   classification definitions, usage terms and missing fields for every input.
   Keep historical observations separate from current service schedules.
2. Preserve accepted signal nodes, crossing/sidewalk geometry and bus stops in
   the network input. Map them to stable road and lane identities. Replace the
   degree-based signal assumption only where evidence supports a control;
   unobserved control types remain unknown rather than becoming surveyed facts.
3. Load observed class-specific flows. Retain separate car, motorcycle, van,
   minibus and bus identities through routing, closures, replay and accounting.
   Motorcycle shares require observations; never fill them from registrations.
4. Run buses on reconciled routes and published departures; distinguish scheduled
   dwell assumptions from observed stopping data. Avoid counting the same buses
   in both aggregate traffic and the explicit transit service.
5. Add parked vehicles only with a mapped curb/space definition and occupancy
   evidence. Add pickup events with location, arrival and dwell observations.
   Lane obstruction must affect SUMO movement, not just the picture.
6. Add pedestrian demand only with a connected walking network and supported
   crossing movements. Export actual SUMO positions and signal states to the
   viewer so visible crossings and lights agree with vehicle permissions.
7. Validate on held-out observed intervals: flows by vehicle class and turn,
   bus departures, travel times, queue lengths, parking occupancy, stop durations
   and crossing counts. Choose tolerances before tuning. Check no disappearing
   agents, no double-counted transit and correct closure/reopening for all modes.

SUMO supports these mechanics; its capability is not evidence that the local
inputs are available or that the model is calibrated. See the official
[pedestrian documentation](https://sumo.dlr.de/docs/Simulation/Pedestrians.html),
[vehicle definitions](https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html)
and [traffic light documentation](https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html).

The corridor input package and working replay are available. Until the listed
gaps are filled, the pilot cannot honestly be described as a validated
representation of current Beirut traffic.
