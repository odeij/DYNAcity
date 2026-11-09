# Explore Beirut GUI

The interface adapts the existing **Bootstrap 5.3.8 Sidebars example**, using
its stock navigation, buttons, cards, list groups, forms and tables. Bootstrap
CSS and JavaScript are bundled locally under `engine/unreal/Content/UI/vendor/`;
its MIT licence and upstream example source are retained. No CDN, account,
external font or internet connection is needed to open the panel.

Source: https://getbootstrap.com/docs/5.3/examples/sidebars/

## Visible actions

The panel opens automatically after editor startup. Open it again from
**Tools → Explore Beirut → Open Explore Beirut control panel**.

- **Explore:** Start/resume driving, end driving, city overview, reference street.
- **Find a place:** Local names, supported Google/OSM links and coordinates;
  selectable results use the existing navigation and safe vehicle placement.
- **Controls:** Separate driving and editor instructions, plus buttons to reset
  the car, change camera and move to the next area while driving.
- **Map layers:** Show/hide trees, vegetation cover and cartographic data markers.
- **City map:** Local road atlas with pan/zoom, car position and destination picking.
- **Atmosphere and traffic:** Day/night and an opt-in buffered SUMO connection during driving.

The [mobility preview](unreal-mobility-preview.md) records the reference street,
coverage measurements, current integration limits and verification commands.

The driving HUD displays **Menu & controls [F1]**, and the shortcut strip names
F1/Tab and F3. F1 or Tab opens the **Controls** page and stops the car (the native
handler passes `controls`; use "← Main menu" there); Resume driving returns
input to the game viewport. F3 opens Find a place in the same panel. Esc ends Play
from the game viewport; Esc in the control panel resumes driving.

Native hosting uses Unreal's existing `SWebBrowser`. `UDynaControlPanel` exposes
an action bridge with a fixed allowlist in `control_panel.py`; page JavaScript
cannot execute arbitrary Python, console commands, file paths or URLs. Browser
navigation/popups are restricted to the bundled interface. Dynamic OSM results
are inserted as text. Key labels use the proportional Bootstrap sans-serif font.

This integration supports the current Unreal editor and editor-hosted Play
prototype. A packaged game still needs a runtime data/action adapter replacing
the editor Python operations, as with the existing location search.

## In-game title and pause menu (31 October 2026)

When Play begins, a full-viewport title menu (`Content/UI/title.html`, `title.css`, `title.js`) is drawn
*inside* the Play window instead of the separate panel window. Native `UDynaControlPanel::ShowOverlay`
adds an `SWebBrowser` to `GEngine->GameViewport` at Z-order 100 (above the driving HUD) and binds the
same allowlisted `beirut` bridge, so every action still goes through `control_panel.execute`.
Because the menu is in the same window, keyboard focus no longer leaks into the car while it is open.

- **Layout**: the tile menu from the "19x1 forza horizon 4 menu" CodePen by sean_codes (MIT), trimmed to
  DynaCity's actions (tab bar, controller hints and showcase tiles removed) and recoloured with Tailwind
  sky-500 → blue-600 (`--g1`, `--g2`, `--accent` at the top of `title.css`). Top row: Fly over the city
  (`fly_resume`), Start driving (`resume`; `drive` from the editor), Find a place. Bottom row: City map, and
  small Settings / Controls / Exit (`end_drive`) cards. An About link sits in the bottom-right status bar.
- **Title vs pause** (`state.menu`): 'title' at each new Play session, 'pause' after the first resume. In
  pause the big tile reads Resume and Exit reads End session. F1/Tab → `ShowOverlay('pause')`, F3 →
  `ShowOverlay('search')`.
- Sub-pages are white cards in the same style: Find a place (`search`/`go_to`), Settings (time of day,
  night, clouds, map layers, minimap style/heading, SUMO connect), Controls, About (coverage numbers).
  City map navigates the overlay to `index.html?overlay=1#map` (sidebar hidden, "Back to menu"/Esc returns
  to `title.html`). Navigation is restricted to those two bundled pages.
- Keys: arrows / WASD move between tiles, Enter selects, Esc goes back (in the pause menu, resumes).
- **Background**: a real Beirut street photo is held, then wiped left to right into a DynaCity render from
  the same viewpoint (`title/match-N-real.jpg` → `title/match-N-model.jpg`); the cycle ends by pulling back to
  the whole-city render (`backdrop-1.jpg`). Photo and render sit in one stack that drifts together, so the slow
  push-in keeps them aligned. Photos: KartaView frames 1288770821, 1288770597, 1288771293 and 1288770701
  (Mar Mikhael, July 2018, CC BY-SA 4.0, © Grab and KartaView contributors; credited in About). Each render
  uses the frame's recorded UTM position and heading, camera 1.4 m above the nearest road node, then pitch
  +12° and yaw −5° (found by overlaying renders on frame 1288770821), cropped to a 63° horizontal field of view
  at 4:3. Roads, horizon and block edges line up; the buildings themselves are the model's simplified blocks.
