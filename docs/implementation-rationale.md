# DynaCITY Task 5.1 — Implementation Rationale

This document explains the reasoning behind the forecasting implementation in this branch. It records not only what the code does, but why each boundary exists, which alternatives were rejected, and which parts remain experimental.

Backcasting and reinforcement learning are deliberately outside this branch. The supported question is narrower:

> Given a versioned snapshot of Beirut urban objects, what probability distribution over future urban states and aggregate urban-form KPIs should be expected after 2, 4, or 6 years?

The answer is observational and probabilistic. It is not a causal recommendation.

## 1. Design principles

The implementation follows six principles.

1. **Forecast real urban change, not survey vocabulary drift.** Raw BBED labels must be normalized before transitions are counted or modeled.
2. **Only use information that existed at the forecast origin.** Dates and point-cloud features are gated by acquisition time.
3. **Make the simplest credible model difficult to beat.** Learned models must improve on persistence and Markov baselines on later-time evidence.
4. **Preserve uncertainty through the entire forecast.** The engine returns probabilities and KPI distributions, not only winning classes.
5. **Keep every forecast auditable.** Object identity, source manifest, data version, model version, evidence level, and warnings travel with the output.
6. **Separate implemented evidence from research ideas.** Graph models, stronger calibration, and causal intervention effects are not presented as production capabilities until they pass their own evidence gates.

## 2. Why this is a state-transition problem

BBED exposes three useful status waves: 2018, 2022, and 2024. Three irregular observations are too sparse for a conventional dense time-series model. Treating every building as an independent long sequence would imply temporal resolution the data does not contain.

The engine instead models a discrete transition:

```text
P(state at target year | state and valid covariates at start year)
```

One row in the modeling panel represents one urban object over one exact interval. The two adjacent intervals are:

- 2018 to 2022: candidate-model training;
- 2022 to 2024: later-time holdout evaluation.

A direct 2018-to-2024 interval is also generated, but only for descriptive reporting. It answers what was observed over six years without pretending that the same rows are independent training evidence.

## 3. Canonical states are the semantic foundation

### 3.1 The problem with raw labels

BBED status values are survey labels, not a stable ontology. The same physical condition can be written differently between waves. For example, `Complete Residential`, `Complete Building`, and `Non-Residential` can all describe a completed built object. If those strings are modeled directly, a terminology edit appears as urban change.

This is not a small cleaning issue. In the live direct 2018-to-2024 comparison:

- 92.13% of complete rows changed raw text label;
- only 20.92% changed canonical state.

Without canonicalization, the system would claim that almost the entire city changed status.

### 3.2 Why eight states

The selected state space is small enough to support the available sample size, but detailed enough to preserve planning-relevant differences.

| Canonical state | Raw BBED examples | Meaning preserved |
| --- | --- | --- |
| `stable_built` | Complete Residential; Complete Building; Non-Residential; Old-Bldg-Inhabited | A completed, standing, usable built object |
| `active_construction` | Under Construction; Construction Site | Work is actively progressing or the site is under development |
| `stalled_or_cancelled` | Construction on-Hold; Cancelled Construction | Development started or was planned but is not progressing |
| `renovated` | Renovated | A completed object explicitly marked as renovated |
| `vacant_or_evicted` | Evicted Building; Old threat of Eviction; Old-Bldg-Uninhabited | A standing building without ordinary occupancy |
| `empty_or_parking` | Empty Lot; Parking Lot | Land without an occupied building, including parking use |
| `demolished` | Demolished | The prior built object has been removed |
| `unknown` | Not Available; missing; unrecognized future labels | The status is not reliably known |

### 3.3 Why these labels were grouped

- Residential versus non-residential is kept in the separate `building_use` feature. Encoding it again in the state would fragment the transition classes without adding status information.
- Active construction is separated from stalled or cancelled construction because their likely next states and planning meaning differ.
- Vacant or evicted remains separate from demolished and empty land because the physical building still exists.
- Empty lots and parking lots are combined because both represent non-built land in the first KPI set. They can be separated later if a planning use case and sufficient labels justify it.
- Renovated remains a state because BBED explicitly distinguishes it and it may represent a meaningful pathway from vacancy or older stock.
- `unknown` is a first-class state rather than a dropped row. Dropping unknown observations would bias the panel toward well-surveyed objects and hide coverage problems.

