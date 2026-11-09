# Beirut 001 pack

[Beirut 001](https://www.salimalkadi.com/07_beirut-001) (Salim al-Kadi and
team, January 2020) is a free Rhino 5 model of Beirut, about 730 MB. The app
does not need the file itself. `dynacity pack-beirut001` reads it once and
writes a pack of about 7 MB including transport meshes (the earlier outline-only
pack was about 2.6 MB) that holds what the map and analysis use.
After that, the source file can be archived.

```powershell
dynacity pack-beirut001 --model $env:DYNACITY_DATA_ROOT\beirut001\source\beirut001.3dm `
  --bbed $env:DYNACITY_DATA_ROOT\bbed-2024.geojson `
  --output $env:DYNACITY_DATA_ROOT\beirut001\beirut001-pack.npz
dynacity serve ... --snapshot ... --bbed ... --beirut001 $env:DYNACITY_DATA_ROOT\beirut001\beirut001-pack.npz
```

The command takes about 90 seconds and needs the `overlay` extra (`rhino3dm`,
`scipy`). Loading a pack needs only numpy and shapely.

## What the model contains

| Layer | Contents | Kept in the pack |
| --- | --- | --- |
| `05_BUILDINGS` | 17,575 solids | Outline, roof level, ground level, BBED link |
| `04_TUNNELS AND BRIDGES` | 102 meshes/solids, 4 curves | Source triangles, plus outline and lowest/highest level of 90 pieces |
| `01_TOPOGRAPHY` | ground meshes | 10 m ground grid |
| `03_BLOCKS`, `02_RIVER`, `00_TEXT` | blocks, river, labels | Not kept |

17,500 of the 17,575 buildings are straight extrusions with one flat roof, so
an outline and two levels describe them exactly. Seven have a stepped roof;
the pack records how many levels each has and draws them flat at the top. Six
objects had no usable outline and are skipped. The model's building names
carry no information (165 are named "1").

## Placement

The model has no geographic anchor. Its building centres are matched as a
pattern to BBED's: rotate, cross-correlate, then refine on nearest pairs
(`beirut_overlay.register_rigid`). Scale is fixed. The result is a turn of
about +1.344° and a shift of about (731999.3, 3752431.4) m into EPSG:32636,
the same as the earlier mesh pilot to within 2 cm. This is inferred, not
surveyed.

### Measured alignment

The placement above was fitted to BBED, so BBED cannot judge it.
`dynacity refine-beirut001-alignment` measures it against Overture building
footprints (68,719 polygons, almost all OpenStreetMap), which share nothing with
the model or BBED, and applies a correction to an existing pack without the
Rhino source:

```powershell
dynacity refine-beirut001-alignment --pack beirut001-pack.npz --reference overture-buildings.geojson `
  --bbed bbed-2024.geojson --terrain terrain-glo30.npz `
  --structures-pack beirut001-structures-pack.npz --structures-output beirut001-structures-pack-refined.npz `
  --display-json beirut-model-display.json --display-output beirut-model-display-refined.json `
  --output beirut001-pack-refined.npz --report alignment-report.json
```

It never overwrites an input (it refuses an output path equal to an input). The
run takes about 9 minutes and needs `scipy` (`overlay` extra). Method:

1. Placed outlines are paired one-to-one with reference footprints (intersecting,
   IoU at least 0.25, centroids within 15 m, area ratio 0.4 to 2.5, best IoU
   first). The residual is the centroid distance in metres.
2. A robust (Huber) fit alternates with re-pairing under the current estimate;
   pairing only against the unmoved placement would favour buildings that
   already overlap and shrink the estimate toward zero.
3. Shift, rigid, similarity, affine and quadratic corrections are compared by
   spatial block cross-validation: buildings are split into 5 folds by 500 m
   tile, pairing and fitting are redone without the held-out tiles, and the
   held-out buildings are scored. A rotation or scale is kept only if its
   tile-block bootstrap interval excludes 0 or 1 *and* it lowers the held-out
   median by at least 0.05 m; affine or quadratic only if they clearly beat that.
4. The winner is applied to outlines, bridges, bridge triangles and the ground
   grid (re-gridded north-up, NaN-aware, so the coast stays put); heights and
   levels are untouched. BBED links are re-matched with the same rule.

The pack then carries `meta["alignment"]` (correction, held-out error, link
counts before and after); the viewer words the alignment from that block and
keeps "inferred alignment" for packs without it. Residuals here are agreement
with OSM footprints, which are traced from imagery and have their own error.

Result on the real pack (run of 7 November 2026): the model sat about 2.2 m
south and 0.4 m west of the OSM footprints, no significant rotation or scale
(0.005° and 1.00013, a rotation interval that includes 0, and neither lowering the
held-out error), and a plain shift was adopted. Held-out centroid error against
the reference went from a median of 3.81 m (90th percentile 7.29 m) to 3.31 m
(7.04 m). The remaining ~3.3 m is mostly outline and tracing difference, not
placement: affine, quadratic and a local-field correction gained under 0.05 m on
held-out tiles even though tile means do vary (median 0.9 m, noise floor 0.36 m).
BBED itself sits about 1.3 m north of OSM, so moving toward OSM moves away
from BBED: the model's median distance to its BBED footprints went from 3.0 to
3.3 m and provisional links from 687 to 590 (372 kept, 315 lost, 218 gained).
The vertical frame is unaffected: pack ground minus GLO-30 has a median of
+0.40 m (207,653 cells).

