#!/usr/bin/env python3
"""
Prepare corridor material UVs: add face-normal NORMAL + TEXCOORD_0 for dominant-axis planar metric mapping.
Input: GLB with POSITION only (no existing NORMAL/TEXCOORD). Output: NEW GLB with same triangles, split vertices per face.
1 UV repeat per --tile-metres (default 1). Preserve exact triangle coordinates/order/winding, indices, materials, extras.
Reject sparse/skin/morph/interleaving/unknown transforms. No overwrite. No source accuracy claims.
"""
import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_TYPE_BYTE = 5120
COMPONENT_TYPE_UNSIGNED_BYTE = 5121
COMPONENT_TYPE_SHORT = 5122
COMPONENT_TYPE_UNSIGNED_SHORT = 5123
COMPONENT_TYPE_UNSIGNED_INT = 5125
COMPONENT_TYPE_FLOAT = 5126

ACCESSOR_TYPE_SCALAR = "SCALAR"
ACCESSOR_TYPE_VEC2 = "VEC2"
ACCESSOR_TYPE_VEC3 = "VEC3"
ACCESSOR_TYPE_VEC4 = "VEC4"
ACCESSOR_TYPE_MAT2 = "MAT2"
ACCESSOR_TYPE_MAT3 = "MAT3"
ACCESSOR_TYPE_MAT4 = "MAT4"

ACCESSOR_TYPE_SIZES = {
    ACCESSOR_TYPE_SCALAR: 1, ACCESSOR_TYPE_VEC2: 2, ACCESSOR_TYPE_VEC3: 3,
    ACCESSOR_TYPE_VEC4: 4, ACCESSOR_TYPE_MAT2: 4, ACCESSOR_TYPE_MAT3: 9, ACCESSOR_TYPE_MAT4: 16,
}
COMPONENT_TYPE_SIZES = {
    COMPONENT_TYPE_BYTE: 1, COMPONENT_TYPE_UNSIGNED_BYTE: 1, COMPONENT_TYPE_SHORT: 2,
    COMPONENT_TYPE_UNSIGNED_SHORT: 2, COMPONENT_TYPE_UNSIGNED_INT: 4, COMPONENT_TYPE_FLOAT: 4,
}
COMPONENT_TYPE_STRUCT = {
    COMPONENT_TYPE_BYTE: "b", COMPONENT_TYPE_UNSIGNED_BYTE: "B", COMPONENT_TYPE_SHORT: "h",
    COMPONENT_TYPE_UNSIGNED_SHORT: "H", COMPONENT_TYPE_UNSIGNED_INT: "I", COMPONENT_TYPE_FLOAT: "f",
}


class GLBError(Exception):
    pass


def read_accessor(gltf: Dict, bin_data: bytes, accessor_idx: int) -> List[float]:
    accessor = gltf["accessors"][accessor_idx]
    bv_idx = accessor.get("bufferView", 0)
    bv = gltf["bufferViews"][bv_idx]
    byte_offset = bv.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    byte_stride = bv.get("byteStride", 0)
    ctype = accessor["componentType"]
    atype = accessor["type"]
    count = accessor["count"]
    num_comp = ACCESSOR_TYPE_SIZES[atype]
    csize = COMPONENT_TYPE_SIZES[ctype]
    fmt_char = COMPONENT_TYPE_STRUCT[ctype]

    if ctype == COMPONENT_TYPE_FLOAT and byte_stride == 0:
        elem = num_comp * csize
        slc = bin_data[byte_offset:byte_offset + count * elem]
        return list(struct.unpack(f"<{count * num_comp}{fmt_char}", slc))

    res = []
    elem = num_comp * csize
    stride = byte_stride if byte_stride > 0 else elem
    for i in range(count):
        off = byte_offset + i * stride
        slc = bin_data[off:off + elem]
        res.extend(struct.unpack(f"<{num_comp}{fmt_char}", slc))
    return res


