#!/usr/bin/env python3
"""
Standalone browser asset preview for portable 2-GLB package (Iteration31 corrected).
Reads dynacity-corridor-appearance-bundle/1 schema manifest.json from private path,
validates GLB files, generates self-contained HTML with software triangle renderer.
No CDN/download/install needed. Plain HTML Canvas 2D context projection.
"""

import argparse
import hashlib
import json
import os
import math
import re
import shutil
import struct
import sys
import html as html_lib
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

# GLB format constants
GLB_MAGIC = 0x46546C67  # 'gltf'
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A  # 'JSON'
CHUNK_BIN = 0x004E4942   # 'BIN\0'

# GLTF accessor component types
COMPONENT_TYPE_BYTE = 5120
COMPONENT_TYPE_UNSIGNED_BYTE = 5121
COMPONENT_TYPE_SHORT = 5122
COMPONENT_TYPE_UNSIGNED_SHORT = 5123
COMPONENT_TYPE_UNSIGNED_INT = 5125
COMPONENT_TYPE_FLOAT = 5126

# GLTF accessor types
ACCESSOR_TYPE_SCALAR = "SCALAR"
ACCESSOR_TYPE_VEC2 = "VEC2"
ACCESSOR_TYPE_VEC3 = "VEC3"
ACCESSOR_TYPE_VEC4 = "VEC4"
ACCESSOR_TYPE_MAT2 = "MAT2"
ACCESSOR_TYPE_MAT3 = "MAT3"
ACCESSOR_TYPE_MAT4 = "MAT4"

ACCESSOR_TYPE_SIZES = {
    ACCESSOR_TYPE_SCALAR: 1,
    ACCESSOR_TYPE_VEC2: 2,
    ACCESSOR_TYPE_VEC3: 3,
    ACCESSOR_TYPE_VEC4: 4,
    ACCESSOR_TYPE_MAT2: 4,
    ACCESSOR_TYPE_MAT3: 9,
    ACCESSOR_TYPE_MAT4: 16,
}

COMPONENT_TYPE_SIZES = {
    COMPONENT_TYPE_BYTE: 1,
    COMPONENT_TYPE_UNSIGNED_BYTE: 1,
    COMPONENT_TYPE_SHORT: 2,
    COMPONENT_TYPE_UNSIGNED_SHORT: 2,
    COMPONENT_TYPE_UNSIGNED_INT: 4,
    COMPONENT_TYPE_FLOAT: 4,
}

COMPONENT_TYPE_STRUCT = {
    COMPONENT_TYPE_BYTE: 'b',
    COMPONENT_TYPE_UNSIGNED_BYTE: 'B',
    COMPONENT_TYPE_SHORT: 'h',
    COMPONENT_TYPE_UNSIGNED_SHORT: 'H',
    COMPONENT_TYPE_UNSIGNED_INT: 'I',
    COMPONENT_TYPE_FLOAT: 'f',
}


class GLBParseError(Exception):
    """Error parsing GLB file."""
    pass


