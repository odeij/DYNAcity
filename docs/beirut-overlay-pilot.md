# Beirut 001 × BBED overlay pilot

The pilot is a separate, local, read-only view. It does not replace the forecast
app's geometry or attach model dimensions to the forecasting dataset.

Open `http://127.0.0.1:8013/bbed/` while the private preview server runs.
The initial camera shows one provisional geometric match. Hover a model object
to inspect it, click to pin the card, and choose **BBED record**, **Beirut 001**
or **Both sources**. Example buttons, a whole-city view, and exact BBED OBJECTID
or model UUID search support repeatable review. Escape releases a pinned card.

## What is linked

The source Rhino model has no geographic anchor. An exploratory registration
compares the building-centroid patterns, searches rigid XY rotations and
translations, and refines with nearby centroid pairs at unchanged metre scale.
For these files it finds approximately +1.346° rotation and a translation of
(731999.313, 3752431.385) m into BBED's EPSG:32636 coordinates. This is an
inferred alignment, not independent control-point or surveyed verification.

Each model object's XY convex silhouette is compared to valid BBED footprints.
A provisional link needs intersection-over-union of at least 0.65, centroid
separation at most 8 m, and at least 0.15 advantage over the next candidate.
A record claimed by multiple model objects remains unlinked. The actual
rendered geometry remains the original mesh; the convex hull is only a matching
proxy. BBED outlines are projected above the linked object's model roof for
visibility; this is a display placement, not a vertical datum transformation.

The checked files produce 709 provisional one-to-one links, 16,866 unlinked
model objects, and four invalid BBED geometries excluded from matching without
repair. Counts depend on input files and thresholds. A source model object is
not guaranteed to equal one real building. Spatial agreement is not identity
verification, especially across January 2020 and later BBED survey waves.

The card preserves every property in a linked BBED feature, including raw
placeholder and missing values. The source-current-status field has no verified
date in this raw file and is not labelled as today's condition. Unlinked
objects get no nearby BBED attributes. The model side reports only the source
UUID, name if present, type, layer, release date, mesh extents and triangle count.
A model vertical extent is not substituted for recorded building height.

## Rebuilding privately

Install the project's optional `overlay` dependencies in the chosen Python
environment. Keep original source files, intermediate buffers, JSON records,
mesh assets and screenshots outside Git. No cloud API or paid service is used.

1. Run `scripts/register_beirut001.py --model MODEL.3dm --bbed BBED.geojson --output PRIVATE_REGISTRATION_DIR`.
2. Review the registration and its residuals; do not interpret a low centroid
   distance as survey accuracy.
3. Run `scripts/prepare_beirut_overlay.py --model MODEL.3dm --bbed BBED.geojson --registration PRIVATE_REGISTRATION_DIR/registration.json --output PRIVATE_VIEW_DIR`.
4. Install the pinned dependencies from `scripts/beirut_overlay.package.json`
   in a private runtime directory. Copy `scripts/beirut_overlay_web.js` into
   that directory and bundle it with its esbuild binary into
   `PRIVATE_VIEW_DIR/overlay.js` (`--bundle --format=esm --minify`).
5. Copy `src/dynacity/beirut_overlay_template.html` to `PRIVATE_VIEW_DIR/index.html`.
6. Serve the output directory with a localhost-only static server. Do not
   publish source or derivative geometry before clarifying the author's reuse
   conditions. This restriction does not prevent local evaluation.

The private JSON includes SHA-256 hashes of both inputs, inferred-registration
parameters, per-object link evidence, model draw ranges and all source records.
Face-to-object IDs are stored separately from geometry. Browser picking uses
an indirect BVH so acceleration does not reorder the source face IDs.

## Main map integration

The overlay now runs inside the existing `dynacity serve` map (port 8012 in the
local preview). The standalone viewer is only an inspection artifact.

After preparing the pilot buffers, run:

```sh
python scripts/prepare_beirut_map_mesh.py --directory PRIVATE_VIEW_DIR
```

Start the normal serve command with `--beirut-overlay-dir PRIVATE_VIEW_DIR`.
This option is restricted to loopback hosts. The main app exposes only explicit
JSON/mesh buffers through `/v1/beirut-overlay/` with private, no-store responses.
The main map has Building geometry and Building information source selectors.
Hover opens the existing right-hand details panel; click pins a record. Original
BBED geometry remains selectable on the same map.

The converter applies the inferred rigid XY transform to UTM 36N, reprojects
into WGS84/Web Mercator, and uses common-world XY offsets with metre Z. Each
object's minimum Z is subtracted so it rests on the existing flat map ground.
This is a disclosed display shift, not terrain alignment. Original source Z
ranges remain in the information card. Native BBED shapes, forecasts and data
files are not overwritten. Mesh picking encodes the original model-object ID
per vertex and returns it through the normal deck picking callback.
