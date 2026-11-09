# Automated facade evidence for all processing passes

`scripts/collect_batch_facade_evidence.py` performs deterministic discovery,
cache validation, geometric screening, image selection, downloads and evidence
packet generation. It makes no LLM calls and does not edit Unreal.

Run all five passes with one command from `C:/Users/PC/DYNAcity`:

```powershell
.venv/Scripts/python.exe scripts/collect_batch_facade_evidence.py
```

Run only the first 3,500-building pass:

```powershell
.venv/Scripts/python.exe scripts/collect_batch_facade_evidence.py --batch 1
```

Process cached evidence without any network requests:

```powershell
.venv/Scripts/python.exe scripts/collect_batch_facade_evidence.py --offline
```

## What the script does

1. Reads the current prepared passes and verifies their assignment checksums,
   unique building ownership and source identity indices.
2. Reuses citywide street metadata, all cached street photographs and the earlier
   pilot's sequence pages and images. Roof evidence remains separate.
3. Groups missing discovery queries into shared 500 m areas. Discovers photo
   sequences, then paginates breadth first so new routes are considered before
   repeatedly extending one route.
4. Tests camera heading, 80 m distance and building occlusion against the whole
   city footprint index. Invalid camera metadata is excluded. Per-frame screening
   results are cached against camera data, algorithms and the inventory hash.
5. Prefers already-cached images, then greedily selects frames covering buildings
   without a cached candidate view. Multiple buildings may share one photo.
6. Downloads selected public processed images with byte limits, validates that
   they decode, and retains original checksums, date, URL, attribution and license.
7. Produces per-pass records for every selected building, explicit coverage gaps,
   lightweight thumbnails and photo/map review pages. Candidate polygons are
   numbered beside the original photo and camera position. These are schematic
   map views, not calibrated image projections.
8. Publishes progress after each ten new metadata calls and at completion. Rerun
   the same command after interruption to reuse cached queries, screening and
   photographs, including caches from other pass runs. A corrupt cached image fails validation rather than becoming
   reviewed evidence.

## Budgets and coordination

Defaults per invocation are 200 new metadata calls, five pages per discovered
sequence, 500 new photos and 750 MB of photo downloads. Metadata calls are
conservatively throttled to 60/hour; image downloads are spaced three seconds
apart. These are local budgets, not a claim about published provider quotas.
Network collection can take hours and runs without agent involvement. The
script sleeps and checkpoints itself; no repeated assistant calls are needed.

The command shares the original citywide collector's lock and request timestamp.
If that collector is running it processes caches first, then waits up to 180
minutes for exclusive network ownership. Three recorded network failures stop
further attempts in the bounded pass. Retrying the command preserves successes.
The byte budget applies to original image downloads; metadata, thumbnails and
reports use additional disk space.

Options include `--max-queries`, `--max-photos`, `--max-download-mb`,
`--pages-per-sequence`, `--requests-per-hour` (1–60) and
`--wait-for-collector-minutes`. For a bounded live check:

```powershell
.venv/Scripts/python.exe scripts/collect_batch_facade_evidence.py --max-queries 1 --max-photos 1 --max-download-mb 15 --wait-for-collector-minutes 0
```

## Outputs and limits

The default all-pass page is:

```text
file:///C:/private/dynacity-data/engine/facade-batch-evidence-20261030/all-batches/index.html
```

First-pass-only output uses `batch-01/index.html` in the same root. The output
folder also contains `report.json`, `network-state.json`, per-pass JSON files,
`photo-catalog.json`, original downloaded images, query caches and review pages.
The root `latest-run.json` points to the last invocation's output.

Processing 3,500 building records does not guarantee photos of 3,500 buildings.
Geometric candidates are not confirmed building identities. Historical images,
GPS, camera calibration, trees and hidden surfaces remain limitations. The
script does not infer wall color from roofs, approve facade appearances or
invent material identity, window counts or balcony dimensions. Existing reviewed
observations are retained; new candidates need identity and architectural review
before entering the Unreal appearance pipeline. Street images are attributed to
Grab and KartaView Contributors under CC BY-SA 4.0, with their cached provenance.

API reference: [KartaView photo documentation](https://kartaview.org/doc/photos).
The implementation follows the parameter/response formats already demonstrated
by the project's cached successful responses; the documentation page currently
requires client-side rendering.
