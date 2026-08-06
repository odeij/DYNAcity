from __future__ import annotations

import argparse
import colorsys
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import laspy
import numpy as np


WEB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_ROOT.parent
DEFAULT_OUTPUTS = REPO_ROOT / "src" / "dynacity_bbed" / "outputs" / "TheStreetScape"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a browser-friendly sample from an augmented LAS/LAZ file."
    )
    parser.add_argument("--las", type=Path, default=DEFAULT_OUTPUTS / "enriched.las")
    parser.add_argument(
        "--geojson", type=Path, default=DEFAULT_OUTPUTS / "registered_bbed.geojson"
    )
    parser.add_argument("--out", type=Path, default=WEB_ROOT / "public" / "data")
    parser.add_argument("--max-points", type=int, default=1_200_000)
    return parser.parse_args()


def web_color(building_id: int) -> tuple[int, int, int]:
    if building_id < 0:
        return (34, 49, 62)
    hue = (building_id * 0.61803398875 + 0.48) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.58, 0.94)
    return (round(red * 255), round(green * 255), round(blue * 255))


def to_u8(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values)
    if values.size and int(np.max(values)) > 255:
        return np.right_shift(values.astype(np.uint16), 8).astype(np.uint8)
    return np.clip(values, 0, 255).astype(np.uint8)


def translate_coordinates(value: Any, origin_x: float, origin_y: float) -> Any:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return [value[0] - origin_x, value[1] - origin_y, *value[2:]]
    if isinstance(value, list):
        return [translate_coordinates(item, origin_x, origin_y) for item in value]
    return value


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def prepare_data(las_path: Path, geojson_path: Path, output_dir: Path, max_points: int) -> None:
    las_path = las_path.resolve()
    geojson_path = geojson_path.resolve()
    output_dir = output_dir.resolve()
    if not las_path.exists():
        raise FileNotFoundError(las_path)
    if not geojson_path.exists():
        raise FileNotFoundError(geojson_path)
    if max_points <= 0:
        raise ValueError("--max-points must be positive")

    output_dir.mkdir(parents=True, exist_ok=True)
    with laspy.open(las_path) as reader:
        header = reader.header
        source_count = int(header.point_count)
        stride = max(1, math.ceil(source_count / max_points))
        render_count = math.ceil(source_count / stride)
        origin_x = float(header.mins[0])
        origin_y = float(header.mins[1])
        dimension_names = set(header.point_format.dimension_names)

        positions = np.empty(render_count * 3, dtype="<f4")
        rgb_colors = np.empty(render_count * 3, dtype=np.uint8)
        bbed_colors = np.empty(render_count * 3, dtype=np.uint8)
        write_cursor = 0
        source_cursor = 0

        for chunk in reader.chunk_iterator(500_000):
            first = (-source_cursor) % stride
            indices = np.arange(first, len(chunk), stride, dtype=np.int64)
            count = len(indices)
            xyz_destination = slice(write_cursor * 3, (write_cursor + count) * 3)

            chunk_positions = np.empty((count, 3), dtype="<f4")
            chunk_positions[:, 0] = np.asarray(chunk.x)[indices] - origin_x
            chunk_positions[:, 1] = np.asarray(chunk.y)[indices] - origin_y
            chunk_positions[:, 2] = 0.0
            positions[xyz_destination] = chunk_positions.reshape(-1)

            if {"red", "green", "blue"}.issubset(dimension_names):
                rgb = np.column_stack(
                    (
                        to_u8(np.asarray(chunk.red)[indices]),
                        to_u8(np.asarray(chunk.green)[indices]),
                        to_u8(np.asarray(chunk.blue)[indices]),
                    )
                )
            else:
                rgb = np.full((count, 3), 188, dtype=np.uint8)
            rgb_colors[xyz_destination] = rgb.reshape(-1)

            if "bbed_id" in dimension_names:
                ids = np.asarray(chunk["bbed_id"])[indices].astype(np.int32)
                colorized = np.empty((count, 3), dtype=np.uint8)
                for building_id in np.unique(ids):
                    colorized[ids == building_id] = web_color(int(building_id))
                bbed_colors[xyz_destination] = colorized.reshape(-1)
            else:
                bbed_colors[xyz_destination] = np.array([34, 49, 62] * count, dtype=np.uint8)

            write_cursor += count
            source_cursor += len(chunk)

    if write_cursor != render_count:
        positions = positions[: write_cursor * 3]
        rgb_colors = rgb_colors[: write_cursor * 3]
        bbed_colors = bbed_colors[: write_cursor * 3]
        render_count = write_cursor

    positions.tofile(output_dir / "positions.bin")
    rgb_colors.tofile(output_dir / "colors-rgb.bin")
    bbed_colors.tofile(output_dir / "colors-bbed.bin")

    with geojson_path.open("r", encoding="utf-8") as handle:
        geojson = json.load(handle)
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        geometry["coordinates"] = translate_coordinates(
            geometry.get("coordinates"), origin_x, origin_y
        )
    geojson = sanitize(geojson)
    geojson["crs"] = {"type": "name", "properties": {"name": "LOCAL_METRE_OFFSETS"}}
    with (output_dir / "buildings.geojson").open("w", encoding="utf-8") as handle:
        json.dump(geojson, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    manifest = {
        "name": las_path.stem,
        "sourceFile": las_path.name,
        "sourcePointCount": source_count,
        "renderedPointCount": render_count,
        "sampleStride": stride,
        "crs": "EPSG:32636",
        "origin": [origin_x, origin_y],
        "bounds": {
            "minX": 0.0,
            "minY": 0.0,
            "maxX": float(header.maxs[0]) - origin_x,
            "maxY": float(header.maxs[1]) - origin_y,
        },
        "positions": "positions.bin",
        "rgbColors": "colors-rgb.bin",
        "bbedColors": "colors-bbed.bin",
        "buildings": "buildings.geojson",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")

    print(
        f"Prepared {render_count:,} of {source_count:,} points "
        f"(stride {stride}) in {output_dir}"
    )


def main() -> None:
    args = parse_args()
    prepare_data(args.las, args.geojson, args.out, args.max_points)


if __name__ == "__main__":
    main()

