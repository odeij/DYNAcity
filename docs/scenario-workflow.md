# Scenario execution and comparison

The live viewer posts prose to `/v1/viewer/scenario`. The configured language-model
provider proposes a draft; deterministic validation resolves building scopes,
effect strengths, hazard footprints and supported KPIs. An empty or unsupported-only
draft is rejected instead of becoming an unchanged forecast. An explicit business-as-usual
request may produce a valid empty scenario. A mixed request is marked `partial`,
and the unsupported parts remain visible. The JSON retains every validation attempt;
the viewer shows current issues and lets users expand earlier attempts.

Accepted building scenarios return `forecast`, `baseline` and `comparison`.
The baseline is calculated with the same snapshot, engine, horizon, requested KPIs,
Monte Carlo draw count and seed, with interventions and hazards removed. It is
independent of any older forecast embedded in the viewer. Road-only events remain
in the separate traffic workflow and do not change building forecasts.

API clients can compile via `/v1/scenarios/compile`, review its `status`
(`accepted`, `partial`, `rejected`) and `scenario`, then send a `ForecastRequest`
to `/v1/forecast/compare`. This endpoint returns full forecast and baseline
contracts, plus per-KPI differences of medians at each two-year offset. The
existing `/v1/forecast` response is unchanged. Direct requests reject levers
outside the horizon and object IDs absent from the snapshot.

For CLI workflows, compile with `--request-output`, then run:

```sh
dynacity forecast --model /private/model.joblib --request /private/request.json \
  --output /private/scenario.json --comparison-output /private/comparison.json
```

The comparison table displays both medians and p05–p95 simulated outcome bands.
Ratio changes use percentage points, not relative percent changes. A difference
of medians is not a distribution of differences: no delta interval or statistical
significance is claimed. These outcome bands exclude uncertainty in assumed effect
sizes and model calibration. Scenario interventions remain sensitivity assumptions;
hazard damage curves remain illustrative and uncalibrated.

Provider failure never invokes a local fake automatically. Missing configuration
and rejected provider API calls return a readable 503; refusal, malformed replies
and unavailable services appear in the compilation audit. Optional SDK contract
tests and explicitly injected UI fixtures are not evidence of live provider behavior.
