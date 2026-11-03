# Mapped speed bumps and stop controls

The private asset library contains two finished Innerscene CC0 models:
[speed bump](https://www.innerscene.com/tools/library/3d-parts/speed-bump-eb8f9af9)
and [stop sign](https://www.innerscene.com/tools/library/3d-parts/stop-sign-c05db2e6).
Their source URLs, downloaded SHA256 hashes and licence records sit beside the
original GLBs; the prepared FBX manifests record conversion hashes and bounds.
They are generic illustrations, not observations of local materials or shapes.

`scripts/prepare_mapped_speed_bumps.py` uses only terrain-positioned OSM
`traffic_calming=bump` nodes with a compatible ground-level road. It produced
nine positions from the 27 October infrastructure extract. Width and cross-road
orientation come from the nearest modeled road and are explicitly inferred.
The source elevation is sampled terrain, not road asphalt: a native importer
must trace the road surface at the location and reject missing road hits rather
than burying the bump. Do not infer operation or calibrated driving effects.

The stop-sign model is staged. OSM `highway=stop` control nodes may lie in the
carriageway and do not establish the physical signpost's position or facing;
the five mapped control nodes are not automatically assigned the model.