### 3.4 Why a literal crosswalk, not fuzzy matching

`status.py` uses an explicit dictionary after normalizing case and punctuation. Fuzzy matching was rejected because a plausible-looking automatic guess could silently turn a new survey label into the wrong physical state. Unknown labels therefore fall into `unknown`, where they are visible and reviewable.

The canonical enum is a public schema contract. Its values appear in panel CSVs, model class ordering, persisted model bundles, request and response JSON, KPI logic, and scenario adjustments. Renaming or reordering the states is a breaking change that requires retraining and versioning.

## 4. Data acquisition and governance

### 4.1 BBED snapshot

`BBEDClient` queries the public BBED ArcGIS FeatureServer layer. The client:

- requests only an explicit field allowlist;
- paginates deterministically using `OBJECTID` ordering;
- requests geometry in EPSG:32636;
- can limit a request to a spatial bounding box;
- writes GeoJSON plus a manifest containing source URL, retrieval time, CRS, fields, feature count, SHA-256 hash, and ODbL attribution.

Names, contacts, ownership fields, and unrelated survey attributes are excluded. Raw data, derived features, model artifacts, and forecasts remain outside the repository through private-root configuration and Git ignore rules.

### 4.2 Stable object identity

`BULBuildingID` is useful but cannot be treated as uniquely complete: the live layer contains duplicates and missing values. ArcGIS `OBJECTID` is unique within the published layer but is less meaningful to domain users.

The implementation therefore creates a composite identity:

```text
BULBuildingID:<value>|OBJECTID:<value>
```

If the preferred building ID is missing, it falls back to `OBJECTID:<value>`. This keeps identities unique while preserving the domain identifier when available. It also ensures that a building fetched from a bounding-box subset receives the same ID as it does in the full-layer snapshot.

## 5. Building a time-valid transition panel

The panel is the single fact table used for description and model selection. Each row contains:

- stable object identity;
- exact start and target years;
- raw and canonical start/target states;
- interval length;
- sector and building use;
- footprint, floors, and height;
- permit/completion recency when those dates were already knowable;
- point-cloud morphology only when temporally eligible;
- explicit missingness indicators.

### 5.1 Date leakage

A permit recorded in 2023 cannot be used to forecast from 2018. Permit and completion recency are therefore exposed only when the recorded date is at or before the interval start. Later dates become missing for that row rather than being treated as zero.

### 5.2 Point-cloud leakage

The AUB point clouds were captured in 2020 after the port blast. They cannot be attached to 2018-to-2022 rows because they reveal post-2018 physical evidence. They are eligible for intervals beginning in 2022 and for current inference.

The code blanks invalid morphology values to missing while retaining the BBED row. This preserves legitimate status evidence and tells the learned model honestly that the modality was unavailable.

`assert_no_temporal_leakage` runs after panel construction and again before training. It raises an error instead of silently filtering contaminated rows because leakage is an upstream defect, not a row-quality preference.

### 5.3 Exact interval selection

The direct 2018-to-2024 descriptive rows share one endpoint with each modeling interval. Selecting training or test rows only by `target_year` would contaminate evaluation. The benchmark pins both `start_year` and `target_year`, and a regression test injects poisoned direct rows to prove that the benchmark metrics remain unchanged.

## 6. Point-cloud processing decisions

### 6.1 Provenance correction

The source files use the LAS container, but inspection showed that the 2020 cloud is photogrammetric rather than laser-scanned LiDAR:

- intensity is constant zero in sampled points;
- return information is always one of one;
- associated mesh metadata identifies Agisoft Metashape;
- PDAL wrote the reconstructed cloud into LAS.

The existing `lidar_*` API and dataframe names remain for contract continuity, but documentation now describes the modality accurately. The temporal rule is unchanged because acquisition time, not sensing technology, determines leakage.

### 6.2 CRS handling

The LAS header does not embed a CRS. EPSG:32636 was established independently through alignment with BBED. The COPC pipeline therefore assigns this CRS explicitly instead of pretending it came from the source header.

### 6.3 Memory and format safety

The lightweight reader supports LAS point formats 0–3, reads chunks, and refuses implicit loads above a configurable point-count guard. Larger or more varied files should use PDAL/COPC tiling. The ordinary Python environment can inspect and process the verified tile; actual COPC materialization requires the supplied Conda environment with PDAL and GDAL.

