# City atlas

The embedded City map uses **Leaflet 1.9.4** (BSD-2-Clause) and **Proj4js 2.12.1** (MIT), vendored locally with licenses. `vendor/map-sources.json` records original download URLs and SHA-256 hashes, including Leaflet's image assets. No CDN JavaScript, runtime package installation, WebGL, API key, or local server is required.

## Geographic sources and policy

All requests use only `https://tiles.maps.eox.at`. EOX explicitly permits embedding its maps in applications with the appropriate attribution and a link to EOX::Maps. Its free service is best-effort and rate-limited; there is no SLA. These terms were checked against <https://maps.eox.at/> and the service's WMTS capabilities during implementation.

- **Street:** `osm_3857/default/g/{z}/{y}/{x}.jpg`, OSM-derived geographic cartography rendered by EOX / MapServer. This is **not** a list of locally driveable roads.
- **Satellite:** `s2cloudless_3857/default/g/{z}/{y}/{x}.jpg`, the actual Sentinel-2 cloudless **2016** endpoint, not a similarly named newer/non-commercial layer. Nominal resolution is 10 m; satellite and hybrid views cap actual map zoom at **14**, not just tile-request zoom. Switching from a closer street view animates out to that limit. This is regional historical imagery, not sharp street-level aerial photography; sharper satellite detail requires a licensed high-resolution provider. Attribution: EOxCloudless by EOX IT Services GmbH, contains modified Copernicus Sentinel data 2016 & 2017, CC BY 4.0. See <https://cloudless.eox.at/license-non-commercial> for the distinction between the 2016 CC BY license and newer restricted imagery.
- **Hybrid:** the same satellite layer plus `streets_3857/default/g/{z}/{y}/{x}.png` (transparent geographic streets, 70% opacity) and `overlay_bright_3857/default/g/{z}/{y}/{x}.png` (broad geographic labels/context). These are EOX geographic layers, not the local export.
- **Teal overlay:** exported local modeled road paths, **off by default** and independently switchable in every mode. Geographic streets and historical imagery do not establish local drivability. Unreal still validates destination safety.

EOX::Maps, EOX, MapServer, OSM copyright and the satellite license are linked in the active layers' attribution. Street is the crisp default, with actual map zoom capped at 18. Hybrid's transparent street and label/context layers stay within the imagery view's zoom-14 cap. No imagery is enlarged beyond its native limit. Those limits were verified against actual endpoint responses (the WMTS matrix advertises higher zooms than individual layers serve). All layers use `keepBuffer: 0`, `updateWhenIdle: true`, `updateWhenZooming: false`, no retina multiplier, and no tile prefetch. Tiles are created only when City map first opens; base layers are detached when it closes. Normal browser HTTP caching is left intact. There is no bulk downloader, service worker, native cache hookup, or automatic error retry loop. Retry tiles explicitly reloads the visible tiles. Network failures and a 15-second slow-load warning are separate from geometry status; local overlays remain usable offline after loading.

**Why not direct OSM or CARTO?** A `file://` Unreal browser does not provide the valid HTTP Referer expected by the OSM public tile service. No request is made to `tile.openstreetmap.org`, and no Referer is forged. CARTO's current basemap terms require a user-obtained API key and usage limits, so it is not treated as a keyless fallback. EOX's published application permission avoids both issues without inventing a Python/native proxy that does not yet exist. If a native tile cache/bridge is integrated later, its endpoint, identification, caching behavior and CSP must be reviewed explicitly; this UI currently requests EOX independently of the native HUD.

Requests disclose the user's IP address and viewed tile coordinates to EOX. Live maps require internet access. All three endpoints were checked for successful image responses, and all modes were tested in Chromium from the actual `file://` panel. The CSP adds only the EOX host to `img-src`; scripts/styles remain local, `connect-src 'none'` is retained, and neither wildcard network access nor inline/eval script permission is introduced.

## Backend contract

Existing `beirutMap.load(payload)`, `show()`, `position(value)` and `resize()` are retained. Navigation additionally calls `hide()` to detach online tiles while other pages are shown. `controls.js` retains ownership of the Unreal bridge and its `map_data` / `map_pick` / `map_settings` actions. `beirutMap.settings(value)` applies confirmed settings without sending an action, resetting the view on unchanged polls, or changing the independent modeled-road overlay.

```json
{
  "origin_utm_m": [731167.7004159444, 3753882.2119486625, 0],
  "map_reference": {"origin_lonlat": [35.5, 33.9], "crs": "EPSG:32636"},
  "roads": [{"id": 1, "path": [[0, 0, 0], [1000, 0, 0]]}]
}
```

`map_position` remains local `[east_m, north_m, height_m]`. The authoritative UTM origin is added to each local road/car position and inversely projected to WGS84. Clicks/keyboard centre picks are forward-projected to UTM36N, then the exact origin is subtracted before `map_pick {x,y}`. Height does not affect the 2D map. If only `origin_lonlat` is provided with the correct CRS, its projected UTM position is used as a fallback. There is no degrees-per-metre approximation or guessed default origin for local data. Missing/invalid georeferencing disables local overlay and picking but leaves geographic maps available. Picks outside zone 36N's longitude/latitude coverage are rejected.

