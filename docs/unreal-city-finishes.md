# Beirut city surface finishes

`engine/unreal/Content/Python/style_city.py` applies reversible material
overrides to the loaded city buildings, roads, terrain and transport structures.
It preserves mesh assets, actor transforms, collision and source identities.
The saved report compares geometry/collision signatures before and after.

The initial free library is stored privately under
`C:/private/dynacity-data/assets-inbox`:

- ambientCG Asphalt012, Plaster001 and Concrete030: 1K JPG material packages,
  including colour, roughness and normal maps. Source pages and CC0 licence
  links are retained in `ambientCG/manifest.json`, with archive SHA256 hashes.
- Poly Haven `plastered_stone_wall`: 1K colour, roughness and DirectX normal
  maps. Downloads are checked against the provider's byte sizes and MD5 values.
  Its companion manifest records the CC0 licence and Powered by Poly Haven
  API credit.

Sources: <https://ambientcg.com/view?id=Asphalt012>,
<https://ambientcg.com/view?id=Plaster001>,
<https://ambientcg.com/view?id=Concrete030>,
<https://polyhaven.com/a/plastered_stone_wall>.
Licences: <https://docs.ambientcg.com/license/> and
<https://polyhaven.com/license>.

In Unreal Python, after loading the city geometry and ending Play:

    import style_city
    style_city.apply(r"C:/private/dynacity-data/assets-inbox/ambientCG")

World-space projection works on the source meshes without replacing geometry
or requiring newly unwrapped UVs. The initial material graph uses colour and
roughness maps. Downloaded normal maps remain available for a subsequent
normal-mapping pass. Facade paint, window patterns, roof finish and assignment
of materials to buildings are illustrative. They are not colours sampled from
Beirut imagery or observed architectural details. Unclassified terrain stays
neutral; it is not indiscriminately painted as vegetation or pavement.

`style_city.daylight()` adjusts the existing directional/fill lights and fixed
exposure for clearer presentation. Its sun direction is illustrative, not a
location/date solar calculation. `style_city.restore_daylight()` restores the
saved light intensities, rotation and exposure values. This remains a prototype
material pass, with flat shader windows rather than measured balconies,
storefronts or individually reconstructed facades.

`style_city.restore()` restores the original component overrides from the
private `Saved/DynaCity/city-finish-backup.json` and saves the map. Keep that
backup when moving the project. Reapplying keeps the original backup.

For location-specific accuracy, collect street-level Beirut photographs with
street/location and capture date, plus georeferenced aerial imagery with a
clear usage licence. Satellite imagery can guide visible roofs and land cover;
it does not reveal obscured facades, shops or balcony details. Do not infer
those details from overhead colour alone.

## Pavements and lane paint

`M_Pavement` is a light neutral grey concrete (Concrete030 with a lifted,
colour-neutral tint) so footways read clearly against the darker brown terrain
material. Batch slot 2 (`roads_*_material_2`) stays pavement and kerb.

`engine_city_surface.py` (`shared-city-surface/3`) now emits lane paint for
every street-level carriageway of class motorway, trunk, primary, secondary,
tertiary, unclassified or residential (and their `_link` classes). Service,
footway, living-street and similar classes get none. Two-way roads get a dashed
centre line (3 m dash, 6 m gap), extra lanes get dashed dividers using the OSM
lane count, and every carriageway gets solid edge lines. Dimensions are
illustrative, and paint is clipped to the asphalt each road owns, so junction
boxes stay unpainted. Paint is a `marking` mesh using glTF material index 10
("Lane paint"), which the batcher turns into `roads_*_material_10` actors.

Missing-ground roads: build both the surface and the buildings with the same
`--fallback-terrain <private>/terrain-glo30.npz` so roads, terrain and
buildings share one ground (lower accuracy than the model; see
`beirut-shared-world.md`).

After this change the surface and batch assets must be regenerated
(`engine_city_surface`, then `engine_city_batches`), and the new materials only
appear after an editor restart: restart Unreal, re-import/rebuild the road
batches, then run `style_city.apply(...)` again. It creates `M_Pavement`
(updated tint) and `M_LaneMarking` and maps `_material_10` to the paint. Until
that refresh, existing road actors keep their old materials.
