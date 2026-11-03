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
| Gen AI (GAN/Diff) | Not implemented | No generative model over layouts/buildings/districts exists in `dynacity`. `scenario_compiler.py` uses a language model for the table's *inference input* ("user constraints, policies, prompts"), translating planner prose into a validated `ScenarioSpec`, but it generates scenario parameters, not layouts, so the row stays open. |
| Spatio-temporal forecasting | **Implemented** | This is the branch's core deliverable. `panel.PanelBuilder` is the historical training input, `models.temporal_benchmark` selects the forecasting model (gated against no-change/Markov baselines on macro-F1 and multiclass Brier), and `engine.ForecastEngine` takes a current `UrbanObjectState` snapshot to a predicted future state distribution via `contracts.ForecastRequest`/`ForecastResult`. |
| Causal Inference | Not implemented (by design); provenance in place | The engine takes a caller-supplied intervention and shifts target-state log-odds by a caller-supplied `delta` (`uncertainty.apply_log_odds_adjustment`) — it does not estimate the effect size itself. `contracts.EvidenceLevel.ASSUMPTION_BASED_SCENARIO` and the required `effect_source`/`ConfidenceGrade` on every `TransitionAdjustment` exist specifically to keep this legible as an unverified assumption, not a learned cause-effect estimate. `evidence.py` now makes those sources checkable: levers can cite a frozen, content-hashed `EvidenceBundle` (`evidence:<id>@evb-…`), with supersession and effective dates enforced at compile time. That is the input a causal estimator would need, not the estimator. |
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
| Backcasting | **Implemented (search-based)** | `planning.plan` takes KPI targets plus candidate levers (each with allowed strengths and activation steps) and returns the least-intensive lever combinations that meet every target, or the closest ones with a warning when none does. It inverts the forward engine by enumeration rather than by a learned planning model, so its answers inherit the levers' assumed effect sizes and are stamped `assumption_based_scenario`. |
| Multi-objective Optimization | **Implemented (exhaustive / seeded-sample Pareto)** | The same search reports the Pareto front over up to four KPI objectives (any of p05/p50/p95) plus total intervention intensity, among target-feasible combinations. Exhaustive when the space fits `max_evaluations`, otherwise a seeded sample that always includes business-as-usual and every single-lever setting. All candidates share the request seed (common random numbers). |
| KPI Evaluation | **Implemented** | `kpis.DEFAULT_KPIS`/`KPI_UNITS` are the KPI definitions, `kpis.compute_kpis` is the evaluation rule set (pure aggregation over one realised state assignment), and the engine calls it once per Monte Carlo draw per rollout step to turn simulation outputs into KPI scores. |

## Reading the gaps

`panel.py`, `models.py`, `engine.py`, `kpis.py`, and `uncertainty.py` share one state
representation (`CanonicalState`) and one probabilistic rollout loop, and the implemented
modules are all directions of that same rollout: forward (scenario forecasting), reduction
(KPI evaluation), and — via `planning.py` — search over its KPI outputs (multi-objective
optimisation) and its inverse by enumeration (backcasting).

Three of the four remaining gaps extend the same substrate rather than being unrelated
builds: a fitted effect-size estimator feeding the intervention mechanism (Causal Inference —
`evidence.py` now supplies the provenance it would consume), a learned policy that chooses
interventions sequentially (Reinforcement Learning — `planning.plan` is the non-learned
baseline such a policy would have to beat), and a fitted calibration step already stubbed
(`TemperatureCalibrator`). Gen AI is the one module that does not extend this substrate; it
operates on layout geometry the engine does not model.
