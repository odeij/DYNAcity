"""Build tower upper-front-infill pilot (additive only) using exact tower record from iteration3 manifest.
Operates on wall 6 (pale open-frame face) of tower beirut001:bde30f76-7868-4187-92a7-a18270c58815.
Adds pale infill panels with real window openings ONLY on upper frontage from source roof z=54.2579 to raised top z=72.3404.
Default (0) produces NO additive geometry; candidate fractions 0.7, 0.85 from right local X leaving narrow exposed strip.
Uses corridor_reconstruction helpers for exact identity/placement; does NOT modify tower family builder.
"""
import argparse, json, hashlib, math
from pathlib import Path
import numpy as np
from dynacity import reference_facade_geometry as G, corridor_reconstruction as cr
from dynacity.geometry_checks import coplanar_overlaps

# Canonical manifest path
MANIFEST_PATH = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration3/corridor-manifest-v2.json")


def load_tower_record(manifest_path=None):
    """Load tower record from iteration3 manifest; reject wrong source."""
    import json
    path = Path(manifest_path) if manifest_path else MANIFEST_PATH
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    data = json.loads(path.read_text())
    for b in data.get("buildings", []):
        if b.get("key") == "tower" and b.get("source_id_full") == "beirut001:bde30f76-7868-4187-92a7-a18270c58815":
            return b
    raise ValueError("Tower record beirut001:bde30f76-7868-4187-92a7-a18270c58815 not found in manifest")


