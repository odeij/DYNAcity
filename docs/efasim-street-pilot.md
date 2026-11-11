# Beirut street and mobility integration pilot

The implementation combines the shared SUMO street model with finished licensed
materials, source-bound facade modules and the existing finished street assets.
The visual target follows the environment-building approach described by EfaSim:
modular architecture, SUMO-aligned roads, finished vehicles, street context and
interpolated traffic. It preserves the existing Beirut source geometry.

## Installed scope

- Asphalt and concrete PBR materials use the cached CC0 ambientCG maps, including
  colour, roughness and normal textures. Overrides cover existing Medouar,
  Rmeil and Achrafieh road/paving batches, not every Beirut district.
- The Medouar paint pilot contains 59 illustrative features and 8,056 imported
  triangles. The Charles Malek extension adds 15 features in two tiles, with
  678 and 267 imported triangles. Paint is clipped and projected onto the actual
  asphalt triangles at a 2 cm lift. Unsupported surfaces and precision slivers
  are reported rather than silently filled.
- Existing Charles Malek/Al-Hikma detail contains 1,396 finished curb/sidewalk
  instances. The existing reference street also contains nine finished lamps,
  five benches and five bins. Their presence does not establish surveyed
  hardware or complete sidewalk continuity.
- Two facade scopes add 1,200 authored Poly Haven wall/window modules. They
  retain source building IDs, source geometry and collision. The second scope
  excludes every first-scope ID. Roofs, balconies and photographic Beirut
  facade reconstruction are outside this pilot.
- The local SUMO backend is available on port 8014. Native Play verification
  received five batches, rendered 150 instances, advanced to 9.221 simulation
  seconds and recorded changing native transforms plus 1,713 moving trajectory
  pairs. The sampled fleet had 70 BMW, 70 Corolla and 10 van instances. The bus
  group existed but had no displayed instances in this check.

The runtime already uses pooled native instances, position/heading interpolation,
stable fleet assignment and distance/count caps. These are rendering mechanisms;
traffic is synthetic and has no player collision. A brief buffer exhaustion was
observed. No uninterrupted real-time rate, 60 FPS or traffic calibration is
claimed.

## Repeating and expanding

`street_twin_finish.py` derives illustrative markings from actual SUMO lanes.
`street_twin_drape.py` validates coordinate references, clips each paint polygon
against the current asphalt triangles and records source hashes and omissions.
`scripts/verify_street_twin_import.py` checks imported triangle count and signed
centimetre bounds before installation. `install_efasim_street_finish.py` applies
only the pilot material/paint changes and saves the affected actor packages.

`building_facade_modules.py` places whole authored modules on source walls.
`install_facade_modules.py` validates the source/index/module hashes, groups by
tile and variant, supports independent manifest scopes and rejects duplicate
source placement IDs. Facade additions have a 180 m distance cull and explicit
instance budgets. Source wall geometry and the library appearance remain
separate evidence; window arrangement is illustrative.

Increase coverage by adding explicit district/tile scopes, retaining source
identities and missing-elevation reports. Road 3186 was omitted from the new
SUMO paint extension because its source elevation is unknown. Existing street
layout reports still contain disconnected sidewalk and branch-junction review
items; the pilot must not be presented as a completed citywide street network.

## Verification and persistence

Private reports live under
`C:/private/dynacity-data/engine/street-twin-20261029/` and
`C:/private/dynacity-data/engine/facade-modules-20261029/`.
`mobility/native-verification.json` records actual native components, advancing
frames, movement and owned-session cleanup. API-only evidence is retained
separately. `cold-load-verification.json` records verification after a clean
editor process restart; inspect its status rather than treating a saved file or
an in-memory unload request as a completed reload check.

The completed cold-load check passed in editor process 62272, after process 5820
exited cleanly. It verified 19 saved source/paint/facade actors, all 1,200 facade
instances, 1,396 curb/sidewalk IDs and transforms, and disabled collision on both
captured visual copies. Source building signatures remained unchanged.

An integration failure exposed mesh-default collision on the Ac_726 visual
district copy. `captured_buildings.py` now disables default, component and actor
collision on visual copies. The original hidden building batch remains the
collision authority. Native wheel support passed after the scoped repair;
the ground safety thresholds were retained.

## How I did it

Used focused category agents for geometry, architecture and traffic, with one
serialized Unreal editor owner. Checked real imports and scoped package saves,
compared source geometry/collision, exercised actual native traffic and captured
the same street camera before and after. Export tests and native readbacks cover
sloping and layered surfaces, coordinate mismatches, small slivers, authored
module orientation, source IDs, instance caps and persistent visual collision.
