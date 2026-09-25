# Forecasting Engine Design

For the decision-by-decision engineering rationale, including the canonical-state ontology and rejected alternatives, see [Implementation Rationale](implementation-rationale.md). For how this engine maps onto the funded Task 5.1/5.2 module breakdown, including which modules are not yet implemented, see [Task 5 AI Module Specification](task-5-module-specification.md).

## Supported question

Given a versioned Beirut building-state snapshot, what distribution of urban states and aggregate urban-form KPIs is expected after 2, 4, or 6 years?

The empirical model is observational. A business-as-usual result is not a causal prediction. A scenario containing interventions is explicitly labeled `assumption_based_scenario` and carries a warning.

## Data flow

`allowlisted BBED snapshot → canonical temporal panel → temporal benchmark → selected transition model → stochastic state rollouts → KPI distributions`

The panel also contains a direct 2018→2024 interval for descriptive status-transition reporting. It is kept out of the time-forward model benchmark.

The optional point-cloud path is:

`LAS header validation → explicit EPSG:32636 assignment → COPC conversion → BBED polygon join → morphology table → 2022→2024-only model ablation`

The LAS headers do not embed a CRS. EPSG:32636 was established externally through BBED alignment. The files were acquired after the 2020 Beirut port blast, so those features are forbidden in the 2018→2022 training rows.

The point clouds are photogrammetric reconstructions (Agisoft Metashape, exported to `.las` via PDAL), not laser-scanned LiDAR — confirmed by constant-zero intensity and single-return points in the source files. `.las`/`lidar_*` naming in the code and CLI is retained for continuity with the existing panel/API contract; it denotes "2020 point-cloud modality," not the sensing method. This does not change the leakage argument: the modality is still forbidden in pre-2020 training rows regardless of how it was captured.

## Canonical state taxonomy

| Canonical state | BBED examples |
|---|---|
| `stable_built` | Complete Residential, Complete Building, Non-Residential, Old-Bldg-Inhabited |
| `active_construction` | Under Construction, Construction Site |
| `stalled_or_cancelled` | Construction on-Hold, Cancelled Construction |
| `renovated` | Renovated |
| `vacant_or_evicted` | Evicted Building, Old threat of Eviction, Old-Bldg-Uninhabited |
| `empty_or_parking` | Empty Lot, Parking Lot |
| `demolished` | Demolished |
| `unknown` | Not Available or an unmapped label |

Raw status values remain in the panel for auditability. Building use remains a separate predictor; it is not erased by the collapsed transition taxonomy.

## Model gate

The primary evaluation is time-forward:

- Train: 2018→2022.
- Test: 2022→2024.
- Descriptive only: direct 2018→2024 observations.
- Baselines: no-change and smoothed empirical Markov transitions.
- Learned candidate: histogram gradient boosting.
- Research candidate: optional GraphSAGE implementation in `dynacity.graph`.

The learned candidate is selected only when it improves both macro-F1 and multiclass Brier score over both baselines. Otherwise, the strongest baseline is the production artifact and the negative result is retained.

After the time-forward selection decision, the chosen production model is refitted on both adjacent observed intervals. This gives the deployed model the exact two-year 2022→2024 transition evidence used by the forecast engine while keeping the reported holdout metrics honest.

Point-cloud value is measured separately through spatially grouped 2022→2024 ablations. It must not be mixed into the primary historical benchmark.

## KPI output

Every two-year rollout step reports p05, p50, and p95 for:

- Completed, active, stalled, vacant, demolished, and empty/parking quantities.
- Estimated gross floor area.
- Median and p90 building height.
- Built land fraction.

Longer horizons repeatedly apply an observed transition model, so uncertainty compounds. The service warns on 4- and 6-year forecasts.

## Scenario adjustment

An intervention adjusts target-state log odds for explicit objects and eligible starting states. Every adjustment requires an effect source and confidence grade. This mechanism exists for sensitivity analysis and future simulator integration; it is not a causal estimator.

### Compiling scenarios from planner prose

`dynacity compile-scenario` (and `POST /v1/scenarios/compile`) turns a written scenario into a `ScenarioSpec` via `scenario_compiler.py`. A language model only drafts; deterministic code decides. The model provider is swappable (`providers.py`): Anthropic Claude (`llm` extra, `ANTHROPIC_API_KEY`, default `claude-opus-5`) or Google Gemini (`gemini` extra, `GEMINI_API_KEY`, default `gemini-3.8-flash`), chosen with `--provider`/`--llm-model`, `DYNACITY_LLM_PROVIDER`, or whichever key is set. Both return JSON validated against the same `ScenarioDraft` schema and go through the same checker:

- **Scope** — the model writes a selector (sectors, building uses, explicit ids) that code resolves against the snapshot. Unknown names are rejected with the valid choices listed.
- **Effect size** — the model picks `weak`/`moderate`/`strong`; the log-odds delta comes from `EFFECT_STRENGTH_LOG_ODDS` (0.5/1.0/2.0). A numeric delta is accepted only if the planner wrote that number.
- **Provenance** — `effect_source` must be quoted from the planner's text or be `unsourced planner assumption` (confidence then forced to `low`). A source the model introduced is rejected as an invented citation.

