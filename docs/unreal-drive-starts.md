# Source-grounded driving starts

`python -m dynacity.engine_drive_routes` prepares one candidate driving start
inside each of the twelve source-labelled production areas. The private
`drive-starts.json` uses the same common origin as the city GLBs.

Candidates use only exported ground-level roads with `height_source` equal to
`source ground`. Elevated offsets, tunnels, high-speed road classes, missing
ground and approaches shorter than the required clearance are excluded.
The traffic model's OSM way ID and drawn endpoints must match the exported road
before its one-way direction is used. A `oneway=-1` road reverses the drawn path;
two-way roads use the drawn direction and an assumed right-hand lane.

Each accepted candidate has a 30 metre forward preview and 3 metres behind it.
The corridor accounts for a 2 metre wide vehicle plus 0.5 metre lateral margin
on either side. It must fit within the modeled road and avoid all source
building footprints. Source ground must exist across the corridor, and sampled
longitudinal grade must stay at or below 10%. The spawn point must be inside
its named source polygon, rather than relying only on whole-road ownership.

The output contains `starts`, `unavailable_areas`, source paths, screening
counts and per-road results. No area receives a fabricated fallback. Each
start records `area`, `road_id`, `road_name`, `source_way`, source direction,
`location_cm`, `yaw_degrees`, `local_position_m`, `surface_local_z_m`, and
`preview_path_cm`. `location_cm` is the modeled asphalt height plus a **1.2 metre
spawn offset**, so the runtime must settle/snap its vehicle to the actual
collision surface. Preview points are on the modeled road surface without that
spawn offset. Unreal positions are X east, Y south, Z up, in centimetres;
source-local positions are X east, Y north, Z up, in metres. Yaw is
`atan2(-north_direction, east_direction)` in degrees.

The current source run screened 5,697 exported roads and found starts in all
12 named areas. There were 8,081 accepted geometric candidates. Screening
excluded 2,129 short roads, 594 high-speed roads and 90 non-ground roads;
candidate rejections included 1,463 building conflicts, 1,491 excessive grades,
193 lane corridors leaving the modeled road and 562 candidates outside the
owning source polygon. These categories count roads or candidates separately
and must not be added as a unique-road total.

This is source-geometry screening, not a street survey, legal routing service or
safety certification. Source alignment and vertical datum remain unverified.
Unmapped parked cars, trees, signs and street furniture are not screened.
The 30 metre preview is an initial approach, not an autonomous route or proof
that a vehicle can cross the next intersection. Runtime collision sweeps and
road-height checks remain necessary.

Run with private input paths:

    python -m dynacity.engine_drive_routes --batches PRIVATE/city-batches.json --surface PRIVATE/shared-world.json --districts PRIVATE/beirut-districts.json --pack PRIVATE/beirut001-structures-pack.npz --traffic PRIVATE/beirut-traffic-structures.json --output PRIVATE/drive-starts.json

Five focused tests cover axis conversion, road elevation, lane placement,
reverse one-way headings, source identity, obstacles, unknown ground, steep
grades and strict area coverage. Source data and generated candidate files
remain private.
