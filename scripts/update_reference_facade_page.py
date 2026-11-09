"""Publish actual source photos, native captures and readback reports together."""
import hashlib
import html
import json
from pathlib import Path

ROOT = Path('C:/private/dynacity-data/engine/facade-reference-20261030')
EVIDENCE = ROOT.parent/'appearance-evidence-20261030'


def generate():
    install = json.loads((ROOT/'native-install.json').read_text())
    verify = json.loads((ROOT/'native-verification.json').read_text())
    layout = json.loads((ROOT/'reference-layout.json').read_text())
    timing = json.loads((ROOT/'editor-frame-diagnostic.json').read_text())
    photo = EVIDENCE/'street-photos/1288771577.jpg'
    observation = next(o for o in json.loads((EVIDENCE/'reviewed-observations.json').read_text())['observations'] if o['source_id'] == install['source_id'])
    shots = ['street-before.png', 'street-after-refined.png', 'whole-building.png']
    if any(not (ROOT/name).is_file() for name in shots):
        raise ValueError('Missing native comparison capture')
    records = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in shots}
    records['reference_photo'] = hashlib.sha256(photo.read_bytes()).hexdigest()
    if records['reference_photo'] != observation['photo_sha256']:
        raise ValueError('Historical photograph differs from reviewed evidence')
    records['source_observation'] = observation
    (ROOT/'comparison-evidence.json').write_text(json.dumps(records, indent=2))
    reload_text = 'Passed after a cold editor restart' if install.get('reload_verified') else 'Cold restart verification pending'
    rows = ''.join(f'<tr><td>{html.escape(p["phase"])}</td><td>{p["median_interval_ms"]:.2f}</td><td>{p["p95_interval_ms"]:.2f}</td></tr>' for p in timing['phases'])
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Beirut · Reference facade comparison</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f3ed;color:#203a30;font:16px/1.6 "Segoe UI",Arial,sans-serif}}main{{max-width:1240px;margin:auto;padding:32px 24px 70px}}h1{{font-size:42px;line-height:1.1}}h2{{font-size:27px}}a{{color:#176652}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}figure{{margin:0;background:white;padding:12px;border-radius:12px}}img{{width:100%;display:block}}figcaption{{padding:10px 4px}}.notice{{padding:20px;background:#fff1d8;border-left:4px solid #b58132}}table{{border-collapse:collapse}}td,th{{text-align:left;padding:10px 20px;border-bottom:1px solid #ccc}}@media(max-width:750px){{.grid{{grid-template-columns:1fr}}}}
</style><main><a href="../appearance-evidence-20261030/beirut-work-and-evidence.html">← Work and evidence</a>
<h1>One building, tied to a real photograph</h1><p>30 October 2026 · Remeil · source ID {html.escape(install['source_id'])}</p>
<p class="notice"><strong>Partial visual match. Further refinement is needed before a district rollout.</strong> The historical photo supports cream surfaces, reddish-brown bands and glazed balconies. Floor count, spacing, balcony depth and hidden elevations are estimates. The source height and footprint remain unchanged; they are not surveyed measurements.</p>
<h2>Before and after in Unreal</h2><div class="grid"><figure><a href="street-before.png"><img src="street-before.png" alt="Native Unreal view before reference treatment"></a><figcaption>Before: generic concrete and window pattern.</figcaption></figure><figure><a href="street-after-refined.png"><img src="street-after-refined.png" alt="Native Unreal view after reference treatment"></a><figcaption>After: cream plaster, brown horizontal bands, authored ledges and glass panels. Same street camera; lighting is not calibrated to the photograph.</figcaption></figure></div>
<h2>Historical reference and full height</h2><div class="grid"><figure><a href="../appearance-evidence-20261030/street-photos/1288771577.jpg"><img src="../appearance-evidence-20261030/street-photos/1288771577.jpg" alt="2018 KartaView facade photograph"></a><figcaption>Captured 21 July 2018. Building identity corroborated using the visible LGB Bank sign and a mapped ATM. © Grab and KartaView Contributors, CC BY-SA 4.0. Current appearance is unknown.</figcaption></figure><figure><a href="whole-building.png"><img src="whole-building.png" alt="Native wide view showing treatment over building height"></a><figcaption>Full height inspection. The current layout assumes {layout['floor_count_assumed']} floors from source height; the photograph does not establish this count. Corners, façade rhythm and balcony forms are not an exact reconstruction.</figcaption></figure></div>
<h2>Validation</h2><p>Native readback: <strong>{html.escape(verify['status'])}</strong>. {reload_text}. The original source mesh, transform and collision remain in place. A visual copy assigns the target's 44 triangles to the new material and retains the other 10,344 triangles with their original material. There are 228 ledges and 228 glass instances, adding 5,016 authored detail triangles.</p>
<p>This test covers one identified building. The separate <a href="../facade-coverage-20261030/review.html">16-building batch coverage repair</a> has its own comparison and validation. No citywide accuracy or calibrated mobility result is implied.</p>
<h2>Editor timing diagnostic</h2><table><tr><th>Visibility phase</th><th>Median ms</th><th>95th percentile ms</th></tr>{rows}</table><p>These are Slate/editor tick intervals, not GPU frame time or gameplay FPS. Both asset sets were resident. This short diagnostic cannot establish citywide performance or memory cost.</p>
<h2>Reproducibility and sources</h2><p>Prepared and fitted cached CC0 Poly Haven façade pieces; retained authored triangles and UVs. Plaster maps use the cached ambientCG Plaster001 material. Observation records retain the photo attribution and mapped OpenStreetMap corroboration.</p>
<p><a href="native-install.json">Native installation</a> · <a href="native-verification.json">Native verification</a> · <a href="offline-verification.json">Source geometry verification</a> · <a href="reference-layout.json">Placement manifest</a> · <a href="editor-frame-diagnostic.json">Timing data</a> · <a href="comparison-evidence.json">Photo and screenshot checksums</a></p></main></html>'''
    (ROOT/'reference-comparison.html').write_text(page, encoding='utf-8')
    print(ROOT/'reference-comparison.html')


if __name__ == '__main__':
    generate()
