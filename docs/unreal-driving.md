# Beirut exploration driving

Status on 25 October 2026: the runtime plugin is **compiled, activated and tested
in Unreal 5.8** in the private Beirut project. Microsoft Build Tools 2022
17.14.37824.6 with the MSVC 14.44 component is installed at
`C:/BuildTools/DynaCity`; the installer reports complete with no reboot required.
Unreal accepted this compiler and built the plugin successfully. Compiler
safety checks have not been bypassed.

The plugin source is in `engine/unreal/Plugins/DynaCityDrive`. It implements a
single-player exploration pawn, source start bookmarks, game mode and native
HUD. The sedan is a visible child of a swept box chassis; four downward probes
follow the loaded surface. Destination overlap checks protect turning poses,
since Unreal translation sweeps do not sweep rotation. Gaps, excessive steps,
steep surfaces and blocked poses stop movement. Initial placement waits for
streamed collision and rejects source-height disagreement above 35 cm.

Movement uses bicycle steering, acceleration, coasting, braking and reversal,
with 50 km/h forward and 20 km/h reverse limits. Bounded substeps protect slow
frames. This is an arcade exploration model, not a calibrated drivetrain or
tyre/suspension simulation. An opt-in editor-hosted SUMO connection now renders
synthetic traffic while driving; it does not interact with player physics.
See [mobility preview and coverage](unreal-mobility-preview.md). Multiplayer is absent.

Controls in Play: W/S or arrows for acceleration/reverse, A/D or arrows
to steer, Space to brake, R to reset, C for chase/hood/high/cockpit camera,
L for day/night, N to jump
to the next source area, right mouse to look, Tab for cursor and Esc to end
editor Play. Open Tools > Explore Beirut > Start driving, or use Play in the
configured city map, then click the game viewport. Keyboard focus itself is
not covered by the automated MCP checks.

## Source starts and collision

[Drive starts](unreal-drive-starts.md) use source roads inside the 12 named
areas. The private export contains 12 screened starts and 30 m approaches.
`setup_driving.check_candidates(filename)` additionally tests the actual loaded
Unreal collision: four wheel supports, road identity, surface-height agreement
and clearance for the218×90×55cm chassis half-extents, aligned to the surface.

Initial native checks passed only 7/12 starts. The source GLB audit showed road
triangles beneath all 48 wheel positions, with terrain11–15cm below asphalt.
Unreal's automatic Nanite fallback had removed thin road geometry and displaced
terrain collision. Exact fallback now explicitly retains 100% of triangles;
setting zero error while leaving fallback target Auto is insufficient.
Physics data is invalidated/rebuilt and the saved map reloaded to activate the
updated collision. All 12 starts then passed actual wheel and chassis checks.

`city_collision.configure_and_save()` performs that sequence. Stop recorded
editor replay and end Play first. Low-level `configure()` reports
`requires_saved_map_reload` when edits need saving/reloading. Source geometry
stays unchanged; only Unreal collision/fallback settings change. Detailed
collision uses more memory and navigation-export geometry than simplified
proxies. Runtime performance remains unmeasured.

## Build and activate

After a supported toolchain is installed, compile to a private host project:

    .venv/Scripts/python.exe scripts/build_unreal_drive.py --engine "UNREAL_INSTALLATION" --output "PRIVATE/drive-build"

The script preserves Unreal's compiler checks and does not change the running
project. A successful build reports the staged plugin folder and DLL. Copy the
staged plugin into the private project's `Plugins/DynaCityDrive`, enable it in
the project descriptor, save the current map, and restart Unreal. The source
plugin is disabled by default so an unbuilt module does not prevent opening
existing scenes.

### Build cache retention

By default, `build_unreal_drive.py` removes the generated host and plugin
`Intermediate` directories after a build returns, including a failed compilation.
Source snapshots, compiled DLLs, `build.log`, and `build-result.json` remain;
`cache-cleanup.json` records deleted paths and logical byte counts. Builds still
use source-hashed hosts, but no longer retain their large compiler caches.
The next build recompiles these files. Use `--keep-intermediates` only when
deliberately retaining an incremental cache; interrupted builds also retain
caches for later guarded cleanup. A per-output lock rejects concurrent builds
or cleanup. A lock left by a forcibly terminated process must only be removed
after confirming that process and its build children have exited.

For accumulated caches across the nine historical build output directories:

    python scripts/unreal_build_cache.py --engine-root C:/private/dynacity-data/engine --report C:/private/dynacity-data/engine/build-cache-cleanup/dry-run.json

Review the report, then repeat with `--apply` and a separate report filename.
The command validates generated host descriptors, rejects links/junctions and
paths outside each build output, and refuses cleanup while compiler/build
processes or temporary host editors are running. The main city editor can stay
open because its project is outside the allowlisted build outputs. Do not start
external builds during manual cleanup. It preserves `Content`, `Binaries`,
source files, logs, city projects, imagery, and backups. This policy does not
prune those retained files or caches in arbitrary output directories; custom
outputs still receive the builder's default post-build cleanup.

In Unreal Python, with the city map open:

    import setup_driving
    setup_driving.prepare(r"PRIVATE/drive-starts.json")
    setup_driving.play()

Preparation validates the source origin and actual collision before changing
the map. It saves 12 always-loaded native start actors, each referencing the
Sedan mesh for asset cooking, and assigns the runtime game mode. Re-running
updates existing bookmarks without duplicating them. Defaults select Minet el
Hosn. Missing or incorrectly scaled vehicle assets fail preparation clearly.
On a cold map, loading external actors can finish before their physics state
is registered on the following editor tick. If all starts report missing wheel
support immediately after loading, let the editor update and retry preparation;
do not rebuild or simplify the source collision as a workaround.

`scripts/verify_unreal_drive.py --output PRIVATE/runtime-verification.json`
uses `driving_tools.DrivingTools` to exercise the actual compiled pawn, releases
external inputs and ends its own Play session even on failure. It refuses to
interrupt an existing Play session.

## Verification completed

- Five source-route tests passed: coordinates, source identity, direction,
  building clearance, missing ground and explicit unavailable areas.
- Standalone C++ checks compiled and passed for speed caps, braking without
  overshoot, reverse, steering direction, slopes, frame-rate subdivision and
  rejecting non-finite controls. They do not verify Unreal integration.
- Static spawn checks passed in the running editor at all 12 starts, after the
  collision rebuild and map reload.
- Unreal native build succeeded after installing the supported toolchain.
- Runtime checks passed for forward motion, braking to rest, steering, reverse,
  reset to the source start, three camera states, and surface readiness in all
  12 distinct named areas. Proof is saved privately as
  `engine/drive-build/runtime-verification.json`.
- Editor images confirm the sedan and runtime HUD render. Street materials,
  facade detail and presentation remain prototype quality. Some views are
  obstructed by nearby geometry; passing movement checks does not establish
  visual quality, accurate present-day streets, or seamless citywide driving.
- Physical keyboard focus, long continuous district crossings, frame-rate
  profiling, and live traffic interaction remain outside these runtime checks.
