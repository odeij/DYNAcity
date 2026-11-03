# Named Beirut production areas

The first city-wide build uses twelve source-labelled production groups from
[BASEMAP Municipal Beirut Sectors (Area), layer 4](https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/BASEMAP/FeatureServer/4).
Source polygons and sector IDs are preserved. This is a production plan, not
verification of current legal boundaries. `Achrafiye` and `Achrafieh` are merged
through an explicit draft transliteration alias; both original labels remain in
the manifest. Four unnamed source records do not become invented districts.

The groups are Achrafieh, Ain el Mraisse, Bachoura, Mazraa, Medouar, Minet el Hosn,
Moussaytbe, Port, Ras Beyrouth, Remeil, Saife and Zoqaq el Blat. Names follow the
source, rather than introducing a new arbitrary grid or unverified spelling.

## Ownership and continuity

`engine_districts.py` assigns each complete building to the named polygon with
the largest footprint overlap. All intersected group names are recorded, and
ties resolve by name for repeatability. Buildings are never sliced at borders.
Objects outside named coverage remain unassigned. Roads retain shared network
IDs and source OSM ways, with cross-group membership; the manifest does not
split SUMO into independent neighborhood simulations.

The actual full structures-pack run on 25 October 2026 contains 17,569 building
records: 17,420 assigned to twelve groups and 149 unassigned. The traffic model
contains 10,817 roads, of which 367 intersect more than one group. These are
source-data counts, not a claim of complete present-day Beirut coverage.

## Incremental assets

Per-object fingerprints include source identity, normalized footprint, ground,
roof and source metadata. Separate building and combined group hashes prevent a
road-only change from rebuilding building meshes. Combined hashes include road
geometry, lanes, direction, class/level and the shared ground grid. The builder
also fingerprints its exporter code and common origin. `--previous` reports
changed, removed and unchanged groups.

`engine_city_build.py` emits immutable, content-addressed GLB building packages.
All twelve packages use the prototype's common UTM origin; no independent floor
normalization is applied. No per-district calibration actors are emitted, so a
future shared world has only one coordinate basis. Cache hits validate a GLB
header and metadata and leave the asset file untouched. Partial builds can resume.

The first generated set contains 17,397 exported buildings across twelve GLBs,
about 60 MB total. Twenty-three assigned records cannot be meshed because of
missing source ground or degenerate footprints (with `--fallback-terrain`, buildings the model ground does not reach sit on the same merged ground as the roads, marked `ground_source: glo30`); their IDs and reasons are kept.
The 149 unassigned records remain in the production manifest. A repeat build
verified twelve cache hits and unchanged asset modification times.

```powershell
python -m dynacity.engine_districts --pack <structures-pack.npz> --sectors <private>/source-sectors.geojson --fetch --traffic <traffic.json> --output <private>/beirut-districts.json
python -m dynacity.engine_city_build --pack <structures-pack.npz> --districts <private>/beirut-districts.json --origin-scene <prototype>/scene.json --output <private>/city-packages
python scripts/render_engine_coverage.py --districts <private>/beirut-districts.json --captures <private>/capture-inventory.json --output <private>/coverage.png
```

The source-derived assets stay outside Git. Building packages are prepared for
Unreal; they have not all been imported into the running prototype. Shared
terrain/roads, World Partition, streaming collision and the packaged driving
client remain the next integration stage. The existing native editor map still
contains the original 629-building neighborhood.
