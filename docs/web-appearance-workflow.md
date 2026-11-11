# Beirut web appearance workflow

Started 8 November 2026 at the user's request. This workflow moves scheduled appearance work to the main browser app. It does not authorize Unreal editor work.

## Baseline and ownership

The initial local and fetched `origin/main` baseline is `6a00371`. Preserve the already shipped terrain/road grades, alignment, building-height priorities/confidence, Beirut colours/roof caps, sky/haze, night windows, sun shadows, source credits, mapped crossings, generic infrastructure and pedestrian paths. Recheck HEAD, origin and dirty files at every implementation handoff. Existing unrelated edits, previews and Unreal work belong to their current owners.

Canonical private checkpoint and worker evidence: `D:/private/dynacity-data/web-appearance-20261108/`. Source images and large assets stay private, with source/capture date/licence/attribution recorded. Do not expose private photos or credentials in the app.

## Work order

1. Detailed corridor buildings and optional facade assets: establish authoritative coordinates, source identities, vertical origin, orientation, bounds and lightweight browser integration before placement.
2. Facade texture matching: preserve source geometry; compare actual browser rendering with dated, licensed references. Checker UV rendering is preparation, not a stone appearance match.
3. New canopy evidence: show measured coverage and review candidates separately; do not promote candidate trees to confirmed placements.
4. Beirut-specific lamps/street furniture, followed by licensed lightweight people and vehicles, where rights and browser performance permit.
5. Matched source/candidate views and useful comparison controls. Refine typography, interaction and visual hierarchy within the existing design; use proportional sans-serif labels.

Select bounded additions after inspecting the current app. Do not redo the baseline or decorate away geographic errors. Preserve mapped buildings, roads and terrain; estimated detail must remain identifiable as estimated.

## Execution and acceptance

**User correction, 8 November 2026: agents implement; Codex only coordinates and checks their work.** Codex must not edit application code, create product stubs/tests/assets, repair worker output, or take over implementation after failed tools, turn caps or test failures. Return concrete defects to the responsible agent; preserve/checkpoint unfinished work if needed. Codex may dispatch workers, write briefs/coordination checkpoints/work-log records, read diffs/artifacts, run acceptance checks and inspect/capture the actual browser. Agents own all subsequent implementation, integration and fixes. This supersedes earlier wording assigning shared integration or fixes to Codex.

Use the claude-code-evidence-workflow skill. Prefer verified existing free OmniRoute routes. On 8 November the user explicitly authorized automatic native Claude Sonnet 5.5 at medium effort when OmniRoute runs out, without asking again. The observed first-party account is `tech@mun.org.lb` (Pro); authentication was checked, but native inference/quota and exact Sonnet 5.5 availability remain unverified. This direct authorization supersedes older OmniRoute-only instructions for this web workflow. No DeepSeek, other paid routes, billing changes, top-ups or overages. Verify zero pricing, actual returned model and real tool compatibility before OmniRoute launch; preserve healthy in-flight work and record session/PID/model/effort. Audit the configured compatible free pool before declaring exhaustion; save checkpoint and route-transition evidence. At fallback verify exact Sonnet 5.5 and medium effort, use subscription authentication without inherited gateway credentials, and do not silently substitute a model. Return new work to usable free OmniRoute at a safe handoff; do not interrupt healthy workers.

Two workers maximum concurrently for this workflow, plus Codex as coordinator and acceptance reviewer. No nested agents. Give each worker explicit, disjoint ownership and a concrete briefing. Subsequent workers implement bounded deliverables with owned files or isolated worktrees, including any shared integration assigned to one explicit owner. Codex owns work-log records and final acceptance, with no product implementation. Do not relaunch work from stale running flags.

For visible changes, inspect the actual running app with matching before/after camera and viewport captures. Check desktop/mobile layout, keyboard/control usability, loading errors, resource cost and affected interactions. Inspect street and oblique views for floating/sunken assets, clipping, overlap, stretched crossings, missing road segments and placement discontinuities. State exact inspected coverage. Compare geographic/appearance accuracy with independent source evidence; screenshots and passing tests alone cannot establish surveyed or citywide accuracy. Require focused functional tests and binary/source/coordinate checks where material. Unavailable browser checks remain unverified.

Keep `docs/beirut-simulation-work-log.md` current and regenerate its visual page with `scripts/update_simulation_work_page.py` after meaningful changes. Save a concise checkpoint at each handoff with actual completed work, verification, failures, active sessions, next owned files and next checks. Continue across scoped iteration checkpoints until the user stops or redirects the workflow. Report meaningful progress, blockers and decisions; avoid unchanged notifications. Do not commit, push or deploy merely because a worker finishes; follow current explicit authorization and repository workflow.

## First local implementation: canopy evidence

The viewer now accepts an optional, default-off canopy review layer. For `serve` or `export-viewer`, add both arguments to the existing command:

```powershell
--canopy-review D:/private/dynacity-data/engine/beirut-tree-inventory-20261108/corridor-canopy-candidates.geojson `
--canopy-screening D:/private/dynacity-data/engine/beirut-tree-inventory-20261108/corridor-native-preflight-queue.json
```

Use **More tools → Canopy evidence**. Amber review rings identify candidates awaiting ground/clearance checks; red rings identify held candidates. Select a location and use **Inspect location** to move the camera, or **Hide evidence** to remove the review overlay. The rings have a fixed display radius; they do not represent measured crown extents. Existing mapped trees are unchanged. Source credits and unknown capture date remain visible in the evidence panel and Sources panel.

`canopy_review.py` verifies the exact candidate file hash, unique identities, complete/disjoint screening coverage, finite coordinates/heights and conflict consistency. Only allowlisted geographic review fields reach the page; private local XYZ/path fields are omitted. The retained pilot has 38 raw candidates: 37 pending and one held for a pedestrian-geometry conflict.

Local implementation and functional/source checks passed on 8 November. Browser access recovered after an initial service startup failure. The actual app was checked at 1280×900 and 390×844, with matched before/after captures, pending/held marker states, keyboard selection/inspection, hide/focus restoration, mobile panel scrolling and source credits. Two defects found in the browser were corrected: pale count labels on the light panel and focus returning to a button inside a collapsed menu. No captured browser warnings/errors remained. Scope is the corridor overview and one held-location oblique view; tree placement, clearance, all-city geometry, dark theme and measured performance are not validated. Private QA: `D:/private/dynacity-data/web-appearance-20261108/canopy-browser-qa.json`; captures and retention manifest are in its `browser-qa/` folder. No commit, push or deployment has been performed.
