# Capture coverage and viewing assets

`engine_capture_inventory.py` records local LAS headers, GLB JSON metadata and
OBJ/MTL/texture dependencies without opening full point buffers or downloading
OneDrive placeholders. LAS creation fields are export metadata; acquisition
dates remain unknown unless independently supplied. The assumed EPSG:32636
placement comes from project alignment evidence, not embedded LAS metadata.
Vertical datum and photographic model transforms still require verification.

The fresh 25 October 2026 local inventory finds:

| Source | Local availability | Data |
| --- | --- | --- |
| StreetScape | Resident | 4,800,668 RGB points |
| NestledVillage | Resident | 14,808,693 RGB points |
| HeritageLandscape | Resident | 98,307,631 RGB points |
| Four other LAS files | Online-only | Headers not hydrated automatically |
| HeritageZone GLB | Resident | 18,210,547 positions, 36,447,423 triangles; vertex colours, no UVs, materials or images |

HeritageZone's accessor bounds are consistent with glTF east/elevation/south
coordinates, but no source CRS is embedded. It is a dense vertex-coloured mesh,
not a textured photographic model. Its density and large coordinates require a
separate simplification/rebasing pilot before Unreal import. Existing individual
OBJ captures and most photographic JPGs are also local now; texture dependencies
and availability are recorded, while geographic transforms remain unresolved.

## Bounded-memory viewing copies

`engine_capture_preview.py` creates a derived LAS viewing copy by retaining every
N-th original point record. Positions, RGB and attributes are exact retained
records; nothing is smoothed, filled or invented. The source file is untouched.
The implementation streams small batches from a memory map and updates subset
bounds and point/return counts. It supports resident, uncompressed RGB LAS 1.2
formats 2/3. Larger or differently encoded sources require a separate decoder.

The prepared NestledVillage preview has 1,851,087 points (stride 8); the
HeritageLandscape preview has 1,966,153 (stride 50). The full StreetScape remains
available at all 4,800,668 points. Previews are explicitly labelled in the Unreal
capture menu and status, including original versus displayed point counts.
These are viewing optimizations, not dense reconstruction or final-quality meshes.

```powershell
python -m dynacity.engine_capture_inventory --root <private-capture-directory> --output <private>/capture-inventory.json
python -m dynacity.engine_capture_preview --source <resident-source.las> --output <private>/capture-preview.las --max-points 2000000
```

Unreal now has a bounded post-process region for each loaded capture, enabled
only with that layer. Manual photographic inspection exposure reduces the
washout caused by the city histogram exposure. It affects the capture region,
not the whole city; original RGB stays unchanged. The region is display setup,
not a claim that capture and model datums are surveyed together.

All three local viewing assets were imported, selected and toggled in Unreal.
Native counts matched the expected full or sampled point counts. No online-only
source was downloaded. A photographic pilot should start in the northeastern
capture coverage shown on the generated map, after alignment checks, rather
than assuming these sources cover the southwest Sea Highway prototype.

## Charles Malek pilot check

The existing 250 m Charles Malek reference centre in
`beirut-street-reference.md` lies inside TheStreetScape LAS header rectangle.
On 27 October a full read of the resident, uncompressed LAS found 1,398,459
points within 50 m horizontally of that centre, assuming the project-provided
EPSG:32636 alignment. Their elevations range from 83.49 to 134.39 m (median
92.27 m). The reference road centre's modeled source-ground elevation is
61.79 m. These numbers do not establish a vertical offset: points may cover
upper building surfaces, the road may slope, and the datums and transforms
are unverified. They do show that the header rectangle is backed by local
points near the reference. Before using them to reconstruct a particular
façade, inspect recognizable features in the point cloud and independent
photographs, verify horizontal and vertical alignment, and identify which
surfaces the capture actually contains.

As a coarse candidate screen, 13 modeled building bounding rectangles come
within 50 m of the reference centre. The rectangle of building
`beirut001:f8867f8b-e612-4403-be1d-97a2fb3d14af` contains 235,484
StreetScape points in plan view; its modeled elevation range is 64.3–112.8 m
and those points span 86.3–135.4 m. Another candidate,
`beirut001:68046da1-e227-496d-b932-73c158c92e73`, has 254,694 points in its
rectangle; modeled elevations are 60.5–88.9 m and point elevations are
83.5–113.7 m. Bounding rectangles include anything above or beside a building,
so these counts do not establish point-to-façade correspondence, shape match,
or a vertical datum correction. They are candidates for the next visual and
survey-control comparison, not aligned reconstructions.

