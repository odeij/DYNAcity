# Beirut 3D data assessment

Checked 23 October 2026. Goal: real building geometry and photographic textures,
without starting a paid service. Google Earth mode and the Map Tiles proxy have
been removed; the existing Carto/Esri viewer remains available.

| Source | What is verified | Access and limitation |
| --- | --- | --- |
| Existing AUB `BeirutMeshes` | Four OBJ models with about 500,000 triangles each, UV texture coordinates, and matching MTL/JPG names | Existing shared project files; `Me_1_127.jpg` has now downloaded and was opened successfully as an 8192 × 8192 image; the remaining photographic JPG files were cloud placeholders during the initial inventory. Three models have local coordinates without a located transform. One has candidate ECEF coordinates in Beirut. This is four individual captures, not city-wide coverage. Redistribution permission and capture dates are not established. |
| AUB `HeritageZone.glb` | Existing approximately 947 MB file in the shared source folder | Still a cloud placeholder; contents, bounds, date, and positioning are unverified. Candidate for larger surveyed coverage, not a verified ready-to-use source. |
| [Beirut 001 — Salim al-Kadi](https://www.salimalkadi.com/07_beirut-001) | Author offers a free Rhino 5 `.3dm` download, approximately 698 MB, mapping buildings, roads, bridges and tunnels; published January 2020 | Downloaded and successfully opened locally: 19,592 objects, including 17,594 Breps, 1,948 meshes and 39 extrusions; metre units; topography, river, blocks, tunnels/bridges and buildings layers. Only two materials; both have no bitmap textures and the file has no embedded files. This model is geometry-only. The page does not promise photographic facade textures or present-day accuracy. A named redistribution licence is not visible on the project page; confirm before shipping derived assets. |
| [UNESCO / DGA / Iconem](https://www.unesco.org/en/articles/unesco-and-partners-launch-3d-urban-documentation-historic-areas-beirut-affected-explosions) | Official project describes drone surveys and a multiscale 3D model of Gemmayzeh, Mar Mikhail and Karantina, with detailed monuments | Relevant real survey data, announced 2021. [3RF progress report](https://www.lebanon3rf.org/sites/default/files/2023-05/3RF-Progress-Report-Jan2021-Jun2022.pdf) says delivered to DGA and available upon request, too large to publish online. No public ready-to-stream endpoint or confirmed free reuse permission found. |
| [Cesium / Vricon Beirut](https://cesium.com/blog/2020/08/21/cesium-vricon-wfh-update/) | Official announcement documents a pre-August-2020 surface model covering 54 square miles, 50 cm resolution, 3 m SE90 absolute accuracy | The advertised free access is for US federal workers with `.gov`/`.mil` addresses. This is not confirmed as freely available for this app. Do not assume a Cesium account grants rights to it. |
| [Anyscape Beirut](https://anyscape.io/3d-city-models/beirut/) | Provider describes LoD1 buildings from Overture, roads and 30 m terrain; free sample and paid catalogue | Massing rather than a verified photographic surface reconstruction. Not a replacement for the realistic facades requested. No purchase made. |

## Local mesh inspection

Actual OBJ geometry was read, not inferred from filenames. The private inventory
is `~/dynacity-private/data/beirut-mesh-inventory.json`.

| Model | Vertices | Faces | Position evidence |
| --- | ---: | ---: | --- |
| Ac_726 | 258,898 | 499,999 | Local coordinate system; transform missing |
| RM_1_312 | 255,356 | 500,000 | Local coordinate system; transform missing |
| Me_1_127 | 258,821 | 499,999 | Coordinates consistent with ECEF; interpreting as EPSG:4978 gives centre 35.515723 E, 33.896214 N, ellipsoid height about 27.7 m. Source CRS remains unconfirmed. |
| RM_2_387 | 253,375 | 500,000 | Local coordinate system; transform missing |

`Me_1_127.mtl` explicitly references `Me_1_127.jpg`. All four OBJs contain UV
coordinates. This establishes that textured models were exported, and its 8192 × 8192 JPG is now readable. Geographic alignment and visual quality still require verification.

## Local pilot result

Converted `Me_1_127.obj` and its original texture to a private GLB, preserving
all 499,999 faces. Local browser inspection confirms that it renders with the
photographic texture, but it is a fragmented surface capture, not a complete
clean building. Do not present it as a finished replacement for the city.
The standalone inspection is served at `http://127.0.0.1:8013/`; it is not
placed in the main map. The GLB and metadata remain outside Git in
`~/dynacity-private/data/beirut-mesh-pilot/`.

## Recommended path

Use the existing AUB captures as the first real-textured-building pilot once
OneDrive supplies the JPG textures and the source coordinate transforms are
verified. Inspect HeritageZone for wider coverage. Keep survey dates visible;
2020/2021 captures must not be presented as current conditions. Beirut 001 is
an independent city-scale geometry candidate, not a promised photorealistic
replacement. No verified free, unrestricted, current, city-wide photographic
3D streaming service for Beirut was found in this search.

## Follow-up: Beirut 001 and Iconem (23 October 2026)

### Beirut 001: usable geometry, missing placement metadata

A second inspection counted **17,575 geometry objects on `05_BUILDINGS`**:
17,536 Breps and 39 extrusions. The previous 19,592 count includes terrain,
block meshes, river, transport structures, curves and two annotations, so it
must not be described as a building count.

The building layer spans approximately 6.71 km × 4.86 km in local XY; this is
its bounding rectangle, not a verified coverage polygon. Topography spans
approximately 7.21 km × 5.87 km. Z values in the building layer range from
−5.22 to 280.40 m; no vertical datum is declared.

The document's Earth anchor is unset (latitude/longitude zero, model origin
zero); there are no document user strings providing CRS information. The
model has cached render meshes for Brep faces (about 1.05 million cached
faces on the building layer), making a browser preview practical without
remodelling the source. These are author-created geometry, not a photographic
survey or verified current state.

The [author's page](https://www.salimalkadi.com/07_beirut-001) explicitly offers
free download and calls the work open source, but a named licence and specific
web redistribution conditions were not found there or in document strings.
[Contact](https://www.salimalkadi.com/contact-1): info@salimalkadi.com. Ask for
CRS/alignment and permission for conversion, optimization and public web serving.

### Iconem: actual Beirut catalogue entry found

On [Iconem's live platform](https://app.iconem.com/), searching “Beirut” returned:

- Historical center - Beirut (2021)
- Beirut, rue Gouraud (avec environnement)
- Eglise St Joseph - Beirut
- Rue Gouraud - Beirut
- Evêché - Beirut

Opening the first result produced an explicit “This model is not public yet —
contact us to request access” notice. The notice attributes restrictions to
partner licensing, says research/educational access can often be arranged, and
mentions other uses may be licensed. This verifies a catalogue entry and an
access route, not actual access to the mesh, confirmed textures, a downloadable
format, permission to embed it, free eligibility, or complete city coverage.

Official contacts: contact@iconem.com ([Iconem](https://iconem.com/research/))
and beirut@unesco.org ([UNESCO Beirut](https://www.unesco.org/en/fieldoffice/beirut)).
Ask for the responsible DGA data custodian and a small textured sample with
metadata. The partner release must explicitly cover public web visualization
and any derivative assets; access to Iconem's viewer alone does not establish
those rights.

The access notice screenshot is `tmp/iconem-beirut-access.png`. Two concrete
request drafts are saved in [data-requests/beirut-3d-access.md](data-requests/beirut-3d-access.md).
No messages have been sent and no paid service has been ordered.

### Beirut 001 standalone preview

Converted original meshes and cached Brep render meshes to a 122 MB GLB for
local inspection only. The preview includes 17,575 building geometry objects
plus terrain, blocks, river, bridges and tunnels; curves and text are omitted.
It contains 5,308,463 triangles after converting quad faces to triangles.
Neutral display colours are added by layer. Geometry is only rotated from
Rhino Z-up to glTF Y-up; no geographic transform is guessed and no photographic
textures are generated. Open `http://127.0.0.1:8013/beirut-001.html` while the
local inspection server runs. Original files and converted assets stay in
`~/dynacity-private/data/`, outside Git. No change was made to the main app's
building dataset.


## BBED hover overlay pilot

A separate local overlay is now available at `http://127.0.0.1:8013/bbed/`.
It preserves original model geometry and provisionally links 709 objects to
BBED by conservative footprint agreement. All remaining model objects stay
unlinked. See [the pilot documentation](beirut-overlay-pilot.md) for thresholds,
source/date boundaries, rebuild steps and limitations. The main forecasting
app has not adopted these links or geometry.