### Shared map preferences

The backend owns `map_settings: {"version":1,"mode":"street"|"satellite"|"hybrid","heading_up":false}` in every state response. The UI sends `map_settings` with optional `mode` and/or `heading_up` fields; the server merges only validated allowed fields, persists `Saved/DynaCity/map-preferences.json`, applies the active HUD setter, and returns `{"map_settings":{...},"message":"..."}`. Settings are available outside PIE and load into the next PIE session. Native HUD changes must update the same shared state.

The atlas renders only valid authoritative settings from bridge state or a successful settings response. It never writes preferences into browser storage or claims a local-only save. Buttons show the confirmed selection; the native heading checkbox also stays at its confirmed value while saving. A dedicated status reports **Saving**, **Saved**, or **Error**. All mode buttons and the heading checkbox are disabled during *any* bridge action, preventing the single-action guard from silently dropping a map click. Poll reads started before an action are ignored, and repeated settings application sends no request. Failed saves keep the previous confirmed choice and show an error. State also carries `map_settings_error`: a nonempty string displays **Error**, even when the backend returns valid fallback settings (`street`, heading-up) after a read/corruption/native-save failure. An empty authoritative error clears that warning, and a successful save restores **Saved**. Polling updates only the dedicated status, without repeating global feedback. Reopening/reloading reads backend state, not an old JavaScript default. Without the bridge/settings contract, settings remain disabled and the status explains why.

The native heading-up checkbox affects only the driving HUD; the atlas always remains north-up. Source point/outline markers start unchecked until authoritative layer state arrives; native source overlay defaults are owned by the HUD/backend, independently of the atlas's optional modeled-road paths.

Follow car pans to valid player updates only while the atlas is visible. Dragging, arrow panning, Whole network or My car disables follow; My car is a one-time centre action. The atlas is north-up. **M** (native map mode) and **B** (native heading) are documented in Controls and apply to the separate driving HUD, not to Leaflet rotation.

## Verification

- `node --test scripts/verify_city_map.cjs`: independently generated PROJ fixtures (forward/inverse), axes, nonzero local origin, height exclusion, fallback, invalid references, pinned vendor hashes.
- `node scripts/verify_mobility_panel.cjs <output-directory>`: Playwright GUI checks with deterministic tile responses, preserving existing search/drive/night/SUMO checks and testing persisted atlas/HUD preferences, failed saves, authoritative state application without request loops, pending-action locks, reload restoration, actual satellite/hybrid zoom caps and street zoom recovery, follow, keyboard/pointer picks, drag suppression, overlay toggle, invalid reference, network errors/retry, hidden-page requests, resizing and CSP.
- Add `--live-tiles` for actual EOX tile loading and real street/satellite/hybrid screenshots. This makes a small number of on-demand requests to the public service. The browser always closes; no HTTP server is launched. Use an installed Playwright package, and optionally `DYNACITY_CHROMIUM` to select an existing Chromium executable.

The browser bridge is mocked in these component tests. Embedded Unreal/CEF integration and native HUD/cache verification remain separate responsibilities.

## Display scale and sharpness (task 04)

The embedded browser draws at the Windows display scale (125% measured on the dev machine). A 256 px tile then covers more than 256 device pixels and is stretched, which looked soft. When `devicePixelRatio` > 1.05 each layer now requests the next zoom level at 128 CSS px (same ground area, 4x the pixels, layer zoom caps unchanged: street 18, imagery 14), so cartography/imagery that exists is shown crisply and nothing past the source limit is invented. Cost: roughly 2-3x more tile requests while zooming (measured 205 vs 78 street requests, 40 vs 24 imagery, in the component test); street labels render at half their previous CSS size. A readout under the map states zoom, ground metres per screen pixel, display scale and the imagery source limit (Sentinel-2 cloudless 2016, 10 m per pixel: zooming closer than that only enlarges it). The page also puts the basemap/zoom controls above the map so the map is visible without scrolling at the 1208x925 panel.

Backend navigation: `control_panel.request_page(page, zoom=None)` publishes `state.page_request {id, page[, zoom]}`; `controls.js` shows that page (allowlisted ids only) and optionally sets the atlas zoom. It is not an action the page can send, and the native `Show()` still forwards only explore/controls/search/layers.

**Street zoom cap lowered 18 -> 16 (measured).** Fetching `osm_3857` tiles for the same Beirut spot at z14-18 showed real cartography up to z15 (14-18 KB JPEGs); z16 is a 2x enlargement and z17/z18 (6.6 KB / 3.9 KB) are heavily smeared server-side overzoom. The older claim that z18 is real was based on the endpoint answering, not on content. Street now requests native z15 and allows at most one extra zoom level of enlargement (16); the crisp local geometry is the optional teal "Exported drivable roads" vector overlay. The satellite layers remain capped at 14 (Sentinel-2, 10 m).