- **Scope limits** — a lever with no spatial filter is rejected unless the request explicitly asks for the whole city ("across Beirut", "toute la ville", "كل بيروت", …); a building-use filter that narrows the eligible set is flagged with the count it excludes.
- **Citations** — a registered source may be cited only if the request names its title or one of its `aliases`.

The scope and citation rules came from live testing on real BBED with Gemini: asked about a place not in the data (Karantina), a model dropped the place and applied the lever to all 3,144 eligible buildings; asked about grants in Medawar, it attached Law 194 although the planner never mentioned it. Both drafts passed the earlier checks and are now hard failures. An overloaded or unreachable model (Gemini returned 503 "high demand" often during testing) yields a `model.unavailable` issue after the SDK's retries and a fallback model chain, never a crash, and each attempt records the model that actually answered.

Hard issues trigger one repair round with the issues fed back; every attempt is kept in the output report. Requests the engine cannot express (traffic, rents, population) are listed as dropped rather than approximated. Only aggregate, allowlisted BBED attributes are sent to the model — no per-object features or point-cloud morphology. Compiled scenarios still forecast as `assumption_based_scenario`.

### Evidence bundles

`evidence.py` holds a registry of citable sources (laws, decrees, reports) curated by people, frozen per snapshot date into an `EvidenceBundle` whose id is a hash of its content (`dynacity freeze-evidence`). A compiled lever may cite a source by id; the checker rejects unknown, superseded, withdrawn, draft, not-yet-effective, and expired sources, naming the replacement when one exists. Unverified sources are allowed but cap the lever at `low` confidence. The resulting `effect_source` is `evidence:<source_id>@<bundle_id>`, and `ScenarioSpec`/`ForecastResult` carry `evidence_bundle_id`, so a forecast identifies the exact text it was conditioned on. `examples/evidence_registry.example.json` is a template: its entry is unverified until someone fills it from the primary document.

### Backcasting and trade-offs

`dynacity plan` (and `POST /v1/plan`) runs the engine over combinations of candidate levers — each tried off and at each allowed log-odds strength and activation step — and reports:

- **backcast** — the least-intensive combinations (Σ |Δ| × buildings in scope) meeting every KPI target, or the closest ones with a warning when none does;
- **Pareto front** — combinations not beaten on every objective at once (KPI objectives at p05/p50/p95, plus intensity by default).

Search is exhaustive up to `max_evaluations` (default 128), otherwise a seeded sample that always includes business-as-usual and every single-lever setting. Every candidate reuses the request seed, so differences come from levers, not Monte Carlo noise. Answers inherit the levers' assumed effect sizes and are labelled `assumption_based_scenario`.

### 3D viewer

`dynacity export-viewer --bbed --snapshot [--forecast] --output` writes one self-contained HTML page (deck.gl from jsDelivr) that extrudes BBED footprints to recorded height (else floors × 3.2 m, else 6 m, flagged as estimated) with four lenses: observed state, most likely state in two years, chance of leaving the current state, and — after a scenario — the change versus baseline. A KPI strip shows p50 and the p05–p95 band per horizon. `dynacity serve --model --snapshot --bbed [--evidence]` serves the same page at `/` with the prompt bar live: prose goes to `/v1/viewer/scenario`, which compiles it against the server-side snapshot, runs the forecast, and returns the audit trail and result. The page embeds footprints, heights, states, sector, use, and model outputs only — never per-object `features` or point-cloud morphology — but it is still a data artifact: write it under `DYNACITY_ARTIFACT_ROOT`. The state colours are a categorical set checked for colour-vision deficiency in legend order.

Footprints are converted from UTM to longitude/latitude (`viewer.utm_to_lonlat`, sub-millimetre against pyproj) and drawn over a CARTO/OpenStreetMap basemap (streets, sea, parks, sky and haze), with OpenStreetMap buildings extruded in pale white as context wherever BBED has no footprint. The camera opens on the densest cluster of surveyed buildings and flies to a scenario's affected buildings when one runs. The basemap requests tiles from CARTO, which reveals the viewed area to that server; `--basemap none` draws the buildings on a plain ground slab with no tile requests. In the Δ lens a building's value is the change in the probability of reaching the target state of the lever covering it, on a symmetric scale fitted to the scenario's largest change.

## Known limitations

- BBED contains few observation waves and substantial class imbalance.
- The 2020 port blast and Lebanon's crises introduce structural breaks.
- Point-cloud coverage is partial and its classifications are unassigned.
- BBED footprints, not watershed instances, remain authoritative at party walls.
- Heritage layers are not used as a v1 KPI because their vintages disagree.
- The present out-of-distribution score covers missing core morphology only; a learned density score is future work.