### 6.4 Morphology extraction

BBED polygons remain authoritative. For each polygon, the extractor:

1. applies a bounding-box candidate filter;
2. performs an exact point-in-polygon test;
3. estimates local ground as a low height quantile on a grid;
4. derives height above ground;
5. uses RGB excess-green as a simple vegetation mask when color is available;
6. calculates point count, density, height quantiles, maximum height, roof roughness, and vegetation ratio.

Point-cloud geometry does not replace BBED boundaries. Attached Beirut buildings cannot be reliably separated at party walls using geometry alone, and a watershed instance is not automatically the same entity as a BBED building.

## 7. Model candidates and why they exist

Every candidate predicts the same ordered eight-state probability vector.

### 7.1 Persistence baseline

The no-change model predicts that each building remains in its current state. It is intentionally difficult to beat because most buildings are stable over two years. Its accuracy can be high, but its one-hot confidence is heavily penalized when a transition occurs.

### 7.2 Empirical Markov baseline

The Markov model estimates `P(next state | current state)` separately by interval length. Additive smoothing of 0.5 prevents unseen transitions from receiving impossible zero probability. It ignores covariates, which makes it transparent and establishes the value a more complex model must add.

### 7.3 Gradient-boosting candidate

Histogram gradient boosting was selected as the learned tabular candidate because the dataset has only a few thousand rows, mixed numeric/categorical features, and substantial missingness.

The preprocessing pipeline:

- median-imputes numeric data and adds missingness indicators;
- most-frequent-imputes categorical data;
- ordinal-encodes categories, mapping unseen values to `-1`;
- discovers optional morphology columns by prefix;
- drops all-empty training features;
- excludes identities and target columns through a feature allowlist;
- applies inverse-frequency sample weights to reduce domination by `stable_built`.

A fixed random seed makes the release decision reproducible.

### 7.4 Why GraphSAGE is not in the gate

`graph.py` contains an optional GraphSAGE research classifier, but it is not wired into the benchmark. Neighborhood information may help, but a graph model introduces adjacency definitions, spatial-transfer risks, and additional tuning. It must be evaluated as an ablation against the tabular gate rather than assumed superior.

## 8. Time-forward evaluation and the release gate

Randomly shuffled cross-validation would let the model learn from later survey conditions while being scored on earlier-like rows. The primary evaluation therefore trains on 2018-to-2022 and tests on 2022-to-2024.

The gate uses two complementary metrics:

- **Macro-F1:** gives each state equal weight and exposes failure on rare transitions;
- **Multiclass Brier score:** evaluates the complete probability vector and penalizes unjustified confidence.

Accuracy and log loss are reported, but the learned model ships only if it improves on the best baselines in both Macro-F1 and Brier score. If it fails either condition, the strongest baseline is selected and the negative result remains visible.

After the selection decision is made honestly on the holdout, the chosen model is refitted on both adjacent intervals for production. This uses all observed two-year transition evidence without changing the reported holdout metrics.

## 9. Forecast engine and uncertainty

The selected model is a one-step transition model. A 4- or 6-year forecast repeatedly applies the two-year step.

For each Monte Carlo draw and step, the engine:

1. predicts a probability distribution for every object;
2. applies eligible scenario adjustments;
3. samples the next state;
4. uses the sampled state as the next step's starting state;
5. computes citywide KPIs.

The output reports p05, p50, and p95 KPI quantiles. Uncertainty therefore compounds across steps instead of being hidden by repeatedly selecting the most likely state.

A deterministic seed makes a request reproducible. The current out-of-distribution score is deliberately simple: it combines the share of unknown states with the share missing both floors and height. It is a warning heuristic, not a learned density estimator.

## 10. Scenario adjustments and evidence labels

An intervention changes the log odds of one target state for selected objects and eligible starting states at a specified step. Log-odds adjustment was chosen because it shifts one outcome while preserving a normalized probability distribution.

Every adjustment requires:

- an intervention ID;
- target state;
- bounded effect size;
- active step;
- effect source;
- confidence grade;
- optional object and starting-state filters.

This mechanism supports sensitivity analysis. It does not infer causal effects. Forecasts without adjustments are labeled `empirical_bau`; forecasts with adjustments are labeled `assumption_based_scenario` and carry an explicit warning.

## 11. KPI design

The first KPI set was selected because it can be computed consistently from state, BBED footprints, floors, and height:

