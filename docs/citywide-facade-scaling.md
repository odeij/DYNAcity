# Citywide facade rollout

Status: batch pilot repair and citywide assignment staging implemented,
2026-10-30. District-scale native rollout remains pending.

The 16-building pilot now uses 2,906 fitted instances on 98 eligible walls;
native verification and cold restart passed. Small corner facets remain on
the existing base material. `scripts/prepare_citywide_appearance_plan.py`
staged all 17,397 building assignments across 336 tiles in 12.93 seconds,
keeping estimated palettes, historical facade observations and unapproved
roof candidates separate. Its 51-photo snapshot produced geometric view
candidates for 113 buildings; these are not confirmed matches. Citywide
assignments have not been imported. The remaining architecture and gates
below still apply.

## Verified starting point

The 12 `buildings-*.objects.json` district indices in the private city-batches
folder contain 17,397 distinct source IDs and no duplicate records. This is the
local source inventory, not a completeness audit of all buildings in Beirut.
The two original facade plans contained 1,200 placements on 16 distinct
buildings; their actors are now hidden and retained for rollback.

The current importer already groups by ownership tile and module using HISM.
The old pilot planner used camera filtering, fixed 3 m rows and a truncating
instance budget. The repair planner now fits complete eligible walls, owns
whole buildings per tile and defers whole buildings at its budget. Per-instance
editor calls and the bounded pilot scope remain limitations. Setting
`is_spatially_loaded` alone does not verify runtime streaming behavior.

## Representation

1. Give every eligible source wall a complete, lightweight base treatment using
   licensed facade materials and the retained geospatial building geometry.
   Use metric wall UVs or an orientation-aware projection; a window grid must
   fit the building height rather than repeat across a clipped upper floor.
   Retain source IDs, footprints, heights, roofs and original collision.
2. Add authored library windows, recesses and architectural pieces only where
   street-level screen size warrants geometry. Initial tuning hypothesis:
   detailed geometry within 80 m, simplified appearance from 80 to 250 m,
   district/block proxies beyond that. Measure these thresholds before release.
   The base treatment remains visible when detailed modules are culled.
3. Reuse a small material palette and licensed architectural kits. Stable
   source-ID-based variation avoids one material per building. Variation is
   illustrative unless supported by observations; it is not architectural truth.
4. Use ISM with Nanite-only content, or benchmark HISM for static conventional
   meshes with LODs. Keep both options measurable rather than assuming a winner.

## Generation and import

- Replace the camera selection with all eligible source walls and one stable
  ownership tile per building, initially 250 m. Keep tall/large-building bounds
  explicit and avoid duplicates across neighboring tiles.
- Plan a complete building before committing its pieces. A tile budget defers
  an entire building or chooses its lightweight tier; it never cuts a facade
  halfway through. Record every deferred or unsupported source ID and reason.
- Fit floor rows to actual wall height with bounded positive scaling and
  authored infill/edge pieces. Reject extreme distortion. The repaired planner
  and installer support bounded positive scales, and native verification
  checks planned scales. Small edge facets still need appropriate treatment.
- Compute plans outside Unreal in parallel district/tile jobs. Cache by source
  geometry, identity index, library, material and generator-version hashes.
  Emit a compact manifest per tile and a citywide summary; do not use one giant
  placement file. Unchanged tile jobs are skipped and interrupted jobs resume.
- Use one serialized editor writer. Import shared meshes/materials once, group
  by tile, mesh and material, and add transforms in bulk through a native batch
  interface after checking the existing component's capabilities. Save and
  checkpoint each completed tile. Avoid rebuilding a HISM tree for every piece.
- Replace only owned tile content with an explicit versioned replacement path.
  The pilot installer currently rejects existing scopes; safe incremental
  replacement must be implemented before reruns are called idempotent.
- Build and test World Partition/HLOD layers for the base and detail tiers.
  Verify actual load/unload and visual transitions along a driven route.

## Rollout gates

Repair the 16-building pilot, validate roughly 100 varied buildings, then one
whole district. Check short/tall/irregular buildings, corners, recessed glass,
roof edges, surface overlap and deterministic reruns. Proceed citywide only
after measuring generation/import time, package sizes, visible instances,
draw calls, GPU/frame time, VRAM and streaming hitches on the target machine.

Report denominators independently: inventoried buildings, eligible buildings,
base-treatment coverage, detailed-treatment coverage, deferred/rejected IDs,
and saved/reload-verified coverage. Check wall coverage by area and residual
height, not just module counts. Include saved source signatures and cold-load
checks. Keep SUMO network/demand independent of visual detail budgets.

## Primary references

- https://dev.epicgames.com/documentation/en-us/unreal-engine/instanced-static-mesh-component-in-unreal-engine
- https://dev.epicgames.com/documentation/en-us/unreal-engine/world-partition---hierarchical-level-of-detail-in-unreal-engine

No citywide runtime or processing-time estimate is established until the
district benchmark is completed.
