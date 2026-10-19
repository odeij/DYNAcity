# Budget planning and educational iterations

Owned implementation: `src/dynacity/flood_planning.py`; tests: `tests/test_flood_planning.py`.

This is a callable planning component, not a rainfall/runoff/hydraulic solver. It evaluates caller-supplied intervention portfolios through a supplied evaluator. It never changes water depths, invents benefits or infers population from building counts. No reinforcement learning, causal optimization, Beirut calibration, browser controls or shared API integration is implemented here. The pilot chat owns final integration.

## Public interface

All data models are frozen Pydantic models with forbidden extra fields. Collections are immutable tuples. Use `model_dump(mode="json")` for JSON-safe values and `model_validate(payload)` for ingestion.

- `InterventionKind`: `drain_maintenance`, `retention`, `permeable_surface`, `access_protection`. These classify choices; they do not specify physical effects. An evaluator must implement the actual intervention representation or explicitly reject it.
- `Parameter(name, value, unit)`: finite numeric physical or operational setting, with caller-declared units. No default capacity, effectiveness or drainage rate is supplied.
- `CostAssumption(amount, currency, source, status="assumed")`: nonnegative caller-supplied total cost for one intervention choice. Exact decimal arithmetic, up to six fractional digits and 24 total digits; uppercase three-letter currency. Every cost is explicitly an assumption, even if its source refers to a quotation. No exchange rates or local market prices are inferred.
- `InterventionChoice(choice_id, kind, target_ids, parameters=(), cost)`: identifiable targeted action. Targets identify upstream assets/cells/areas; this module does not resolve geometry.
- `Plan(plan_id, choices=())`: intervention portfolio, unique choice IDs. Cost sums the supplied choice totals. The caller must account for overlaps, mobilization costs, interactions and recurring costs in those totals. `BASELINE` is the reserved empty plan with ID `baseline`; user plans cannot use that ID. `Plan.fingerprint` hashes the complete serialized plan, including costs and parameters.
- `Budget(amount, currency)`: nonnegative spending ceiling in the same currency as every cost. Feasible means sum of supplied costs is less than or equal to budget. Infeasible plans are reported but never evaluated. Currency mismatches fail before any evaluation.
- `ExperimentInputs.from_inputs(forcing=dict, baseline=dict, settings=dict, seed=0)`: canonical JSON copies of the fixed storm, physical baseline and solver settings. Direct construction accepts `forcing_json`, `baseline_json`, `settings_json`, `seed`. `fingerprint` is SHA-256 over the canonical manifests and seed. Mutable original dictionaries cannot change the experiment after construction.
- `Evaluator` protocol: property `evaluator_id: str`; method `evaluate(inputs: ExperimentInputs, plan: Plan) -> Evaluation`. Identity must include engine/version and metric implementation. Every run receives the same immutable context, including seed; choices vary. Return explicit unsupported/unavailable results or raise an exception; exceptions propagate. Never substitute visual flood levels for missing physical outputs.
- `Evaluation(inputs_fingerprint, plan_fingerprint, evaluator_id, evidence, provenance, metrics=(), warnings=(), unavailable_reason=None)`: output echo hashes are checked against the exact call. `evidence` is `modelled`, `measured`, `synthetic_fixture`, or `unavailable`. The last requires a reason and no metrics. Illustrative outputs are not an accepted evidence kind. Provenance must identify the real engine/data/artifacts and relevant uncertainty limitations; keep private artifacts outside Git.
- `MetricValue(metric, value, definition, coverage, uncertainty, lower=None, upper=None)`: finite nonnegative value, explicit aggregation/threshold/time definition, inventory/extent coverage and uncertainty description. Both bounds or neither; bounds must contain the value. Use an explicit statement such as “not quantified” when uncertainty is unavailable. Missing metrics are omitted, never encoded as zero.
- `compare_plans(*, inputs, plans: tuple[Plan, ...], budget, evaluator, objectives=tuple(Metric)) -> ComparisonReport`: fresh baseline first, then feasible portfolios in sorted plan-ID order. Nonempty unique objectives required. Reports full inputs, budget, baseline provenance, per-plan feasibility/evaluation/objective changes, warnings, and `point_estimate_pareto` plan IDs. No single weighted score is produced. `ComparisonReport.fingerprint` hashes the entire serialized report for audit comparison.
- `PlanningChallenge(challenge_id, inputs, budget, objectives=tuple(Metric), iterations=())`: immutable educational journal. `iterate(hypothesis=..., plans=..., evaluator=...)` returns a new challenge with one appended `ChallengeIteration(hypothesis, report, learning_notes)`. Notes identify budget issues, missing outcomes, synthetic practice, tradeoffs and unquantified uncertainty. There are no points, flood-reduction rewards or implied real-world achievements. Iterations require stable baseline engine, evidence and numerical outputs; a changed baseline requires a new challenge. Fresh run provenance/log paths and warnings may differ.

## Metrics, units and interpretation

| Metric | Unit | Preferred direction |
| --- | --- | --- |
| `Metric.EXPOSED_BUILDINGS` / `exposed_buildings` | buildings | minimize |
| `Metric.EXPOSED_AREA` / `exposed_area_m2` | m² (`m2`) | minimize |
| `Metric.CLOSURE_DURATION` / `closure_duration_hours` | hours (`h`) | minimize |
| `Metric.ACCESS_FRACTION` / `accessible_services_fraction` | fraction [0, 1] | maximize |

Exposure must disclose depth thresholds and whether this is peak, time-integrated or another aggregation. Closure duration must specify the road inventory, closure threshold, observation period, and whether duration is maximum, mean or another aggregation; do not silently mix hours with road-hours. Access must specify the service inventory, origin/destination population or asset coverage, routing criteria and time aggregation. No population inference is performed. No per-building feature is derived from absent point-cloud inputs.

