# Task 5 AI Module Specification

The tables below are the funding proposal's module breakdown for Task 5.1 (per-object
forecasting) and Task 5.2 (scenario planning over those forecasts). They are recorded here
verbatim as the specification this branch is scoped against. The status column after each
table maps every row to what exists in `src/dynacity` today; see
[Forecasting Engine Design](forecasting-engine.md) and
[Implementation Rationale](implementation-rationale.md) for the supporting detail.

## Task 5.1

| AI Module | Training Input | Training Output | Inference Input | Inference Output |
|---|---|---|---|---|
| Gen AI (GAN/Diff) | Existing urban layouts, GIS, LIDAR, CityGML | Trained generative model | User constraints, policies, prompts | New city layouts/buildings/districts |
| Spatio-temporal forecasting | Historical sensor data, weather, traffic, energy | Forecasting model | Current city state | Predicted future state |
| Causal Inference | Historical interventions + observed impact | Causal graph/model | Proposed urban intervention | Estimated cause-effect relations |
| Reinforcement Learning | State, Action, Reward trajectories | Learned policy | Current city state | Optimal intervention |
| Uncertainty Quantification | Prediction datasets | Calibration model | Model prediction | Confidence interval/uncertainty score |

### Status against this branch

| Module | Status | Notes |
|---|---|---|
| Gen AI (GAN/Diff) | Not implemented | No generative model over layouts/buildings/districts exists in `dynacity`. Out of scope for the current forecasting engine. |
| Spatio-temporal forecasting | **Implemented** | This is the branch's core deliverable. `panel.PanelBuilder` is the historical training input, `models.temporal_benchmark` selects the forecasting model (gated against no-change/Markov baselines on macro-F1 and multiclass Brier), and `engine.ForecastEngine` takes a current `UrbanObjectState` snapshot to a predicted future state distribution via `contracts.ForecastRequest`/`ForecastResult`. |
| Causal Inference | Not implemented (by design) | The engine takes a caller-supplied intervention and shifts target-state log-odds by a caller-supplied `delta` (`uncertainty.apply_log_odds_adjustment`) — it does not estimate the effect size itself. `contracts.EvidenceLevel.ASSUMPTION_BASED_SCENARIO` and the required `effect_source`/`ConfidenceGrade` on every `TransitionAdjustment` exist specifically to keep this legible as an unverified assumption, not a learned cause-effect estimate. `implementation-rationale.md` states intervention effect sizes need causal or policy evidence before they can support recommendations. |
| Reinforcement Learning | Not implemented | No state/action/reward loop or policy search exists. `engine.py` rolls a fixed scenario forward; it does not search over interventions. |
| Uncertainty Quantification | Partially implemented | Monte Carlo propagation is wired in and shipping: `uncertainty.sample_categorical` draws per-object states across `draws` (seeded), rolled forward per step, and `kpis.compute_kpis` reduces each draw to KPI values whose spread across draws is reported as p05/p50/p95. The formal calibration half of the spec is not: `uncertainty.TemperatureCalibrator` exists but its own docstring says it is "not currently wired into `temporal_benchmark`" — nothing fits or applies it today. |

## Task 5.2

| AI Module | Training Input | Training Output | Inference Input | Inference Output |
|---|---|---|---|---|
| Scenario Forecasting | Existing simulation datasets | Scenario model | Urban scenario | Future city evolution |
| Backcasting | Historical transitions + planning targets | Planning model | Desired KPI targets | Sequence of interventions |
| Multi-objective Optimization | Simulation results | Optimization model | KPIs + constraints | Pareto-optimal solutions |
| KPI Evaluation | KPI definition | Evaluation rules | Simulation outputs | KPI scores |

### Status against this branch

| Module | Status | Notes |
|---|---|---|
| Scenario Forecasting | **Implemented** | `contracts.ForecastRequest.scenario` (interventions + `horizon_years`) drives `engine.ForecastEngine`'s stochastic rollout to a `ForecastResult`, i.e. urban scenario in, future city evolution out. This is the forward direction of the same engine as Task 5.1's spatio-temporal module. |
| Backcasting | Not implemented | Nothing solves the inverse problem — given desired KPI targets, produce the intervention sequence that would reach them. The engine only runs forward from a supplied scenario. |
| Multi-objective Optimization | Not implemented | No Pareto search over KPIs and constraints exists; KPI trade-offs are left for a human to read off the reported distributions. |
| KPI Evaluation | **Implemented** | `kpis.DEFAULT_KPIS`/`KPI_UNITS` are the KPI definitions, `kpis.compute_kpis` is the evaluation rule set (pure aggregation over one realised state assignment), and the engine calls it once per Monte Carlo draw per rollout step to turn simulation outputs into KPI scores. |

## Reading the gaps

Six of the nine modules above (Gen AI, Causal Inference, Reinforcement Learning, formal
calibration, Backcasting, Multi-objective Optimization) are absent from this branch, and one
more (Uncertainty Quantification) is only half-shipped. That is not an oversight to patch
individually — `panel.py`, `models.py`, `engine.py`, `kpis.py`, and `uncertainty.py` already
share one state representation (`CanonicalState`) and one probabilistic rollout loop. The two
implemented modules on each table are the two directions of that same rollout: forward
(scenario forecasting) and reduction (KPI evaluation). The absent modules are better read as
five specific capabilities to add onto that shared substrate — an inverse solver over it
(Backcasting), a search procedure over its KPI outputs (Multi-objective Optimization), a
fitted effect-size estimator feeding its intervention mechanism (Causal Inference), a policy
that chooses interventions from it (Reinforcement Learning), and a fitted calibration step
already stubbed for it (`TemperatureCalibrator`) — rather than five unrelated builds. Gen AI
is the one module that does not extend this substrate at all; it operates on layout geometry
the engine does not model.
