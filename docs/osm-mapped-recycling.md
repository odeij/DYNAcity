# Mapped recycling containers

Five mapped OSM nodes carry `recycling_type=container` and usable sampled
ground positions. `scripts/prepare_mapped_recycling.py` creates a private
source-linked manifest. A recycling centre and unspecified recycling locations
are not treated as individual containers. Mapped coordinates are not surveys;
source elevation can be approximate.

The finished [Innerscene public recycling station](https://www.innerscene.com/tools/library/3d-parts/litter-and-recycling-station-public-transport-furniture-e0500679)
is CC0, with source and conversion hashes retained. This 1.2 m generic model is
an illustrative choice: local size, appearance, orientation and current
operation are unknown. The data does not identify what the mapped units look
like. The staging manifest itself does not imply installation in Unreal.