def read_indices(gltf: Dict, bin_data: bytes, accessor_idx: int) -> List[int]:
    accessor = gltf["accessors"][accessor_idx]
    bv_idx = accessor.get("bufferView", 0)
    bv = gltf["bufferViews"][bv_idx]
    byte_offset = bv.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    ctype = accessor["componentType"]
    count = accessor["count"]
    csize = COMPONENT_TYPE_SIZES[ctype]
    fmt_char = COMPONENT_TYPE_STRUCT[ctype]
    slc = bin_data[byte_offset:byte_offset + count * csize]
    return list(struct.unpack(f"<{count}{fmt_char}", slc))


def write_accessor_float(data: List[float], components: int) -> Tuple[bytes, int, int]:
    b = struct.pack(f"<{len(data)}f", *data)
    pad = (-len(b)) % 4
    if pad:
        b += b"\x00" * pad
    return b, len(b), len(data) // components


def write_accessor_uint(data: List[int]) -> Tuple[bytes, int, int]:
    b = struct.pack(f"<{len(data)}I", *data)
    pad = (-len(b)) % 4
    if pad:
        b += b"\x00" * pad
    return b, len(b), len(data)


def cross(ax, ay, az, bx, by, bz):
    return ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx


def normalize(x, y, z):
    l = math.sqrt(x*x + y*y + z*z)
    if l == 0:
        return 0.0, 0.0, 0.0
    return x/l, y/l, z/l


def dominant_axis_planar_uv(p0, p1, p2, normal, tile_metres: float) -> Tuple[List[float], str]:
    nx, ny, nz = normal
    ax = abs(nx); ay = abs(ny); az = abs(nz)
    if ax >= ay and ax >= az:
        uvs = [
            p0[1] / tile_metres, p0[2] / tile_metres,
            p1[1] / tile_metres, p1[2] / tile_metres,
            p2[1] / tile_metres, p2[2] / tile_metres,
        ]
        axis = "X"
    elif ay >= ax and ay >= az:
        uvs = [
            p0[0] / tile_metres, p0[2] / tile_metres,
            p1[0] / tile_metres, p1[2] / tile_metres,
            p2[0] / tile_metres, p2[2] / tile_metres,
        ]
        axis = "Y"
    else:
        uvs = [
            p0[0] / tile_metres, p0[1] / tile_metres,
            p1[0] / tile_metres, p1[1] / tile_metres,
            p2[0] / tile_metres, p2[1] / tile_metres,
        ]
        axis = "Z"
    return uvs, axis


def parse_gltb(path: Path) -> Tuple[Dict, bytes]:
    data = path.read_bytes()
    if len(data) < 12:
        raise GLBError("File too small for GLB header")
    magic, version, total_len = struct.unpack("<III", data[:12])
    if magic != GLB_MAGIC:
        raise GLBError(f"Invalid GLB magic: 0x{magic:08x}")
    if version != GLB_VERSION:
        raise GLBError(f"Unsupported GLB version: {version}")
    if total_len != len(data):
        raise GLBError(f"Length mismatch: header {total_len}, file {len(data)}")

    offset = 12
    json_chunk = None
    bin_chunk = None
    while offset < len(data):
        if offset + 8 > len(data):
            raise GLBError("Truncated chunk header")
        chunk_len, chunk_type = struct.unpack("<II", data[offset:offset+8])
        offset += 8
        if offset + chunk_len > len(data):
            raise GLBError("Chunk extends past file end")
        chunk_data = data[offset:offset+chunk_len]
        offset += chunk_len
        if chunk_len % 4 != 0:
            offset += 4 - (chunk_len % 4)
        if chunk_type == CHUNK_JSON:
            if json_chunk is not None:
                raise GLBError("Multiple JSON chunks")
            json_chunk = json.loads(chunk_data.decode("utf-8"))
        elif chunk_type == CHUNK_BIN:
            if bin_chunk is not None:
                raise GLBError("Multiple BIN chunks")
            bin_chunk = chunk_data
    if json_chunk is None:
        raise GLBError("No JSON chunk")
    if bin_chunk is None:
        raise GLBError("No BIN chunk")
    return json_chunk, bin_chunk


