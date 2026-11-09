"""Generate a local, visual index of the Beirut simulation work and photos."""
import argparse
import html
import json
from pathlib import Path

from dynacity.building_appearance_evidence import digest


def generate(root):
    root = Path(root)
    progress = json.loads((root/'evidence-progress.json').read_text(encoding='utf-8'))
    photo_report = json.loads((root/'street-photo-review-report.json').read_text(encoding='utf-8'))
    assessments = json.loads((root/'photo-visual-assessments.json').read_text(encoding='utf-8'))
    reviews = {item['photo_id']: item for item in assessments['records']}
    cards = []
    for photo in photo_report['photos']:
        pid = str(photo['photo_id'])
        if not pid.isdigit():
            raise ValueError('Unexpected photo ID')
        photo_path = root/'street-photos'/(pid+'.jpg')
        map_path = root/'street-photos'/(pid+'-map.png')
        review = reviews[pid]
        if digest(photo_path) != photo['sha256'] or digest(photo_path) != review['photo_sha256'] or not map_path.is_file():
            raise ValueError('Photo, review or map mismatch: '+pid)
        status = 'Useful for architectural review' if review['status'] == 'usable_for_visual_architecture' else 'Limited for color extraction'
        cards.append(f'<article class="photo"><a href="street-photos/{pid}.jpg">'
                     f'<img src="street-photos/{pid}.jpg" loading="lazy" alt="Street photograph {pid}"></a>'
                     f'<div class="photo-body"><span class="tag">{html.escape(status)}</span>'
                     f'<h3>Photo {pid}</h3><p>{html.escape(photo["captured_at"][:10])} · KartaView</p>'
                     f'<p>{html.escape(review["notes"])}</p>'
                     f'<a href="street-photos/{pid}-map.png">View building map →</a></div></article>')
    shot = Path('C:/Users/PC/Pictures/Screenshots/DYNAcity-street-finish-20261029')
    before, after = (shot/'charles-before.png'), (shot/'charles-after-cold-load.png')
    if not before.is_file() or not after.is_file():
        raise ValueError('Before/after pilot screenshots unavailable')
    latest = root.parent/'appearance-citywide-20261030'/'latest-run.json'
    city_link = ''
    if latest.exists():
        fingerprint = json.loads(latest.read_text(encoding='utf-8')).get('fingerprint', '')
        if len(fingerprint) == 16 and all(c in '0123456789abcdef' for c in fingerprint):
            city_link = f'<p><a href="../appearance-citywide-20261030/runs/{fingerprint}/index.html">Open citywide collection progress →</a></p>'
    passes_link = ''
    passes_pointer = root.parent/'appearance-passes-20261030'/'latest-run.json'
    if passes_pointer.exists():
        pass_run = json.loads(passes_pointer.read_text()).get('fingerprint', '')
        if len(pass_run) == 16 and all(c in '0123456789abcdef' for c in pass_run):
            passes_link = f'<article class="entry"><span class="date">30 OCTOBER 2026 · PROCESSING PASSES</span><h3>3,500 whole buildings per pass</h3><p>All 17,397 indexed buildings are partitioned into five prepared passes: four of 3,500 and a final 3,397. Every source ID belongs to exactly one pass. These are staged processing jobs, not applied Unreal facades or confirmed photo coverage.</p><a href="../appearance-passes-20261030/runs/{pass_run}/index.html">Open pass sizes, evidence counts and manifests →</a></article>'
    ambient_report = root.parent/'ambient-effects-20261030'/'report.json'
    ambient_entry = ''
    if ambient_report.is_file():
        ambient = json.loads(ambient_report.read_text(encoding='utf-8'))
        sky_previews = ''
        if ambient.get('isolated_sky_render_verified') and (ambient_report.parent/'sky-afternoon.png').is_file():
            sky_previews = '<figure><img style="width:100%;border-radius:12px" src="../ambient-effects-20261030/sky-afternoon.png" alt="Isolated Unreal afternoon cloud-material check"><figcaption>Afternoon cloud material · isolated GPU check, not a full-city Play or performance test.</figcaption></figure>'
        ambient_entry = '<article class="entry"><span class="date">30 OCTOBER 2026 · AMBIENT EFFECTS</span><h3>Clouds, lighting and vehicle signals</h3><p>Unreal presentation adds a panning cloud shell, illustrative lighting presets, and brake/turn/hazard fixtures. SUMO owns traffic signal states. No live weather or traffic-demand changes.</p><p>' + html.escape(ambient.get('verification_summary', 'Verification pending.')) + '</p>' + sky_previews + '<a href="../ambient-effects-20261030/report.json">Open verification record →</a></article>'
    live_ambient = root.parent/'ambient-effects-20261031'/'report.json'
    # UX/QA run: links to small durable text reports only (no temporary screenshots, which the user deletes).
    ux_qa = root.parent/'ux-review-20261031'/'05-integrated-qa'/'qa-report.md'
    ux_log = Path(__file__).resolve().parents[1]/'docs'/'beirut-simulation-work-log.md'
    ux_entry = ''
    if ux_qa.is_file():
        ux_entry = ('<article class="entry"><span class="date">31 OCTOBER 2026 · UNREAL UX REVIEW (PARTIAL)</span><h3>Control panel purpose, menu and map review</h3>'
            '<p>Main menu states what the twin is for and labels it a prototype with synthetic traffic and no forecasts; Play opens the menu with the car held; the 2D atlas shows its true source-resolution limit. '
            '<strong>Partial:</strong> native keyboard reliability is not fixed or verified (C++ fix written but not compiled: paging-file build failure) and 3D viewport sharpness was not tested. '
            'Browser component checks are separate from native Unreal checks.</p>'
            f'<a href="{ux_qa.as_uri()}">QA report</a> · <a href="{ux_log.as_uri()}">Work log</a></article>')
    baseline_entry = ''
    baseline_dir = root.parent/'goal-20261031'/'02-baseline'
    network_json = baseline_dir/'beirut-network'/'observation-baseline.json'
    wide_json = baseline_dir/'lebanon-file-wide'/'observation-baseline.json'
    if network_json.is_file() and wide_json.is_file():
        net, wide = (json.loads(f.read_text(encoding='utf-8')) for f in (network_json, wide_json))

        def mae_line(rep):
            if rep['status'] != 'completed':
                return 'Blocked: ' + html.escape(str(rep['blocked_reason'])) + '; no scores reported.'
            r, b = rep['results'], rep['results']['baselines']
            return (f"{r['heldout_cells_eligible']:,} of {r['heldout_cells_total']:,} held-out cells ({100*r['heldout_cells_eligible']/r['heldout_cells_total']:.1f}%) have enough readings to score; "
                    f"the other {r['heldout_cells_below_min_count']:,} ({r['heldout_readings_in_below_min_count_cells']:,} readings) are not scored. "
                    f"Mean absolute error in the file's speed units: {b['global_hour']['metrics']['mae']:.1f} for a global hourly median over {'the selected ways' if rep['domain']['mode'] != 'source_file_wide' else 'all ways'}, "
                    f"{b['road_hour']['metrics']['mae']:.1f} for a per-way hourly median ({b['road_hour']['scored_cells']:,} cells with enough calibration data), "
                    f"{b['road_daytype_hour']['metrics']['mae']:.1f} with day type added ({b['road_daytype_hour']['scored_cells']:,} cells).")
        cfg, dom = net['config'], net['domain']
        baseline_entry = ('<article class="entry"><span class="date">31 OCTOBER 2026 · HISTORICAL BASELINE</span><h3>Held-out Tari\'ak speed baselines on two domains</h3>'
            f"<p>Climatology baselines fitted on {cfg['calibration_years'][0]}-{cfg['calibration_years'][-1]} Tari'ak probe speeds and scored on held-out "
            f"{cfg['holdout_years'][0]}-{cfg['holdout_years'][-1]}, with at least {cfg['min_count']} readings per way, day-type and hour cell.</p>"
            f"<p><strong>Mapped traffic-model ways ({dom['unique_way_ids']:,} unique OSM ways of the saved traffic model file {html.escape(dom['model_file_name'])}, not the current default beirut-traffic-20261024.json; {dom['selected_valid_rows']:,} readings; "
            f"{dom['selected_unique_ways_with_valid_rows']:,} ways have readings):</strong> {mae_line(net)}</p>"
            f"<p><strong>Whole source file (Lebanon dataset per its data package; this file's geographic footprint is not verified):</strong> {mae_line(wide)} "
            'These file-wide scores are not Beirut targets.</p>'
            '<p class="notice">Historical probe-speed climatology, not a simulation and not SUMO validation. The existing traffic model already used this data, so the split does not make it independent. '
            'Cells pool all dates in the years (no season or holiday matching). Readings are pooled individual probe points: points from one trip are not independent and cell medians are not time-weighted. Probe sampling bias is unquantified, and the p10-p90 band is a spread of readings, not a confidence interval or a survey measurement uncertainty. '
            'The speed unit is unresolved (data package says m/s, values look like km/h). Only the model\'s way IDs were used, not its speeds.</p>'
            f'<a href="{(baseline_dir/"beirut-network"/"results.md").as_uri()}">Traffic-model ways</a> · <a href="{(baseline_dir/"lebanon-file-wide"/"results.md").as_uri()}">Whole file</a> · '
            f'<a href="{network_json.as_uri()}">Full record</a></article>')
    reference_loading_entry = ''
    reference_dir = root.parent/'goal-20261031'/'04-appearance'
    reference_check = reference_dir/'codex-loading-integration.json'
    reference_native = reference_dir/'native-verification-codex.json'
    if reference_check.is_file() and reference_native.is_file():
        loading = json.loads(reference_check.read_text(encoding='utf-8'))
        native = json.loads(reference_native.read_text(encoding='utf-8'))
        if loading.get('status') == 'pass' and native.get('status') == 'pass':
            reference_loading_entry = ('<article class="entry"><span class="date">31 OCTOBER 2026 · EDITOR LOADING</span>'
                '<h3>Historical LGB facade returns with its district</h3>'
                '<p>Normal Remeil loading restored the three saved replacement actors after a scoped unload. '
                'Ownership uses the installation record, so renamed actors still work and same-label unrelated actors are excluded. '
                'The retained source mesh, transform and collision passed verification; a second load preserved the recorded state.</p>'
                '<p class="notice">Editor loading verified. Cold restart and Play runtime streaming were not tested. '
                'The facade remains an illustrative adaptation of the July 2018 photograph; its dimensions, floor count, '
                'materials and photographic resemblance were not newly measured or refined.</p>'
                '<div class="split"><div><img src="../goal-20261031/04-appearance/retained-evidence/codex-unloaded-before.png" '
                'alt="LGB facade absent after the scoped test unload"><p class="caption">Before: scoped unload reproduces the missing facade.</p></div>'
                '<div><img src="../goal-20261031/04-appearance/retained-evidence/codex-normal-load-final.png" '
                'alt="LGB facade restored by normal Remeil loading"><p class="caption">After: normal district loading, same camera pose.</p></div></div>'
                '<p><a href="../goal-20261031/04-appearance/codex-loading-integration.json">Native loading record</a> · '
                '<a href="../goal-20261031/05-appearance-qa/qa-final.md">Independent Codex QA</a></p></article>')
    cloud_report = root.parent/'cloud-fix-20261031'/'report.json'
    if cloud_report.is_file():
        cloud = json.loads(cloud_report.read_text(encoding='utf-8'))
        ambient_entry += '<article class="entry"><span class="date">31 OCTOBER 2026 · CLOUD APPEARANCE</span><h3>Softer clouds above the city</h3><p>' + html.escape(cloud['verification_summary']) + '</p><p>Illustrative fair-weather layer; no live weather calibration or citywide GPU benchmark.</p><img style="width:100%;border-radius:12px" src="../cloud-fix-20261031/after.png" alt="Unreal Play cloud appearance above Charles Malek Avenue"><p><a href="../cloud-fix-20261031/report.json">Open cloud verification →</a></p></article>'
    if live_ambient.is_file():
        followup = json.loads(live_ambient.read_text(encoding='utf-8'))
        ambient_entry += '<article class="entry"><span class="date">31 OCTOBER 2026 · PLAY RUNTIME</span><h3>Prepared materials load directly during Play</h3><p>' + html.escape(followup['verification_summary']) + '</p><a href="../ambient-effects-20261031/report.json">Open current verification and cleanup status →</a></article>'
    yellow_report = root.parent/'street-lamps-20261031'/'report.json'
    if yellow_report.is_file():
        yellow = json.loads(yellow_report.read_text(encoding='utf-8'))
        ambient_entry += '<article class="entry"><span class="date">31 OCTOBER 2026 · STREET LIGHTS</span><h3>Original yellow lattice poles installed</h3><p>' + html.escape(yellow['verification_summary']) + '</p><p>Original photo-referenced model; dimensions and mounting remain illustrative. Nine preview placements and one OSM location.</p><a href="../street-lamps-20261031/report.json">Open lamp verification record →</a></article>'
    if yellow_report.is_file() and yellow.get('replacement_installed'):
        ambient_entry += '<figure><img style="width:100%;border-radius:12px" src="../street-lamps-20261031/lattice-pole-play.png" alt="Original yellow lattice street lamps in Unreal Play"><figcaption>Daylight Play appearance; pole dimensions and fixture mounting are illustrative.</figcaption></figure>'
    crossing_report = root.parent/'crossings-20261031'/'report.json'
    if crossing_report.is_file():
        crossings = json.loads(crossing_report.read_text(encoding='utf-8'))
        ambient_entry += '<article class="entry"><span class="date">31 OCTOBER 2026 · MAPPED CROSSINGS</span><h3>' + str(crossings['painted_instances']) + ' painted crossings saved in Unreal</h3><p>Rebuilt only the crossing layer, including older uncontrolled tags. Unmarked and unspecified crossings excluded. ' + str(crossings['road_surface_verified']) + ' placements matched loaded road collision; remaining source-height placements need review. Zebra styles are illustrative unless explicitly mapped.</p><a href="../crossings-20261031/report.json">Open placement verification →</a><p><img style="width:100%;border-radius:12px" src="../crossings-20261031/crossing-sample.png" alt="Native Unreal sample of mapped crossing stripes on the road"></p></article>'
    inventory_report = root.parent/'street-lamp-inventory-20261031'/'report.json'
    if inventory_report.is_file():
        inventory = json.loads(inventory_report.read_text(encoding='utf-8'))
        ambient_entry += '<article class="entry"><span class="date">31 OCTOBER 2026 · LAMP COVERAGE AUDIT</span><h3>Citywide lighting source coverage</h3><p>' + html.escape(inventory['verification_summary']) + '</p><p>Lighting tags describe road segments; they do not locate individual poles.</p><a href="../street-lamp-inventory-20261031/report.json">Open source audit</a></article>'
    photo_batch_entry = ''
    photo_batch = root.parent/'goal-20261031'/'07-photo-batch-2055'
    photo_batch_progress = photo_batch/'coordinator'/'progress.json'
    if photo_batch_progress.is_file():
        batch = json.loads(photo_batch_progress.read_text(encoding='utf-8'))
        photo_batch_entry = (
            '<article class="entry"><span class="date">'
            + html.escape(batch['date_label']) + ' · PHOTO-BACKED BATCH APPEARANCE</span>'
            + '<h3>' + html.escape(batch['stage']) + '</h3><p>'
            + html.escape(batch['summary']) + '</p><p>'
            + html.escape(batch['native_summary']) + '</p>'
            + f'<a href="{photo_batch_progress.as_uri()}">Current verification stage</a> · '
            + f'<a href="{(photo_batch/"photo-library"/"handoff.md").as_uri()}">Photo processing and limits</a> · '
            + f'<a href="{(photo_batch/"photo-library"/"curated-references.json").as_uri()}">Curated reference provenance</a>'
            + '</article>')
    web_mobility_entry = ''
    web_report = root.parent.parent/'web-mobility-20261102'/'verification.json'
    if web_report.is_file():
        web = json.loads(web_report.read_text(encoding='utf-8'))
        web_preview = ('<img style="width:100%;border-radius:12px" src="../../web-mobility-20261102/playground.png" '
                       'alt="Browser pavement controls, walking overlay and simulated traffic">'
                       if (web_report.parent/'playground.png').is_file() else '')
        web_mobility_entry = (
            '<article class="entry"><span class="date">2 NOVEMBER 2026 · WEB MOBILITY</span>'
            '<h3>Pedestrians and a mobility playground in the browser</h3>'
            f'<p>{int(web["pedestrian_segments"]):,} shared pavement paths and about '
            f'{round(web["modelled_occupancy"]):,} modelled PM walkers. Shared data and optional demand/display controls. '
            'The initial animation controls were superseded by street interventions below. '
            'Newer greenery, infrastructure, waterfront and lighting inventories share source data with Unreal.</p>'
            '<p>Proxy demand, not observed counts. Pavement closures remove occupants without pedestrian detours; '
            'no car avoidance or pedestrian–vehicle coupling. Web meshes remain illustrative.</p>'
            + web_preview + '<a href="../../web-mobility-20261102/verification.json">Open verification record →</a></article>')
    web_planning_entry = ''
    planning_report = root.parent.parent/'web-planning-20261102'/'verification.json'
    if planning_report.is_file():
        web_planning_entry = (
            '<article class="entry"><span class="date">2 NOVEMBER 2026 · STREET PLANNING</span>'
            '<h3>Change streets, then compare the consequences</h3>'
            '<p>Select a road section to pedestrianise it, cap motor speed, or give a curb lane to pavements. '
            'SUMO applies lane permissions and speed limits to the scenario while retaining the original comparison. '
            'Restore individual sections or clear all changes.</p>'
            '<p>Hamra section 5847: illustrative pavement area 347 → 578 m² with a lane reallocated, '
            'holding modelled occupancy constant. These are geometric indicators, not calibrated pedestrian '
            'routing or safety predictions. Live citywide traffic and narrow/desktop controls inspected; '
            'visual coverage is one section, not a citywide audit.</p>'
            '<img style="width:100%;max-width:406px;border-radius:12px" '
            'src="../../web-planning-20261102/planning-evidence.png" alt="Live street planning actions in the browser">'
            '<a href="../../web-planning-20261102/verification.json">Planning verification record →</a></article>')
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DYNAcity · Beirut work and evidence</title><style>
:root{{--ink:#182722;--muted:#5a6d63;--green:#164a3c;--accent:#d8ac62;--paper:#f4f3ed;--line:#dbe2da}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 "Segoe UI",Arial,sans-serif}}
a{{color:#176652}}header{{background:#173c33;color:#fff;padding:52px max(24px,calc((100% - 1180px)/2)) 44px}}
.eyebrow{{text-transform:uppercase;letter-spacing:.14em;font-size:12px;color:#e1c68e;font-weight:700}}
h1{{font-size:clamp(34px,5vw,58px);line-height:1.06;letter-spacing:-.035em;margin:12px 0 16px;max-width:800px}}header p{{max-width:720px;color:#d6e5dc;font-size:18px}}
nav{{display:flex;gap:20px;flex-wrap:wrap;margin-top:24px}}nav a{{color:#fff;text-decoration:none;border-bottom:1px solid #9fc9b4}}
main{{max-width:1180px;margin:auto;padding:38px 24px 80px}}section{{margin-bottom:54px}}h2{{font-size:30px;letter-spacing:-.025em;margin:0 0 12px}}
h3{{font-size:18px;margin:8px 0}}p{{margin:8px 0 14px}}.sub{{color:var(--muted);max-width:820px}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.metric,.entry,.photo,.pending{{background:#fff;border:1px solid var(--line);border-radius:16px}}
.metric{{padding:20px}}.metric strong{{display:block;font-size:30px;line-height:1.1;color:var(--green)}}.metric span{{display:block;color:var(--muted);font-size:14px;margin-top:8px}}
.entry,.pending{{padding:24px;margin:14px 0}}.date{{color:#986e31;font-weight:700;font-size:13px}}.split{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.split img{{width:100%;display:block;border-radius:12px}}.caption{{font-size:14px;color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
.photo{{overflow:hidden}}.photo img{{width:100%;aspect-ratio:4/3;object-fit:cover;display:block}}.photo-body{{padding:16px}}.photo p{{font-size:14px;color:var(--muted)}}
.tag{{font-size:12px;font-weight:600;color:#305b4c;background:#e4f0e8;padding:5px 9px;border-radius:30px}}.notice{{border-left:4px solid var(--accent);padding:10px 16px;background:#fbf5e9}}
footer{{font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:20px}}@media(max-width:850px){{.metrics{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:560px){{.grid,.split{{grid-template-columns:1fr}}}}
</style></head><body><header><div class="eyebrow">DYNAcity · project record</div><h1>Building Beirut’s digital twin</h1>
<p>A visual record of the street and mobility pilot, building evidence, photographs and the work still ahead.</p>
<nav><a href="#progress">Progress</a><a href="#work">Work history</a><a href="#photos">Photographs</a><a href="#next">Next steps</a><a href="#sources">Sources</a></nav></header>
<main><section id="progress"><div class="eyebrow">Last documented · 30 October 2026</div><h2>Where things stand</h2>
<p class="sub">The appearance evidence pass covers a sample of {progress['sample_buildings']} buildings near Charles Malek; it is not a citywide completion count.</p>
<div class="metrics"><div class="metric"><strong>16</strong><span>buildings with pilot facade modules</span></div>
<div class="metric"><strong>{progress['historically_corroborated_facades']}</strong><span>historically corroborated facade in the sample</span></div>
<div class="metric"><strong>{progress['shared_photos_reviewed']}</strong><span>street photos reviewed</span></div>
<div class="metric"><strong>{progress['roof_candidates']}</strong><span>roof color candidates awaiting review</span></div></div></section>
<section id="work"><div class="eyebrow">Project history</div><h2>What has been done</h2>
{web_planning_entry}
{web_mobility_entry}
{photo_batch_entry}
{reference_loading_entry}
{baseline_entry}
<article class="entry"><span class="date">30 OCTOBER 2026 · PHOTO IDENTITY REVIEW</span><h3>17 photographs checked against the candidate buildings</h3><p>Rejected a tunnel view and a garage interior, flagged conflicting bank signage, and retained 14 uncertain views for further identity review. Seven candidate pairs were excluded from the downstream review records. No new facade identity was confirmed and no Unreal appearance changed.</p><a href="../facade-batch-evidence-20261030/reviewed/index.html">Open reviewed photographs and rejection reasons →</a></article>
<article class="entry"><span class="date">30 OCTOBER 2026 · AUTOMATED PHOTO PIPELINE</span><h3>One script for all five evidence passes</h3><p>The new script reuses cached photographs, walks photo sequences, screens camera direction and building occlusion, selects useful images, and builds photo/map review packets. It checkpoints itself without LLM calls. Geometric matches remain candidates; missing photographic coverage is reported explicitly.</p><a href="../facade-batch-evidence-20261030/all-batches/index.html">Open automated evidence progress and photo maps →</a></article>
{ux_entry}
{passes_link}
{ambient_entry}
<article class="entry"><span class="date">30 OCTOBER 2026 · BATCH COVERAGE REPAIR</span><h3>Full-height panels on the 16-building pilot</h3><p>Replaced the truncated panel layouts with 2,906 fitted instances on 98 eligible walls across all 16 pilot buildings. Whole-building budget admission prevents partial grids. Short corner facets retain their existing base material. This remains estimated architecture.</p><p><a href="../facade-coverage-20261030/review.html">Open repair comparison, verification and citywide staging →</a></p><div class="split"><div><img src="../facade-coverage-20261030/before.png" alt="Pilot facade before coverage repair"><p class="caption">Before: panels stop below the roofline</p></div><div><img src="../facade-coverage-20261030/after.png" alt="Pilot facade after full-height fitting"><p class="caption">After: fitted rows reach the source wall top</p></div></div><p>Prepared appearance assignments for all 17,397 indexed buildings across 336 owner tiles. Citywide assignments are staged, not imported. Roof observations remain separate from estimated wall palettes.</p></article>
<article class="entry"><span class="date">30 OCTOBER 2026 · REFERENCE FACADE TEST</span><h3>A building tied to a historical photograph</h3><p>Applied cream plaster, brown bands and glass balcony fronts to the corroborated LGB Bank building, using cached authored library pieces. Original source geometry and collision are retained. The result is a partial visual match: spacing, floor count, balcony dimensions and hidden sides remain estimates.</p><p><a href="../facade-reference-20261030/reference-comparison.html">Open historical photo, before/after and validation →</a></p><div class="split"><div><img src="../facade-reference-20261030/street-before.png" alt="Unreal reference building before treatment"><p class="caption">Before the reference test</p></div><div><img src="../facade-reference-20261030/street-after-refined.png" alt="Unreal reference building with cream and brown treatment"><p class="caption">After the reference test · one building</p></div></div></article>
<article class="entry"><span class="date">30 OCTOBER 2026 · AUTOMATION</span><h3>Repeatable evidence collection</h3>
<p>A single pilot command now collects imagery, finds and screens nearby photos, prepares the review pages and refreshes documentation. It reuses cached files and flags new photos for review. The existing Charles Malek cached run took about 13 seconds; new downloads and human review take additional time.</p></article>
<article class="entry"><span class="date">30 OCTOBER 2026 · CITYWIDE COLLECTION</span><h3>Extending to indexed districts</h3><p>The command now batches the 17,397 indexed buildings into 336 roof tiles and 99 street-query areas. It saves checkpoints, rate-limits provider requests and preserves photos for review. These are historical candidates, not confirmed current facade matches.</p>{city_link}</article>
<article class="entry"><span class="date">30 OCTOBER 2026 · BUILDING EVIDENCE</span><h3>Finding real-world references</h3>
<p>We checked historical aerial imagery and street photographs against existing building footprints. Camera direction and neighboring buildings narrowed the street-photo shortlist to 33 buildings with plausible views.</p>
<p>One facade was corroborated using a visible LGB Bank sign and a mapped ATM. Its visible portion has cream areas, reddish-brown bands and glass balconies. The photo dates to 2018; current appearance and hidden sides are unknown.</p>
<p>Another 32 buildings have view candidates needing identity checks, while 67 have no plausible view in the bounded search. Roof colors are candidates only.</p>
<a href="street-identity-review.html">Open photographs beside their building maps →</a></article>
<article class="entry"><span class="date">29 OCTOBER 2026 · UNREAL PILOT</span><h3>Street materials, facade modules and moving traffic</h3>
<p>Applied asphalt and paving materials to selected district batches, added illustrative lane paint and checked existing curb and sidewalk details. Installed 1,200 facade modules on 16 buildings. Native SUMO testing displayed 150 vehicle instances and recorded movement.</p>
<p class="notice">This initial pilot left unfinished upper strips, addressed by the later batch coverage repair above. Traffic is synthetic; gameplay performance and traffic calibration have not been established.</p>
<div class="split"><div><a href="{before.as_uri()}"><img src="{before.as_uri()}" alt="Unreal street before the pilot"></a><p class="caption">Before the street finish pilot</p></div>
<div><a href="{after.as_uri()}"><img src="{after.as_uri()}" alt="Unreal street after the pilot and editor reload"></a><p class="caption">After the pilot and editor reload</p></div></div></article></section>
<section id="photos"><div class="eyebrow">Evidence library</div><h2>Street photographs</h2>
<p class="sub">Click a photo for the full image or open its numbered building map. Yellow map outlines are candidates; the green outline identifies the corroborated historical facade.</p>
<p><a href="photo-catalog.md">Photo catalog and checksums</a> · <a href="review.html">Aerial roof review</a> · <a href="street-identity-review.html">Side-by-side photo review</a></p>
<div class="grid">{''.join(cards)}</div></section>
<section id="next"><div class="eyebrow">Outstanding</div><h2>What comes next</h2><div class="pending"><ol>
<li>Benchmark a varied building sample and district before importing citywide appearance assignments.</li>
<li>Refine the reference facade's corner and balcony forms against the photo; the first Unreal comparison is available above.</li>
<li>Confirm more photo-to-building matches and collect better views where needed.</li>
<li>Benchmark a district-scale workflow before expanding across the city.</li></ol></div></section>
<section id="sources"><div class="eyebrow">Traceability</div><h2>Sources and detail</h2>
<p>Street photographs: © Grab and KartaView Contributors, <a href="https://kartaview.org/terms">CC BY-SA 4.0</a>. Aerial context: Maxar via OpenAerialMap; the repository records CC BY-NC 4.0 and live provider metadata could not be verified. Mapped corroboration: © OpenStreetMap contributors.</p>
<p><a href="evidence-progress.json">Evidence progress data</a> · <a href="appearance-evidence-reviewed.json">Reviewed observations</a> · <a href="capture-evidence-gaps.geojson">Evidence gap map data</a></p>
<p>Large imagery and photographs live in the private project data folder. Back up that folder along with the repository.</p></section>
<footer>Maintained alongside the project work log. Visual evidence records historical observations and does not establish present-day conditions.</footer></main></body></html>'''
    target = root/'beirut-work-and-evidence.html'
    target.write_text(page, encoding='utf-8')
    print(json.dumps({'page': str(target), 'photos': len(cards)}))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    generate(parser.parse_args().output)