class GLBAsset:
    """Parsed GLB asset with decoded geometry and per-triangle material indices."""

    def __init__(self, key: str, glb_path: Path):
        self.key = key
        self.glb_path = glb_path
        self.gltf: Dict[str, Any] = {}
        self.bin_data: bytes = b''
        # Triangles with (vertex0, vertex1, vertex2, material_index)
        self.triangles: List[Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float], int]] = []
        self.materials: List[Dict[str, Any]] = []
        self.node_transforms: Dict[int, List[float]] = {}
        self.bounds_local: Tuple[Tuple[float, float, float], Tuple[float, float, float]] = ((0,0,0), (0,0,0))
        self.triangle_count = 0
        self.material_indices: List[int] = []  # per-triangle material index
        self._parse()

    def _parse(self):
        data = self.glb_path.read_bytes()

        if len(data) < 12:
            raise GLBParseError(f"File too small for GLB header: {len(data)} bytes")

        magic, version, total_length = struct.unpack('<III', data[0:12])
        if magic != GLB_MAGIC:
            raise GLBParseError(f"Invalid GLB magic: 0x{magic:08x} (expected 0x{GLB_MAGIC:08x})")
        if version != GLB_VERSION:
            raise GLBParseError(f"Unsupported GLB version: {version} (expected {GLB_VERSION})")
        if total_length != len(data):
            raise GLBParseError(f"GLB length mismatch: header says {total_length}, file is {len(data)}")

        offset = 12
        json_chunk_found = False
        bin_chunk_found = False

        while offset < len(data):
            if offset + 8 > len(data):
                raise GLBParseError(f"Truncated chunk header at offset {offset}")

            chunk_length, chunk_type = struct.unpack('<II', data[offset:offset+8])
            offset += 8

            if offset + chunk_length > len(data):
                raise GLBParseError(f"Chunk extends past file end: offset={offset}, length={chunk_length}, file={len(data)}")

            chunk_data = data[offset:offset+chunk_length]
            offset += chunk_length

            if chunk_length % 4 != 0:
                offset += 4 - (chunk_length % 4)

            if chunk_type == CHUNK_JSON:
                if json_chunk_found:
                    raise GLBParseError("Multiple JSON chunks found")
                json_chunk_found = True
                self.gltf = json.loads(chunk_data.decode('utf-8'))
            elif chunk_type == CHUNK_BIN:
                if bin_chunk_found:
                    raise GLBParseError("Multiple BIN chunks found")
                bin_chunk_found = True
                self.bin_data = chunk_data

        if not json_chunk_found:
            raise GLBParseError("No JSON chunk found in GLB")

        self._parse_node_transforms()
        self._decode_geometry()
        if not self.triangles or not all(math.isfinite(v) for tri in self.triangles for point in tri[:3] for v in point):
            raise GLBParseError('Empty or nonfinite geometry')
        self._compute_bounds()

    def _parse_node_transforms(self):
        if any(n.get('children') for n in self.gltf.get('nodes', [])):
            raise GLBParseError('Node hierarchy not supported by this bounded preview')
        nodes = self.gltf.get('nodes', [])
        for i, node in enumerate(nodes):
            matrix = self._get_node_matrix(node)
            self.node_transforms[i] = matrix

    def _get_node_matrix(self, node: Dict[str, Any]) -> List[float]:
        matrix = [1.0, 0.0, 0.0, 0.0,
                  0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0,
                  0.0, 0.0, 0.0, 1.0]

        if 'matrix' in node:
            matrix = node['matrix']
        else:
            translation = node.get('translation', [0.0, 0.0, 0.0])
            rotation = node.get('rotation', [0.0, 0.0, 0.0, 1.0])
            scale = node.get('scale', [1.0, 1.0, 1.0])

            x, y, z, w = rotation
            norm = (x*x + y*y + z*z + w*w) ** 0.5
            if norm > 0:
                x, y, z, w = x/norm, y/norm, z/norm, w/norm

            xx, yy, zz = x*x, y*y, z*z
            xy, xz, yz = x*y, x*z, y*z
            wx, wy, wz = w*x, w*y, w*z

            rot = [
                1 - 2*(yy + zz), 2*(xy - wz), 2*(xz + wy), 0,
                2*(xy + wz), 1 - 2*(xx + zz), 2*(yz - wx), 0,
                2*(xz - wy), 2*(yz + wx), 1 - 2*(xx + yy), 0,
                0, 0, 0, 1
            ]

            for i in range(3):
                for j in range(3):
                    rot[i*4 + j] *= scale[i]

            rot[12] = translation[0]
            rot[13] = translation[1]
            rot[14] = translation[2]

            matrix = rot

        return matrix

    def _read_accessor(self, accessor_idx: int) -> List[float]:
        accessor = self.gltf['accessors'][accessor_idx]
        buffer_view_idx = accessor.get('bufferView', 0)
        buffer_view = self.gltf['bufferViews'][buffer_view_idx]
        byte_offset = buffer_view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
        byte_stride = buffer_view.get('byteStride', 0)

        component_type = accessor['componentType']
        accessor_type = accessor['type']
        count = accessor['count']
        num_components = ACCESSOR_TYPE_SIZES[accessor_type]
        component_size = COMPONENT_TYPE_SIZES[component_type]
        struct_fmt = COMPONENT_TYPE_STRUCT[component_type]

        if component_type == COMPONENT_TYPE_FLOAT and byte_stride == 0:
            element_size = num_components * component_size
            data_slice = self.bin_data[byte_offset:byte_offset + count * element_size]
            fmt = f'<{count * num_components}{struct_fmt}'
            return list(struct.unpack(fmt, data_slice))

        result = []
        element_size = num_components * component_size
        for i in range(count):
            elem_offset = byte_offset + i * (byte_stride if byte_stride > 0 else element_size)
            data_slice = self.bin_data[elem_offset:elem_offset + element_size]
            fmt = f'<{num_components}{struct_fmt}'
            result.extend(struct.unpack(fmt, data_slice))

        return result

    def _read_indices(self, accessor_idx: int) -> List[int]:
        accessor = self.gltf['accessors'][accessor_idx]
        buffer_view_idx = accessor.get('bufferView', 0)
        buffer_view = self.gltf['bufferViews'][buffer_view_idx]
        byte_offset = buffer_view.get('byteOffset', 0) + accessor.get('byteOffset', 0)

        component_type = accessor['componentType']
        count = accessor['count']
        component_size = COMPONENT_TYPE_SIZES[component_type]
        struct_fmt = COMPONENT_TYPE_STRUCT[component_type]

        data_slice = self.bin_data[byte_offset:byte_offset + count * component_size]
        fmt = f'<{count}{struct_fmt}'
        return list(struct.unpack(fmt, data_slice))

    def _apply_transform(self, matrix: List[float], point: Tuple[float, float, float]) -> Tuple[float, float, float]:
        x, y, z = point
        tx = matrix[0]*x + matrix[4]*y + matrix[8]*z + matrix[12]
        ty = matrix[1]*x + matrix[5]*y + matrix[9]*z + matrix[13]
        tz = matrix[2]*x + matrix[6]*y + matrix[10]*z + matrix[14]
        return (tx, ty, tz)

    def _decode_geometry(self):
        meshes = self.gltf.get('meshes', [])
        materials = self.gltf.get('materials', [])
        self.materials = materials

        nodes = self.gltf.get('nodes', [])

        for node_idx, node in enumerate(nodes):
            if 'mesh' not in node:
                continue
            mesh_idx = node['mesh']
            if mesh_idx >= len(meshes):
                continue
            mesh = meshes[mesh_idx]
            node_matrix = self.node_transforms[node_idx]

            for primitive in mesh.get('primitives', []):
                attrs = primitive.get('attributes', {})
                if 'POSITION' not in attrs:
                    continue
                pos_accessor_idx = attrs['POSITION']
                positions_flat = self._read_accessor(pos_accessor_idx)
                positions = [(positions_flat[i], positions_flat[i+1], positions_flat[i+2])
                            for i in range(0, len(positions_flat), 3)]

                world_positions = [self._apply_transform(node_matrix, p) for p in positions]

                if 'indices' not in primitive:
                    # Non-indexed: assume triangle list
                    indices = list(range(len(world_positions)))
                else:
                    indices = self._read_indices(primitive['indices'])

                material_idx = primitive.get('material', 0)
                if material_idx >= len(materials):
                    raise GLBParseError(f"Primitive material index {material_idx} out of range (materials={len(materials)})")

                for i in range(0, len(indices), 3):
                    if i + 2 >= len(indices):
                        raise GLBParseError('Incomplete triangle indices')
                    i0, i1, i2 = indices[i], indices[i+1], indices[i+2]
                    if not (0 <= i0 < len(world_positions) and 0 <= i1 < len(world_positions) and 0 <= i2 < len(world_positions)):
                        raise GLBParseError(f"Index out of bounds: indices=({i0},{i1},{i2}), vertices={len(world_positions)}")
                    tri = (world_positions[i0], world_positions[i1], world_positions[i2], material_idx)
                    self.triangles.append(tri)
                    self.material_indices.append(material_idx)
                    self.triangle_count += 1

    def _compute_bounds(self):
        if not self.triangles:
            self.bounds_local = ((0,0,0), (0,0,0))
            return

        min_x = min_y = min_z = float('inf')
        max_x = max_y = max_z = float('-inf')

        for tri in self.triangles:
            for x, y, z in tri[:3]:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                min_z = min(min_z, z)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                max_z = max(max_z, z)

        self.bounds_local = ((min_x, min_y, min_z), (max_x, max_y, max_z))


def validate_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Validate dynacity-corridor-appearance-bundle/1 schema manifest.json."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    # Verify schema
    schema = manifest.get('schema', '')
    if schema != 'dynacity-corridor-appearance-bundle/1':
        raise ValueError(f"Unexpected schema: {schema} (expected dynacity-corridor-appearance-bundle/1)")

    # Verify required fields
    required = ['schema', 'timestamp', 'origin_utm_m', 'coordinate_system', 'candidates']
    for field in required:
        if field not in manifest:
            raise ValueError(f"Manifest missing required field: {field}")

    candidates = manifest['candidates']
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ValueError(f"Expected exactly 2 candidates, got {len(candidates)}")

    for cand in candidates:
        if not re.fullmatch(r'beirut001:[0-9a-f-]{36}', cand.get('source_building_uuid','')):
            raise ValueError('Invalid source building UUID')
        required_cand = ['key', 'geometry_kind', 'source_building_uuid', 'gltf_bounds_yup',
                        'local_enu_bounds', 'glb_asset', 'material_parameters', 'status']
        for field in required_cand:
            if field not in cand:
                raise ValueError(f"Candidate {cand.get('key', '?')} missing field: {field}")

        glb_asset = cand['glb_asset']
        for field in ['path', 'sha256', 'size_bytes']:
            if field not in glb_asset:
                raise ValueError(f"Candidate {cand['key']} glb_asset missing field: {field}")

    return manifest


