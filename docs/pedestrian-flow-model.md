# Modelled pedestrian flows (UNA)

Added 1 November 2026. **Modelled with proxy inputs, not observed counts.**

## Method

- Tool: [UNA](https://github.com/City-Form-Lab/urban_network_analysis) 2.7.2, MIT licence, MIT City Form Lab (successor to the archived Madina package). Installed in a private environment, `C:/private/dynacity-data/tools/una-venv`, together with `pyarrow`, which UNA's reader needs but its install list omits.
- Formula: Sevtsuk et al. (2024), *Pedestrian-oriented development in Beirut*, Cities 149, 104927, Table 2 (weekday PM peak, 16:30–17:30). Pedestrians per hour = 61.62 + 3.49 F(amenities→amenities) + 0.08 F(jobs→homes) + 0.08 F(parks→amenities) + 0.02 F(homes→worship) + 9.77 (mean Monday–Thursday effect). The jobs and parks flows use only origins with high access to amenities (above the 90th percentile of exponential gravity); the worship flow uses only low-access origins. Search radius 800 m, exponential decay with β = 0.002, gravity-cap trip generation.
- Inputs (`scripts/prepare_pedestrian_inputs.py`):
  - OSM walkable ways, split at shared junctions: 11,890 segments. Motorways and trunk roads are excluded, so Charles Helou is not walkable.
  - 2,504 shops and services, 152 places of worship, 90 parks, 139 bus stops.
  - Homes: 17,308 Beirut 001 buildings, footprint × (height ÷ 3 m) ÷ 100 m² per resident, about 460,000 people in total.
  - Jobs: a guessed proxy from OSM offices, shops, schools and hospitals.
- Run: `scripts/run_pedestrian_una.py --inputs C:/private/dynacity-data/pedestrian-una/inputs --output C:/private/dynacity-data/pedestrian-una/results`. Output: `pedestrian-flows-pm.geojson` with `ped_per_hour_pm` per segment. The map is `previews/pedestrian-flows-pm.png`.

## Result and checks

The busiest segments are Hamra Street (up to about 370 per hour), Damascus Road, the Gemmayzeh–Armenia Street corridor, Achrafieh and Mar Elias. Gouraud Street comes out at 158–267 per hour against the paper's single published baseline of 173 at Gouraud × George Haddad. That is one spot check, not a validation. Most residential streets sit near the formula's constant of about 71 per hour.

## Limits

- The paper's own inputs and per-pair parameters (in its supplementary material) are not available.
- Our homes and jobs layers are proxies; the parks layer includes parks that may be closed.
- OSM coverage varies by area.
- The constant gives every street at least about 71 per hour, which overstates quiet streets.
- Calibration against the 90 counts (2021–22), requested from the authors, is still needed before any number is used as evidence.

## Walkers in the 3D city (in progress)

- `scripts/prepare_pedestrian_walkers.py`: turns the flows into people per segment (people/h ÷ 3600 × length ÷ 1.3 m/s), after removing the regression constant so quiet streets stay quiet. Each flow segment is snapped to the parallel 3D-model road it runs along, and walkers use the middle of that road's pavement as `engine_city_surface` builds it: road width ÷ 2 + pavement width ÷ 2, with 1.6 m pavements on residential streets, 2.4 m elsewhere and none on fast roads, raised 15 cm above the road at every vertex. The result is 2,307 segments and about 732 people citywide at any moment. Output: `C:/private/dynacity-data/pedestrian-una/pedestrian-walkers.json`.
- `engine/unreal/Content/Python/pedestrian_local.py`: keeps each segment's modelled number of walkers within 200 m of the player, walking both ways inside both pavements at 1.1–1.6 m/s (about 78 near the busiest street, none on low-flow streets).
- `engine/unreal/Content/Python/pedestrian_runtime.py`: drives City Sample Crowd characters (`BP_CrowdCharacter`) from those frames, configured by `Saved/DynaCity/pedestrian-characters.json` (limit 15 for memory). Python cannot spawn actors into a running Play world here, so run `prepare_pool()` in the editor before Play: it places that many characters below the city, hidden. Play copies them and the walkers show, move and hide them. Afterwards `remove_pool()` deletes them; the pool is never saved. The blueprint animates only the face, so each walker loops a City Sample walk cycle on its base skeleton with root motion locked. Each character traces the rendered ground once when it appears and corrects its height by up to 4.5 m. No collision; walkers do not avoid cars.
- Tests on 1 November 2026:
  - First editor test: three characters on Hamra Street stood about 1 m too low, in the parking lane, and grey while their materials compiled. They were removed without saving.
  - After the placement fixes: 15 walkers ran in Play around Hamra and Gemmayzeh. A close capture on Cheikh Elias Gaspard Street shows a grey-suited man standing on the raised pavement, animated (`previews/pedestrian-walking-gaspard-street.png`).
  - Every pooled character came out the same man (`m_tal_nrw`, blazer and tie), because `BP_CrowdCharacter` was not randomized; adding variety is still to do.
  - Memory: the editor with the city, Play and 15 characters reached about 50 of 50.6 GB committed (page file grew). Keep the limit low on this machine.
- Incident during testing: a console helper sent Ctrl+A then Backspace while focus was on the level. In the earlier editor session this deleted one selected actor, `buildings_context_e486_n2502_material_9`, and the later save-all wrote that deletion. In the restarted editor it deleted every loaded actor, but that was discarded by closing without saving. The saved deletion was restored from `C:/private/dynacity-data/pedestrian-una/backup-before-full-save-20261101-232224`, and all 980 actor descriptors were confirmed present afterwards. The helper now clicks the console box and sets its text through its widget, never with keyboard shortcuts.
