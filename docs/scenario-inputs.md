# Portable scenario inputs

Open **More tools → Saved scenarios** to save the text in **Scenario** mode, load a saved prompt, or import/export JSON. The library stores up to 50 inputs in this browser and origin. Export a portable copy before clearing browser data. Storage or schema errors are reported; an unreadable existing library is preserved. Exports also appear as selectable JSON for browsers that block file downloads.

A version 1 file has exactly these fields:

```json
{
  "format": "dynacity-scenario-input",
  "schema_version": 1,
  "name": "Renovation",
  "saved_at": "2026-10-08T09:00:00.000Z",
  "context": {
    "snapshot_id": "example-survey",
    "data_version": "example-data-v1",
    "model_version": null
  },
  "input": {
    "text": "Renovate stalled buildings",
    "hour": 8
  }
}
```

Imports reject extra fields, unsupported versions, empty text, text over 4,000 characters, names over 80 characters, invalid timestamps, invalid context identifiers, hours outside 0–23 and files over 32 KB. Files are data only and are never evaluated. Imported text and names are rendered with DOM values or escaped HTML.

**This is an input archive, not an execution archive.** Loading restores the name and prompt for review. It never submits a request, replaces displayed forecast results, restores dropped disasters or road edits, or changes the traffic hour. The saved and current hour are displayed for comparison. Current map results remain until the user runs a new scenario. Version mismatch warnings compare snapshot ID, data version and model version; an unknown model version is explicitly treated as unverifiable. Even matching version identifiers cannot guarantee identical provider interpretation or Monte Carlo outcomes. Exact compiled-spec replay belongs to the scenario execution workflow and is not offered here.

No building features, private datasets, forecast outputs or provider keys are exported. The input schema lives in the viewer so the same behavior works in standalone exported pages; no new backend endpoint or provider call is required. In a read-only exported viewer, imported inputs can still be saved/exported, but running requires the live viewer.

Verification:

```sh
node tests/viewer_usability.test.cjs
node tests/viewer_traffic.test.cjs
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python -m pytest tests/test_viewer.py -k 'not served_viewer and not google_basemap and not viewer_routes'
```
