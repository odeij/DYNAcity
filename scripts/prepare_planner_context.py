"""Prepare the street planner's optional context files from raw downloads.

    python scripts/prepare_planner_context.py population --tif lbn_pop_2025_CN_100m_R2025A_v1.tif --output population-beirut.json
    python scripts/prepare_planner_context.py places --geojson places-beirut.geojson --release 2026-11-18.0 --output places-beirut.json

Raw files stay outside the repository (see CLAUDE.md, private data boundary).
`overturemaps download --bbox=… -f geojson -t place -r <release> -o places-beirut.geojson` fetches the places.
The outputs go to `dynacity serve --population … --places …`.
"""
from __future__ import annotations

import argparse
import json

from dynacity.planner_context import prepare_places, prepare_population

WORLDPOP_ATTRIBUTION = (
    "Bondarenko M., Priyatikanto R., Tejedor-Garavito N., Zhang W., McKeen T., Cunningham A., Woods T., Hilton J., "
    "Cihan D., Nosatiuk B., Brinkhoff T., Tatem A., Sorichetta A. (2025). WorldPop, University of Southampton; "
    "Global Demographic Data Project (Gates Foundation INV-045237).")
OVERTURE_ATTRIBUTION = (
    "Overture Maps Foundation (overturemaps.org). Most places come from Meta, Microsoft, Uberall and others under "
    "CDLA Permissive 2.0; Foursquare data is under Apache 2.0 and AllThePlaces data under CC0 1.0. "
    "Foursquare data was transformed to the Overture schema.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    pop = sub.add_parser("population", help="cut a WorldPop 100 m GeoTIFF to the planner extent")
    pop.add_argument("--tif", required=True)
    pop.add_argument("--output", required=True)
    # Beirut road model extent plus a margin wider than the 200 m counting radius.
    pop.add_argument("--bounds", type=float, nargs=4, default=[35.463, 33.825, 35.577, 33.911],
                     metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    pop.add_argument("--product", default="WorldPop Global 2015-2030 R2025A v1, constrained, 100 m")
    pop.add_argument("--year", type=int, default=2025)
    pop.add_argument("--source-url", required=True)

    places = sub.add_parser("places", help="keep the allowlisted fields of an Overture places download")
    places.add_argument("--geojson", required=True)
    places.add_argument("--output", required=True)
    places.add_argument("--release", required=True)
    places.add_argument("--min-confidence", type=float, default=0.5)
    places.add_argument("--source-url", default="https://docs.overturemaps.org/guides/places/")

    args = parser.parse_args()
    if args.command == "population":
        info = prepare_population(
            args.tif, args.output, args.bounds, product=args.product, year=args.year, source_url=args.source_url,
            licence="CC BY 4.0", attribution=WORLDPOP_ATTRIBUTION, doi="10.5258/SOTON/WP00840")
    else:
        info = prepare_places(
            args.geojson, args.output, release=args.release, source_url=args.source_url,
            licence="CDLA Permissive 2.0", attribution=OVERTURE_ATTRIBUTION, min_confidence=args.min_confidence)
    print(json.dumps(info, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