def validate_gltf(gltf: Dict, bin_data: bytes = None) -> int:
    if len(gltf.get('buffers', [])) != 1 or 'uri' in gltf['buffers'][0]:
        raise GLBError('One embedded buffer required')
    if bin_data is not None:
        declared = gltf['buffers'][0]['byteLength']
        if not isinstance(declared, int) or declared < 0 or declared > len(bin_data):
            raise GLBError('Invalid embedded buffer length')
        for acc in gltf.get('accessors', []):
            vi = acc.get('bufferView', -1)
            if not isinstance(vi, int) or not 0 <= vi < len(gltf.get('bufferViews', [])):
                raise GLBError('Invalid accessor bufferView')
            view = gltf['bufferViews'][vi]
            start, length, local, count = view.get('byteOffset', 0), view['byteLength'], acc.get('byteOffset', 0), acc['count']
            if view.get('buffer', 0) != 0 or view.get('byteStride', 0):
                raise GLBError('Only non-interleaved buffer0 supported')
            if not all(isinstance(x, int) and x >= 0 for x in (start, length, local, count)) or count == 0:
                raise GLBError('Invalid accessor range')
            size = COMPONENT_TYPE_SIZES[acc['componentType']] * ACCESSOR_TYPE_SIZES[acc['type']]
            if start + length > declared or local + count * size > length:
                raise GLBError('Accessor exceeds bufferView or buffer')
    # Reject unsupported top-level features
    if "skins" in gltf and gltf["skins"]:
        raise GLBError("Skinning not supported")
    if "animations" in gltf and gltf["animations"]:
        raise GLBError("Animations not supported")
    if "extensionsRequired" in gltf or "extensionsUsed" in gltf:
        raise GLBError("Extensions not supported")

    # Must have exactly one mesh with exactly one primitive
    if not gltf.get("meshes") or len(gltf["meshes"]) != 1:
        raise GLBError("Exactly one mesh required")
    mesh = gltf["meshes"][0]
    if not mesh.get("primitives") or len(mesh["primitives"]) != 1:
        raise GLBError("Exactly one primitive required")
    prim = mesh["primitives"][0]

    # Reject morph targets
    if "targets" in prim and prim["targets"]:
        raise GLBError("Morph targets not supported")

    # Validate primitive mode (must be TRIANGLES = 4, or absent which defaults to 4)
    mode = prim.get("mode", 4)
    if mode != 4:
        raise GLBError("Only TRIANGLES mode (4) supported")

    attrs = prim.get("attributes", {})
    if "POSITION" not in attrs:
        raise GLBError("POSITION attribute required")

    # Reject existing NORMAL or TEXCOORD_0
    if "NORMAL" in attrs or "TEXCOORD_0" in attrs:
        raise GLBError("NORMAL or TEXCOORD_0 already present; not supported")

    # Reject any other attributes besides POSITION
    for attr_name in attrs:
        if attr_name != "POSITION":
            raise GLBError(f"Unsupported attribute: {attr_name}")

    # Validate indices
    if "indices" not in prim:
        raise GLBError("Indexed geometry required")

    # Validate material index
    material_idx = prim.get("material", 0)

    # Validate accessors and bufferViews for POSITION and indices
    pos_accessor_idx = attrs["POSITION"]
    pos_accessor = gltf["accessors"][pos_accessor_idx]
    if pos_accessor["componentType"] != COMPONENT_TYPE_FLOAT:
        raise GLBError("POSITION must be float (componentType 5126)")
    if pos_accessor["type"] != "VEC3":
        raise GLBError("POSITION must be VEC3")

    idx_accessor = gltf["accessors"][prim["indices"]]
    if idx_accessor["componentType"] not in (COMPONENT_TYPE_UNSIGNED_SHORT, COMPONENT_TYPE_UNSIGNED_INT):
        raise GLBError("Indices must be UNSIGNED_SHORT (5123) or UNSIGNED_INT (5125)")
    if idx_accessor["type"] != "SCALAR":
        raise GLBError("Indices must be SCALAR")

    # Reject sparse accessors
    for acc in gltf.get("accessors", []):
        if "sparse" in acc:
            raise GLBError("Sparse accessors not supported")
        if "normalized" in acc and acc["normalized"]:
            raise GLBError("Normalized accessors not supported")

    # Reject interleaved bufferViews (byteStride != 0 for POSITION)
    pos_bv_idx = pos_accessor.get("bufferView", 0)
    pos_bv = gltf["bufferViews"][pos_bv_idx]
    if pos_bv.get("byteStride", 0) != 0:
        raise GLBError("Interleaved bufferViews not supported")

    # Reject node transforms (must be identity)
    if "nodes" in gltf:
        for node in gltf["nodes"]:
            if "matrix" in node:
                raise GLBError("Node matrix transform not supported")
            if "translation" in node and node["translation"] != [0.0, 0.0, 0.0]:
                raise GLBError("Node translation not supported")
            if "rotation" in node and node["rotation"] != [0.0, 0.0, 0.0, 1.0]:
                raise GLBError("Node rotation not supported")
            if "scale" in node and node["scale"] != [1.0, 1.0, 1.0]:
                raise GLBError("Node scale not supported")

    # Reject images/samplers/textures (material textures)
    if "textures" in gltf or "samplers" in gltf or "images" in gltf:
        raise GLBError("Textures not supported")

    return material_idx


