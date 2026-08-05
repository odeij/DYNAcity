from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .arcgis import DEFAULT_BBED_LAYER
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dynacity-bbed",
        description="Register BBED footprints to a Beirut LAS/LAZ point cloud.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run retrieval, matching, enrichment, and reporting")
    run.add_argument("--input", required=True, type=Path, help="input LAS or LAZ file")
    run.add_argument(
        "--bbed-source",
        default=DEFAULT_BBED_LAYER,
        help="ArcGIS FeatureServer layer URL or local GeoJSON/Shapefile",
    )
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument(
        "--point-crs",
        help="point CRS, required when LAS lacks CRS (the current tile uses EPSG:32636)",
    )
    run.add_argument("--bbed-crs", help="CRS override required for a local BBED file without CRS")
    run.add_argument("--chunk-size", type=int, default=1_000_000)
    run.add_argument("--bbox-buffer", type=float, default=2.0)
    run.add_argument(
        "--building-class",
        type=int,
        action="append",
        default=[],
        help="LAS class used for per-building statistics; may be repeated",
    )
    run.add_argument(
        "--instances",
        help="optional segmented building-instance GeoJSON/Shapefile for IoU evaluation",
    )
    run.add_argument("--instance-crs", help="CRS override for instance polygons")
    run.add_argument("--iou-threshold", type=float, default=0.1)
    run.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            report = run_pipeline(
                args.input,
                args.bbed_source,
                args.output_dir,
                point_crs_value=args.point_crs,
                bbed_crs_value=args.bbed_crs,
                chunk_size=args.chunk_size,
                bbox_buffer=args.bbox_buffer,
                building_classes=tuple(args.building_class),
                instances_source=args.instances,
                instance_crs_value=args.instance_crs,
                iou_threshold=args.iou_threshold,
                overwrite=args.overwrite,
            )
            print(json.dumps(report["matching"], indent=2))
            return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