- completed, active, stalled, vacant, and demolished counts;
- empty or parking area;
- estimated gross floor area;
- median and 90th-percentile building height;
- built-land fraction.

Estimated gross floor area is footprint area multiplied by floors for states treated as built. It is a planning-scale estimate, not a substitute for architectural floor plans. Heritage KPIs were deferred because the available source vintages disagree.

## 12. Shared contracts and interfaces

Pydantic models with `extra="forbid"` define the same request and response schema for the CLI, engine, and FastAPI service. This prevents accidental fields from passing silently and keeps all interfaces aligned.

The output includes:

- schema, model, and data versions;
- snapshot and scenario identity;
- evidence level;
- first-step object probabilities;
- KPI quantiles;
- out-of-distribution score;
- warnings.

The service exposes health, model metadata, and forecast endpoints. The CLI exposes every data-preparation, training, description, inference, and serving stage so each step can be rerun and inspected independently.

## 13. Observed implementation results

### Data and panel

- 3,349 allowlisted BBED objects fetched;
- 3,318 complete 2018-to-2022 training rows;
- 3,323 complete 2022-to-2024 holdout rows;
- 3,318 complete direct 2018-to-2024 descriptive rows;
- 9,959 total interval rows;
- 694 of 3,318 direct rows changed canonical state: 20.92%;
- 3,057 direct rows changed raw label: 92.13%.

### StreetScape verification tile

- 4,800,668 LAS points;
- 17 BBED footprints intersected the tile bounds;
- 15 footprints contained points;
- 630,024 points assigned to BBED footprints;
- 0 point-cloud-enabled 2018 rows;
- 17 point-cloud-enabled 2022 rows.

### Model gate

| Candidate | Accuracy | Macro-F1 | Brier score | Decision |
| --- | ---: | ---: | ---: | --- |
| No change | 0.8116 | 0.3997 | 0.3768 | Baseline |
| Empirical Markov | 0.8116 | 0.3997 | **0.2789** | **Selected** |
| Gradient boosting | 0.8080 | 0.3926 | 0.3778 | Rejected |

The learned model improved neither gate metric. The Markov baseline matched the strongest class scores and produced substantially better probability quality.

Automated verification currently reports 14 passing tests. The suite covers canonical mapping, contract validation, BBED pagination and allowlisting, stable identities, temporal leakage, exact interval selection, probability normalization, benchmark contamination, deterministic forecasting, API parity, LAS guards, and feature extraction.

## 14. Honest implementation boundary

### Implemented and exercised

- canonical state crosswalk and explicit unknown fallback;
- allowlisted BBED ingestion and provenance manifest;
- stable composite identities;
- three-interval transition panel;
- date and point-cloud temporal gating;
- direct transition summary;
- persistence, Markov, and gradient candidates;
- time-forward model gate and production refit;
- Monte Carlo KPI rollouts;
- scenario adjustments and evidence labels;
- versioned model bundle, CLI, FastAPI, and tests.

### Present in code but not production-integrated

- `TemperatureCalibrator`: implemented as a utility, but not fitted or applied by the current benchmark or forecast engine;
- `GraphSAGETransitionClassifier`: research-only and outside the candidate gate;
- OOD scoring: implemented as a core-missingness heuristic, not a learned distribution model.

### Environment-dependent or pending

- actual COPC materialization requires the PDAL/GDAL Conda environment;
- point-cloud value still needs a spatially grouped ablation over broader coverage;
- intervention effect sizes need causal or policy evidence before they can support recommendations;
- external-city or later-wave validation is required to establish transfer.

## 15. What would justify the next version

The next version should be driven by evidence, not model novelty:

1. Obtain another observation wave or defensible external validation set.
2. Expand point-cloud coverage and run a grouped morphology ablation.
3. Audit the canonical crosswalk with urban-domain reviewers and version any changes.
4. Integrate calibration only if it improves held-out probability quality without using test data for fitting.
5. Add graph features only if they improve the same release gate and remain stable under spatial transfer.
6. Replace heuristic OOD scoring with a validated distributional method.
7. Treat interventions as causal only after effect sizes are supported by appropriate evidence.

The central engineering contribution is therefore not a particular algorithm. It is the set of semantic, temporal, probabilistic, and provenance boundaries that prevent a plausible-looking urban forecast from making claims the evidence cannot support.