def build_output_gltf(input_gltf: Dict, new_positions: List[float], new_normals: List[float],
                       new_uvs: List[float], new_indices: List[int],
                       material_idx: int, tile_metres: float, source_uuid: str,
                       input_hash: str) -> Tuple[Dict, bytes]:
    gltf = json.loads(json.dumps(input_gltf))

    pos_bytes, pos_len, pos_count = write_accessor_float(new_positions, 3)
    norm_bytes, norm_len, norm_count = write_accessor_float(new_normals, 3)
    uv_bytes, uv_len, uv_count = write_accessor_float(new_uvs, 2)
    idx_bytes, idx_len, idx_count = write_accessor_uint(new_indices)

    new_bin = pos_bytes + norm_bytes + uv_bytes + idx_bytes

    pos_offset = 0
    norm_offset = pos_len
    uv_offset = norm_offset + norm_len
    idx_offset = uv_offset + uv_len

    gltf["buffers"] = [{"byteLength": len(new_bin)}]
    gltf["bufferViews"] = [
        {"buffer": 0, "byteOffset": pos_offset, "byteLength": pos_len, "target": 34962},
        {"buffer": 0, "byteOffset": norm_offset, "byteLength": norm_len, "target": 34962},
        {"buffer": 0, "byteOffset": uv_offset, "byteLength": uv_len, "target": 34962},
        {"buffer": 0, "byteOffset": idx_offset, "byteLength": idx_len, "target": 34963},
    ]
    gltf["accessors"] = [
        {"bufferView": 0, "componentType": COMPONENT_TYPE_FLOAT, "count": pos_count, "type": "VEC3",
         "min": [min(new_positions[i::3]) for i in range(3)],
         "max": [max(new_positions[i::3]) for i in range(3)]},
        {"bufferView": 1, "componentType": COMPONENT_TYPE_FLOAT, "count": norm_count, "type": "VEC3"},
        {"bufferView": 2, "componentType": COMPONENT_TYPE_FLOAT, "count": uv_count, "type": "VEC2"},
        {"bufferView": 3, "componentType": COMPONENT_TYPE_UNSIGNED_INT, "count": idx_count, "type": "SCALAR"},
    ]

    if gltf["meshes"] and gltf["meshes"][0].get("primitives"):
        prim = gltf["meshes"][0]["primitives"][0]
        prim["attributes"] = {
            "POSITION": 0,
            "NORMAL": 1,
            "TEXCOORD_0": 2,
        }
        prim["indices"] = 3
        prim["material"] = material_idx

    gltf.setdefault("extras", {})
    gltf["extras"]["dynacity_material_uv"] = {
        "tile_metres": tile_metres,
        "uv_strategy": "dominant_axis_planar_per_face",
        "source_building_uuid": source_uuid,
        "input_sha256": input_hash,
        "generated_by": "prepare_corridor_material_uv.py",
        "warnings": [
            "UV seams at panel edges (per-face split vertices)",
            "Source geometry license UNKNOWN; no source accuracy claim",
            "Vertical datum UNKNOWN; height not surveyed",
            "Native correspondence UNVERIFIED",
        ]
    }

    return gltf, new_bin


