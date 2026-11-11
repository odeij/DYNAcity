# Vehicle road alignment

Vehicles now follow the displayed bridge slope as well as its height. Pitch is
computed from the closest road segment and the authoritative vehicle heading,
so opposing traffic pitches in the opposite direction. Existing acceleration
and braking body movement is added separately and cannot accumulate on each
render. Ground roads reset the grade; internal junction positions outside road
geometry retain the last known grade, matching the existing height behavior.
The mesh centre is shifted behind the authoritative front bumper along the
displayed road plane, including the height change on ramps. Suspension pitch
does not alter this centre height or any authoritative position.

SUMO remains authoritative for vehicle identity, position, speed, heading,
spacing, class and signal indicators. No cosmetic lane offsets, extrapolated
movement, additional vehicles or assumed local fleet percentages are added.
Bridge heights and slopes remain illustrative where survey geometry is absent.
Traffic demand, assumed controls and routing retain their existing calibration
limits; this is a rendering improvement rather than behavioral validation.

Verification: `node --test tests/viewer_traffic_realism.test.cjs
tests/viewer_traffic.test.cjs tests/viewer_bridges.test.cjs`.