- **Keyboard focus** (`DynaControlPanel.cpp`): the overlay sets UI-only input and then, for up to 6 s, focuses
  CEF's own `SViewport` inside the browser (it appears only once CEF has created the page). Focusing the outer
  `SWebBrowser` alone leaves keys undelivered. On application reactivation (alt-tab back) focus is passed
  through the game viewport and back, because CEF drops its focus when the OS window deactivates and needs a
  fresh focus event. Verified with real Windows key events: arrows/Enter after Play starts, Enter → driving,
  F1 → pause menu, arrows after switching windows, Esc → resume (Play keeps running).
- F1 is also the editor viewport's wireframe shortcut; `OpenControls` restores `viewmode lit` 50 ms later.
- Without the rebuilt native module, `control_panel._show_menu` falls back to the old panel window.

## Main menu, modes and action identifiers

The **Main menu** (page id `explore`, kept because the native `Show()` whitelists
`explore|controls|search|layers`) states what the twin is, labels it a prototype with
synthetic traffic and no forecasts, and lists entry points: See the whole city
(`overview`), Find a place (page `search`), City map (page `map`), Map layers (page
`layers`), Visit Charles Malek Avenue (`street`) and Mobility → Start driving (`drive`).

`control_panel.state()['mode']`: `city_view` (editor camera, no Play) · `driving_menu`
(Play running, car held, cursor shown) · `driving` (game-only input).

Layout: the Main menu is compact and fits the 1208x925 native panel without scrolling
(component-checked at 1208x880 and 960x655; 800x600 overflows, below the supported
minimum). It shows a mode badge, a one-line purpose, a one-line provenance note (OpenStreetMap and
cached reference data; partly approximate geometry; synthetic traffic; not a forecast or validated
measurement), a "Now:" status and a two-column tool grid with Start driving as the primary tile.
"Atmosphere and traffic" (drive-only) and "Data coverage and credits" are collapsed sections.
A freshly loaded panel does not replay an earlier `page_request` (it opens on the page the native
`Show()` asked for).

Transitions (all in `control_panel._update`, no native recompile): Play begun by the
editor Play button → `pawn.open_controls()` + panel on `explore` (`driving_menu`);
Start driving (`drive`) → resumes immediately; `resume`/`close`/Esc → `driving`;
F1/Tab → `driving_menu`; Play ends → panel on `explore` (`city_view`).
Bridge action ids: `drive end_drive resume close overview street search go_to map_data
map_pick map_settings layer night morning afternoon sunset clouds sumo_start sumo_stop
camera reset next_area fly recover trees_closeup conifers_closeup`.

## Known limitations (unresolved)

- **Native keyboard focus is not fixed or verified.** The panel is a separate OS window and Slate
  keyboard focus stays on the Play viewport, so Tab/Enter/Esc typed into the panel reach the game
  (reproduced natively: two Tabs showed the Controls page). Driving keys R N U F C L Z X H M B also
  still fire while the menu is open.
- A C++ fix is written in the repository (`DynaControlPanel` `FocusBrowser()`, `DynaDrivePawn`
  key gating while the cursor is shown, key flush) but is **not compiled or deployed**: the isolated
  build (`scripts/build_unreal_drive.py`) failed with a VirtualAlloc/paging-file error. Needed: build,
  copy the DLL into the private plugin, restart the editor, then check Play → Tab, Tab, Enter on Find a
  place, Esc/F1 resume, and R/N/F inert in the menu.
- 3D viewport sharpness was not verified; the 2D atlas is limited by source imagery (Sentinel-2 10 m).
- Browser component checks (`scripts/verify_mobility_panel.cjs`, mock bridge) do not establish native behavior.

## Implementation and checks

Main files: `Content/UI/index.html`, `controls.js`, `controls.css`, native
`DynaControlPanel`, `DynaDrivePawn`, and `Content/Python/control_panel.py`.
The Bootstrap version, source URLs and SHA-256 digests are in
`Content/UI/vendor/sources.json`.

Rendered component checks use Playwright when the Browser plugin is unavailable.
Checks cover initial page, controls navigation, search/results, mode-dependent
actions, layer switches, console health and 960×700 / 780×560 layouts. Their
mock action bridge verifies the component separately from native integration.
Native verification must additionally check actual action dispatch, input
handoff, startup visibility and driving menu behavior before claiming completion.

Verified on 26 October 2026: the panel opened automatically in the private
Unreal project; native browser tests exercised actual tree visibility, OSM
search/result navigation, Start driving, F1 menu opening, F3 unified search,
Change camera and End driving. Browser console checks passed. Component tests
passed at both supported sizes, the native module compiled, and all 20 existing
location/drive-route tests passed. Native screenshots and JSON evidence are
saved privately under `C:/private/dynacity-data/engine/controls-*`.