`ObjectiveChange.delta = candidate − baseline`, in the metric’s native unit. `improvement` equals delta for maximized access and negative delta for minimized exposure/closures. Positive improvement means the preferred direction of the supplied output. It is **not** an established causal benefit. Status retains measured/modelled/synthetic evidence; missing metrics, mismatched evidence kinds, definitions or coverage give `unavailable` with no numerical delta. A measured difference alone does not establish intervention causality.

Optional improvement bounds are conservative endpoint differences of the supplied value ranges; they are not statistically estimated effect confidence intervals. Correlation, common random numbers, input error and paired-run uncertainty remain the evaluator’s responsibility. If either run lacks bounds, effect bounds remain absent. Raw uncertainty descriptions and model warnings are retained in the evaluations.

The Pareto set includes only portfolios with complete comparable measured/modelled requested metrics. Synthetic practice and incomplete results are excluded. Baseline dominates a plan that is no better on every requested objective and worse on at least one. Otherwise a plan is excluded when another evaluated plan is at least as good on all objectives and strictly better on one. Equal plans and objective tradeoffs remain. This uses point estimates only, considers cost through the feasibility gate rather than as an objective, and makes no safety or robust optimality claim. Always display uncertainty and missing coverage beside it.

## Fairness, reproducibility and adapter responsibilities

Forcing and baseline manifests must carry actual upstream source IDs/content checksums, rainfall amounts/intensities and units, storm duration/timestep/time reference, terrain CRS/vertical datum/resolution/nodata, baseline drainage/surface identities and bounded pilot extent. Settings must include engine version, boundary conditions, numerical options and metric implementation/version. This component is spatially agnostic and does no reprojection, vertical transformation or time conversion. Include every consequential input in these manifests; JSON strings alone cannot establish scientific completeness.

Input and plan hashes and a stable evaluator identity detect accidentally mixed runs. They cannot establish that an adapter obeyed the protocol or that a solver is physically valid. The evaluator must actually run identical forcing and baseline systems with only the choices changed, honor the seed/common random inputs as appropriate, report unsupported actions, and obtain closure/access/exposure metrics from a real physical and/or routing model. A precomputed adapter must preserve the original run hashes and provenance rather than stamp new hashes on unrelated outputs. Preserve measured, estimated and synthetic input distinctions in the manifests/provenance. Never relabel fixture outcomes as real model output.

Order of submitted portfolios does not affect execution order or report identity. Repeated deterministic evaluator outputs produce the same report fingerprint. Run log/provenance changes correctly change the audit digest. The module does not claim to make a nondeterministic solver reproducible; reproducibility requires pinned source content, engine/settings and stable outputs as well as seed.

## Integration example: precomputed real-model adapter

The producer can emit a private bundle after running the same physical model for baseline and selected plans. `evaluations` is keyed by each original `Plan.fingerprint`; each value is a serialized `Evaluation`. The bundle must include original inputs and candidate plans. Its evaluations mapping must also include the empty `BASELINE` record; the candidate plans list excludes that reserved baseline. This example loads existing results; it computes no benefits and assigns no modelled evidence labels itself.

```python
import json
from pathlib import Path
from dynacity.flood_planning import (
    Budget, Evaluation, ExperimentInputs, Plan, compare_plans, PlanningChallenge,
)

bundle = json.loads(Path("/private/tmp/dynacity-model-bundle.json").read_text())
inputs = ExperimentInputs.model_validate(bundle["inputs"])
plans = tuple(Plan.model_validate(p) for p in bundle["plans"])
budget = Budget.model_validate(bundle["budget"])

class PrecomputedEvaluator:
    evaluator_id = bundle["evaluator_id"]  # original engine/version/metric version

    def evaluate(self, inputs, plan):
        # Missing run fails clearly; compare_plans verifies original echo hashes.
        return Evaluation.model_validate(bundle["evaluations"][plan.fingerprint])

evaluator = PrecomputedEvaluator()
report = compare_plans(inputs=inputs, plans=plans, budget=budget, evaluator=evaluator)
challenge = PlanningChallenge(challenge_id="pilot-iteration", inputs=inputs, budget=budget)
challenge = challenge.iterate(
    hypothesis="Test whether this portfolio trades closure duration for exposure",
    plans=plans, evaluator=evaluator,
)
# Serialize to private storage / serve through the pilot-owned API.
result_payload = report.model_dump(mode="json")
```

The pilot should expose `unavailable_reason` and evaluation warnings, distinguish synthetic practice from measured/modelled results, and show assumptions with cost/budget. Drainage/surface adapters may implement this protocol directly without importing any browser or traffic state. No solver or shared API dependency is introduced by this module; only existing Pydantic and Python standard library are used.

## Verification and evidence limits

Command: `.venv/bin/python -m pytest tests/test_flood_planning.py -q`

Result: **33 passed**. Tests cover exact decimal budget feasibility, over-budget evaluation exclusion, currency rejection, correct objective directions/units, missing/incompatible outcomes, conservative uncertainty propagation, shared immutable forcing/seed, canonical hashes and order independence, mismatched run identity rejection, unavailable/failing evaluators, Pareto dominance/tradeoffs/baseline dominance, educational iteration stability, invalid costs/metrics and synthetic evidence exclusion. All numerical outcomes in these tests are synthetic fixtures, including fixtures used to exercise the modelled/measured handling branches. No observed Beirut data or real hydraulic/routing solver has been used to validate benefits.

Integration status: ready
