# Building appearance evidence pilot

The first run selects 100 source buildings nearest the Charles Malek pilot
from 17,397 unique building records across 12 district indices. It reconstructs
projected footprints from the source triangles rather than using bounding boxes
as roof masks. Source geometry and indices remain unchanged.

Run with the existing environment (rasterio, pyproj, Pillow and httpx installed;
new environments can install the project's `appearance` optional dependencies):

```powershell
.venv/Scripts/python.exe scripts/prepare_appearance_evidence.py --output C:/private/dynacity-data/engine/appearance-evidence-20261030
.venv/Scripts/python.exe scripts/discover_street_photo_evidence.py --output C:/private/dynacity-data/engine/appearance-evidence-20261030
```

The roof pipeline range-reads a bounded crop from the existing 31 July 2020
Maxar/OpenAerialMap source. It caches a 2,066,502-byte RGB GeoTIFF with source
URL, ETag, modification date and SHA256. Source index hashes and selected GLB
hashes accompany the inventory. Capture date is separate from server file date.
It requests no Esri imagery and does not download the whole 819 MB source.

The first successful run took 36.93 seconds, producing 74 roof-color candidates
and 26 unknowns. A cache-reusing run took 2.58 seconds. These are local measured
sample processing times, not citywide forecasts or human-review time.

The sampler insets footprints by 1 m, checks valid pixels, rejects resolution
coarser than 1 m/pixel, and heuristically excludes very dark, near-white and
green-dominant pixels. It records median apparent RGB, not physical albedo or a
material identity. Unknowns in this run: 23 failed heuristic retention, two had
insufficient pixels and one had an empty inset. Sampling cannot establish
alignment: roof displacement, inferred source placement, occlusion and image
age require visual review. Bright true roofs can be rejected by the conservative
heuristic. At this roof-candidate stage, no candidate was automatically approved
and every facade was unknown. A subsequent manual review corroborated one
historical facade, as described below.

The output `review.html` shows 100 aerial crops, source-footprint outlines,
candidate color swatches and unknown reasons. `evidence.json` retains source
IDs and historical observations. Private images and downloaded data stay
outside Git. The source is recorded as CC BY-NC 4.0 in `viewer.py`; the live
OAM metadata endpoint returned HTTP 500. This is a research/review dataset,
not a production texture export or freshly verified provider-license record.

Street discovery uses the working `api.kartaview.org` endpoint: the hostname
in the provider's older documentation failed TLS validation. The script checks
application status 600 as well as HTTP success. It discovers at most three
sequences and three 100-frame pages per sequence, caches responses, filters
frames within 500 m and lists at most three photos within 80 m per building.
Proximity is only a review shortlist: heading, visibility, occlusion, capture
date and identity still need validation. It downloads no street images and
confirms no facade matches. Mapillary access was not configured in the checked
environment. KartaView terms identify imagery as CC BY-SA 4.0 and require
credit to Grab and KartaView Contributors; include provenance for any future
extraction or image reuse.

The bounded discovery returned 241 unique frames within 500 m; 66 of the 100
sample buildings have a frame within 80 m. The discovered sequences date to
2018. Three pages were inspected for each of three sequences; some sequences
have additional unqueried pages. These figures are proximity coverage, not
confirmed building visibility. `street-review.html` links the nearest candidate
photos with dates and headings. A cache-only rerun makes no API calls.

Seven focused tests cover coarse resolution rejection, pixel masking, dark/
green exclusion, insufficient data, invalid inputs and georeferenced sampling
that retains an unknown facade and image provenance. No Unreal assets changed.

References:

- https://kartaview.org/doc/photos
- https://kartaview.org/terms
- https://help.mapillary.com/hc/en-us/articles/360010234680-Accessing-imagery-and-data-through-the-Mapillary-API

## Street visibility and reviewed observations

`scripts/screen_street_photo_evidence.py` reconstructs 432 source footprints
intersecting the sample neighborhood plus a 100 m margin. It screens 838 cached
camera records against the 100 sample buildings in about 4.3 seconds on the
first measured run. True camera headings are converted to grid headings using
a geodesic forward point and the source CRS, avoiding a true/grid north mix-up.
Several wall points are tested against a spatial index of the neighboring
building footprints; rear walls and walls blocked by another building fail.

There are 33 buildings with a geometrically plausible view and 82 candidate
pairs after camera-station deduplication. The half viewing angle of 50 degrees
is an explicit assumption because the provider commonly records zero FOV.
These counts do not establish that the building is identifiable or unobstructed
by trees, vehicles, scaffolding or a windscreen. Source/GPS alignment remains
inferred. Six geometry tests cover heading wrap, near versus rear walls,
occlusion, a camera inside a footprint, viewing angle and range limits.

`scripts/prepare_street_evidence_review.py` greedily selects shared photographs
to cover the candidate buildings, caches image bytes with hashes and source
metadata, and draws numbered footprint maps beside the original processed
street photos. Eight photographs covered 23 candidates and occupied 8,499,859
bytes; acquisition plus map preparation took 131.83 seconds and a cached rerun
took 1.36 seconds. Seventeen photographs are sufficient to show all 33 candidate
buildings in this bounded metadata sample. A geometric candidate can still be
absent or unidentifiable in the actual photo.

The first eight original processed photographs were visually inspected. Five
are useful for architectural review; three are poor for wall-color extraction
because of blur, trees, glare or overexposure. These are qualitative reviews,
not calibrated image-quality scores. Three photo-backed style references are
stored privately in `appearance-style-references.json`: pale cream recessed
balconies, gray panels/glass balconies, and cream/reddish-brown bands. The first
two remain unassigned; the third has a corroborated historical building identity.
No physical material or albedo was measured.

One facade observation is corroborated: source ID
`beirut001:d31eb718-f947-49d5-b82d-14838045a9c7` (building 65 in the review).
The LGB BANK sign is readable in processed photo `1288771577` from 21 July
2018. The independently cached OSM node 4708488989 is tagged as an LGB Bank ATM
and lies 2.718 m from that source footprint; the next nearest footprint among
all 432 neighborhood buildings is 26.790 m away. The OSM extract was retrieved
24 October 2026, not in 2018. The photo, sign, mapped anchor and route context
support the historical appearance assignment; they do not establish current
condition, hidden elevations, source height or physical wall composition.
Visible features are cream/off-white areas, reddish-brown horizontal bands,
glazed balcony fronts and recessed balcony areas.

Keep `reviewed-observations.json` separate from generated candidates.
`scripts/apply_appearance_review.py` validates the selected source IDs,
geometry signature and photo bytes before producing
`appearance-evidence-reviewed.json`. The candidate roof dataset remains
reproducible, with zero automatic approvals. Reviewed facade counts are separate
from roof candidate counts: one historically observed facade, 99 unknowns in
the 100-building sample. Four additional tests cover immutable overlays,
photo hash drift, wrong evidence kinds and selection changes. Seventeen focused
tests pass across the three evidence modules.

Continue with:

```powershell
.venv/Scripts/python.exe scripts/screen_street_photo_evidence.py --output C:/private/dynacity-data/engine/appearance-evidence-20261030
.venv/Scripts/python.exe scripts/prepare_street_evidence_review.py --output C:/private/dynacity-data/engine/appearance-evidence-20261030 --photo-budget 17
.venv/Scripts/python.exe scripts/apply_appearance_review.py --output C:/private/dynacity-data/engine/appearance-evidence-20261030
```

No Unreal changes are established by this evidence pipeline. Historical photo
observations are inputs for later material selection, not a completed visual
rollout. KartaView attribution accompanies photos and OSM attribution accompanies
mapped anchors; the aerial source remains the separately recorded noncommercial
research context described above.

### Reference facade loading integration — 31 October 2026

The separately installed historical LGB approximation now accompanies the normal
full-city/Remeil editor loading path. `city_world.load_editor_geometry()` resolves
the three replacement actors through `load_reference_facade.installed_guids()`
and the saved installation's actor paths. Renaming a label does not break the
association, and unrelated actors with the same label are excluded. An incomplete
or invalid existing installation fails before loading. Projects without an
installation manifest retain their previous source-loading behaviour; recovery
from a lost manifest is not established.

Checkpoints resolve against the active Unreal project, including when development
tools import the module from the repository. Native testing unloaded all three
owned actors, restored them through the ordinary Remeil load, and checked a second
load for idempotence. The retained source geometry, transforms and collision pass
verification; the saved map and asset packages were unchanged. This repairs editor
visibility, not facade dimensions/materials, cold-start behaviour or runtime
streaming. The historical photograph remains the existing catalogued original;
no new image, current observation or facade identity was added.

[Native loading record](C:/private/dynacity-data/engine/goal-20261031/04-appearance/codex-loading-integration.json),
[source/instance verification](C:/private/dynacity-data/engine/goal-20261031/04-appearance/native-verification-codex.json),
[paired retained captures](C:/private/dynacity-data/engine/goal-20261031/04-appearance/retained-evidence),
and [independent Codex QA](C:/private/dynacity-data/engine/goal-20261031/05-appearance-qa).

The completed bounded review inspected all 17 selected processed street photos:
12 are useful for architectural review and five are poor for color extraction.
They total 19,114,941 bytes. The expansion from eight cached images to seventeen
took 164.60 seconds; a subsequent full cached photo/map rebuild took 2.43 seconds.
Do not interpret photo quality as confirmed candidate-building visibility.
`street-identity-review.html` pairs each photo with its footprint map, quality
notes and the corroborated match. Green marks the historical match; yellow
marks candidates still requiring identity corroboration.

`scripts/report_appearance_evidence.py` consolidates the stage reports in
`evidence-progress.json` and writes `capture-evidence-gaps.geojson` in geographic
coordinates. The 99 unresolved facade targets consist of 32 buildings with
geometric candidates but no unique identity corroboration and 67 with no clear
wall in the assumed viewing cone in this bounded metadata search. These are
building evidence targets, not camera positions, walking routes, or assertions
that the provider has no other imagery. Roof coverage remains 74 unapproved
candidates and 26 unknowns.

## LGB reference facade missing in a live session (31 October 2026)

The saved map still registers the three LGB reference-facade actors (visual
source batch, 228 ledges, 228 glass panes), but in the observed editor session
(PID 53412) World Partition had not loaded them. Meanwhile
`buildings_remeil_material_9`, the original source batch, is saved hidden because the
visual copy replaces it. With the copy unloaded, the LGB tower and the other
buildings in that batch did not render. A capture from the position of the
21 July 2018 KartaView frame (camera 34.7 m from the wall, heading grid 87.95°)
showed a plain pink block where the tower should be.

`engine/unreal/Content/Python/load_reference_facade.py` loads exactly the three
owned actor paths and refuses to continue if one is not registered. Loading
saved nothing, changed no transforms and left source collision untouched. The
existing verifier, writing to a separate folder, passed live: 228 and 228
instances, 10,388 source triangles (44 target, 10,344 other), maximum position
error 0 cm, yaw error 2.6e-6 degrees, no collision on owned visuals, original
mesh, collision and transform unchanged. Reload after restart was not repeated
(it would restart someone's editor); the 30 October restart verification remains
the saved-state evidence.

Visual comparison against the dated photo is qualitative only. The model shows
cream body, brown bands and glazed fronts, supported by the photo. It differs:
real balcony slabs are deeper and darker chestnut with cream stone behind;
the model's ledges are thin and look orange in sunlight (the photo is overcast),
some glass panes protrude past the right edge, and the ground floor is
estimated glass, not the recessed LGB storefront. Camera FOV, time of day and
lighting are not matched. This is historical (2018) appearance, not current
condition. Artifacts: `goal-20261031/04-appearance/` (retained-evidence,
native-verification-live.json, project-file-hashes.json).