## BBED links

A model outline is linked to a BBED footprint when they overlap by at least
65% (intersection over union), their centres are at most 8 m apart, and the
next-best footprint overlaps at least 15 points less. A BBED record claimed by
two model objects links to neither. With exact outlines, 687 of the 17,569
model buildings link. The earlier pilot's convex outlines gave 709 (712 now),
because a fatter outline passes the overlap test slightly more often. BBED
covers only about 3,345 footprints, so most model buildings have nothing to
link to. A link is spatial agreement, not proof of the same building:
the model is from January 2020, and BBED was surveyed in later rounds.

The pack stores only the linked OBJECTID. `serve` reads the BBED record from
its own `--bbed` file.

## Heights

- **Datum.** The model's ground agrees with Copernicus GLO-30 (metres above
  the EGM2008 geoid) to a median of +0.4 m (interquartile range −1.6 to
  +2.5 m, correlation 0.98, over 207,653 cells). It is read as height above
  sea level, although the source declares no datum.
- **Sunk buildings.** Nearly every building's base is 5.0 m below the model
  ground (interquartile range −5.04 to −4.96 m). Building height is therefore
  roof level minus the ground under the building, not the solid's full extent.
  The median is 19.9 m.
- **Against BBED.** For 670 linked buildings with a recorded height, the model
  is a median of 5.4 m taller than `Building_Hight_m` (interquartile range
  −0.4 to 10.5 m), or about 3.8 m per recorded floor. BBED heights look like
  floors × 3 m. Neither source is adjusted to match the other.

## Ground grid

The pack's ground grid is the highest model ground surface at each 10 m cell
centre, north-up in EPSG:32636; cells off the ground meshes are NaN. Its keys
match `terrain.GroundModel`, so `--terrain PACK.npz` would attach its ground
levels to buildings. Serving it that way is not the default yet; GLO-30 remains the documented
terrain.

## On the map

New packs retain the original transport triangles as well as the outlines. The
static Beirut 001 model view renders these concrete-coloured source structures.
During traffic playback they are hidden: their source surfaces can disagree with
the simplified road-height profiles and obscure asphalt or vehicles. Traffic
uses the concrete structure generated directly under its own elevated paths.
Geometry is aligned with the same inferred transform;
heights are relative to the sampled model ground to match the flat display.
Below-ground parts are clipped at that display ground. Supports, sidewalls and
other details appear only where present in the source; no pillars are invented.
These are January 2020 model shapes, not a current engineering survey. Older packs
still load, but need rebuilding to gain the source structure mesh.

The source pieces do not provide a labelled inventory of pillars, girders and
decks. The viewer therefore also gives mapped elevated roads an illustrative
0.36 m deck slab, a tapered concrete box girder, solid 0.86 m edge barriers,
and 1.8–2.5 m diameter round concrete piers with broad caps and plinths. Piers
are spaced at about 32 m within each road piece where height permits; a narrow
deck uses a central pier where clear, while broad decks use paired supports.
Piers are omitted inside mapped lower-road
corridors. Hover identifies these added structures as illustrative; they are not
extracted support locations, measured dimensions or an engineering design.

The concrete appearance is based on photographs of
[Charles Hélou](https://www.hourie.com/projects/charles-h-lou-multi-level-bus-terminal-construction-beirut-east-entrance),
[Dora Viaduct](https://www.manenterprise.com/our-work/dora-viaduct) and
[Mudeirij Bridge](https://www.manenterprise.com/our-work/mudeirij-bridge).
The urban reference drives the thick deck/overhang, cylindrical piers and broad
support caps; the finish is warm, matte concrete with restrained formwork seams.
These photos guide the illustrative style, not the dimensions of every bridge.
Photographs are linked as references and are not embedded as model textures.

With `--beirut001`, the map draws model outlines raised to their height above
the model ground, coloured by the linked BBED building's lens colour or a
neutral grey when unlinked. **Buildings** switches between this and the
original BBED footprints. **Info** chooses what the card shows: BBED record,
Beirut 001, or both. `--beirut001` is refused on anything but a loopback host,
and the endpoints (`/v1/beirut001`, `/v1/beirut001/buildings`) send
`private, no-store`.

## Licence

The author's page calls the model open source and free to download, but it
names no licence and gives no terms for web redistribution. The source `.3dm`
is not redistributed. The two derived packs are published in
`datasets/beirut001/` (Git LFS) with attribution to the author, and the server
still serves them only on a loopback host.
A request for the coordinate reference and reuse terms is drafted in
[data-requests/beirut-3d-access.md](data-requests/beirut-3d-access.md) and has
not been sent.
