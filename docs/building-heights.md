# Building heights in the viewer

One order, in `src/dynacity/heights.py`, for BBED, Beirut 001 and context buildings. The first source a building has wins:

1. **Recorded** (`height_kind: recorded`): Beirut 001 model height (roof minus model ground), else BBED `Building_Hight_m`, else an OSM / Overture `height` tag.
2. **From floors** (`floors`): floors x 3.0 m (BBED `NoofFloor`, Overture `num_floors`, OSM `building:levels`).
3. **Size bin** (`size_bin`): median recorded height of buildings with a similar footprint area. BBED uses the medians of its own recorded, built buildings (at least 5 per bin, else the overall median, and none under 5 buildings in total); vacant and demolished BBED sites are never given one. Context buildings use Beirut 001 medians.
4. **Fallback** (`fallback`): 6 m, illustrative, only when nothing above exists. A Beirut 001 building with no model ground under it (20 of 17,569) is also `fallback`, because its height is the full solid.

Only `recorded` has `estimated: false`. Every building keeps `height_source` (text shown on hover), `height_kind` and `estimated`. The viewer draws estimates lighter and slightly transparent (`heightConfidence`), and the legend counts recorded / from floors / estimated for the drawn building set and the mapped context.

## Measurements (BBED 2024, Beirut 001 pack)

- **Storey height.** BBED buildings with both a height and a floor count: n = 3,150. The median height per floor is exactly 3.00 m, and 2,808 (89 %) are exactly 3.0 per floor. 5-fold held-out error against the recorded height: constant 3.2 m, mean abs 1.77 m, bias +1.54 m; median ratio 3.0 m, mean abs 0.23 m, bias -0.23 m, p90 0.50 m; least squares with an intercept 3.007 m x floors + 0.16 m, mean abs 0.40 m. The intercept does not help, so the fit is 3.0 m per floor, no intercept.
- **Caveat.** BBED "recorded" heights are therefore mostly 3 m x floors: they carry little information beyond the floor count. Against Beirut 001 the 3.0 m model is not better than 3.2 m (below).
- **Beirut 001 vs BBED on the 687 provisional links** (669 with both a positive BBED height and floors; the others hold -999 placeholders): model minus BBED height: median abs 7.3 m, mean abs 9.5 m, median bias +5.4 m, p90 abs 19.2 m, correlation 0.74. Worst: object 2414 (model 159.6 m, BBED 66 m), 1569 (22.7 m vs 81 m), 2858 (85.5 vs 33), 2629 (57.1 vs 108). The model is taller in most cases; links are provisional and the two sources date from different years.
- **Floors x height versus Beirut 001** (same 669): 3.2 m gives median abs 6.7 m, mean abs 8.9 m; 3.0 m gives 7.2 m and 9.5 m. A line fitted to the model heights directly (2.89 m per floor + 6.9 m) reaches mean abs 8.2 m held out, but the 6.9 m intercept has no physical reading and the model heights themselves are noisy, so it is not adopted.
- **Size-bin estimate.** For BBED, held out against recorded heights (n = 3,151): median abs 9.0 m, p90 24 m, against 21 m median abs for the flat 6 m. For context, the Beirut 001 bins are far lower than OSM-mapped heights (n = 158, median 40 m: median abs 22.9 m, bias -22 m), though mapped heights skew to tall landmarks, so this is not a fair sample of the unmapped buildings.

## Why Beirut 001 beats BBED where both exist

The model height comes from roof geometry; the BBED height is, in 89 % of records, floors x 3 m. The hover shows both with the difference.

## Rebuild

`engine_context_buildings --web-output` rewrites the context companion. Its `osm_heights` entries are `[metres, label, kind]`; two-element entries from older files still load as `size_bin`.
