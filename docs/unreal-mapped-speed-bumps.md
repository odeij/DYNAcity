# Mapped speed bumps

Nine `traffic_calming=bump` nodes from the 27 October 2026 infrastructure
extract are displayed in the private Beirut city map: seven in Ras Beyrouth
and two in Ain el Mraisse. Each point was traced against a loaded road mesh at
its source coordinates; road surface heights were 11–16 cm above sampled
ground. The model's orientation and width are inferred from the nearest road,
and its appearance is illustrative. These meshes do not change SUMO or
vehicle physics.

The finished [Innerscene speed bump](https://www.innerscene.com/tools/library/3d-parts/speed-bump-eb8f9af9)
is CC0; source and conversion hashes are recorded in the private asset and
placement manifests. Source-linked placement is produced by
`scripts/prepare_mapped_speed_bumps.py` and imported by
`engine/unreal/Content/Python/mapped_speed_bump_import.py`. The importer checks
the city origin and source and asset digests, skips locations without a suitable
road hit, and saves only its new actor packages. It refuses duplicate imports.

Native reports under the private project's `Saved/DynaCity` record OSM IDs,
road actors and surface adjustments. All nine source IDs were checked after
unloading and reloading the two new World Partition actors. A full map reload
was not attempted because unrelated packages were already dirty. Visual
appearance, mapped road association and runtime performance were not surveyed.
