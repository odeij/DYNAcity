# Flood-planning implementation workstreams

Requested 16 October 2026: separate chats to implement terrain, rainfall, drainage/runoff, surface-water modelling, validation, a neighborhood pilot, and budget-based gamification.

## Shared context

The present flood tool is an illustrative water-level/exposure model. It uses a filtered Copernicus GLO-30 surface raster, and it does not solve rainfall-runoff, drainage or hydraulic flow. The running snapshot has 3,349 objects and no loaded point-cloud features. Existing forecasts and traffic must remain usable. Private data and simulation artifacts belong outside Git. No new work may be described as calibrated for Beirut without observed-data validation.

The checkout already contains user-authorized traffic lane-alignment and playback changes. Preserve them. Follow AGENTS.md; use proportional sans-serif fonts, never Courier/typewriter-style UI labels. Do not commit, push, deploy, buy datasets or start paid services unless separately authorized.

## Ownership to avoid concurrent edits

- Terrain chat: `src/dynacity/flood_terrain.py`, `tests/test_flood_terrain.py`, `docs/flood/terrain.md`.
- Rainfall chat: `src/dynacity/flood_rainfall.py`, `tests/test_flood_rainfall.py`, `docs/flood/rainfall.md`.
- Drainage/runoff chat: `src/dynacity/flood_drainage.py`, `tests/test_flood_drainage.py`, `docs/flood/drainage.md`.
- Surface-water chat: `src/dynacity/flood_surface.py`, `tests/test_flood_surface.py`, `docs/flood/surface.md`.
- Validation chat: `src/dynacity/flood_validation.py`, `tests/test_flood_validation.py`, `docs/flood/validation.md`.
- Gamification chat: `src/dynacity/flood_planning.py`, `tests/test_flood_planning.py`, `docs/flood/gamification.md`.
- Pilot/integration chat: shared existing API/CLI/viewer/contracts/configuration files, its new pilot module/tests, `docs/flood/pilot.md`, and dependency declarations only if required. Integrate producer modules after their handoff documents state they are ready. Do not modify producer-owned files concurrently.

Producers should implement tested, callable components rather than plans alone. Document the exact public interface, units, CRS/vertical datum/time conventions, evidence limitations, test command/results and integration example in the owned handoff document. Finish with an explicit `Integration status: ready` only when the component and tests are ready. Missing local observations/runtime may prevent empirical validation, but should not stop useful adapter, ingestion and fixture-test work.

## Integration conventions

- Separate user-specified rainfall from model-computed water depths. Include durations/time steps and explicit units.
- Terrain must declare CRS, vertical datum, resolution, nodata and provenance. Resampling cannot create source detail.
- Preserve the difference between measured, estimated, synthetic and missing inputs. Do not invent drains, ground surveys, historical losses, rainfall return periods, cost estimates, or causal policy effects.
- Flood outputs should expose timestamped spatial depth, optional velocity, validity/nodata, model identity/provenance and warnings. Do not silently treat missing velocity as zero.
- Keep the same forcing and baseline inputs when comparing interventions. Budget costs are supplied assumptions, not established market quotations.
- Prefer established model engines/adapters and documented numerical checks. An unavailable solver must fail clearly or accept explicitly labelled precomputed model output; never substitute the old visual flood and call it hydraulics.
- Start with a bounded pilot extent, not a citywide performance or safety claim. Exposure/access metrics should disclose their coverage and thresholds. Do not infer population from building count.

## Dependencies

Terrain and rainfall feed runoff/drainage and surface modelling. Validation evaluates those outputs against independent observations when available. Planning compares feasible interventions using the same model interface; it must not award flood-reduction scores from arbitrary visual depth changes. The pilot owns final integration and should advance independently with ingestion, interface wiring and honest unavailable states while waiting for producer handoffs.