def write_glb(gltf: Dict, bin_data: bytes, out_path: Path):
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_pad = (-len(json_bytes)) % 4
    if json_pad:
        json_bytes += b" " * json_pad

    bin_pad = (-len(bin_data)) % 4
    if bin_pad:
        bin_data += b"\x00" * bin_pad

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    header = struct.pack("<III", GLB_MAGIC, GLB_VERSION, total_len)
    json_chunk = struct.pack("<II", len(json_bytes), CHUNK_JSON) + json_bytes
    bin_chunk = struct.pack("<II", len(bin_data), CHUNK_BIN) + bin_data

    out_path.write_bytes(header + json_chunk + bin_chunk)


def main():
    parser = argparse.ArgumentParser(description="Add NORMAL + TEXCOORD_0 to corridor GLB for material tiling")
    parser.add_argument("--input", type=Path, required=True, help="Input GLB path (POSITION only)")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for new GLB + report")
    parser.add_argument("--tile-metres", type=float, default=1.0, help="UV repeat distance in metres (default 1.0)")
    parser.add_argument("--source-building-uuid", type=str, required=True, help="Source building UUID (e.g., beirut001:...)")
    args = parser.parse_args()

    if not math.isfinite(args.tile_metres) or args.tile_metres <= 0:
        print("ERROR: --tile-metres must be positive", file=sys.stderr)
        return 1

    if not args.source_building_uuid:
        print("ERROR: --source-building-uuid required", file=sys.stderr)
        return 1

    in_path = args.input
    out_dir = args.output

    if not in_path.exists():
        print(f"ERROR: Input not found: {in_path}", file=sys.stderr)
        return 1

    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"ERROR: Output directory not empty: {out_dir}", file=sys.stderr)
        return 1

    input_bytes = in_path.read_bytes()
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    gltf, bin_data = parse_gltb(in_path)

    material_idx = validate_gltf(gltf, bin_data)

    prim = gltf["meshes"][0]["primitives"][0]
    pos_accessor_idx = prim["attributes"]["POSITION"]
    positions_flat = read_accessor(gltf, bin_data, pos_accessor_idx)
    positions = [(positions_flat[i], positions_flat[i+1], positions_flat[i+2])
                 for i in range(0, len(positions_flat), 3)]

    # Check for NaN/inf in positions
    for p in positions:
        for v in p:
            if not math.isfinite(v):
                raise GLBError("Non-finite vertex position")

    if "indices" not in prim:
        raise GLBError("Indexed geometry required")
    indices = read_indices(gltf, bin_data, prim["indices"])

    # Validate indices
    for idx in indices:
        if idx < 0 or idx >= len(positions):
            raise GLBError("Index out of bounds")

    tri_count = len(indices) // 3
    if len(indices) % 3 != 0:
        raise GLBError("Index count not multiple of 3")

    new_positions = []
    new_normals = []
    new_uvs = []
    new_indices = []
    dominant_axes = {"X": 0, "Y": 0, "Z": 0}

    for tri_idx in range(tri_count):
        i0, i1, i2 = indices[tri_idx*3], indices[tri_idx*3+1], indices[tri_idx*3+2]
        p0 = positions[i0]
        p1 = positions[i1]
        p2 = positions[i2]

        e1x, e1y, e1z = p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]
        e2x, e2y, e2z = p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2]
        nx, ny, nz = cross(e1x, e1y, e1z, e2x, e2y, e2z)

        # Reject degenerate faces
        nx2, ny2, nz2 = nx*nx, ny*ny, nz*nz
        if nx2 + ny2 + nz2 < 1e-12:
            raise GLBError("Degenerate face (zero area)")

        nx, ny, nz = normalize(nx, ny, nz)

        # Check for NaN/inf in normal
        if not (math.isfinite(nx) and math.isfinite(ny) and math.isfinite(nz)):
            raise GLBError("Non-finite normal")

        uvs, axis = dominant_axis_planar_uv(p0, p1, p2, (nx, ny, nz), args.tile_metres)

        # Check for NaN/inf in UV
        for uv in uvs:
            if not math.isfinite(uv):
                raise GLBError("Non-finite UV coordinate")

        dominant_axes[axis] += 1

        base_idx = len(new_positions) // 3
        new_positions.extend([p0[0], p0[1], p0[2], p1[0], p1[1], p1[2], p2[0], p2[1], p2[2]])
        new_normals.extend([nx, ny, nz] * 3)
        new_uvs.extend(uvs)
        new_indices.extend([base_idx, base_idx+1, base_idx+2])

    # Create output directory only after validation
    out_dir.mkdir(parents=True, exist_ok=True)

    out_gltf, out_bin = build_output_gltf(gltf, new_positions, new_normals, new_uvs, new_indices,
                                           material_idx, args.tile_metres, args.source_building_uuid, input_hash)

    out_glb_path = out_dir / f"{in_path.stem}_uv.glb"
    write_glb(out_gltf, out_bin, out_glb_path)

    out_bytes = out_glb_path.read_bytes()
    out_hash = hashlib.sha256(out_bytes).hexdigest()

    # Bounds in GLTF Y-up coordinate space (input coordinate system)
    all_x = new_positions[0::3]
    all_y = new_positions[1::3]
    all_z = new_positions[2::3]
    gltf_bounds = {
        "min": [min(all_x), min(all_y), min(all_z)],
        "max": [max(all_x), max(all_y), max(all_z)],
    }

    report = {
        "schema": "dynacity-corridor-material-uv-report/1",
        "input": {
            "path": str(in_path),
            "sha256": input_hash,
            "size_bytes": len(input_bytes),
            "triangle_count": tri_count,
            "vertex_count": len(positions),
        },
        "output": {
            "glb_path": str(out_glb_path),
            "sha256": out_hash,
            "size_bytes": len(out_bytes),
            "triangle_count": tri_count,
            "vertex_count": len(new_positions) // 3,
            "gltf_bounds_yup": gltf_bounds,
        },
        "uv": {
            "tile_metres": args.tile_metres,
            "strategy": "dominant_axis_planar_per_face",
            "dominant_axis_counts": dominant_axes,
            "seams": "Per-face vertex split; UV discontinuities at panel edges",
            "note": "No source stone claim; diagnostic only",
        },
        "materials": gltf.get("materials", []),
        "source_building_uuid": args.source_building_uuid,
        "source_geometry_license": "UNKNOWN - keep private; no external upload",
        "vertical_datum": "UNKNOWN model height",
        "native_correspondence": "UNVERIFIED",
        "warnings": [
            "UV seams at every triangle edge due to per-face vertex splitting",
            "Source geometry not surveyed; estimated from single photo",
            "Vertical datum UNKNOWN; Z is model height not MSL",
            "Native correspondence UNVERIFIED",
            "License UNKNOWN; keep private, no external upload",
        ],
    }

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')

    print(json.dumps({
        "output_dir": str(out_dir),
        "input_hash": input_hash,
        "output_hash": out_hash,
        "triangles": tri_count,
        "output_vertices": len(new_positions) // 3,
        "dominant_axes": dominant_axes,
        "report": str(report_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
