# Beirut flood pilot: real-input acquisition inventory

Audited 16 October 2026. **A real, calibrated street-flood pilot is not yet supportable.** Genuine rainfall archives, surveyed ponding/condition mapping and existing building/context data are available. A verified stormwater hydraulic network, adequate bare-earth terrain, matched event forcing/boundaries and independent event observations are still missing. No missing parameter has been filled with a Beirut numerical default.

19 October follow-up: [ground extraction review](ground-review.md) now recovers supported candidate elevations from the original local LAS and the existing draft road/vehicle annotations. It deliberately retains unknown datum and incomplete coverage; it does not establish accepted hydraulic terrain. The seven additional OneDrive source clouds remain cloud-only; a bounded header read of `TheStreetScape.las` failed with an OS timeout. Focused local filename searches for INP, DWG/DXF, SHP/GPKG, storm, drain and rainfall files did not surface a network deliverable; this is not an exhaustive content search. Fresh ArcGIS catalog searches produced no accepted connected Beirut drainage model; US places named Lebanon/Mt. Lebanon were excluded. Two bounded Overpass attempts failed (timeout/HTTP 500), so no absence or completeness conclusion about OSM drains follows. Raw discovery responses and the failed-query record are retained privately in `flood-discovery-20261019/`.

## Delivered files and machine handoff

Raw downloads and derived observations are outside Git, in:

`/Users/johnkofdarelli/dynacity-private/data/flood-inputs-20261016/`

The directory is durable local storage, rather than the temporary acquisition staging directory. Do not commit or publish these files. Public accessibility does not establish a redistribution license.

| File | Actual content and use |
| --- | --- |
| `real-input-handoff.json` | Machine-readable inventory, source URLs, byte SHA256 checksums, retrieval timestamps, explicit license fields, local asset audit, acquisition failures, rainfall summaries and GIS category counts. `planning_ready=false`; unsupported model inputs, event and hydraulic pilot boundary remain `null`. This is an acquisition manifest, **not** a solver input or readiness certificate. |
| `rainfall-accumulations.csv` | 3,518 precipitation fields decoded from genuine ISD records, with source filename/line, UTC interval endpoints, accumulation period, original encoded amount, usable amount, raw tag and condition/quality flags. Blank values remain unknown. |
| `beirut-2005-isd.gz`, `beirut-2010-isd.gz`, `beirut-2018-isd.gz`, `beirut-2023-isd.gz` | Original NOAA station archives, retained unchanged. Genuine observations do not imply sufficient rainfall completeness. |
| `isd-history.csv`, `isd-format.pdf`, `isd-format.txt` | NOAA station catalog, original field specification and local text extraction. |
| `unhabitat-beirut-stormwater-features.json` | 224 mapped **water-ponding points**, not inlets. Public ArcGIS JSON geometry and structural attributes only. |
| `unhabitat-beirut-street-condition.json` | 679 street geometries with stormwater condition and survey-year attributes. No pipe graph. |
| `hamra-profile-2020.pdf`, `hamra-stormwater-page70.png` | Official downloadable UN-Habitat/RELIEF Centre report and visually verified PDF page 70, printed page 64, Figures 42–43. Documentary maps; not georeferenced hydraulic input. |
| `*-layers.json`, `unhabitat-*-layer.json`, `unhabitat-*-item.json`, `unhabitat-*-data.json` | Actual saved service schemas, item terms and app/webmap configurations used to establish field content and coverage. |
| `local-assets.json`, `acquisition-attempts.json`, `*.provenance.json` | Existing private asset audit, unsuccessful/rejected acquisition attempts, and original-response source/checksum sidecars. |

The small repository helper [audit_flood_inputs.py](../../scripts/audit_flood_inputs.py) regenerates the CSV and handoff from the preserved originals, verifies sidecar checksums and refuses a directory inside this repository:

