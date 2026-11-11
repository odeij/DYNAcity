#!/usr/bin/env python3
"""
Build standalone browser texture preview using deck.gl SimpleMeshLayer.
Loads corrected UV GLB, applies procedural checker/plain texture via deck.gl WebGL.
No Canvas triangle fallback. No CDN install; uses pinned deck.gl@9.4.0 CDN.
"""
import argparse
from html import escape
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import shutil

# GLB format constants
GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_TYPE_FLOAT = 5126
COMPONENT_TYPE_UNSIGNED_SHORT = 5123
COMPONENT_TYPE_UNSIGNED_INT = 5125

ACCESSOR_TYPE_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
COMPONENT_TYPE_SIZES = {5126: 4, 5123: 2, 5125: 4}


class GLBError(Exception):
    pass


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

    if ctype == COMPONENT_TYPE_FLOAT and byte_stride == 0:
        elem = num_comp * csize
        slc = bin_data[byte_offset:byte_offset + count * elem]
        fmt = f"<{count * num_comp}f"
        return list(struct.unpack(fmt, slc))

    res = []
    elem = num_comp * csize
    stride = byte_stride if byte_stride > 0 else elem
    for i in range(count):
        off = byte_offset + i * stride
        slc = bin_data[off:off + elem]
        fmt = f"<{num_comp}f"
        res.extend(struct.unpack(fmt, slc))
    return res


def read_indices(gltf: Dict, bin_data: bytes, accessor_idx: int) -> List[int]:
    accessor = gltf["accessors"][accessor_idx]
    bv_idx = accessor.get("bufferView", 0)
    bv = gltf["bufferViews"][bv_idx]
    byte_offset = bv.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    ctype = accessor["componentType"]
    count = accessor["count"]
    csize = COMPONENT_TYPE_SIZES[ctype]

    if ctype == COMPONENT_TYPE_UNSIGNED_INT:
        slc = bin_data[byte_offset:byte_offset + count * csize]
        return list(struct.unpack(f"<{count}I", slc))
    elif ctype == COMPONENT_TYPE_UNSIGNED_SHORT:
        slc = bin_data[byte_offset:byte_offset + count * csize]
        return list(struct.unpack(f"<{count}H", slc))
    else:
        raise GLBError(f"Unsupported index component type: {ctype}")


def validate_gltf(gltf: Dict) -> Tuple[int, int, int, int]:
    """Validate and return (pos_accessor_idx, normal_accessor_idx, texcoord_accessor_idx, indices_accessor_idx)."""
    if not gltf.get("meshes") or len(gltf["meshes"]) != 1:
        raise GLBError("Exactly one mesh required")
    mesh = gltf["meshes"][0]
    if not mesh.get("primitives") or len(mesh["primitives"]) != 1:
        raise GLBError("Exactly one primitive required")
    prim = mesh["primitives"][0]

    attrs = prim.get("attributes", {})
    required = ["POSITION", "NORMAL", "TEXCOORD_0"]
    for r in required:
        if r not in attrs:
            raise GLBError(f"Required attribute missing: {r}")

    pos_idx = attrs["POSITION"]
    norm_idx = attrs["NORMAL"]
    uv_idx = attrs["TEXCOORD_0"]

    if "indices" not in prim:
        raise GLBError("Indexed geometry required")
    idx_idx = prim["indices"]

    # Verify counts match
    pos_count = gltf["accessors"][pos_idx]["count"]
    norm_count = gltf["accessors"][norm_idx]["count"]
    uv_count = gltf["accessors"][uv_idx]["count"]
    if not (pos_count == norm_count == uv_count):
        raise GLBError("POSITION/NORMAL/TEXCOORD_0 count mismatch")

    # Verify component types
    if gltf["accessors"][pos_idx]["componentType"] != COMPONENT_TYPE_FLOAT:
        raise GLBError("POSITION must be float")
    if gltf["accessors"][norm_idx]["componentType"] != COMPONENT_TYPE_FLOAT:
        raise GLBError("NORMAL must be float")
    if gltf["accessors"][uv_idx]["componentType"] != COMPONENT_TYPE_FLOAT:
        raise GLBError("TEXCOORD_0 must be float")
    if gltf["accessors"][pos_idx]["type"] != "VEC3":
        raise GLBError("POSITION must be VEC3")
    if gltf["accessors"][norm_idx]["type"] != "VEC3":
        raise GLBError("NORMAL must be VEC3")
    if gltf["accessors"][uv_idx]["type"] != "VEC2":
        raise GLBError("TEXCOORD_0 must be VEC2")

    return pos_idx, norm_idx, uv_idx, idx_idx