## Candidate georeference in a textured OBJ

Unlike the other inspected OBJ examples, `Me_1_127.obj` uses coordinates of
roughly four million units per axis. Interpreting every 100th vertex as WGS84
Earth-centred coordinates (EPSG:4978) puts 2,589 sampled vertices around
35.51549–35.51595° E, 33.89603–33.89645° N, near the port and Charles Hélou
Avenue. The sampled WGS84 ellipsoidal heights are 16.3–37.9 m. This is a
plausibility finding, not an embedded CRS declaration: verify independent
landmarks, source documentation and vertical datum before placing the mesh.
The other three inspected OBJs use small local coordinates and still require
their transforms. This candidate is not at the Charles Malek pilot centre;
do not place it there merely because it is textured.

For a second plausibility check, one in 20 OBJ vertices was transformed under
that same EPSG:4978 assumption and compared to the plan-view bounding boxes
of the current building batches. Of 12,942 sampled vertices, 3,748 fall in
the bounding rectangle of `beirut001:3bdd34b3-d625-4c5f-bc39-48a6208d79d8`
and 3,054 in `beirut001:efa2b104-1b1f-4814-81d7-adfa0d50220a`. Eight
building rectangles contain sampled vertices, so the OBJ may span more than
one building or include neighboring surfaces. The first box's modeled height
range is 17.0–28.9 m while its contained sample spans 20.5–33.9 m in WGS84
ellipsoidal height. Box overlap does not prove the match or determine a datum
offset. Inspect the actual geometry and recognizable landmarks before deriving
a transform or replacing any city geometry.

The same sampled vertices were also compared to the union of the actual city
mesh triangles projected horizontally, not just bounding rectangles. Under
the assumed coordinate conversion, 2,745 fall inside the projected triangles
of `beirut001:3bdd34b3-d625-4c5f-bc39-48a6208d79d8` and 2,483 inside those of
`beirut001:efa2b104-1b1f-4814-81d7-adfa0d50220a`. This strengthens the case
for inspecting these two locations, but points at different heights may overlap
in plan and the counts do not prove source identity or vertical registration.

For a separate provisional textured copy, use
`python -m dynacity.engine_capture_ecef --source <Me_1_127.obj> --output <new-private-directory> --origin <east> <north> <height> --assume-ecef`.
The explicit flag acknowledges the unverified source CRS. The converter retains
faces and UVs, copies the matching MTL/JPG, records a source hash, and refuses
to overwrite an existing directory. Its ellipsoidal heights are not an approved
vertical conversion and its centre-based normal transform is an approximation.
The result is for inspection, not an authorized replacement of city geometry.
The provisional textured mesh was loaded in an isolated local Three.js viewer.
It shows identifiable building surfaces alongside incomplete geometry and
floating fragments; it is not ready to replace a whole modeled city block.
The inspection confirms the material and texture load in that viewer, not
geographic or vertical alignment, source date, or Unreal import behavior.
The local inspection viewer also offers a toggleable wireframe of nine nearby
city-batch building meshes, reconstructed from their recorded source triangle
ranges in the same local frame. The overlay loads and toggles in the browser;
it is a visual comparison aid, not a registration, collision test, or proof of
which photograph belongs to any building.

An exploratory surface-distance diagnostic sampled every 100th vertex of the
provisional textured mesh (2,589 vertices) and measured nearest 3-D distances
to 224 triangles in nine nearby city buildings. With no vertical shift the
median was 2.26 m; among the coarse shifts tested, -5 m gave 1.64 m. At that
shift 31.9% of sampled vertices were within 1 m and 72.4% within 3 m of some
city triangle. The full results are private at
`engine/coverage/me127-provisional-mesh/surface-distance-diagnostic-20261027.json`.
This is an exploratory comparison, not a measured vertical offset: the samples
include incomplete and background surfaces, may match the wrong building, and
both the source CRS and relative vertical datums remain unverified. Do not
apply the tested shift to the city on this evidence.

A 27 October sidecar check of the capture source folder found no `.prj`, WKT,
XML, JSON, text, CSV or world-file metadata. The locally copied `Me_1_127.jpg`
texture has no EXIF tags, so it supplies no embedded camera location or datum
information. This is limited to the checked folder and texture; it does not
rule out external project documentation or survey control elsewhere.

The completed texture metadata scan found no EXIF tags in any of the ten JPG
files in the capture folder. This does not rule out external survey records.
