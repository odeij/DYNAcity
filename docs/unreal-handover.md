# Unreal Beirut handover

Snapshot: 27 October 2026. Check the live working tree and private project
before acting; this is not a clean release or an asset backup.

## Source and private project

- The editable Unreal integration is under `engine/unreal/`; related Python
  exporters and verifiers are under `src/dynacity/`, `scripts/`, and `tests/`.
- The installed working project and generated captures, reports and large
  source datasets live under the private data root outside Git. The private
  project and source datasets must be preserved separately; repository code
  alone cannot reconstruct missing captures or licensed source assets.
- Source, licence and conversion hashes are tracked by the existing finished
  asset pipeline. Review them and the asset licences before redistribution.
- The city map is `/Game/Maps/BeirutCityBatched`; do not assume a loaded
  World Partition map has all actors resident. Refer to `unreal-city-world.md`
  and `unreal-terrain-loading-repair.md` for loading and validation.

## Current worktree and runtime

Runtime recheck on 27 October: no Unreal Editor process or mobility listeners on
ports 8014 and 8016 were running at the time of inspection. The saved city
traffic profile showed world tick intervals clustered around 333 ms even with
traffic disabled; those values cannot establish rendering performance or FPS.
The profiler now flags this pattern in each affected phase. A fresh controlled
editor measurement is still needed.

At the snapshot there are staged deletions, modified tracked files, and many
untracked source, documentation and test files. These represent mixed ongoing
work; review each change with `git status` and `git diff` (including staged
changes) before creating a commit. Do not reset or clean the checkout as a
shortcut. A filename/size inventory found no untracked files over 5 MiB and no
common private-key filenames; this is not a content-level secrets audit.

The SUMO backend now starts the comparison process when a disruption first
requires it. `tests/test_traffic_sumo.py` and `tests/test_mobility_inputs.py`
passed locally, including comparison and observed-signal checks. Backend-only
profiling and its limits are recorded in `unreal-mobility-preview.md`. A live
backend must be restarted to load changed Python source; an existing process
may still be running older code. Do not stop a process owned by another user
or session without coordinating first.

Current-asset Unreal performance, expanded driving coverage, post-recovery
barrier state and the photographic pilot still need live verification. Saved
reports record the barrier import and 38 visible instances before the later
editor startup problem. Existing
recovery packages may contain unsaved work: compare them with the saved city
and back up both before restoring or discarding anything.

The earlier `agent2-reload-verification.json` reports 217 visible bus-stop pole
instances and 12 visible traffic-island actors. A later realism-pass record
explicitly says the 11 pole actors were hidden, not deleted, because no
sufficiently realistic pole/sign model was found. Its subsequent verification
records them hidden. The later generic instance-count summary reports zero
island instances, but the islands are individual static mesh actors, so that
field does not establish that they are absent. Check current post-recovery state
before altering either, and keep source locations when replacing the poles.

For native C++ changes, Visual Studio Enterprise has MSVC `14.42.34433`, which
the installed Unreal 5.8 configuration bans. A separate Build Tools installation
at `C:/BuildTools/DynaCity` has compiler product version `14.44.35229`, outside
the banned ranges. An isolated host build of the current plugin succeeded on
27 October; its private result and log are under `engine/drive-build/drive-661cad237afefee4`.
This is a compile check, not live runtime verification. Do not bypass compiler
restrictions. The earlier editor process did not expose the project's MCP endpoint; no
live editor verification is claimed here.

The newest saved Unreal crash-context record inspected for this snapshot is an
`Ensure` concerning a missing Level Editor realtime override; it belongs to a
different process than the currently stalled editor. Its recorded available
physical memory was about 101 MiB. This is evidence of memory pressure during
that earlier event, not proof of the current startup problem's cause or of a
fatal crash. Preserve recovery packages while investigating.

The current `Saved/Autosaves/PackageRestoreData.json` has restoration enabled
and lists three Beirut material packages. The saved and autosaved copies and
the restore manifest were backed up under the private data root at
`engine/recovery-audit/20261027-173527/`; preserve that directory. The running
editor's process owns a hidden window titled `Restore Packages`, and its
control port's status call timed out. The title and timeout do not establish
which material version should be restored. Do not discard or overwrite a
recovery package without comparing the backed-up versions.