def gltf_to_enu(positions: List[float]) -> List[float]:
    """Convert GLTF Y-up (X=East, Y=Up, Z=-North) to ENU (X=East, Y=North, Z=Up).
    ENU: [x, -z, y]"""
    out = []
    for i in range(0, len(positions), 3):
        x, y, z = positions[i], positions[i+1], positions[i+2]
        out.extend([x, -z, y])
    return out


def gltf_normal_to_enu(normals: List[float]) -> List[float]:
    """Convert normals same as positions."""
    out = []
    for i in range(0, len(normals), 3):
        x, y, z = normals[i], normals[i+1], normals[i+2]
        out.extend([x, -z, y])
    return out


def compute_bounds(positions: List[float]) -> Tuple[List[float], List[float]]:
    xs = positions[0::3]
    ys = positions[1::3]
    zs = positions[2::3]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def rebase_positions(positions: List[float], center: List[float]) -> List[float]:
    """Rebase positions around center for numeric stability in WebGL."""
    out = []
    for i in range(0, len(positions), 3):
        out.extend([positions[i] - center[0], positions[i+1] - center[1], positions[i+2] - center[2]])
    return out


def generate_checker_canvas_js(tile_metres: float) -> str:
    """Generate JS code for procedural checker texture."""
    return f"""
// Procedural checker texture generation
function createCheckerTexture(tileMetres) {{
    const size = 256;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    const checkSize = size / 2;
    const imgData = ctx.createImageData(size, size);
    const data = imgData.data;
    for (let y = 0; y < size; y++) {{
        for (let x = 0; x < size; x++) {{
            const cx = Math.floor(x / checkSize) % 2;
            const cy = Math.floor(y / checkSize) % 2;
            const idx = (y * size + x) * 4;
            if ((cx ^ cy) === 0) {{
                data[idx] = 42; data[idx+1] = 83; data[idx+2] = 112; data[idx+3] = 255;
            }} else {{
                data[idx] = 219; data[idx+1] = 206; data[idx+2] = 177; data[idx+3] = 255;
            }}
        }}
    }}
    ctx.putImageData(imgData, 0, 0);
    return canvas;
}}
"""


