# Forecasting Engine Design

For the decision-by-decision engineering rationale, including the canonical-state ontology and rejected alternatives, see [Implementation Rationale](implementation-rationale.md).

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

## Known limitations

- BBED contains few observation waves and substantial class imbalance.
- The 2020 port blast and Lebanon's crises introduce structural breaks.
- Point-cloud coverage is partial and its classifications are unassigned.
- BBED footprints, not watershed instances, remain authoritative at party walls.
- Heritage layers are not used as a v1 KPI because their vintages disagree.
- The present out-of-distribution score covers missing core morphology only; a learned density score is future work.