The repository's optional traffic fleet code can map SUMO type IDs to mesh
groups. The isolated build at `engine/drive-build/drive-a7769c4431226e9a/`
compiled, and its `DynaCity.Traffic.FleetLifecycle` automation passed with
test meshes. This is not installed or visually tested in the city project.
Mixed traffic is off by default; real vehicle models, compatible dimensions,
type mappings and city performance verification are still needed. See
`unreal-mobility-preview.md` for the configuration and its limits.
The private `traffic/marmikhael-mobility.json` validates as a mobility package
with eight vehicle types and a historical 2015 fleet mix. It can support a
labelled pilot, but its weights are not a current observation of Beirut, and
it does not provide a scheduled bus service. The separate OSM mobility package
has no fleet. On the latest check, neither configured local SUMO server port
8012 nor 8014 was listening; an editor cannot connect until an appropriate
server is started with the intended input package.

### Wall-clock profiler deployment (27 October 2026)

The wall-clock game-tick instrumentation compiled in the isolated host build
`engine/mobility-builds/drive-bcc2c34c5ab27432` and was copied into the private
city project. The previous plugin source and binaries were backed up at
`engine/recovery-audit/plugin-before-wall-clock-20261027`. The editor log shows
the new plugin DLL loaded; no Play or traffic profiling run has yet verified it.
A hidden editor startup left less than 0.5 GiB physical memory free and logged
large navigation collision exports, so foreground performance remains unmeasured.

A subsequent check while the same editor was idle found six navigation-export
warnings covering approximately 2.14 million triangles in total (largest warning:
428,534 triangles), with roughly 0.5 GiB of physical memory free. These are
collision-geometry export warnings, not proof of the memory use attributable to
navigation, and disabling navigation has not been tested. The SUMO backend and
this agent-started editor remained running; Play was not started under that
memory pressure.

The agent-started editor subsequently received a normal window-close request.
Unreal opened a `Save Content` dialog and remains running. No save or discard
was selected. A status request timed out while the dialog was open. Computer
Use could not connect its native pipe, so the dialog has not been dismissed.
Do not assume the pending content can safely be saved or discarded; cancel the
close request to continue editing, preserving the current in-memory state.

A read-only preservation inventory is saved privately at
`engine/recovery-audit/project-inventory-20261027.json`: it records per-extension
file counts and sizes for the installed Content, Config, Plugins and Autosaves,
and hashes the descriptor, engine configuration, driving DLL and restore
manifest. At that moment Content contained 16,800 files (about 1.65 GB), and
Autosaves contained 184 files (about 19 MB). This inventory is not a backup,
contains no per-asset content hashes, and may already be stale while the editor
is open. Preserve the actual project and recovery backups separately.

A live-editor on-disk snapshot was copied to
`engine/recovery-audit/project-saved-snapshot-20261027`. It includes project
root files, Content, Config, Plugins and Saved (including autosaves); it excludes
the project-level DerivedDataCache and Intermediate directories. The copy
checked 17,483 files (2,079,283,179 bytes) against source SHA-256 hashes and
reported zero copy errors, changes detected during copy, or hash mismatches.
The snapshot was taken with Unreal open and does not capture unsaved in-memory
changes or guarantee a single atomic project state. Its `snapshot-report.json`
records the check. Keep the separate existing recovery backups too.

A private repository snapshot is at `engine/recovery-audit/repository-snapshot-20261027`.
It records HEAD, binary-capable staged and unstaged patches, Git status and an
archive of all 260 non-ignored untracked files. Archive entries were compared
against their source hashes; no errors, mismatches or changes during collection
were detected, and Git status and patches were unchanged on recheck. It excludes
ignored files and is not an atomic snapshot or a Git commit. The private archive
may contain sensitive project data; review it before sharing. Preserve the Git
repository and ignored/private assets separately.
