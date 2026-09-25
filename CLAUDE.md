# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

DynaCITY Task 5.1 forecasting engine — Beirut urban-form forecasting from BBED (Beirut Built Environment Database) building-status transitions, optionally enriched with AUB point-cloud morphology. Python package `dynacity-forecasting`, source in `src/dynacity/`. Backcasting and reinforcement learning are deliberately out of scope on this branch.

## Commands

Setup (lightweight, tested path):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```
Setup with PDAL/GDAL for real LAS/COPC processing:
```powershell
conda env create -f environment.yml
conda activate dynacity-forecasting
```

Tests (synthetic LAS/BBED fixtures in `tests/conftest.py` — no real data is downloaded or redistributed):
```powershell
pytest
python -m pytest tests/test_bbed_panel.py::test_panel_includes_direct_2018_to_2024_analysis_interval  # single test
```

There is no lint/typecheck command configured in `pyproject.toml`.

CLI entry point `dynacity` (`src/dynacity/cli.py`, registered via `[project.scripts]`) chains the pipeline:
- `dynacity inspect-las <path>` — read LAS header without loading the point cloud
- `dynacity build-copc <in> <out> --dry-run` then without `--dry-run` — LAS → COPC
- `dynacity fetch-bbed --output <path> [--bbox x0 y0 x1 y1]` — pulls only allowlisted `bbed.BBED_FIELDS`
- `dynacity extract-features --las --bbed --output` — point cloud → BBED-building morphology CSV
- `dynacity build-panel --bbed --output [--lidar-features --lidar-year]` — builds the leakage-safe transition panel
- `dynacity summarize-transitions --panel --output [--start-year --target-year]` — descriptive interval summary (default 2018→2024; excluded from the model benchmark)
- `dynacity build-snapshot --bbed --snapshot-id --data-version --output`
- `dynacity train --panel --output` — runs `temporal_benchmark`, writes the selected `ModelBundle` + a sibling `.metrics.json`
- `dynacity compile-scenario --snapshot --text|--text-file --output [--request-output] [--provider anthropic|gemini --llm-model]` — planner prose → LLM draft → deterministic check (`scenario_compiler.check_draft`) → `ScenarioSpec`; providers in `providers.py` (`llm` extra + `ANTHROPIC_API_KEY`, or `gemini` extra + `GEMINI_API_KEY`)
- `dynacity freeze-evidence --registry --as-of --output` — curated source registry → content-hashed `evidence.EvidenceBundle`; `compile-scenario --evidence` lets levers cite it
- `dynacity forecast --model --request --output`
- `dynacity plan --model --request --output` — `planning.plan`: backcast (least-intensive lever combination meeting KPI targets) + Pareto front, by enumerating/sampling lever settings through `ForecastEngine`
- `dynacity export-viewer --bbed --snapshot [--forecast] --output` — self-contained deck.gl 3D page (`viewer.py` + `viewer_template.html`)
- `dynacity serve --model [--snapshot --bbed --forecast --evidence] [--host --port]` — FastAPI app via uvicorn; with `--snapshot --bbed` it also serves the live 3D viewer at `/` and `/v1/viewer/scenario`

## Private data boundary

Raw LAS/COPC files, BBED downloads, features, trained models, and forecast artifacts are gitignored (`data/`, `artifacts/`, `models/`, `*.las`, `*.copc.laz`, `*.joblib`, …) and must live outside the repo:
```powershell
$env:DYNACITY_DATA_ROOT='C:\private\dynacity-data'
$env:DYNACITY_ARTIFACT_ROOT='C:\private\dynacity-artifacts'
```
`config.Settings.validate_private_roots` enforces these roots are not inside the repository. Only fields in `bbed.BBED_FIELDS` are ever fetched (no names/contacts/owner info); BBED outputs retain ODbL attribution metadata.

## Architecture

Data flow: allowlisted BBED snapshot → canonical temporal panel → temporal benchmark → selected transition model → stochastic state rollouts → KPI distributions. Optional point-cloud path runs alongside: LAS header validation → explicit `EPSG:32636` assignment (LAS headers don't embed a CRS; this was established externally via BBED alignment) → COPC conversion → BBED polygon join → morphology table → 2022→2024-only model ablation. The point clouds are photogrammetric (Agisoft Metashape, exported to `.las` via PDAL) rather than laser-scanned LiDAR — confirmed by constant-zero intensity and single-return points in the source files — but the `lidar_*` naming in code/CLI is kept for continuity with the existing panel/API contract. They were acquired after the 2020 port blast, so this modality is forbidden in 2018→2022 training rows — enforced by `panel.assert_no_temporal_leakage`.

**Core model**: buildings move between 8 canonical states (`status.CanonicalState`, mapped from raw BBED labels via `canonicalize_status`) as a discrete-time transition model. `engine.ForecastEngine` repeatedly applies the one-step transition model and Monte Carlo-samples (`uncertainty.py`) to produce p05/p50/p95 KPI bands (`kpis.py`) over 2/4/6-year horizons — uncertainty compounds with each additional step.

**Time-forward validation is load-bearing, not incidental** — it's the repo's stated engineering contribution. `panel.PanelBuilder` builds three intervals: `(2018, 2022)` and `(2022, 2024)` for modeling, plus `(2018, 2024)` as a descriptive-only interval (summarized via `transitions.summarize_interval`, exposed through `summarize-transitions`). `models.temporal_benchmark` filters by *both* `start_year` and `target_year` so the descriptive interval can never leak into train/test. Model selection is a gate: the learned histogram-gradient-boosting candidate replaces the no-change/Markov baselines only if it beats both on macro-F1 *and* multiclass Brier score; otherwise the strongest baseline ships and the negative result is retained rather than hidden. `graph.py` holds an optional, torch-gated GraphSAGE research candidate (install via the `graph` extra) that is not currently wired into `temporal_benchmark`'s candidate set.

**Shared contracts** (`contracts.py`, pydantic models with `extra="forbid"`) are the JSON schema used identically by the CLI, `ForecastEngine`, and the FastAPI `service.py`: `ForecastRequest` (a `UrbanStateSnapshot` + `ScenarioSpec` with `TransitionAdjustment` interventions) in, `ForecastResult` (first-step transitions + KPI bands + `out_of_distribution_score` + warnings) out. Scenario interventions shift target-state log-odds for explicit objects/states and require an `effect_source` + `confidence_grade`; this is sensitivity analysis, not a causal estimator — `ForecastResult.evidence_level` distinguishes `empirical_bau` from `assumption_based_scenario` accordingly.

**Object identity**: each object gets a composite ID from `BULBuildingID` + ArcGIS `OBJECTID` (`bbed.resolved_object_ids`), preserving duplicate building IDs and keeping point-cloud features joinable to BBED subsets extracted from a spatial bounding box.

**Scenario front end** (all optional, all downstream of the engine): `scenario_compiler.py` has an LLM draft a `ScenarioDraft` that deterministic code checks and resolves (scope, effect size from `EFFECT_STRENGTH_LOG_ODDS`, provenance quoted/cited/unsourced) — the model never sets numbers or scope. `evidence.py` freezes citable sources. `planning.py` searches lever combinations for backcasts and Pareto fronts. `viewer.py` renders the 3D page; it must never embed per-object `features`. Only aggregate allowlisted BBED attributes are sent to the LLM.

Full design detail (status taxonomy table, evaluation gate, KPI list, known limitations) lives in `docs/forecasting-engine.md`.
