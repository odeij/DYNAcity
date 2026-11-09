# Find a location in Unreal

Use **Tools → Explore Beirut → Search location…**. In editor-hosted driving,
**F3** opens the same native search window. Enter a place name, paste a supported
map link, or enter `latitude, longitude`. Press Search/Enter and select a result.

Name search works offline against the downloaded OSM extract, including available
Arabic, English, French and alternative names. Missing names and spelling variants
may not match. No public geocoder or Google Places API is connected. OSM object
links resolve when that named object is in the local index.

Supported coordinate inputs include Google Maps query/centre/place-pin links,
OpenStreetMap marker and map links, comma-separated coordinates and geo URIs.
Google place-pin coordinates take priority over a different map viewport centre.
Directions links use their destination. Google share links are expanded in a
background worker, with HTTPS redirects restricted to supported Google hosts;
links that require JavaScript or lack an identifiable location may need a full
URL or coordinates. Map imagery and page contents are not imported.

Selecting a result focuses the editor camera at its coordinates. Camera height
uses a nearby exported road elevation as a reference, not a surveyed entrance.
During Play, navigation tries nearby modeled roads (within 200 metres), checking
wheel support and vehicle clearance before moving the player. If no safe loaded
surface is available, the player stays where they are. The chosen road offset is
shown. Close the window and use Tab to return the cursor to driving mode.

This integration targets the current Unreal editor and editor-hosted Play
prototype. A packaged application still needs a runtime search data adapter;
the native window and driving placement API themselves are C++.

## Prepare the private search index

Run `scripts/prepare_unreal_locations.py` with `--context` pointing to the merged
OSM display GeoJSON and `--roads` pointing to the city checkpoint's
`roads_metadata`. Write `--output` to the private Unreal project's
`Saved/DynaCity/location-index.json`. This preserves the city's UTM origin and
source road heights. Rebuild when source names, geometry or origin change.

Source data stays outside Git. The window displays OpenStreetMap attribution.
Search is local unless a user explicitly submits a Google short share link.

Google URL parameter reference:
https://developers.google.com/maps/documentation/urls/get-started

Verification is recorded separately from implementation: automated parsing,
projection and bounded road placement checks are in `tests/test_unreal_locations.py`.

Verified in the private editor on 26 October 2026: native text entry submitted a
Google coordinate URL; result selection focused the editor camera; F3 opened the
panel during Play; Google/OSM coordinate destinations placed the vehicle on
validated road surfaces, including a roughly 790 m jump; a Paris coordinate was
rejected with the vehicle position unchanged. The native plugin compiled with
the supported toolchain. The 15 location checks and five existing drive-route
checks passed. Google short-link expansion is implemented but was not exercised
with an actual share link during this verification.
