# DynaCITY — Beirut Urban-Form Forecasting

This branch implements the forecasting portion of DynaCITY Task 5.1. Backcasting and reinforcement learning are deliberately out of scope here.

The engine builds a leakage-safe transition panel from the Beirut Built Environment Database (BBED), optionally enriches the 2022→2024 interval with the post-port-blast 2020 AUB point clouds, benchmarks simple and learned transition models, and produces probabilistic 2-, 4-, or 6-year urban-form KPI forecasts.

## Engineering contribution

The core contribution is a versioned urban-state forecasting boundary that combines:

- BBED status transitions from 2018, 2022, and 2024.
- Static morphology and missing-modality indicators from point clouds.
- Explicit temporal-leakage prevention for the 2020 LiDAR acquisition.
- Honest baseline/model selection instead of assuming a graph model is superior.
- Monte Carlo KPI trajectories with p05/p50/p95 uncertainty.
- A shared JSON contract, CLI, and FastAPI service.

BBED polygons remain the canonical building boundaries. Point-cloud-derived watershed instances are useful for detection QA, but attached Beirut buildings cannot be reliably split at party walls from geometry alone.

## Private data boundary

Raw LAS/COPC files, BBED downloads, features, trained models, and forecast artifacts are ignored by Git. Set private locations outside the repository:

```powershell
$env:DYNACITY_DATA_ROOT='C:\private\dynacity-data'
$env:DYNACITY_ARTIFACT_ROOT='C:\private\dynacity-artifacts'
```

Only the BBED fields in `dynacity.bbed.BBED_FIELDS` are downloaded. Names, contacts, owner information, and unrelated survey fields are excluded. BBED outputs retain ODbL attribution metadata.

Each object receives a stable composite identity from `BULBuildingID` and ArcGIS `OBJECTID`. This preserves duplicate building IDs and keeps point-cloud features joinable when they were extracted from a spatial subset of the full BBED layer.

## Setup

The recommended environment includes PDAL for safe processing of the multi-gigabyte LAS files:

```powershell
conda env create -f environment.yml
conda activate dynacity-forecasting
```

For the tested lightweight stack:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

## Pipeline

1. Inspect LAS metadata without loading the cloud:

   ```powershell
   dynacity inspect-las "C:\path\TheStreetScape.las" --sample-classes 20000
   ```

2. Convert an explicitly hydrated LAS file to COPC. Dry-run first:

   ```powershell
   dynacity build-copc input.las output.copc.laz --dry-run
   dynacity build-copc input.las output.copc.laz
   ```

3. Fetch only the allowlisted BBED fields, optionally limited to a UTM 36N bounding box:

   ```powershell
   dynacity fetch-bbed --bbox 732900 3752900 733400 3753400 --output C:\private\bbed.geojson
   ```

4. Extract BBED-building morphology from the working LAS tile:

   ```powershell
   dynacity extract-features --las TheStreetScape.las --bbed C:\private\bbed.geojson --output C:\private\lidar-features.csv
   ```

5. Build and validate the temporal panel:

   ```powershell
   dynacity build-panel --bbed C:\private\bbed.geojson --lidar-features C:\private\lidar-features.csv --output C:\private\panel.csv
   ```

6. Run the time-forward benchmark and save the selected model:

   ```powershell
   dynacity train --panel C:\private\panel.csv --output C:\private\forecast.joblib
   ```

7. Build a current snapshot and forecast:

   ```powershell
   dynacity build-snapshot --bbed C:\private\bbed.geojson --snapshot-id beirut-2024 --data-version BBED-2024 --output C:\private\snapshot.json
   dynacity forecast --model C:\private\forecast.joblib --request request.json --output C:\private\forecast.json
   ```

8. Serve the same model through HTTP:

   ```powershell
   dynacity serve --model C:\private\forecast.joblib
   ```

See [the forecasting design](docs/forecasting-engine.md) for the status taxonomy, evaluation gate, API behavior, and limitations.

## Verification

```powershell
pytest
```

The tests use synthetic LAS and BBED fixtures. They do not download or redistribute AUB data.