def validate_context_manifest(context_path: Path) -> Dict[str, Any]:
    """Validate dynacity-offline-corridor-context/1 schema context.json."""
    if not context_path.exists():
        raise FileNotFoundError(f"Context manifest not found: {context_path}")

    with open(context_path, 'r', encoding='utf-8') as f:
        context = json.load(f)

    # Verify schema
    schema = context.get('schema', '')
    if schema != 'dynacity-offline-corridor-context/1':
        raise ValueError(f"Unexpected context schema: {schema} (expected dynacity-offline-corridor-context/1)")

    # Verify required fields
    required = ['schema', 'status', 'context_asset', 'source_building_ids',
                'gltf_bounds_yup', 'local_enu_bounds', 'origin_utm_m',
                'horizontal_crs', 'vertical_datum', 'units', 'material_parameters',
                'native_snapshot_geometry_hash_prefix', 'matches_native_snapshot',
                'source_geometry_license']
    for field in required:
        if field not in context:
            raise ValueError(f"Context manifest missing required field: {field}")

    # Verify context_asset
    ctx_asset = context['context_asset']
    for field in ['path', 'sha256', 'size_bytes']:
        if field not in ctx_asset:
            raise ValueError(f"Context asset missing field: {field}")

    # Verify matches_native_snapshot is False (native correspondence UNVERIFIED)
    if context['matches_native_snapshot'] is not False:
        raise ValueError("Context must have matches_native_snapshot: false (native correspondence UNVERIFIED)")

    # Verify source_geometry_license is UNKNOWN
    if context['source_geometry_license'] != "UNKNOWN - keep private; no external upload":
        raise ValueError("Context must have source_geometry_license: 'UNKNOWN - keep private; no external upload'")

    # Verify source_building_ids is a list with multiple UUIDs
    source_ids = context['source_building_ids']
    if not isinstance(source_ids, list) or len(source_ids) < 2:
        raise ValueError("Context must have multiple source_building_ids (multi-source)")

    if len(set(source_ids)) != len(source_ids):
        raise ValueError('Duplicate context source identities')
    if context.get('gltf_axes') != 'East,Up,South' or context['units'] != 'metres':
        raise ValueError('Context axis/units mismatch')
    for sid in source_ids:
        if not re.fullmatch(r'beirut001:[0-9a-f-]{36}', sid):
            raise ValueError(f"Invalid source building UUID in context: {sid}")

    return context


def verify_context_asset(context: Dict[str, Any], context_dir: Path) -> GLBAsset:
    """Verify context GLB file matches manifest and parse it."""
    glb_rel_path = context['context_asset']['path']
    if Path(glb_rel_path).is_absolute() or '\\' in glb_rel_path:
        raise ValueError('Context asset path must be portable and relative')
    glb_path = (context_dir / glb_rel_path).resolve()
    if not glb_path.is_relative_to(context_dir.resolve()):
        raise ValueError('Context asset path escapes context directory')
    if not glb_path.exists():
        raise FileNotFoundError(f"Context GLB file not found: {glb_path}")

    # Verify size
    actual_size = glb_path.stat().st_size
    expected_size = context['context_asset']['size_bytes']
    if actual_size != expected_size:
        raise ValueError(f"Context size mismatch: expected {expected_size}, got {actual_size}")

    # Verify hash
    data = glb_path.read_bytes()
    actual_hash = hashlib.sha256(data).hexdigest()
    expected_hash = context['context_asset']['sha256']
    if actual_hash != expected_hash:
        raise ValueError(f"Context hash mismatch: expected {expected_hash}, got {actual_hash}")

    # Parse and validate geometry
    glb_asset = GLBAsset('offline_context', glb_path)

    return glb_asset


def verify_glb_asset(cand: Dict[str, Any], bundle_dir: Path) -> GLBAsset:
    """Verify GLB file matches manifest and parse it."""
    glb_rel_path = cand['glb_asset']['path']
    if Path(glb_rel_path).is_absolute() or '\\' in glb_rel_path:
        raise ValueError('Asset path must be portable and relative')
    glb_path = (bundle_dir / glb_rel_path).resolve()
    if not glb_path.is_relative_to(bundle_dir.resolve()):
        raise ValueError('Asset path escapes bundle')
    if not glb_path.exists():
        raise FileNotFoundError(f"GLB file not found: {glb_path}")

    # Verify size
    actual_size = glb_path.stat().st_size
    expected_size = cand['glb_asset']['size_bytes']
    if actual_size != expected_size:
        raise ValueError(f"Size mismatch for {cand['key']}: expected {expected_size}, got {actual_size}")

    # Verify hash
    data = glb_path.read_bytes()
    actual_hash = hashlib.sha256(data).hexdigest()
    expected_hash = cand['glb_asset']['sha256']
    if actual_hash != expected_hash:
        raise ValueError(f"Hash mismatch for {cand['key']}: expected {expected_hash}, got {actual_hash}")

    # Parse and validate geometry
    glb_asset = GLBAsset(cand['key'], glb_path)

    return glb_asset


