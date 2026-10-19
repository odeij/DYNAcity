"""Read-only survey context. Never convert ponding/condition records to hydraulics."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from .viewer import utm_to_lonlat


LAYERS = {
    "unhabitat-beirut-stormwater-features.json": "ponding",
    "unhabitat-beirut-street-condition.json": "street",
}
CONDITIONS = {"No drains": "no_drains", "Blocked drains": "blocked", "Functional drains": "functional"}


def _text(value):
    return str(value).strip() if value is not None else None


def _position(values):
    if len(values) < 2 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values[:2]):
        raise ValueError("survey geometry requires finite metre x/y coordinates")
    # Source Z=0 has no datum; discard it rather than manufacture an elevation.
    lon, lat = utm_to_lonlat(values[0], values[1], 36)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("invalid transformed survey location")
    return [lon, lat]


def load_flood_context(manifest_path: str | Path) -> dict:
    """Verify private acquisition hashes, normalize only actual source fields.

    Paths and unrelated downloaded content never enter the public response.
    A complete extract is required; partial/truncated layers fail explicitly.
    """
    path = Path(manifest_path).resolve(strict=True)
    handoff = json.loads(path.read_text())
    if handoff.get("schema") != "dynacity.real-input-acquisition.v1":
        raise ValueError("unsupported real-input acquisition schema")
    records = handoff.get("context_layers", [])
    by_name = {Path(r["path"]).name: r for r in records}
    if len(by_name) != len(records) or by_name.keys() != LAYERS.keys():
        raise ValueError("exactly the documented ponding and street-condition layers are required")
    points, streets, sources = [], [], []
    for name, kind in LAYERS.items():
        record = by_name[name]
        file = (path.parent / record["path"]).resolve(strict=True)
        # The acquisition package is self-contained; never follow another local path.
        if file.parent != path.parent:
            raise ValueError("survey layers must reside beside the acquisition manifest")
        raw = file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != record.get("sha256"):
            raise ValueError(f"survey checksum mismatch: {name}")
        entries = [r for r in handoff.get("files", []) if (path.parent / r["path"]).resolve() == file]
        if len(entries) != 1 or entries[0].get("sha256") != digest:
            raise ValueError(f"missing matching source provenance: {name}")
        source = entries[0]
        url = source.get("source_url", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("survey source requires a public HTTPS URL")
        data = json.loads(raw)
        if data.get("error") or data.get("exceededTransferLimit"):
            raise ValueError("incomplete survey extract")
        if data.get("spatialReference", {}).get("latestWkid", data.get("spatialReference", {}).get("wkid")) != 32636:
            raise ValueError("survey extract must explicitly use EPSG:32636")
        if data.get("geometryType") != ("esriGeometryPoint" if kind == "ponding" else "esriGeometryPolyline"):
            raise ValueError("survey geometry type disagrees with layer")
        features = data.get("features")
        if not isinstance(features, list) or len(features) != record.get("features") or not features:
            raise ValueError("survey feature count disagrees with acquisition inventory")
        seen = set()
        for feature in features:
            a, g = feature["attributes"], feature["geometry"]
            fid = a.get("FID")
            if fid is None or str(fid) in seen:
                raise ValueError("survey feature IDs must be present and unique")
            seen.add(str(fid))
            common = {"id": f"{kind}:{fid}", "neighborhood": _text(a.get("NBH_Name")) or None,
                      "survey_year": _text(a.get("Year")) or None, "source": kind,
                      "hydraulic_asset": False}
            if kind == "ponding":
                if a.get("Feature") != "Water ponding":
                    raise ValueError("unrecognized ponding feature; cannot infer a drain")
                points.append({**common, "position": _position([g["x"], g["y"]]), "condition": "ponding"})
            else:
                condition = _text(a.get("StormW_Dra")) or None
                paths = g.get("paths")
                if not isinstance(paths, list) or not paths or any(len(p) < 2 for p in paths):
                    raise ValueError("street observation requires nonempty source paths")
                streets.append({**common, "road_id": _text(a.get("Road_ID")) or None,
                                "condition": CONDITIONS.get(condition, "unknown"), "source_condition": condition,
                                "paths": [[_position(p) for p in line] for line in paths]})
        sources.append({"id": kind, "label": "UN-Habitat neighborhood survey",
                        "url": url, "sha256": digest, "retrieved_utc": source.get("retrieved_utc"),
                        "reuse_license": record.get("reuse_license")})
    observations = points + streets
    return {"status": "available", "mode": "survey_context", "simulation_ready": False,
            "audit_date": handoff.get("audit_date"), "points": points, "streets": streets,
            "sources": sources, "counts": {"ponding_points": len(points), "street_records": len(streets),
                                           "conditions": dict(Counter(r["condition"] for r in streets))},
            "neighborhoods": sorted({r["neighborhood"] for r in observations if r["neighborhood"]}),
            "survey_years": sorted({r["survey_year"] for r in streets if r["survey_year"]}),
            "model_inputs": {k: "unavailable" if v is None else "requires_review"
                             for k,v in handoff.get("model_inputs", {}).items()},
            "limitations": ["Historical survey context, not current drain condition or a connected hydraulic network.",
                            "Ponding points have no dated event, measured depth or verified elevation.",
                            "Street categories do not locate individual drains or establish pipe capacity.",
                            "Survey coverage is partial and includes neighborhoods outside municipal Beirut.",
                            "Reuse license is unverified; this configuration is for local research."]}