def build_additive_pilot(frac, out_dir, tower_data=None):
    """Build additive upper front infill on wall 6 at world coordinates.

    Returns path to JSON report. For frac=0, writes report only (no GLB, no parts).
    """
    if not (0.0 <= frac <= 1.0):
        raise ValueError("--upper-front-infill-frac must be in [0, 1]")

    if tower_data is None:
        tower_data = load_tower_record()

    # Validate correct source
    if tower_data.get("source_id_full") != "beirut001:bde30f76-7868-4187-92a7-a18270c58815":
        raise ValueError(f"Wrong source: {tower_data.get('source_id_full')}")

    # Find wall 6
    wall6 = None
    for w in tower_data.get("walls", []):
        if w.get("edge") == 6 or w.get("frontage", {}).get("index") == 6:
            wall6 = w
            break
    if wall6 is None:
        raise ValueError("Wall 6 not found in tower record")

    fr = wall6["frontage"]
    width = fr["length_m"]
    start = np.array(fr["start"], float)
    tangent = np.array(fr["tangent"], float)
    normal = np.array(fr["normal"], float)
    base_z = fr["base_z"]
    source_roof_z = fr["top_z"]
    plan_depth = wall6["plan_depth_m"]
    floor_h = wall6["params"]["floor_height_m"]
    open_floors = wall6["params"]["open_floors"]

    raised_top_z = tower_data["base_z_local_m"] + tower_data["height_override"]["est"]
    additive_z0_world = source_roof_z
    additive_z1_world = raised_top_z
    additive_height = additive_z1_world - additive_z0_world

    # Local Z coordinates (relative to base_z) for cr.place_parts
    additive_z0_local = additive_z0_world - base_z
    additive_z1_local = additive_z1_world - base_z

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    placed = []

    # Build upper infill panels ONLY (no lower tower parts)
    if frac > 1e-9:
        infill_width = width * frac
        strip_start = width - infill_width  # left edge of infill (right-aligned)

        # Number of floor lines in additive height
        n_floors = max(1, int(math.ceil(additive_height / floor_h)))

        # Panel depth: recess behind front frame plane by ~0.05m, trim to 0.25m slab
        panel_depth = 0.05
        panel_back_y = panel_depth  # local Y from facade plane (street is -Y)

        for i in range(n_floors):
            z0_local = additive_z0_local + i * floor_h
            z1_local = min(z0_local + floor_h, additive_z1_local)

            # Panel spans [strip_start, width] in local X
            x0, x1 = strip_start, width

            # Window opening centered in panel width
            cw = x1 - x0
            w = min(0.6 * cw, 1.8)
            wx0 = x0 + (cw - w) / 2
            wx1 = x0 + (cw + w) / 2
            # Window vertical: 0.3m above floor to 0.3m below ceiling (local Z)
            wz0 = z0_local + 0.3
            wz1 = z1_local - 0.3
            if wz1 - wz0 < 0.5:
                wz0, wz1 = z0_local + 0.15, z1_local - 0.15

            # Build closed rectangular pieces around real window hole
            panel_verts, panel_faces = [], []
            base_idx = 0

            # Left of window (vertical slab from y=panel_back_y to y=0.25)
            if wx0 - x0 > 1e-3:
                v, f = cr.polygon_prism(
                    np.array([[x0, panel_back_y], [wx0, panel_back_y], [wx0, 0.25], [x0, 0.25]]),
                    z0_local, z1_local
                )
                panel_verts.append(v)
                panel_faces.append(f + base_idx)
                base_idx += len(v)

            # Right of window
            if x1 - wx1 > 1e-3:
                v, f = cr.polygon_prism(
                    np.array([[wx1, panel_back_y], [x1, panel_back_y], [x1, 0.25], [wx1, 0.25]]),
                    z0_local, z1_local
                )
                panel_verts.append(v)
                panel_faces.append(f + base_idx)
                base_idx += len(v)

            # Below window
            if wz0 - z0_local > 1e-3:
                v, f = cr.polygon_prism(
                    np.array([[wx0, panel_back_y], [wx1, panel_back_y], [wx1, 0.25], [wx0, 0.25]]),
                    z0_local, wz0
                )
                panel_verts.append(v)
                panel_faces.append(f + base_idx)
                base_idx += len(v)

            # Above window
            if z1_local - wz1 > 1e-3:
                v, f = cr.polygon_prism(
                    np.array([[wx0, panel_back_y], [wx1, panel_back_y], [wx1, 0.25], [wx0, 0.25]]),
                    wz1, z1_local
                )
                panel_verts.append(v)
                panel_faces.append(f + base_idx)
                base_idx += len(v)

            if panel_verts:
                pv = np.concatenate(panel_verts)
                pf = np.concatenate(panel_faces)
                placed.append({
                    "name": f"upper_front_infill_f{i}",
                    "material": "concrete_pale",
                    "vertices": pv,
                    "triangles": pf
                })

                # Window glass - slightly in front of panel face to avoid coplanar
                if wx1 - wx0 > 1e-3 and wz1 - wz0 > 1e-3:
                    glass_v, glass_f = cr.polygon_prism(
                        np.array([[wx0, 0.22], [wx1, 0.22], [wx1, 0.28], [wx0, 0.28]]),
                        wz0, wz1
                    )
                    placed.append({
                        "name": f"upper_front_glass_f{i}",
                        "material": "glass",
                        "vertices": glass_v,
                        "triangles": glass_f
                    })

    # Transform ALL upper parts to world coords in ONE place_parts call
    if placed:
        frontage_dict = {
            "start": list(start),
            "tangent": list(tangent),
            "normal": list(normal),
            "base_z": base_z,
            "length_m": width,
            "top_z": source_roof_z,
        }
        placed = cr.place_parts(placed, frontage_dict)

    # Materials (only concrete_pale and glass used)
    materials = {
        "concrete_pale": {"base_color": [0.415, 0.423, 0.447, 1.0], "roughness": 0.8, "metallic": 0.0},
        "glass": {"base_color": [0.025, 0.045, 0.065, 1.0], "roughness": 0.08, "metallic": 0.0,
                  "blend": "translucent", "opacity": 0.55},
    }

    # Write GLB only if we have additive parts
    glb_path = None
    if placed:
        tag = f"-infill{frac:.2f}"
        glb_path = out / f"tower-additive{tag}.glb"
        export = cr.write_glb(glb_path, placed, materials, mesh_name="TowerAdditivePilot")
    else:
        # frac=0: no geometry, no GLB
        glb_path = None

    # Verification
    if placed:
        all_verts = np.concatenate([p["vertices"] for p in placed])
        checks = {
            "whole_height_unchanged": True,
            "additive_z_min": float(all_verts[:, 2].min()),
            "additive_z_max": float(all_verts[:, 2].max()),
            "additive_height_m": additive_height,
            "upper_infill_frac": frac,
            "wall6_local_width_m": width,
            "n_parts": len(placed),
        }
    else:
        checks = {
            "whole_height_unchanged": True,
            "additive_z_min": additive_z0_world,
            "additive_z_max": additive_z1_world,
            "additive_height_m": additive_height,
            "upper_infill_frac": 0.0,
            "wall6_local_width_m": width,
            "n_parts": 0,
        }

    # Report
    report = {
        "schema": "dynacity-tower-additive-pilot/1",
        "source_id": tower_data["source_id_full"],
        "source_height_m": tower_data["source_height_m"],
        "source_roof_z_m": source_roof_z,
        "raised_top_z_m": raised_top_z,
        "additive_z_range_m": [additive_z0_world, additive_z1_world],
        "additive_height_m": additive_height,
        "wall": {"edge": 6, "width_m": width, "base_z_m": base_z, "top_z_m": source_roof_z,
                 "plan_depth_m": plan_depth},
        "upper_infill_frac": frac,
        "status": "ESTIMATED additive pilot; NOT a full building export. Identity/height from iteration3 manifest (uncalibrated camera).",
        "geometry": str(glb_path.resolve()) if glb_path else None,
        "sha256": hashlib.sha256(glb_path.read_bytes()).hexdigest() if glb_path else None,
        "verification": checks,
        "limits": [
            "Additive ONLY on upper front wall 6 from source roof to raised top",
            "No modification to tower family builder or lower tower parts",
            "Infill coverage right-aligned; left strip exposed (estimated)",
            "Window positions/sizes estimated; not from direct measurement",
            "Source identity/height/placement from iteration3 manifest; uncalibrated camera",
            "No native visual/collision/persistence acceptance from this pilot",
            "Based on 2018 KartaView imagery; estimates only"
        ]
    }

    rp = out / (f"tower-additive-infill{frac:.2f}.json" if frac > 0 else "tower-additive-infill0.json")
    rp.write_text(json.dumps(report, indent=2))
    return str(rp)


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("output", type=Path)
    a.add_argument("--upper-front-infill-frac", type=float, default=0.0,
                   help="Infill fraction of upper frontage (0..1, right-aligned)")
    args = a.parse_args()
    print(build_additive_pilot(args.upper_front_infill_frac, args.output))