def generate_html(assets: List[GLBAsset], manifest: Dict[str, Any], output_dir: Path, context_asset: Optional[GLBAsset] = None, context_manifest: Optional[Dict[str, Any]] = None) -> str:
    """Generate self-contained HTML with embedded triangle data and per-triangle material indices."""

    # Prepare triangle data for embedding
    asset_data = []
    candidates = manifest['candidates']

    # First add the two main candidates
    for asset in assets:
        # Find matching candidate
        cand = next((c for c in candidates if c['key'] == asset.key), None)
        if not cand:
            raise ValueError(f"No candidate found for asset key: {asset.key}")

        triangles_json = []
        for tri in asset.triangles:
            triangles_json.append({
                'v0': [tri[0][0], -tri[0][2], tri[0][1]],
                'v1': [tri[1][0], -tri[1][2], tri[1][1]],
                'v2': [tri[2][0], -tri[2][2], tri[2][1]],
                'material': tri[3]
            })

        materials_json = []
        for mat in asset.materials:
            pbr = mat.get('pbrMetallicRoughness', {})
            base_color = pbr.get('baseColorFactor', [0.5, 0.5, 0.5, 1.0])
            metallic = pbr.get('metallicFactor', 0.0)
            roughness = pbr.get('roughnessFactor', 0.5)
            double_sided = mat.get('doubleSided', True)
            materials_json.append({
                'name': mat.get('name', 'default'),
                'baseColor': base_color,
                'metallic': metallic,
                'roughness': roughness,
                'doubleSided': double_sided
            })

        # Add default fallback material if empty
        if not materials_json:
            materials_json.append({
                'name': 'default_fallback',
                'baseColor': [0.5, 0.5, 0.5, 1.0],
                'metallic': 0.0,
                'roughness': 0.5,
                'doubleSided': True
            })

        asset_data.append({
            'key': asset.key,
            'role': 'candidate',
            'source_building_uuid': cand['source_building_uuid'],
            'geometry_kind': cand['geometry_kind'],
            'status': cand['status'],
            'triangle_count': asset.triangle_count,
            'material_count': len(asset.materials),
            'bounds_local': ((asset.bounds_local[0][0], -asset.bounds_local[1][2], asset.bounds_local[0][1]), (asset.bounds_local[1][0], -asset.bounds_local[0][2], asset.bounds_local[1][1])),
            'gltf_bounds_yup': cand['gltf_bounds_yup'],
            'local_enu_bounds': cand['local_enu_bounds'],
            'triangles': triangles_json,
            'materials': materials_json,
            'sha256': cand['glb_asset']['sha256'],
            'glb_file': 'assets/' + Path(cand['glb_asset']['path']).name
        })

    # Add context asset if provided
    if context_asset and context_manifest:
        triangles_json = []
        for tri in context_asset.triangles:
            triangles_json.append({
                'v0': [tri[0][0], -tri[0][2], tri[0][1]],
                'v1': [tri[1][0], -tri[1][2], tri[1][1]],
                'v2': [tri[2][0], -tri[2][2], tri[2][1]],
                'material': tri[3]
            })

        materials_json = []
        for mat in context_asset.materials:
            pbr = mat.get('pbrMetallicRoughness', {})
            base_color = pbr.get('baseColorFactor', [0.5, 0.5, 0.5, 1.0])
            metallic = pbr.get('metallicFactor', 0.0)
            roughness = pbr.get('roughnessFactor', 0.5)
            double_sided = mat.get('doubleSided', True)
            materials_json.append({
                'name': mat.get('name', 'default'),
                'baseColor': base_color,
                'metallic': metallic,
                'roughness': roughness,
                'doubleSided': double_sided
            })

        if not materials_json:
            materials_json.append({
                'name': 'default_fallback',
                'baseColor': [0.5, 0.5, 0.5, 1.0],
                'metallic': 0.0,
                'roughness': 0.5,
                'doubleSided': True
            })

        # Escape source building IDs for safe HTML
        source_ids_html = ', '.join(html_lib.escape(sid) for sid in context_manifest['source_building_ids'])

        asset_data.append({
            'key': 'offline_context',
            'role': 'context',
            'source_building_ids': context_manifest['source_building_ids'],
            'source_building_uuid': source_ids_html,
            'geometry_kind': context_manifest.get('geometry_kind', 'Retained multi-source frontage context; DIFFERENT native version'),
            'status': context_manifest['status'],
            'triangle_count': context_asset.triangle_count,
            'material_count': len(context_asset.materials),
            'bounds_local': ((context_asset.bounds_local[0][0], -context_asset.bounds_local[1][2], context_asset.bounds_local[0][1]), (context_asset.bounds_local[1][0], -context_asset.bounds_local[0][2], context_asset.bounds_local[1][1])),
            'gltf_bounds_yup': context_manifest['gltf_bounds_yup'],
            'local_enu_bounds': context_manifest['local_enu_bounds'],
            'triangles': triangles_json,
            'materials': materials_json,
            'sha256': context_manifest['context_asset']['sha256'],
            'glb_file': 'assets/' + Path(context_manifest['context_asset']['path']).name
        })

    # Compute shared center for preview (fit all assets in local ENU space)
    all_min = [float('inf'), float('inf'), float('inf')]
    all_max = [float('-inf'), float('-inf'), float('-inf')]
    for asset in assets:
        (min_x, min_y, min_z), (max_x, max_y, max_z) = ((asset.bounds_local[0][0], -asset.bounds_local[1][2], asset.bounds_local[0][1]), (asset.bounds_local[1][0], -asset.bounds_local[0][2], asset.bounds_local[1][1]))
        all_min[0] = min(all_min[0], min_x)
        all_min[1] = min(all_min[1], min_y)
        all_min[2] = min(all_min[2], min_z)
        all_max[0] = max(all_max[0], max_x)
        all_max[1] = max(all_max[1], max_y)
        all_max[2] = max(all_max[2], max_z)

    if context_asset:
        (min_x, min_y, min_z), (max_x, max_y, max_z) = ((context_asset.bounds_local[0][0], -context_asset.bounds_local[1][2], context_asset.bounds_local[0][1]), (context_asset.bounds_local[1][0], -context_asset.bounds_local[0][2], context_asset.bounds_local[1][1]))
        all_min[0] = min(all_min[0], min_x)
        all_min[1] = min(all_min[1], min_y)
        all_min[2] = min(all_min[2], min_z)
        all_max[0] = max(all_max[0], max_x)
        all_max[1] = max(all_max[1], max_y)
        all_max[2] = max(all_max[2], max_z)

    center = [(all_min[i] + all_max[i]) * 0.5 for i in range(3)]
    size = [all_max[i] - all_min[i] for i in range(3)]
    max_dim = max(size)

    # Escape for safe embedding in HTML
    asset_json = json.dumps(asset_data, separators=(',', ':')).replace('</script>', '<\\/script>')

    # Extract Python values for template substitution
    center_x, center_y, center_z = center
    size_x, size_y, size_z = size
    max_dim_val = max_dim
    origin_utm = manifest['origin_utm_m']

    # Determine if context is present
    has_context = context_asset is not None and context_manifest is not None

    context_warning = '<div class="context-warning">Offline context is a retained DIFFERENT native geometry version; native correspondence and source accuracy remain unverified. <a href="context.json">Context provenance</a></div>' if has_context else ''
    context_checkbox = '<label><input type="checkbox" id="showContext" checked onchange="redraw()"> Show offline context</label>' if has_context else ''
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DYNAcity — Corridor asset preview</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: Arial, Helvetica, sans-serif; background: #1a1a2e; color: #eaeaea; line-height: 1.6; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; color: #fff; word-break: break-word; }}
        .subtitle {{ color: #aaa; margin-bottom: 1rem; font-size: 0.85rem; word-break: break-word; }}
        .controls {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; padding: 10px; background: #222; border-radius: 8px; }}
        .controls label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; white-space: nowrap; }}
        .controls select, .controls button {{ padding: 6px 10px; background: #333; color: #eee; border: 1px solid #444; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }}
        .controls select:hover, .controls button:hover {{ background: #444; }}
        .main {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 360px); gap: 16px; }}
        .viewer-panel {{ min-width: 0; background: #111; border-radius: 8px; overflow: hidden; position: relative; }}
        canvas {{ display: block; width: 100%; height: 600px; background: #0a0a12; }}
        .info-panel {{ min-width: 0; display: flex; flex-direction: column; gap: 12px; }}
        .asset-card {{ min-width: 0; overflow-wrap: anywhere; background: #222; border-radius: 8px; padding: 16px; border: 1px solid #333; }}
        .asset-card h3 {{ flex-wrap: wrap; margin-bottom: 10px; font-size: 1.1rem; color: #fff; display: flex; align-items: center; gap: 8px; word-break: break-word; }}
        .badge {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 12px; background: #333; color: #ccc; white-space: nowrap; }}
        .badge.estimated {{ background: #4a3; color: #fff; }}
        .badge.verified {{ background: #3a7; color: #fff; }}
        .badge.context {{ background: #b93; color: #fff; }}
        .asset-info {{ min-width: 0; overflow-wrap: anywhere; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.85rem; }}
        .asset-info dt {{ color: #888; word-break: break-word; }}
        .asset-info dd {{ color: #ddd; font-family: Arial, Helvetica, sans-serif; font-size: 0.8rem; word-break: break-all; overflow-wrap: anywhere; }}
        .axis-legend {{ display: flex; flex-wrap: wrap; gap: 12px; padding: 8px; background: #1a1a2e; border-radius: 4px; font-size: 0.8rem; }}
        .axis-item {{ display: flex; align-items: center; gap: 6px; }}
        .axis-color {{ width: 16px; height: 16px; border-radius: 3px; flex-shrink: 0; }}
        .download-link {{ display: inline-block; max-width: 100%; overflow-wrap: anywhere; margin-top: 12px; padding: 8px 16px; background: #2a5; color: #fff; text-decoration: none; border-radius: 4px; font-size: 0.85rem; word-break: break-all; }}
        .download-link:hover {{ background: #3b7; }}
        .disclaimer {{ font-size: 0.7rem; color: #777; margin-top: 16px; padding: 12px; background: #1a1a2e; border-radius: 4px; border-left: 3px solid #555; word-break: break-word; }}
        .view-toggle {{ display: flex; gap: 8px; }}
        .view-btn {{ flex: 1; min-width: 0; padding: 8px; background: #333; color: #eee; border: 1px solid #444; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }}
        .view-btn.active {{ background: #2a5; border-color: #3a7; }}
        .coordinate-display {{ font-family: Arial, Helvetica, sans-serif; font-size: 0.7rem; color: #888; padding: 8px; background: #111; border-radius: 4px; word-break: break-all; }}
        .asset-selector {{ margin-bottom: 12px; padding: 8px; background: #222; border-radius: 4px; }}
        .asset-selector label {{ margin-right: 12px; cursor: pointer; white-space: nowrap; }}
        .context-warning {{ font-size: 0.75rem; color: #fff; margin-top: 12px; margin-bottom: 12px; padding: 12px; background: #594310; border-radius: 6px; border-left: 3px solid #b93; word-break: break-word; }}
        .context-warning a {{ color: #fff; text-decoration: underline; }}

        /* Responsive breakpoints - after base rules so they win on specificity */
        @media (max-width: 1000px) {{
            .main {{ grid-template-columns: minmax(0, 1fr); }}
            canvas {{ height: 400px; }}
        }}
        @media (max-width: 480px) {{
            .container {{ padding: 12px; }}
            h1 {{ font-size: 1.2rem; }}
            .controls {{ gap: 6px; padding: 8px; }}
            .controls label {{ font-size: 0.8rem; }}
            .controls select, .controls button {{ padding: 5px 8px; font-size: 0.8rem; }}
            .asset-selector label {{ margin-right: 8px; font-size: 0.8rem; }}
            .asset-card {{ padding: 12px; }}
            .asset-card h3 {{ font-size: 1rem; }}
            .asset-info {{ grid-template-columns: 1fr; font-size: 0.8rem; }}
            canvas {{ height: 350px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>DYNAcity — Corridor asset preview</h1>
        <p class="subtitle">Software triangle renderer (Canvas 2D) — Y-Up GLTF (X=East, Y=Up, Z=-North) / Local ENU (X=East, Y=North, Z=Up) — Additive features only</p>

    <div class="controls">
            <div class="view-toggle">
                <button class="view-btn active" data-view="perspective" onclick="setView('perspective')">Perspective</button>
                <button class="view-btn" data-view="orthographic" onclick="setView('orthographic')">Orthographic</button>
            </div>
            <label><input type="checkbox" id="showAxes" checked onchange="redraw()"> Show Axes (ENU)</label>
            {context_checkbox}
<label><input type="checkbox" id="showGeometry" checked onchange="redraw()"> Show candidate geometry</label>
            <label><input type="checkbox" id="showWireframe" onchange="redraw()"> Wireframe</label>
            <label><input type="checkbox" id="showSourceId" onchange="redraw()"> Show Source UUID</label>
            <label><input type="checkbox" id="showStatus" checked onchange="redraw()"> Show Status</label>
            <button onclick="resetView()">Reset View</button>
        </div>

        <div class="asset-selector">
            <strong>Asset:</strong>
            <label><input type="radio" name="assetView" value="both" checked onchange="setAssetView('both')"> All assets (shared fit)</label>
            <label><input type="radio" name="assetView" value="tower70" onchange="setAssetView('tower70')"> Tower70</label>
            <label><input type="radio" name="assetView" value="granite91" onchange="setAssetView('granite91')"> Granite91</label>
        </div>

        {context_warning}
<div class="main">
            <div class="viewer-panel">
                <canvas id="canvas" width="1200" height="600"></canvas>
                <div class="coordinate-display" id="coordDisplay">Move mouse over canvas for coordinates</div>
            </div>

            <div class="info-panel" id="infoPanel"></div>
        </div>

        <div class="disclaimer">
            <strong>Disclaimer:</strong> This preview uses a software-projected triangle renderer on HTML Canvas 2D.
            No WebGL, no production fidelity. GLB assets have no UVs; materials show PBR factors only.
            Glass/transparent surfaces rendered with approximate alpha (painter's algorithm, no depth peel).
            Geometry is ESTIMATED, not surveyed. Additive features only — not whole buildings.
            Coordinate system: GLTF Y-Up (X=East, Y=Up, Z=-North); Local ENU (X=East, Y=North, Z=Up).
            Original world bounds (ENU) shown for reference. UTM origin: [730784.7582802251, 3751551.1589951646, 51.10578950470706].
            This is for geometry asset readiness inspection while Unreal modal is pending — NOT a web app replacement.
            Geometry inspection only. Map placement, source resemblance and Unreal rendering remain unverified.
        </div>
    </div>

    
    <script>
    // Embedded asset data (escaped for safe inline embedding)
    window.previewErrors = [];
    window.addEventListener('error', e => window.previewErrors.push(e.message));
    const ASSET_DATA = {asset_json};

    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d');
    const coordDisplay = document.getElementById('coordDisplay');
    const infoPanel = document.getElementById('infoPanel');

    // Camera state
    let camera = {{
        distance: {max_dim_val * 1.5},
        azimuth: 0.6,
        elevation: 0.4,
        target: [{center_x}, {center_y}, {center_z}],
        viewMode: 'perspective',
        fov: 45 * Math.PI / 180
    }};
    let currentAssetView = 'both';

    // Mouse interaction
    let isDragging = false;
    let lastMouseX = 0, lastMouseY = 0;

    canvas.addEventListener('mousedown', (e) => {{
        isDragging = true;
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
        canvas.style.cursor = 'grabbing';
    }});

    window.addEventListener('mousemove', (e) => {{
        if (!isDragging) return;
        const dx = e.clientX - lastMouseX;
        const dy = e.clientY - lastMouseY;
        camera.azimuth -= dx * 0.005;
        camera.elevation = Math.max(-Math.PI/2 + 0.01, Math.min(Math.PI/2 - 0.01, camera.elevation - dy * 0.005));
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
        redraw();
    }});

    window.addEventListener('mouseup', () => {{
        isDragging = false;
        canvas.style.cursor = 'grab';
    }});

    canvas.addEventListener('wheel', (e) => {{
        e.preventDefault();
        camera.distance *= (e.deltaY > 0) ? 1.1 : 0.9;
        camera.distance = Math.max(0.1, Math.min(10000, camera.distance));
        redraw();
    }}, {{passive: false}});

    canvas.addEventListener('mousemove', (e) => {{
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        coordDisplay.textContent = `Canvas: (${{x.toFixed(1)}}, ${{y.toFixed(1)}}) | Camera: dist=${{camera.distance.toFixed(2)}}, az=${{camera.azimuth.toFixed(3)}}, el=${{camera.elevation.toFixed(3)}} | View: ${{currentAssetView}}`;
    }});

    function setView(mode) {{
        camera.viewMode = mode;
        document.querySelectorAll('.view-btn').forEach(btn => {{
            btn.classList.toggle('active', btn.dataset.view === mode);
        }});
        redraw();
    }}

    function setAssetView(key) {{
        currentAssetView = key;
        // Reset camera to fit selected asset
        if (key !== 'both') {{
            const asset = ASSET_DATA.find(a => a.key === key);
            if (asset) {{
                const [min, max] = asset.bounds_local;
                const c = [(min[0]+max[0])*0.5, (min[1]+max[1])*0.5, (min[2]+max[2])*0.5];
                const s = [max[0]-min[0], max[1]-min[1], max[2]-min[2]];
                const md = Math.max(...s);
                camera.target = c;
                camera.distance = md * 1.5;
                camera.azimuth = 0.6;
                camera.elevation = 0.4;
            }}
        }} else {{
            // Shared fit
            camera.target = [{center[0]}, {center[1]}, {center[2]}];
            camera.distance = {max_dim * 1.5};
            camera.azimuth = 0.6;
            camera.elevation = 0.4;
        }}
        // Update radio buttons
        document.querySelectorAll('input[name="assetView"]').forEach(r => r.checked = r.value === key);
        redraw();
    }}

    function resetView() {{
        setAssetView(currentAssetView);
    }}

    // Build view matrix (right-handed, Z-up for ENU local space)
    function buildViewMatrix() {{
        const {{distance, azimuth, elevation, target}} = camera;
        const eyeX = target[0] + distance * Math.cos(elevation) * Math.sin(azimuth);
        const eyeY = target[1] + distance * Math.cos(elevation) * Math.cos(azimuth);
        const eyeZ = target[2] + distance * Math.sin(elevation);

        const f = [target[0] - eyeX, target[1] - eyeY, target[2] - eyeZ];
        const flen = Math.sqrt(f[0]*f[0] + f[1]*f[1] + f[2]*f[2]);
        if (flen > 0) {{ f[0]/=flen; f[1]/=flen; f[2]/=flen; }}

        const upVec = [0, 0, 1]; // Z-up for ENU local space

        const s = [
            f[1]*upVec[2] - f[2]*upVec[1],
            f[2]*upVec[0] - f[0]*upVec[2],
            f[0]*upVec[1] - f[1]*upVec[0]
        ];
        const slen = Math.sqrt(s[0]*s[0] + s[1]*s[1] + s[2]*s[2]);
        if (slen > 0) {{ s[0]/=slen; s[1]/=slen; s[2]/=slen; }}

        const u = [
            s[1]*f[2] - s[2]*f[1],
            s[2]*f[0] - s[0]*f[2],
            s[0]*f[1] - s[1]*f[0]
        ];

        return [
            [s[0], u[0], -f[0], 0],
            [s[1], u[1], -f[1], 0],
            [s[2], u[2], -f[2], 0],
            [-s[0]*eyeX - s[1]*eyeY - s[2]*eyeZ,
             -u[0]*eyeX - u[1]*eyeY - u[2]*eyeZ,
              f[0]*eyeX + f[1]*eyeY + f[2]*eyeZ, 1]
        ];
    }}

    function buildProjMatrix() {{
        const {{viewMode, fov}} = camera;
        const aspect = canvas.width / canvas.height;
        const near = 0.01;
        const far = 10000;

        if (viewMode === 'perspective') {{
            const f = 1.0 / Math.tan(fov / 2);
            return [
                [f/aspect, 0, 0, 0],
                [0, f, 0, 0],
                [0, 0, (far+near)/(near-far), -1],
                [0, 0, 2*far*near/(near-far), 0]
            ];
        }} else {{
            const size = camera.distance;
            return [
                [1/size, 0, 0, 0],
                [0, 1/size, 0, 0],
                [0, 0, -2/(far-near), 0],
                [0, 0, -(far+near)/(far-near), 1]
            ];
        }}
    }}

    function mulMatVec(mat, vec) {{
        return [0,1,2,3].map(row => mat[0][row]*vec[0]+mat[1][row]*vec[1]+mat[2][row]*vec[2]+mat[3][row]*vec[3]);
    }}

    function projectTri(tri, viewMat, projMat) {{
        const result = [];
        for (const pt of tri) {{
            const v = mulMatVec(viewMat, [pt[0], pt[1], pt[2], 1]);
            if (v[3] === 0) v[3] = 1e-6;
            const p = mulMatVec(projMat, v);
            if (p[3] === 0) p[3] = 1e-6;
            const ndcX = p[0] / p[3];
            const ndcY = p[1] / p[3];
            const canvasX = (ndcX + 1) * 0.5 * canvas.width;
            const canvasY = (1 - ndcY) * 0.5 * canvas.height;
            result.push([canvasX, canvasY, p[2]/p[3]]);
        }}
        return result;
    }}

    function drawTri(ctx, tri2d, color, wireframe) {{
        ctx.beginPath();
        ctx.moveTo(tri2d[0][0], tri2d[0][1]);
        ctx.lineTo(tri2d[1][0], tri2d[1][1]);
        ctx.lineTo(tri2d[2][0], tri2d[2][1]);
        ctx.closePath();

        if (!wireframe) {{
            ctx.fillStyle = color;
            ctx.fill();
        }}
        ctx.strokeStyle = wireframe ? '#fff' : color;
        ctx.lineWidth = wireframe ? 1 : 0.5;
        ctx.stroke();
    }}

    function isBackface(tri2d) {{
        const [a, b, c] = tri2d;
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]) < 0;
    }}

    function avgDepth(tri2d) {{
        return (tri2d[0][2] + tri2d[1][2] + tri2d[2][2]) / 3;
    }}

    function rgbaColor(mat) {{
        const encode = c => Math.round(255 * (c <= 0.0031308 ? 12.92*c : 1.055*Math.pow(c,1/2.4)-0.055));
        const bc = mat.baseColor;
        const alpha = bc[3] !== undefined ? bc[3] : 1.0; // preserve exact alpha, no fallback to 1
        return `rgba(${{encode(bc[0])}}, ${{encode(bc[1])}}, ${{encode(bc[2])}}, ${{alpha}})`;
    }}

    function redraw() {{
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#0a0a12';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        const viewMat = buildViewMatrix();
        const projMat = buildProjMatrix();
        const showAxes = document.getElementById('showAxes').checked;
        const showWireframe = document.getElementById('showWireframe').checked;
        const showSourceId = document.getElementById('showSourceId').checked;
        const showStatus = document.getElementById('showStatus').checked;

        const allTris = [];
        for (const asset of ASSET_DATA) {{
            if (asset.role !== 'context' && !document.getElementById('showGeometry').checked) continue;
            if (asset.role === 'context' && !document.getElementById('showContext')?.checked) continue;
            if (currentAssetView !== 'both' && asset.key !== currentAssetView && asset.role !== 'context') continue;
            for (const triData of asset.triangles) {{
                const tri = [triData.v0, triData.v1, triData.v2];
                const tri2d = projectTri(tri, viewMat, projMat);
                if (tri2d.some(p => p.some(v => !Number.isFinite(v)))) continue;
                const depth = avgDepth(tri2d);
                const matIdx = triData.material;
                const mat = (matIdx >= 0 && matIdx < asset.materials.length) ? asset.materials[matIdx] : asset.materials[0];
                const color = rgbaColor(mat);
                allTris.push({{tri2d, depth, color, asset, mat, triIdx: triData.material}});
            }}
        }}

        allTris.sort((a, b) => b.depth - a.depth);

        for (const t of allTris) {{
            drawTri(ctx, t.tri2d, t.color, showWireframe);
        }}

        if (showAxes) {{
            const origin = projectTri([[{center[0]}, {center[1]}, {center[2]}]], viewMat, projMat)[0];
            const axisLen = Math.max({size[0]}, {size[1]}, {size[2]}) * 0.5;
            const axes = [
                [[{center[0]}, {center[1]}, {center[2]}], [{center[0]}+axisLen, {center[1]}, {center[2]}], '#ff4444', 'X (East)'],
                [[{center[0]}, {center[1]}, {center[2]}], [{center[0]}, {center[1]}+axisLen, {center[2]}], '#44ff44', 'Y (North)'],
                [[{center[0]}, {center[1]}, {center[2]}], [{center[0]}, {center[1]}, {center[2]}+axisLen], '#4488ff', 'Z (Up)']
            ];
            for (const [a, b, color, label] of axes) {{
                const pa = projectTri([a], viewMat, projMat)[0];
                const pb = projectTri([b], viewMat, projMat)[0];
                ctx.beginPath();
                ctx.moveTo(pa[0], pa[1]);
                ctx.lineTo(pb[0], pb[1]);
                ctx.strokeStyle = color;
                ctx.lineWidth = 2;
                ctx.stroke();
                ctx.fillStyle = color;
                ctx.font = '12px Arial';
                ctx.fillText(label, pb[0]+4, pb[1]-4);
            }}
        }}

        updateInfoPanel();
    }}

    function updateInfoPanel() {{
        const showSourceId = document.getElementById('showSourceId').checked;
        const showStatus = document.getElementById('showStatus').checked;

        let html = '';
        for (const asset of ASSET_DATA) {{
            if (currentAssetView !== 'both' && asset.key !== currentAssetView && asset.role !== 'context') continue;
            html += `<div class="asset-card">
                <h3>${{asset.key}} <span class="badge ${{asset.role === 'context' ? 'context' : 'estimated'}}">${{asset.role === 'context' ? 'Offline context — native unverified' : 'Estimated candidate'}}</span></h3>
                <dl class="asset-info">
                    <dt>Kind</dt><dd>${{asset.geometry_kind}}</dd>
                    <dt>Triangles</dt><dd>${{asset.triangle_count}}</dd>
                    <dt>Materials</dt><dd>${{asset.material_count}}</dd>
                    <dt>Local ENU bounds</dt><dd>[${{asset.bounds_local[0].map(v=>v.toFixed(3)).join(', ')}}] → [${{asset.bounds_local[1].map(v=>v.toFixed(3)).join(', ')}}]</dd>
                    <dt>GLTF Y-Up bounds</dt><dd>[${{asset.gltf_bounds_yup.min.map(v=>v.toFixed(3)).join(', ')}}] → [${{asset.gltf_bounds_yup.max.map(v=>v.toFixed(3)).join(', ')}}]</dd>
                    <dt>Local ENU bounds (manifest)</dt><dd>[${{asset.local_enu_bounds.min.map(v=>v.toFixed(3)).join(', ')}}] → [${{asset.local_enu_bounds.max.map(v=>v.toFixed(3)).join(', ')}}]</dd>
                </dl>`;
            if (showSourceId) {{
                html += `<div style="margin-top:8px; font-size:0.75rem; color:#aaa; word-break:break-all;">Source(s): ${{asset.source_building_uuid}}</div>`;
            }}
            if (showStatus) {{
                html += `<div style="margin-top:4px; font-size:0.75rem; color:#888;">Status: ${{asset.status}}</div>`;
            }}
            html += `<a class="download-link" href="${{asset.glb_file}}" download>Download ${{asset.glb_file}} (SHA256: ${{asset.sha256.slice(0,16)}}...)</a>`;
            html += `</div>`;
        }}
        infoPanel.innerHTML = html;
    }}


    const resize = new ResizeObserver(() => {{
        canvas.width = Math.max(1, Math.round(canvas.clientWidth));
        canvas.height = Math.max(1, Math.round(canvas.clientHeight));
        redraw();
    }});
    resize.observe(canvas.parentElement);
    if (new URLSearchParams(location.search).has('selftest')) setTimeout(() => {{
        const checks = [];
        const check = (name, pass) => checks.push({{name, pass: Boolean(pass)}});
        try {{
            check('two real assets', ASSET_DATA.length === 2 && ASSET_DATA.every(a => a.triangle_count > 0));
            document.querySelector('input[value="tower70"]').click();
            check('tower focus', currentAssetView === 'tower70');
            const beforeZoom = camera.distance;
            canvas.dispatchEvent(new WheelEvent('wheel', {{deltaY:100, cancelable:true}}));
            check('wheel zoom', camera.distance > beforeZoom);
            document.querySelector('[data-view="orthographic"]').click();
            check('orthographic control', camera.viewMode === 'orthographic');
            document.querySelector('[data-view="perspective"]').click();
            const target = camera.target;
            const v = buildViewMatrix(), p = buildProjMatrix();
            const projected = projectTri([target,[target[0],target[1],target[2]+1]],v,p);
            check('vertical up projects upward', projected[1][1] < projected[0][1]);
            const drawn = canvas.toDataURL();
            document.getElementById('showGeometry').click();
            check('candidate toggle redraws', canvas.toDataURL() !== drawn);
            document.getElementById('showGeometry').click();
            document.querySelector('input[value="granite91"]').click();
            check('granite focus', currentAssetView === 'granite91');
            document.querySelector('input[value="both"]').click();
            check('shared view restores', currentAssetView === 'both');
            check('alpha zero preserved', rgbaColor({{baseColor:[1,1,1,0]}}).endsWith(', 0)'));
            check('page fits viewport', document.documentElement.scrollWidth <= innerWidth + 1);
            check('canvas aspect matches actual pixels', Math.abs(canvas.width/canvas.height-canvas.clientWidth/canvas.clientHeight)<0.01);
            check('no browser exceptions', window.previewErrors.length === 0);
        }} catch(e) {{ checks.push({{name:'selftest exception',pass:false,error:String(e)}}); }}
        const result = document.createElement('script'); result.type='application/json'; result.id='browser-smoke-results';
        result.textContent=JSON.stringify({{checks,errors:window.previewErrors,viewport:[innerWidth,innerHeight]}});document.body.append(result);
    }}, 100);

    const initialAsset = new URLSearchParams(location.search).get('asset');
    if (['tower70','granite91'].includes(initialAsset)) setAssetView(initialAsset);
    else redraw();
    </script>
</body>
</html>'''

    return html


def build_preview(bundle_path: Path, output_dir: Path, context_path: Optional[Path] = None) -> Dict[str, Any]:
    """Main build function."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('Output directory is not empty; refusing overwrite')
    manifest_path = bundle_path / 'manifest.json'
    manifest = validate_manifest(manifest_path)

    # Verify and parse both GLB assets
    assets = []
    for cand in manifest['candidates']:
        glb_asset = verify_glb_asset(cand, bundle_path)
        assets.append(glb_asset)

    context_asset = None
    context = None
    if context_path is not None:
        context = validate_context_manifest(context_path)
        if context['origin_utm_m'] != manifest['origin_utm_m'] or context['horizontal_crs'] != manifest.get('horizontal_crs',manifest['coordinate_system'].get('horizontal_crs')):
            raise ValueError('Context coordinate frame mismatch')
        if not {c['source_building_uuid'] for c in manifest['candidates']}.issubset(context['source_building_ids']):
            raise ValueError('Context does not cover candidate source identities')
        context_asset = verify_context_asset(context, context_path.parent)
        context = dict(context, context_asset=dict(context['context_asset'],path='assets/'+context['context_asset']['sha256']+'.glb'))

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Ensure assets subdir exists
    assets_out_dir = output_dir / 'assets'
    assets_out_dir.mkdir(parents=True, exist_ok=True)

    # Copy GLB files to output/assets/
    for cand in manifest['candidates']:
        src = bundle_path / cand['glb_asset']['path']
        dst = assets_out_dir / Path(cand['glb_asset']['path']).name
        if not dst.resolve().is_relative_to(output_dir.resolve()):
            raise ValueError(f"Path traversal detected: {dst}")
        shutil.copy2(src, dst)

    # Generate portable manifest (relative paths only)
    portable_manifest = {
        'schema': 'dynacity-corridor-asset-preview/1',
        'source_bundle_schema': manifest['schema'],
        'context': context,
        'generated_by': 'build_corridor_asset_preview.py',
        'timestamp': manifest.get('timestamp'),
        'origin_utm_m': manifest['origin_utm_m'],
        'coordinate_system': manifest['coordinate_system'],
        'status': manifest.get('status'),
        'candidates': [
            {
                'key': c['key'],
                'geometry_kind': c['geometry_kind'],
                'source_building_uuid': c['source_building_uuid'],
                'gltf_bounds_yup': c['gltf_bounds_yup'],
                'local_enu_bounds': c['local_enu_bounds'],
                'glb_asset': {
                    'path': f"assets/{Path(c['glb_asset']['path']).name}",
                    'sha256': c['glb_asset']['sha256'],
                    'size_bytes': c['glb_asset']['size_bytes']
                },
                'material_parameters': c['material_parameters'],
                'status': c['status']
            }
            for c in manifest['candidates']
        ]
    }

    with open(output_dir / 'manifest.json', 'w', encoding='utf-8') as f:
        json.dump(portable_manifest, f, indent=2)

    if context_asset is not None:
        target = output_dir/context['context_asset']['path']
        shutil.copy2(context_asset.glb_path,target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=context['context_asset']['sha256']:
            raise ValueError('Context copy hash mismatch')
        (output_dir/'context.json').write_text(json.dumps(context,indent=2), encoding='utf-8')
    # Generate HTML
    html = generate_html(assets, manifest, output_dir, context_asset, context)
    with open(output_dir / 'index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    return {
        'output_dir': str(output_dir),
        'assets_processed': len(assets) + (1 if context_asset is not None else 0),
        'total_triangles': sum(a.triangle_count for a in assets) + (context_asset.triangle_count if context_asset is not None else 0),
        'files_created': ['index.html', 'manifest.json'] + [f"assets/{Path(c['glb_asset']['path']).name}" for c in manifest['candidates']] + (['context.json', context['context_asset']['path']] if context is not None else [])
    }


def main():
    parser = argparse.ArgumentParser(
        description='Build standalone browser preview for portable 2-GLB package (Iteration31)'
    )
    parser.add_argument('--bundle', type=Path, required=True,
                        help='Path to bundle directory containing manifest.json and assets/ GLB files')
    parser.add_argument('--output', type=Path, required=True,
                        help='Output directory for preview (will contain index.html, assets/, manifest.json)')

    parser.add_argument('--context', type=Path, help='Optional private context.json with native-version warning')
    args = parser.parse_args()

    try:
        result = build_preview(args.bundle, args.output, args.context)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
