# Beirut coastline and vessels

Research checked 25 October 2026. This is a source shortlist and integration design; no live vessel feed is connected and no coastline scene is claimed complete.

## Photographic references

| Reference | Date / attribution | Use and limitations |
| --- | --- | --- |
| [Corniche Beirut](https://commons.wikimedia.org/wiki/File:Corniche_Beirut,_Beirut,_Lebanon.jpg) | 20 September 2008; Vyacheslav Argenberg; CC BY 4.0 | Promenade, shoreline and material reference. Older appearance, not current-condition evidence. |
| [Corniche from the sea](https://commons.wikimedia.org/wiki/File:Beirut_Corniche_from_Sea.JPG) | Wusel007; CC BY-SA 3.0 option; camera location 33.907331,35.492750 | Seafront silhouette and massing; confirm capture date from original metadata before temporal claims. Camera position is not an object survey. |
| [Beirut harbour](https://commons.wikimedia.org/wiki/File:Hafen_von_Beirut.jpg) | 16 September 2003; Ismail Küpeli; CC BY 2.0 | Historical harbour appearance only. Not a present-day port layout or ship inventory. |
| [Port photo collection](https://commons.wikimedia.org/wiki/Category:Port_of_Beirut) | Multiple authors, dates and individual licences | Select recent, dated references before reconstructing port infrastructure. Many images are historical or disaster-era. |

Preserve each photo's author, capture date, source page and licence. These are visual references, not automatically reusable texture atlases or a photogrammetric reconstruction. Current vessel paint needs an identified ship's dated photograph; flag, AIS ship type and position do not establish hull colour.

## First scene

Start with the Beirut Corniche / Manara waterfront, then a separate port basin pass. Match local rock forms, seawalls, promenade surfaces, vegetation and shoreline silhouette using photographs and available mapped/source geometry. Build the sea beyond the terrain boundary with an explicit assumed sea plane and vertical reference; do not extend unknown land elevation into the sea. Wave appearance can be illustrative initially, with observed/forecast marine conditions a later input.

Keep three independently toggleable layers: fixed coastal geometry, illustrative/reference-matched vessels, and observed AIS vessels. An illustrative boat must never be labelled as a current observation. Shoreline and vessel appearance remain independent of the live-feed connection.

## AIS candidates

- [AISStream documentation](https://www.aisstream.io/documentation/): WebSocket subscription by bounding box, API key required, server-side credentials, position and static message types. Test actual reception around Beirut before making a coverage claim; geographic subscription capability does not establish reception. Check provider reuse terms before redistribution.
- [VesselFinder API](https://api.vesselfinder.com/docs/vessels.html): timestamp, latitude/longitude, course, speed, heading, navigational status and vessel identity; paid commercial service with terrestrial/satellite options. [Pricing overview](https://www.vesselfinder.com/vessel-positions-api). Do not purchase without user authorization.
- [Global Fishing Watch](https://api-doc.globalfishingwatch.org/): identity/activity research alternative with non-commercial terms; do not substitute processed activity layers for a verified live all-vessel position feed.

No Beirut reception, update latency or completeness has been verified in this research pass. Small/non-transmitting craft and reception gaps must remain visible as limits.

## Integration contract

Use a separate backend collector and local snapshot/replay store. Retain source provider, MMSI, IMO where supplied, name, type, dimensions, position, course, heading, speed, report time and receipt time. Preserve unknowns and AIS sentinel values. Convert WGS84 positions through the existing city coordinate basis, use headings/course correctly, and handle antenna-to-hull offsets where dimensions permit.

Unreal renders a bounded set of nearby vessels using reusable meshes. Interpolate between observations with a short display buffer; cap dead reckoning and freeze/fade stale vessels instead of inventing indefinite motion. Show report age and feed status. Store replay separately from live mode. Match hull geometry to reported dimensions/type where available, and colour only from a dated identified-vessel photo or a clearly illustrative default.

A 30–60 minute reception sample around Beirut port and approaches should establish unique vessel count, timestamps, delays, gaps and static-metadata availability before enabling a Live label. A dry connection or zero reports must not be represented as an empty sea.
