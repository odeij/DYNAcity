"""Privacy-minimizing client for the public BBED ArcGIS feature layer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import DEFAULT_BBED_LAYER_URL


BBED_FIELDS: tuple[str, ...] = (
    "OBJECTID",
    "BULBuildingID",
    "ParcelID",
    "Sector",
    "Building_Use",
    "NoofFloor",
    "Building_Hight_m",
    "YearCompleted",
    "PermitYear",
    "Status2018",
    "Status2022",
    "F5__Current_status",
    "Shape__Area",
    "Shape__Length",
)


def resolved_object_ids(
    features: Iterable[dict], *, preferred_field: str = "BULBuildingID"
) -> list[str]:
    """Return stable composite IDs across full-layer and spatial BBED queries."""
    feature_list = list(features)
    resolved: list[str] = []
    for feature in feature_list:
        properties = feature.get("properties", {})
        arcgis_id = properties.get("OBJECTID")
        if arcgis_id is None or not str(arcgis_id).strip():
            raise ValueError("BBED feature is missing the required OBJECTID")
        preferred = properties.get(preferred_field)
        if preferred is None or not str(preferred).strip():
            resolved.append(f"OBJECTID:{arcgis_id}")
        else:
            resolved.append(f"{preferred_field}:{preferred}|OBJECTID:{arcgis_id}")
    if len(set(resolved)) != len(resolved):
        raise ValueError("BBED OBJECTID values do not provide unique object identities")
    return resolved


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "DynaCITY-AUB/0.1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed public source
        return response.read()


@dataclass
class BBEDClient:
    layer_url: str = DEFAULT_BBED_LAYER_URL
    transport: Callable[[str], bytes] = _download
    page_size: int = 2000

    def metadata(self) -> dict:
        payload = self.transport(f"{self.layer_url}?f=pjson")
        return json.loads(payload)

    def fetch_features(
        self,
        *,
        where: str = "1=1",
        bbox: tuple[float, float, float, float] | None = None,
        fields: Iterable[str] = BBED_FIELDS,
    ) -> dict:
        requested = tuple(fields)
        disallowed = sorted(set(requested) - set(BBED_FIELDS))
        if disallowed:
            raise ValueError(f"fields are not in the privacy allowlist: {disallowed}")

        features: list[dict] = []
        offset = 0
        while True:
            params: dict[str, str | int] = {
                "f": "geojson",
                "where": where,
                "outFields": ",".join(requested),
                "returnGeometry": "true",
                "outSR": 32636,
                "resultOffset": offset,
                "resultRecordCount": self.page_size,
                "orderByFields": "OBJECTID",
            }
            if bbox is not None:
                params.update(
                    {
                        "geometry": ",".join(str(value) for value in bbox),
                        "geometryType": "esriGeometryEnvelope",
                        "inSR": 32636,
                        "spatialRel": "esriSpatialRelIntersects",
                    }
                )
            url = f"{self.layer_url}/query?{urlencode(params)}"
            page = json.loads(self.transport(url))
            if "error" in page:
                raise RuntimeError(f"BBED query failed: {page['error']}")
            page_features = page.get("features", [])
            features.extend(page_features)
            if len(page_features) < self.page_size:
                break
            offset += len(page_features)

        return {
            "type": "FeatureCollection",
            "name": "BBED_forecasting_allowlist",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:EPSG::32636"},
            },
            "features": features,
        }

    def snapshot_manifest(self, collection: dict) -> dict:
        canonical = json.dumps(collection, sort_keys=True, separators=(",", ":"))
        return {
            "source": self.layer_url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "crs": "EPSG:32636",
            "fields": list(BBED_FIELDS),
            "feature_count": len(collection.get("features", [])),
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "license": "ODbL; attribute Beirut Built Environment Database, Beirut Urban Lab (AUB)",
        }


def write_snapshot(
    collection: dict,
    manifest: dict,
    output_path: str | Path,
) -> tuple[Path, Path]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output, manifest_path
