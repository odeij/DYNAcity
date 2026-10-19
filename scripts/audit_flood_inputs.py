#!/usr/bin/env python3
"""Audit acquired real inputs outside Git; never construct a Beirut model.

Input directory contains original NOAA ISD .gz files and source provenance
sidecars. Output retains missing/trace values, overlapping accumulation periods,
and quality flags. No temporal disaggregation or gap filling is performed.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import re


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root: Path) -> dict:
    repo = Path(__file__).resolve().parents[1]
    root = root.resolve(strict=True)
    if root == repo or repo in root.parents:
        raise ValueError("Raw inputs and generated handoff must remain outside Git")
    files = []
    for sidecar in sorted(root.glob("*.provenance.json")):
        path = root / sidecar.name.removesuffix(".provenance.json")
        source = json.loads(sidecar.read_text())
        source.setdefault("license", None)
        digest = sha256(path)
        if source["sha256"] != digest:
            raise ValueError(f"Checksum mismatch: {path.name}")
        files.append({"path": str(path), **source, "sha256": digest})

    rainfall = []
    summaries = []
    for path in sorted(root.glob("beirut-*-isd.gz")):
        lines = gzip.decompress(path.read_bytes()).decode("ascii").splitlines()
        rows = []
        for number, line in enumerate(lines, 1):
            if len(line) < 105 or line[4:15] != "40100099999":
                raise ValueError(f"Unexpected ISD station/record at {path.name}:{number}")
            end = dt.datetime.strptime(line[15:27], "%Y%m%d%H%M").replace(tzinfo=dt.UTC)
            # AA identifiers are parsed only before free-text remarks/quality data.
            additional = re.split(r"REM|EQD", line[105:], maxsplit=1)[0]
            for match in re.finditer(r"(AA[1-4])(\d{2})(\d{4})(.)(.)", additional):
                tag, period, depth, condition, quality = match.groups()
                hours = None if period == "99" else int(period)
                measured = None if depth == "9999" else int(depth) / 10
                status = "missing" if measured is None else "reported_amount"
                if condition == "2":
                    status = "trace"
                elif condition == "E":
                    status = "estimated"
                elif condition == "1":
                    status = "impossible_or_inaccurate"
                rows.append({
                    "source_file": path.name, "source_line": number,
                    "station": "401000-99999", "end_utc": end.isoformat(),
                    "start_utc": (end - dt.timedelta(hours=hours)).isoformat() if hours else None,
                    "period_hours": hours, "raw_tag": match.group(0),
                    "field": tag, "encoded_depth_mm": measured,
                    "depth_mm": measured if status in {"reported_amount", "estimated"} else None,
                    "condition_code": condition, "quality_code": quality,
                    "status": status,
                })
        rainfall.extend(rows)
        summaries.append({
            "file": path.name, "weather_records": len(lines),
            "precipitation_fields": len(rows),
            "status_counts": dict(collections.Counter(r["status"] for r in rows)),
            "numeric_nonmissing_fields": sum(r["encoded_depth_mm"] is not None for r in rows),
            "positive_numeric_fields": sum(r["encoded_depth_mm"] is not None and r["encoded_depth_mm"] > 0 for r in rows),
            "period_hours": sorted({r["period_hours"] for r in rows if r["period_hours"] is not None}),
            "quality_counts": dict(collections.Counter(r["quality_code"] for r in rows)),
        })
    csv_path = root / "rainfall-accumulations.csv"
    if rainfall:
        with csv_path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rainfall[0]))
            writer.writeheader()
            writer.writerows(rainfall)

    context = []
    for name in ["unhabitat-beirut-stormwater-features.json", "unhabitat-beirut-street-condition.json"]:
        path = root / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if data.get("error") or data.get("exceededTransferLimit"):
            raise ValueError(f"Incomplete/error ArcGIS response: {name}")
        attrs = [feature["attributes"] for feature in data["features"]]
        context.append({
            "path": str(path), "sha256": sha256(path), "features": len(attrs),
            "crs": data.get("spatialReference"),
            "categories": {key: dict(collections.Counter(str(row.get(key)) for row in attrs))
                           for key in ["NBH_Name", "Feature", "StormW_Dra", "Year"]
                           if any(key in row for row in attrs)},
            "evidence_kind": "documented_survey_mapping",
            "hydraulic_connectivity_verified": False,
            "vertical_datum": None, "reuse_license": None,
        })
    report = {
        "schema": "dynacity.real-input-acquisition.v1",
        "audit_date": "2026-10-16", "planning_ready": False,
        "pilot_boundary": None, "event_id": None,
        "files": files, "rainfall_summaries": summaries,
        "local_assets": json.loads((root / "local-assets.json").read_text()) if (root / "local-assets.json").exists() else None,
        "acquisition_attempts": json.loads((root / "acquisition-attempts.json").read_text()) if (root / "acquisition-attempts.json").exists() else None,
        "rainfall_accumulations": {"path": str(csv_path), "sha256": sha256(csv_path),
                                  "rows": len(rainfall), "evidence_kind": "reported_station_observations_with_flags"},
        "context_layers": context,
        "model_inputs": {
            "surveyed_stormwater_network": None,
            "inlets_catchbasins_manholes_outfalls": None,
            "pipe_connectivity_diameter_invert_capacity_condition": None,
            "verified_bare_earth_terrain": None, "terrain_vertical_datum_checkpoints": None,
            "kerbs_barriers_underpasses_culverts": None,
            "event_subhourly_measured_rainfall": None,
            "measured_infiltration": None, "calibrated_roughness": None,
            "event_coastal_river_levels": None,
            "independent_measured_flood_depth_extent_timing": None,
        },
        "restrictions": [
            "No manufactured coordinates, elevations, network links, events, capacities or numerical defaults.",
            "Station accumulation intervals may overlap; do not sum or disaggregate without an explicit documented method.",
            "Trace, missing and impossible/inaccurate amounts remain unknown depth_mm.",
            "Blank CSV cells mean unknown, never zero. Keep source flags and raw archives.",
            "Airport rainfall is not verified spatially representative of any selected pilot.",
            "UN-Habitat ponding points and street condition are not inlet or pipe connectivity data.",
            "No dated storm, measured water depth or event footprint is attached to the ponding points.",
            "Public query access is not an explicit redistribution license; UN-Habitat license remains unknown.",
        ],
    }
    (root / "real-input-handoff.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    print(json.dumps({"planning_ready": result["planning_ready"],
                      "rainfall_summaries": result["rainfall_summaries"],
                      "context_layers": result["context_layers"]}, indent=2))
