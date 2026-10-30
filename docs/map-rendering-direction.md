# Map rendering direction

The browser map remains the planning interface. A game-like experience (free
exploration, camera presets, mobility challenges and comparing interventions)
can be developed without changing the simulation or renderer.

An engine migration is not a performance fix by itself. Measure separately:
SUMO simulation seconds per wall-clock second, response size and latency,
browser frame times, geometry upload cost, memory and scene startup time.
Keep authoritative SUMO positions and the same scenario API in either client.

For a future immersive desktop client, Unreal Engine with Cesium is a useful
candidate for georeferencing, streamed scene detail, lighting and walking or
driving interaction. It needs an explicit asset pipeline, coordinate conversion,
object identity, picking and analytics UI, packaging and suitable graphics hardware.
Missing mapped detail still needs evidence or explicit illustrative treatment.

Do not replace the current application before a one-block prototype reproduces
the same bridge, signal, vehicle and building identities and shows better frame
times at equal visual quality. Compare browser and engine builds on the target
machine with equal traffic counts, camera paths and scene content. A native
engine client and a browser dashboard can share the same backend.

References checked 24 October 2026:

- [deck.gl performance](https://deck.gl/docs/developer-guide/performance):
  retain stable data arrays, avoid unnecessary buffer regeneration and bound detail.
- [Cesium for Unreal](https://cesium.com/learn/unreal/): geospatial scenes and
  interactive experiences in Unreal.
- [Epic hardware requirements](https://dev.epicgames.com/documentation/en-us/unreal-engine/hardware-and-software-specifications-for-unreal-engine):
  assess target hardware before selecting advanced rendering features.

No engine migration or game mechanics are enabled by the six realism improvements.

The subsequent [neighbourhood prototype](unreal-neighbourhood-prototype.md)
prepares private GLB assets and recorded SUMO frames. Unreal 5.8.4 has imported,
saved and reopened the 629-building scene; its built-in MCP runs the editor
replay and verifies source scale. Lighting refinement, packaged runtime
integration and comparative performance remain separate verification steps.