```sh
.venv/bin/python scripts/audit_flood_inputs.py \
  /Users/johnkofdarelli/dynacity-private/data/flood-inputs-20261016
```

It does not download, invent a network, choose a storm, fill missing rainfall, assign model parameters or run a simulation. SHA256 values in the handoff fingerprint the saved bytes, not the scientific truth of the source.

## Actual acquired survey/context GIS

The official [UN-Habitat neighbourhood geoportal](https://lebanonportal.unhabitat.org/geoportal/) links to [this public application](https://un-habitat.maps.arcgis.com/apps/webappviewer/index.html?id=3a38c8bbdc104a7ebb41089644aa1505). Its saved configuration resolves webmap `5bafb815d7a840daa41a87970b41a040` and these services:

| Verified layer | Actual schema / extracted coverage | Limits |
| --- | --- | --- |
| [Stormwater feature layer 5](https://services3.arcgis.com/YFWOEqdx8EzAkyqs/arcgis/rest/services/NBHs_Infra_Features_WML_031319/FeatureServer/5) | `FID, OBJECTID_1, Subject, Feature, NBH_Name`. All 224 retrieved features have `Feature=Water ponding`. | No inlet, catch-basin, manhole or outfall identity; no dated storm, measured depth, duration, hydraulic invert or pipe connection. `hasZ=true` is a container property; a checked point has Z=0 with no vertical reference. Never use these Z values as elevations. |
| [Street condition layer 0](https://services3.arcgis.com/YFWOEqdx8EzAkyqs/arcgis/rest/services/NBHS_Infra_Line_WebMapLayer_030419/FeatureServer/0) | Extract preserves `FID, Road_ID, NBH_Name, StormW_Dra, Year`. 385 records say `No drains`, 112 `Blocked drains`, 171 `Functional drains`; 11 are blank. Survey-year values are 2016–2019. | Street segments and categorical survey observations, not conduit centre-lines or a capacity/blockage measurement. Blank condition remains unknown. Database editing dates do not establish field-survey dates. |

Both extracts were queried in EPSG:32636, with `exceededTransferLimit` absent and below the requested 2,000-record response limit. The retrieval envelope `(726862,3748245,736072,3756145)` comes from the existing terrain rectangle; it is **not** a catchment delineation. Coverage includes Daouk-Ghawash, Hayy El-Jadid, Hayy Tamlis, Karm El-Zeytoun, Maraash, Nabaa and Sabra, including areas outside municipal Beirut. This is not complete Beirut drainage coverage.

Karm El-Zeytoun is a concrete **survey-reconciliation candidate**: the extract contains 146 street records labelled 2019 and nine ponding points. Those points have no event date. The candidate is bounded by actual mapped features, but a hydraulic domain must additionally include contributing upstream area and downstream outlets. No hydraulic pilot boundary or event has been chosen merely to make the model run.

The [feature-service item](https://www.arcgis.com/sharing/rest/content/items/f3fa432c75314caa93824b6beb02139f?f=pjson) and [street-service item](https://www.arcgis.com/sharing/rest/content/items/0bb36558e59e4d1ea8d315aa3cc2b463?f=pjson) returned no explicit `licenseInfo`. Extracts are retained for local research; redistribution/derived-database terms remain unverified. Do not assume BBED's ODbL applies to these separate UN-Habitat services.

The [Hamra profile](https://unhabitat.org/sites/default/files/2020/11/hamraneighbourhoodprofilebeirutlebanonoct2020_0_1.pdf) independently provides mapped stormwater condition, ponding and building connections. The reviewed page describes partial joining of stormwater and wastewater at street level. Its map is not a connected pipe survey; the original GIS, event dates, measured levels and present-day condition were not obtained. Hamra is also absent from the acquired geoportal subset. No map symbols were turned into invented surveyed coordinates.

## Measured rainfall: files verified, temporal sufficiency fails

[NOAA ISD](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database) provides public original observations. The downloaded station catalog identifies `401000-99999`, `RAFIC HARIRI INTL`, ICAO `OLBA`, latitude 33.821, longitude 35.488, station elevation 26.5 m. These are **catalog metadata**, not a pilot rain-gauge location or surveyed elevation benchmark. The catalog's available record span ends 24 August 2025; it does not prove rainfall continuity within that span.

| Original archive | Weather records | Precipitation fields | Non-missing encoded amounts, including trace | Positive encoded amounts | Accumulation periods |
| --- | ---: | ---: | ---: | ---: | --- |
| [2005](https://www.ncei.noaa.gov/pub/data/noaa/2005/401000-99999-2005.gz) | 8,717 | 770 | 761 | 153 | 6 / 12 h |
| [2010](https://www.ncei.noaa.gov/pub/data/noaa/2010/401000-99999-2010.gz) | 8,907 | 1,304 | 1,297 | 109 | 6 / 24 h |
| [2018](https://www.ncei.noaa.gov/pub/data/noaa/2018/401000-99999-2018.gz) | 9,901 | 831 | 4 | 0 | 6 / 24 h |
| [2023](https://www.ncei.noaa.gov/pub/data/noaa/2023/401000-99999-2023.gz) | 9,833 | 613 | 0 | 0 | 6 h |

Counts describe records, **not independent non-overlapping rainfall intervals or complete years**. In 2005 there are 26 trace fields and nine missing amounts; in 2010 there are 34 trace fields and seven missing amounts. The 827 remaining 2018 fields and all 613 in 2023 encode missing amount and an impossible/inaccurate condition. Recent weather-record density must not be mistaken for rainfall coverage.

The [ISD specification, printed page 13 onward](https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf) defines accumulation hours, tenths-of-mm encoding, missing sentinels, trace and quality flags. The handoff preserves them: trace does not become a measured zero, and missing amounts do not become dry periods. All numeric amounts in these four files have quality code 1; this is source QC, not independent local validation. Overlapping 6-/12-/24-hour totals cannot simply be summed. None supplies measured sub-hourly event forcing, and airport representativeness for a selected neighbourhood remains unverified.

The [2026 Beirut IDF paper](https://www.frontiersin.org/journals/water/articles/10.3389/frwa.2026.1727182/full) uses IMERG and temporal disaggregation, with a government IDF reference. Its derived intensities are estimates, not measured sub-hourly hyetographs. No raw station-event table was obtained from that paper. The cited 1982 stormwater master plan is a useful archive lead; the linked studies.gov.lb record failed TLS hostname verification. No certificate bypass was attempted.

## Existing local assets and other verified schemas

The private data root `/Users/johnkofdarelli/dynacity-private/data/` was inspected read-only. Focused filename searches of Documents, Downloads and StudioProjects found the known AlMasar LAS/terrain work, not a documented stormwater CAD/GIS or surveyed DTM. This is not proof that such data exists nowhere else.

| Asset | Verified content | Fitness and remaining unknowns |
| --- | --- | --- |
| `bbed.geojson` and sibling manifest | 3,349 features, EPSG:32636; bounds `(728354.2408,3749735.2180,734620.2747,3754655.5401)`. Original retrieval 25 September 2026. Manifest canonical JSON SHA256 verified. Raw-byte SHA256 differs by serialization as expected; both hashes are retained. | Building/context geometry, not a watertight hydraulic barrier model. No measured thresholds, openings, kerbs or flood-proofing. [BBED's published ODbL attribution](https://beiruturbanlab.com/en/Details/845) applies to its released layers. |
| `terrain.npz` | 790×921, EPSG:32636, 10 m output posting; existing filtered/interpolated Copernicus GLO-30 workflow. Bounds match the retrieval envelope above. | Estimated ground from a DSM, not demonstrated street-scale bare earth; no local vertical accuracy, acquisition date or checkpoint tie. Output spacing does not create finer source detail. See [terrain audit](terrain.md). |
| AlMasar LAS and `work/terrain.npy` in Documents project | Photogrammetric cloud and draft derived terrain exist; prior audit records missing embedded CRS/vertical reference and unvalidated classification/interpolation. | A candidate for survey-led reconstruction and independent checks. LAS format or draft ground labels do not establish bare earth or a vertical datum. No culvert/underpass/kerb elevations inferred. |
| [BBED basemap](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/BaseMap_BBED2024/FeatureServer/layers) | Actual saved schemas include river, contour, road edge, garden and buildings. | Contour elevation attributes and road-edge geometry lack required benchmark/kerb hydraulic detail. Schema presence is not an accepted DTM deliverable. |
| [Water_BED](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/Water_BED/FeatureServer/layers) | Water transmission/distribution metadata; a transmission layer has `Diameter`, `Material`, etc. | **Water supply**, not stormwater. These fields cannot populate drainage pipes. |
| [Sewage_Mapping_Layer](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/Sewage_Mapping_Layer/FeatureServer/0) | `Waste_Type`, `Verification` and editing identifiers, not a pipe graph. | Sewer/discharge mapping does not prove stormwater connections; no hydraulic fields delivered. |
| [Landuse_Karantina](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/Landuse_Karantina/FeatureServer/0) | Saved schema: `FID, Id, Typology, Shape__Area, Shape__Length`, EPSG:3857 extent around Karantina. | Only schema inspected; no land-cover feature extract or accepted license/date/imperviousness calibration. It is not measured infiltration or Manning roughness. |

## Documented acquisition leads and blockers

These are evidence of relevant work or data custodians, **not acquired hydraulic datasets**. No one was contacted and no request was sent.

| Source / custodian lead | What was verified | What still must be supplied |
| --- | --- | --- |
| [SEATEC: Beirut Storm Water Plan](https://seatec-sarl.com/project.php?id=233) | Saved project page identifies Municipality of Beirut as client, 2020 conceptual design, city storm-flow estimates and marine-outfall zones. | Actual drawing, calculations, CRS/datum, ownership/reuse terms, version and existing-versus-proposed status. Conceptual outfalls are not surveyed operational outfalls. |
| [Rafik El Khoury: Hay Assolom Lot IV-3](https://www.rafikelkhoury.com/project/146) | Consultant describes stormwater construction separately from sewer works and preparation of as-built/O&M documents. | Corresponding CDR/municipal/utility/consultant as-built GIS/CAD, surveyed node/inlet/outfall coordinates, pipe endpoints/diameters/inverts, completion revisions and condition. Project-level diameter ranges cannot populate individual pipes. Location is southern suburbs; overlap with a chosen pilot is unverified. |
| [CDR Capital Investment Programme](https://www.cdr.gov.lb/CDR/media/CDR/StudiesandReports/CIP/Cedre_Capital-Investment-Programme-CIP-ENGLISH.pdf) and [reporting portal](https://portal.cdr.gov.lb/) | Official programme describes stormwater/sewer separation work and remaining upgrades; portal identifies infrastructure contracts. | Original asset GIS/CAD and acceptance/survey records, not contract summaries. Installed status and separation must be checked at the pilot and event date. |
| [KarimYassine_Updates_WP5_2026_07_30](https://docs.google.com/presentation/d/19sJMW80QAIuASlOzZ5it2CzDOeoQR8c378zI7wzoNV0/edit) | Connected Drive text readback lists stormwater drainage and sewer network as categories still to acquire/import. | Actual deliverable files. Drive searches for DYNAcity, drainage, stormwater and rainfall did not yield a usable network, rain-gauge file or survey DTM. Search is not an exhaustive audit of every shared folder. |
| [NCNE flood dashboard](https://ncne.gov.lb/flood-dashboard/) | Public flood page describes monitoring/model infrastructure and links Ghzayel/Ostouane applications. | Beirut event rain/river-level records and independent urban inundation observations; dashboard descriptions are not delivered data. |
| [EGU24-957 high-resolution flood study](https://meetingorganizer.copernicus.org/EGU24/EGU24-957.html) | Describes 15 cm terrain and January 2019 crowdsourced validation in **Ostouane**, not Beirut. | Beirut coverage and actual measured-data release. Do not transplant its terrain resolution, event or validation observations to Beirut. |
| [IHO 2019 Lebanon assessment](https://iho.int/uploads/user/Capacity%20Building/Reports%20Assessments/2019/2019-A09-TV_Lebanon_Report.pdf), [PSMSL](https://psmsl.org/data/obtaining/), [UHSLC](https://uhslc.soest.hawaii.edu/datainfo/) | The historical IHO report discusses Lebanese tide-gauge responsibility and then-current absence. Public service discovery did not deliver a Beirut gauge series. | Current measured coastal levels at the event time, sensor datum, benchmark tie and outfall applicability. A 2019 statement does not establish the present state of the network; no zero sea level is assumed. |
| [Copernicus Mediterranean physics reanalysis](https://data.marine.copernicus.eu/product/MEDSEA_MULTIYEAR_PHY_006_004/services) | Official catalog lists hourly sea-level product `cmems_mod_med_phy-ssh_my_4.2km_PT1H-m`. | No event subset acquired. This is a model/reanalysis candidate, not measured local outfall level; verify its reference and coastal-cell suitability before any sensitivity use. |

## Required input status and acceptance conditions

| Required input | Status at handoff | Acquisition/acceptance requirement |
| --- | --- | --- |
| Inlets / catch basins / manholes / storm outfalls | **Unavailable** as a surveyed connected asset inventory | Supply asset ID, geometry, survey date/accuracy, rim/invert datum, capture/outlet type and inspection/operational status. Ponding points cannot stand in for assets. |
| Pipe graph, diameters, inverts, capacity and condition | **Unavailable** | Supply authoritative as-builts, endpoint IDs and connectivity, section/material, levels, restrictions/pumps/controls and event-date condition. Capacities may later be documented derived calculations, explicitly separated from measurements. |
| High-resolution bare-earth terrain and datum/checkpoints | **Unavailable** | Supply native DTM/ground points, classification method, coverage/nodata, date, accuracy and independent benchmarks; document any vertical transformation. |
| Kerbs, walls/building barriers, underpasses and culverts | **Unavailable** as hydraulic geometry | Survey breaklines, opening widths/heights, floors/inverts and connectivity; verify against terrain and network datum. |
| Dated measured rainfall | **Partially acquired**, coarse historical airport totals | Obtain local event-gauge hyetograph with measured interval, timezone, QA, gaps, exposure/instrument metadata and spatial representativeness; do not disaggregate airport totals as measurements. |
| Infiltration / imperviousness / land-cover / roughness | **No measured local hydraulic parameters** | Reconcile dated imagery/survey land cover with tests or justified parameter sources; separate documented estimates from measured infiltration. Never assign a citywide soil conductivity or Manning n as Beirut evidence. |
| Coastal / river boundary hydrographs | **Unavailable** | Obtain event water levels/discharge, rating curves where applicable, datum/control structures and uncertainty. The river polyline is not a hydrograph or channel cross-section. |
| Independent flood depth / extent / timing | **Unavailable** for an identified event | Obtain surveyed marks/sensors or independently documented event imagery with locations, times, water-depth reference, uncertainty and wet/dry extent; keep calibration and validation observations separate. General ponding points are qualitative context only. |

No quantitative validation, intervention benefit, drainage design, flood depth, return period, cost or capacity claim is warranted from this inventory alone. The acquired files make the evidence gaps inspectable and provide real material for a bounded survey/data reconciliation effort; the missing values remain explicit.
