# Data coverage and checked updates

Run commands from an installed checkout (or with `PYTHONPATH=src`). Keep extracts,
reports, morphology tables and generated candidates outside Git. The two new
commands refuse output within Git checkouts; existing pipeline commands still
require care when choosing output paths.

## Repeatable audit

```bash
dynacity audit-coverage \
  --bbed /mnt/windows/private/dynacity-data/bbed-2024.geojson \
  --manifest /mnt/windows/private/dynacity-data/bbed-2024.geojson.manifest.json \
  --snapshot /mnt/windows/private/dynacity-data/snapshot-2024-located.json \
  --output /tmp/dynacity-coverage.json
```

Optional `--panel CSV` checks missing, unexpected, duplicated and mismatched
transition rows against source intervals. Optional `--lidar-features CSV` checks morphology keys, including unmatched and
zero/missing point counts. Optional `--previous GEOJSON` reports additions,
removals and changed records. Exit 2 means the report was written but blocking
checks failed. The deterministic `dynacity-coverage/1` report includes canonical
source hash, CRS and privacy checks, source record count, geometry validity,
missing domain IDs, survey-wave completeness, unknown statuses, positive recorded
versus estimated rendering heights, numeric quality, skipped transition intervals,
source/snapshot joins and value differences. No per-building data appears in the
report; keep even aggregate reports private by default.

“Recorded height” means a positive source attribute, **not verified measurement**.
A nonpositive height uses floors when positive; otherwise the viewer uses its
existing fallback. No inferred height is inserted into the snapshot. Unknown
survey labels are retained as unknown; blank endpoints do not create training
transitions. Invalid/missing/nonpolygon geometry is excluded from rendering,
centroid attachment and point-cloud extraction, with an explicit quality count.
Objects remain in snapshots, so usable source attributes are not discarded.
The legacy required area field uses zero if both source area and usable geometry
are absent; the report flags that geometry gap and staged updates reject it.

`payload.coverage.quality` exposes the same structured report to the viewer.
Existing drawn/missing_geometry/estimated_height counters remain compatible;
`missing_geometry` includes unusable polygons. `quality.source` is null when no
manifest was supplied, with a warning rather than an invented retrieval time.
UI wording and presentation can consume this contract separately.

## Safe update workflow

1. Fetch to a new private path with `fetch-bbed`, preserving the sibling manifest.
   New manifests record the query/filter, CRS, hash and retrieval time. Paging
   honours transfer-limit flags and rejects repeated row IDs, malformed pages
   and stalled pagination. A completed page sequence does not prove citywide
   completeness or detect all concurrent upstream edits. Repeat the fetch and
   compare hashes if the layer may have changed during extraction.
2. Audit with the active source as `--previous`. Review missing IDs, geometry,
   statuses, unknown labels, heights, intervals and morphology joins. Get source
   corrections for invalid polygons; do not silently repair, delete, or invent
   missing observations. Hashes prove content consistency, not source authenticity.
3. Build a new immutable candidate:

   ```bash
   dynacity stage-update \
     --bbed /private/new-bbed.geojson \
     --manifest /private/new-bbed.geojson.manifest.json \
     --previous /private/active-bbed.geojson \
     --snapshot-id beirut-reviewed-candidate \
     --data-version BBED-reviewed-candidate \
     --as-of 2024-04-30 \
     --output-dir /private/candidates/reviewed-candidate
   ```

   This requires a matching manifest, explicit supported source CRS, unique row
   keys, valid polygons and matching snapshot IDs/values. Missing optional
   attributes remain missing. Source removals require `--acknowledge-removals`
   after review. Duplicate/null morphology keys and unmatched morphology IDs
   block staging. Morphology is assumed to be the established 2020 capture; a
   different acquisition period needs a separately reviewed ingestion change.
   No active file is overwritten. All generation completes in a sibling temporary
   directory, then the new output directory is reserved exclusively and its
   `data/` bundle is published by a directory rename. A failed publication can
   leave an empty reserved directory; choose a new candidate name after checking
   it. Existing candidate directories are refused.
4. Inspect `data/coverage.json`, `data/update.json`, the source manifest,
   `data/snapshot.json` and `data/panel.csv`. `update.json` carries input lineage,
   explicit survey and acquisition periods, prior hash, removal acknowledgement
   and SHA-256 hashes of every generated artifact (including copied morphology).
   Validate hashes again before activation; candidate immutability is a workflow
   rule, not a filesystem permission guarantee.
5. Retrain with the candidate panel, review temporal benchmark metrics, regenerate
   forecasts using the candidate snapshot and matching model/data version, and
   render a separate preview. Check joins, coverage and the observed lens. Only
   then change the service's input paths together and restart. Keep prior paths
   and artifacts for rollback. Never serve an old forecast against the candidate
   snapshot. Forecast ID validation alone does not establish matching content.

The supported survey periods remain 2018, 2022 and 2024. Retrieval date,
`data_version`, forecast horizon and `as_of` do not establish a new survey.
Source records have no per-building observation timestamp; latest available wave
and value mismatches are useful checks, but cannot establish real-world freshness.
Records absent from the supplied extract cannot be counted without an independent
building inventory or survey. Query extent and prior-ID comparison only establish
coverage relative to those inputs.

## Local audit findings (2026-10-08)

The private 2024 extract contains 3,349 records; its canonical hash matches its
manifest. Both saved snapshots match all 3,349 IDs, heights/floors and source status
values. The stored transition panel also matches all expected interval rows and
canonical endpoint states (no missing, extra or duplicated rows). The located snapshot supplies centroids; it predates these checks and
still carries centroids on the four invalid source polygons. Rebuild after source
correction instead of treating those centroids as verified locations.

| Check | Result |
| --- | ---: |
| Valid polygons / invalid polygons | 3,345 / 4 |
| Missing domain building ID (OBJECTID fallback retained) | 26 |
| Missing status 2018 / 2022 / 2024 | 31 / 26 / 0 |
| Unknown canonical status 2018 / 2022 / 2024 | 328 / 174 / 0 |
| Positive recorded heights / fallback rendering estimates | 3,151 / 198 |
| Complete 2018–2022 / 2022–2024 / 2018–2024 intervals | 3,318 / 3,323 / 3,318 |

The invalid polygons block staged publication of the current uncorrected extract.
No source data was rewritten. No morphology table was available under the local
private data root, so real point-cloud joins remain unverified. The manifest says
retrieved 2026-08-05; it supplies no observations after the 2024 wave. Citywide
missing buildings, source height measurement methods, upstream completeness and
actual post-2024 changes remain unknown.
