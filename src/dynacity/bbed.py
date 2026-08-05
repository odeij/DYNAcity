"""Privacy-minimizing client for the public BBED ArcGIS feature layer.

Fetches building records from the Beirut Built Environment Database (Beirut
Urban Lab, AUB) through its public ArcGIS REST endpoint.

"Privacy-minimizing" is enforced, not aspirational: `BBED_FIELDS` is an
allowlist and `fetch_features` raises on any request outside it, so owner names,
contacts, and other occupant-identifying attributes cannot be pulled even by
mistake. The layer exposes more than this module will ask for.

Downloads are paired with a `snapshot_manifest` recording source, timestamp,
field list, content hash, and the ODbL attribution the licence requires — a
fetched extract stays traceable and correctly credited.
"""

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


# The privacy allowlist. Every field here is structural (identity, geometry,
# use, status history) — none identifies an owner or occupant. This tuple is the
# single point of control: `fetch_features` validates against it, and widening
# it is a privacy decision, not a configuration tweak.
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
    """Return stable composite IDs across full-layer and spatial BBED queries.

    Neither available identifier works alone. `BULBuildingID` is the meaningful
    building reference but is not unique — the same id repeats across records —
    while ArcGIS `OBJECTID` is unique but is a row identifier that carries no
    domain meaning. Composing them (`BULBuildingID:x|OBJECTID:y`) yields an id
    that is both unique and traceable.

    Crucially the composite is *stable across query subsets*: pulling a spatial
    bounding box rather than the full layer returns the same ids for the same
    buildings, which is what lets a morphology table extracted from one extent
    join to a panel built from another.

    Records missing the preferred field fall back to `OBJECTID:` alone rather
    than being dropped. Raises if uniqueness still fails — that indicates the
    source layer violated its own key, and continuing would corrupt every
    downstream join.
    """

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
        # The privacy gate. Checked before any network call, so a disallowed
        # field can never reach the server — not even to be discarded locally.
        disallowed = sorted(set(requested) - set(BBED_FIELDS))
        if disallowed:
            raise ValueError(f"fields are not in the privacy allowlist: {disallowed}")

        features: list[dict] = []
        offset = 0
        # ArcGIS caps rows per response, so paginate until a short page arrives.
        # Ordering by OBJECTID makes the paging deterministic — without a stable
        # sort the server may repeat or skip records between pages.
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
        """Describe a fetched extract so it stays reproducible and attributable.

        The hash is taken over a canonical serialisation (sorted keys, no
        incidental whitespace) so the same features hash identically regardless
        of how the JSON was formatted — it identifies content, not bytes on
        disk. The licence string is not decorative: BBED is ODbL, and
        redistribution requires this attribution.
        """

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