def build_html(
    glb_path: Path,
    gltf: Dict,
    positions: List[float],
    normals: List[float],
    uvs: List[float],
    indices: List[int],
    mode: str,
    tile_metres: float,
    material: Dict,
    input_hash: str,
    output_hash: str,
    gltf_bounds: Dict,
    center: List[float],
    span: List[float],
) -> str:
    """Generate standalone HTML with deck.gl SimpleMeshLayer."""

    # Convert to ENU for display
    positions_enu = gltf_to_enu(positions)
    normals_enu = gltf_normal_to_enu(normals)

    # Rebase around center for numeric stability (use ENU center for rebasing)
    positions_enu_rebased = rebase_positions(positions_enu, [center[0], center[1], center[2]])

    # Compute bounds in ENU
    max_span = max(span)

    # Prepare data arrays as JS literals
    pos_js = "[" + ",".join(f"{v:.6f}" for v in positions_enu_rebased) + "]"
    norm_js = "[" + ",".join(f"{v:.6f}" for v in normals_enu) + "]"
    uv_js = "[" + ",".join(f"{v:.6f}" for v in uvs) + "]"
    idx_js = "[" + ",".join(str(i) for i in indices) + "]"

    # Material color
    base_color = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [0.5, 0.5, 0.5, 1.0])
    metallic = material.get("pbrMetallicRoughness", {}).get("metallicFactor", 0.0)
    roughness = material.get("pbrMetallicRoughness", {}).get("roughnessFactor", 0.5)

    texture_js = generate_checker_canvas_js(tile_metres)
    plain_color = [round(255 * (12.92*c if c <= 0.0031308 else 1.055*c**(1/2.4)-0.055)) for c in base_color[:3]]

    # Deck.gl mesh data structure
    mesh_json = f"""{{
    attributes: {{
        positions: {{ value: new Float32Array({pos_js}), size: 3 }},
        normals: {{ value: new Float32Array({norm_js}), size: 3 }},
        texCoords: {{ value: new Float32Array({uv_js}), size: 2 }}
    }},
    indices: {{ value: new Uint32Array({idx_js}), size: 1 }}
}}"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DYNAcity — Corridor texture preview (deck.gl)</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: Arial, Helvetica, sans-serif; background: #1a1a2e; color: #eaeaea; line-height: 1.6; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; color: #fff; }}
        .subtitle {{ color: #aaa; margin-bottom: 1rem; font-size: 0.9rem; }}
        .controls {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; padding: 12px; background: #222; border-radius: 8px; }}
        .controls label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.9rem; }}
        .controls select, .controls button {{ padding: 6px 12px; background: #333; color: #eee; border: 1px solid #444; border-radius: 4px; cursor: pointer; }}
        .controls select:hover, .controls button:hover {{ background: #444; }}
        #viewport {{ height: 600px; position: relative; overflow: hidden; background: #0a0a12; border-radius: 8px; }}
        #deck-canvas {{ display: block; }}
        .info-panel {{ margin-top: 20px; padding: 16px; background: #222; border-radius: 8px; border: 1px solid #333; }}
        .info-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.85rem; margin-bottom: 8px; }}
        .info-row dt {{ color: #888; }}
        .info-row dd {{ color: #ddd; font-family: Arial, Helvetica, sans-serif; word-break: break-all; }}
        .disclaimer {{ font-size: 0.75rem; color: #777; margin-top: 16px; padding: 12px; background: #1a1a2e; border-radius: 4px; border-left: 3px solid #555; }}
        .render-status {{ padding: 8px; border-radius: 4px; font-size: 0.85rem; margin-bottom: 12px; }}
        .render-status.success {{ background: #183c2c; color: #c6f1d6; }}
        .render-status.error {{ background: #311; color: #f44; }}
        .render-status.pending {{ background: #331; color: #ff0; }}
        @media (max-width: 768px) {{
            .container {{ padding: 12px; }}
            h1 {{ font-size: 1.2rem; }}
            #viewport {{ height: 400px; }}
            .info-row {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>DYNAcity — Corridor texture preview</h1>
        <p class="subtitle">deck.gl SimpleMeshLayer WebGL — procedural {mode} texture — ENU coordinates (X=East, Y=North, Z=Up)</p>

        <div class="controls">
            <label>Mode: <select id="modeSelect">
                <option value="checker" {"selected" if mode == "checker" else ""}>Checker (1 UV repeat = {tile_metres:g} metre)</option>
                <option value="plain" {"selected" if mode == "plain" else ""}>Plain material color</option>
            </select></label>
            <label><input type="checkbox" id="showWireframe"> Wireframe</label>
            <button id="resetView">Reset View</button>
        </div>

        <div class="render-status pending" id="renderStatus">Initializing WebGL...</div>

        <div id="viewport"><canvas id="deck-canvas"></canvas></div>

        <div class="info-panel">
            <dl class="info-row">
                <dt>Input GLB</dt><dd>{escape(glb_path.name)}</dd>
                <dt>Input SHA256</dt><dd>{input_hash}</dd>
                <dt>Output SHA256</dt><dd>{output_hash}</dd>
                <dt>Triangles</dt><dd>{len(indices) // 3}</dd>
                <dt>Vertices</dt><dd>{len(positions) // 3}</dd>
                <dt>GLTF Y-Up bounds</dt><dd>[{gltf_bounds['min'][0]:.3f}, {gltf_bounds['min'][1]:.3f}, {gltf_bounds['min'][2]:.3f}] → [{gltf_bounds['max'][0]:.3f}, {gltf_bounds['max'][1]:.3f}, {gltf_bounds['max'][2]:.3f}]</dd>
                <dt>ENU Center</dt><dd>[{center[0]:.3f}, {center[1]:.3f}, {center[2]:.3f}]</dd>
                <dt>ENU Span</dt><dd>[{span[0]:.3f}, {span[1]:.3f}, {span[2]:.3f}]</dd>
                <dt>Material</dt><dd>{escape(material.get('name', 'unknown'))}</dd>
                <dt>Base Color</dt><dd>rgba({base_color[0]:.3f}, {base_color[1]:.3f}, {base_color[2]:.3f}, {base_color[3]:.3f})</dd>
                <dt>Metallic</dt><dd>{metallic}</dd>
                <dt>Roughness</dt><dd>{roughness}</dd>
                <dt>Texture Mode</dt><dd>{mode}</dd>
            </dl>
        </div>

        <div class="disclaimer">
            <strong>Disclaimer:</strong> This preview uses deck.gl SimpleMeshLayer with WebGL.
            Texture is PROCEDURAL DIAGNOSTIC only ({mode}); no source photo/material match claimed.
            UV coordinates from dominant-axis planar mapping (1 repeat per {tile_metres:g} metre), per-face vertex split.
            Geometry is ESTIMATED, not surveyed. Additive parapet overlay only — no contextual ground.
            Coordinate system: GLTF Y-Up (X=East, Y=Up, Z=-North); Display ENU (X=East, Y=North, Z=Up).
            Native correspondence and source accuracy UNVERIFIED. License UNKNOWN — keep private.
            Physical collision/floating NOT claimed. No map placement acceptance.
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/deck.gl@9.4.0/dist.min.js"></script>
    <script>
    (() => {{
        const status = document.getElementById('renderStatus');
        const fail = err => {{ status.className = 'render-status error'; status.textContent = 'WebGL error: ' + (err.message || err); }};
        try {{
        const DECK = window.deck;
        if (!DECK) throw new Error('deck.gl library did not load');
        const {{SimpleMeshLayer, OrbitView, COORDINATE_SYSTEM}} = DECK;
        const mesh = {mesh_json};
        {texture_js}
        const canvas = document.getElementById('deck-canvas');
        const select = document.getElementById('modeSelect');
        const viewport = document.getElementById('viewport');
        const checker = createCheckerTexture();
        const camera = () => ({{
            target: [0, 0, 0], rotationX: 20, rotationOrbit: 32,
            zoom: Math.log2(0.72 * Math.min(viewport.clientWidth, viewport.clientHeight) / {max_span!r})
        }});
        const layer = () => new SimpleMeshLayer({{
            id: 'corridor-mesh', data: [{{position: [0, 0, 0]}}], mesh,
            getPosition: d => d.position, getColor: {plain_color},
            texture: select.value === 'checker' ? checker : null,
            textureParameters: {{addressModeU: 'repeat', addressModeV: 'repeat', minFilter: 'nearest', magFilter: 'nearest'}},
            wireframe: document.getElementById('showWireframe').checked,
            material: false,
            coordinateSystem: COORDINATE_SYSTEM.CARTESIAN
        }});
        const viewer = new DECK.Deck({{
            canvas, width: viewport.clientWidth, height: viewport.clientHeight, views: [new OrbitView({{id: 'corridor', controller: true, orthographic: true}})],
            initialViewState: camera(), layers: [layer()],
            onAfterRender: () => {{
                if (status.classList.contains('error')) return;
                status.className = 'render-status success';
                status.textContent = 'WebGL frame drawn';
            }},
            onError: fail
        }});
        new ResizeObserver(() => viewer.setProps({{width: viewport.clientWidth, height: viewport.clientHeight}})).observe(viewport);
        select.addEventListener('change', () => viewer.setProps({{layers: [layer()]}}));
        document.getElementById('showWireframe').addEventListener('change', () => viewer.setProps({{layers: [layer()]}}));
        document.getElementById('resetView').addEventListener('click', () => viewer.setProps({{initialViewState: camera()}}));
        }} catch (err) {{ fail(err); }}
    }})();
    </script>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description="Build standalone texture preview with deck.gl SimpleMeshLayer")
    parser.add_argument("--input", type=Path, required=True, help="Input corrected UV GLB path")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for preview")
    parser.add_argument("--mode", choices=["plain", "checker"], default="checker", help="Texture mode (default: checker)")
    args = parser.parse_args()

    in_path = args.input
    out_dir = args.output

    if not in_path.exists():
        print(f"ERROR: Input not found: {in_path}", file=sys.stderr)
        return 1

    if out_dir.exists():
        print(f"ERROR: Output directory not empty: {out_dir}", file=sys.stderr)
        return 1

    input_bytes = in_path.read_bytes()
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    gltf, bin_data = parse_gltb(in_path)

    pos_idx, norm_idx, uv_idx, idx_idx = validate_gltf(gltf)

    positions = read_accessor(gltf, bin_data, pos_idx)
    normals = read_accessor(gltf, bin_data, norm_idx)
    uvs = read_accessor(gltf, bin_data, uv_idx)
    indices = read_indices(gltf, bin_data, idx_idx)

    # Verify attribute counts
    vert_count = len(positions) // 3
    if len(normals) != len(positions) or len(uvs) != vert_count * 2:
        raise GLBError("Attribute count mismatch")

    if not indices or len(indices) % 3 or min(indices) < 0 or max(indices) >= vert_count:
        raise GLBError("Invalid triangle indices")
    if not all(math.isfinite(value) for values in (positions, normals, uvs) for value in values):
        raise GLBError("Non-finite mesh attributes")

    primitive = gltf["meshes"][0]["primitives"][0]
    materials = gltf.get("materials", [])
    material = materials[primitive["material"]] if "material" in primitive else {}

    # Bounds in GLTF Y-up
    min_b, max_b = compute_bounds(positions)
    gltf_bounds = {"min": min_b, "max": max_b}

    # Center and span in ENU
    positions_enu = gltf_to_enu(positions)
    min_enu, max_enu = compute_bounds(positions_enu)
    center_enu = [(max_enu[i] + min_enu[i]) / 2 for i in range(3)]
    span = [max_enu[i] - min_enu[i] for i in range(3)]

    # Tile metres from report if available, else default 1.0
    tile_metres = 1.0
    if "extras" in gltf and "dynacity_material_uv" in gltf["extras"]:
        tile_metres = gltf["extras"]["dynacity_material_uv"].get("tile_metres", 1.0)

    # Generate HTML
    html = build_html(
        in_path, gltf, positions, normals, uvs, indices,
        args.mode, tile_metres, material,
        input_hash, input_hash,  # output hash = input hash (we don't modify GLB)
        gltf_bounds, center_enu, span
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_html = out_dir / "index.html"
    out_html.write_text(html, encoding="utf-8")

    # Copy GLB to output for reference
    out_glb = out_dir / in_path.name
    shutil.copy2(in_path, out_glb)

    # Manifest
    manifest = {
        "schema": "dynacity-corridor-texture-preview/1",
        "source_glb": {
            "path": str(in_path),
            "sha256": input_hash,
            "size_bytes": len(input_bytes)
        },
        "output": {
            "preview_dir": str(out_dir),
            "index_html": str(out_html),
            "glb_copy": str(out_glb)
        },
        "mesh": {
            "triangles": len(indices) // 3,
            "vertices": vert_count,
            "attributes": {
                "POSITION": vert_count,
                "NORMAL": vert_count,
                "TEXCOORD_0": vert_count
            },
            "gltf_bounds_yup": gltf_bounds
        },
        "texture": {
            "mode": args.mode,
            "tile_metres": tile_metres,
            "note": "Procedural diagnostic only; no source photo/material match"
        },
        "material": material,
        "render": {
            "library": "deck.gl@9.4.0",
            "layer": "SimpleMeshLayer",
            "view": "OrbitView",
            "coordinate_system": "CARTESIAN (ENU display)"
        },
        "warnings": [
            "Texture is PROCEDURAL DIAGNOSTIC only; no source accuracy claim",
            "UV from dominant-axis planar mapping; per-face vertex split",
            "Geometry ESTIMATED; not surveyed",
            "Native correspondence UNVERIFIED",
            "License UNKNOWN; keep private",
            "No map placement / collision / physical floating acceptance"
        ]
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({
        "output_dir": str(out_dir),
        "input_hash": input_hash,
        "triangles": len(indices) // 3,
        "vertices": vert_count,
        "mode": args.mode,
        "manifest": str(manifest_path),
        "html": str(out_html)
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
