"""Key buildings for the 3D map: the public services people go to, from OSM and official lists.

Health, education, safety (police, fire, ambulance), government services
(municipality, courts, post, government offices), welfare and other public
buildings. Display only: they never feed the transition model or the traffic
demand.
OpenStreetMap gives the currently mapped set (ODbL). The Ministry of Education's
public-school list (May 2017, CC0 on HDX) confirms OSM schools within
`OFFICIAL_MATCH_M` and adds the ones OSM lacks. Neither source says whether a
facility still operates.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from .traffic import OVERPASS_URL, _overpass

SCHEMA = "dynacity-facilities/1"

# kind -> (group, label). OSM tags map onto these in `facility_kind`.
KINDS: dict[str, tuple[str, str]] = {
    "hospital": ("health", "Hospital"),
    "clinic": ("health", "Clinic or doctor"),
    "pharmacy": ("health", "Pharmacy"),
    "university": ("education", "University or college"),
    "school": ("education", "School"),
    "kindergarten": ("education", "Kindergarten"),
    "police": ("safety", "Police station"),
    "fire_station": ("safety", "Fire or civil defence station"),
    "ambulance": ("safety", "Ambulance station"),
    "townhall": ("government", "Municipality or town hall"),
    "courthouse": ("government", "Courthouse"),
    "post_office": ("government", "Post office"),
    "government_office": ("government", "Government office"),
    "social_facility": ("welfare", "Social or welfare service"),
    "childcare": ("welfare", "Childcare"),
    "community_centre": ("welfare", "Community centre"),
    "library": ("public", "Library"),
    "public_building": ("public", "Other public building"),
}
_AMENITY = {"hospital": "hospital", "clinic": "clinic", "doctors": "clinic", "dentist": "clinic",
            "pharmacy": "pharmacy", "school": "school", "kindergarten": "kindergarten",
            "university": "university", "college": "university", "police": "police",
            "fire_station": "fire_station", "townhall": "townhall", "courthouse": "courthouse",
            "post_office": "post_office", "social_facility": "social_facility", "nursing_home": "social_facility",
            "childcare": "childcare", "community_centre": "community_centre", "social_centre": "community_centre",
            "library": "library"}
_HEALTHCARE = {"hospital": "hospital", "clinic": "clinic", "centre": "clinic", "doctor": "clinic",
               "dentist": "clinic", "pharmacy": "pharmacy"}
# Building types that say only "public" or "government"; a more specific tag wins.
_BUILDING = {"government": "government_office", "public": "public_building", "civic": "public_building"}
# OSM tags worth showing on hover; contact details and the rest stay in the source extract.
KEPT_TAGS = ("operator", "operator:type", "government", "social_facility", "social_facility:for", "emergency",
             "beds", "healthcare:speciality", "isced:level", "school:gender", "wheelchair", "opening_hours",
             "website")

# An official school is taken to be an OSM school only when it is this close and
# the two names share a distinctive word: public and private schools often stand
# side by side, so distance alone pairs different schools.
OFFICIAL_MATCH_M = 80.0
_GENERIC_WORDS = {"school", "public", "mixed", "secondary", "intermediate", "elementary", "primary", "official",
                  "girls", "boys", "first", "second", "third", "fourth", "high", "college", "ecole", "lycee",
                  "with", "beirut", "lebanon", "lebanese", "saint", "sainte"}
# The same in Arabic, after `_arabic` folds letter variants and drops the article.
_GENERIC_ARABIC = {"مدرسه", "مدارس", "رسميه", "حكوميه", "مختلطه", "ثانويه", "متوسطه", "تكميليه", "ابتدائيه",
                   "بنات", "للبنات", "صبيان", "للصبيان", "اولي", "ثانيه", "ثالثه", "رابعه", "جديده", "بيروت",
                   "لبنانيه", "روضه"}
OFFICIAL_SOURCE = "MEHE public and UNRWA schools, May 2017 (HDX, CC0)"


def facility_kind(tags: dict[str, str]) -> str | None:
    if tags.get("amenity") in _AMENITY:
        return _AMENITY[tags["amenity"]]
    if tags.get("healthcare") in _HEALTHCARE:
        return _HEALTHCARE[tags["healthcare"]]
    if tags.get("emergency") == "ambulance_station":
        return "ambulance"
    if tags.get("office") == "government":
        return "government_office"
    return _BUILDING.get(tags.get("building", ""))


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    box = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    amenities = "|".join(sorted(_AMENITY))
    healthcare = "|".join(sorted(_HEALTHCARE))
    buildings = "|".join(sorted(_BUILDING))
    return (f'[out:json][timeout:180];(nwr["amenity"~"^({amenities})$"]{box};'
            f'nwr["healthcare"~"^({healthcare})$"]{box};nwr["emergency"="ambulance_station"]{box};'
            f'nwr["office"="government"]{box};wr["building"~"^({buildings})$"]{box};);out center tags;')


def fetch_facilities(bbox: tuple[float, float, float, float], *, url: str = OVERPASS_URL) -> dict:
    """Mapped key buildings in the box, as Overpass JSON with way/relation centres."""

    return _overpass(overpass_query(bbox), url)


def _point(element: dict) -> list[float] | None:
    if element.get("type") == "node":
        lon, lat = element.get("lon"), element.get("lat")
    elif element.get("center"):
        lon, lat = element["center"].get("lon"), element["center"].get("lat")
    elif element.get("geometry"):
        points = [(p["lon"], p["lat"]) for p in element["geometry"] if p]
        lon, lat = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)) if points else (None, None)
    else:
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lon, lat)):
        return None
    return [round(float(lon), 7), round(float(lat), 7)]


def _feature(identifier: str, point: list[float], kind: str, name: str | None, source: str,
             **extra: Any) -> dict:
    group, label = KINDS[kind]
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": point},
            "properties": {"id": identifier, "facility": kind, "group": group, "name": name or label,
                           "named": bool(name), "source": source, **extra}}


def facilities_from_osm(osm: dict) -> list[dict]:
    """One point per mapped facility: nodes as they are, ways and relations at their centre."""

    as_of = osm.get("osm3s", {}).get("timestamp_osm_base")
    out = []
    for element in osm.get("elements", []):
        tags = element.get("tags") or {}
        kind = facility_kind(tags)
        point = _point(element) if kind else None
        if point is None:
            continue
        name = tags.get("name:en") or tags.get("name") or tags.get("name:ar")
        out.append(_feature(f"{element['type']}/{element['id']}", point, kind, name, "OpenStreetMap",
                            name_ar=tags.get("name:ar") or (tags.get("name") if tags.get("name") != name else None),
                            tags={k: tags[k] for k in KEPT_TAGS if k in tags}, as_of=as_of))
    return out


def read_official_schools(path: str | Path) -> list[dict]:
    """Rows of the MEHE school list (.xlsx needs openpyxl, or a .csv of the same columns)."""

    import pandas as pd

    path = Path(path)
    frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    columns = {str(c).strip().lower(): c for c in frame.columns}

    def column(*names: str) -> str:
        for name in names:
            if name in columns:
                return columns[name]
        raise ValueError(f"{path.name}: no column among {names}; found {list(frame.columns)}")

    code, name, lat, lon = (column("cerd"), column("school name"), column("latitude", "lat"),
                            column("longitude", "lon", "long"))
    arabic = columns.get("school name - arabic")
    return [{"code": str(r[code]), "name": r[name], "name_ar": r[arabic] if arabic else None,
             "lat": r[lat], "lon": r[lon]} for r in frame.to_dict("records")]


def _arabic(word: str) -> str:
    word = re.sub("[أإآ]", "ا", word).replace("ة", "ه").replace("ى", "ي")
    return word[2:] if word.startswith("ال") and len(word) > 4 else word


def _words(*names: Any) -> set[str]:
    """Distinctive Latin and Arabic words of the names, for telling schools apart."""

    out: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        latin = re.findall(r"[a-z]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())
        out |= {w for w in latin if len(w) >= 4 and w not in _GENERIC_WORDS}
        arabic = (_arabic(w) for w in re.findall(r"[ء-ي]+", name))
        out |= {w for w in arabic if len(w) >= 3 and w not in _GENERIC_ARABIC}
    return out


def _metres(a: list[float], b: list[float]) -> float:
    x = (a[0] - b[0]) * 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(x, (a[1] - b[1]) * 111_320)


def merge_official_schools(features: list[dict], rows: list[dict], *, match_m: float = OFFICIAL_MATCH_M) -> tuple[int, int]:
    """Mark OSM schools that are on the official list; add official schools OSM lacks.

    A pair needs both `match_m` and a shared distinctive name word; otherwise
    both are kept. Returns (confirmed, added). Rows without finite coordinates
    are skipped.
    """

    schools = [f for f in features if f["properties"]["facility"] in ("school", "kindergarten")]
    confirmed = added = 0
    for row in rows:
        try:
            point = [round(float(row["lon"]), 7), round(float(row["lat"]), 7)]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in point):
            continue
        official = {"list": OFFICIAL_SOURCE, "code": row["code"], "name": row["name"]}
        words = _words(row["name"], row.get("name_ar"))
        near = [f for f in schools if "official" not in f["properties"] and f["properties"]["named"]
                and _metres(f["geometry"]["coordinates"], point) <= match_m
                and words & _words(f["properties"]["name"], f["properties"].get("name_ar"))]
        if near:
            min(near, key=lambda f: _metres(f["geometry"]["coordinates"], point))["properties"]["official"] = official
            confirmed += 1
            continue
        name = row["name"] if isinstance(row["name"], str) and row["name"].strip() else None
        features.append(_feature(f"mehe/{row['code']}", point, "school", name, OFFICIAL_SOURCE,
                                 name_ar=row.get("name_ar") if isinstance(row.get("name_ar"), str) else None,
                                 official=official))
        added += 1
    return confirmed, added


def build_facilities(osm: dict, official_schools: list[dict] | None = None, *,
                     bbox: tuple[float, float, float, float] | None = None) -> dict:
    features = facilities_from_osm(osm)
    sources = [{"name": "OpenStreetMap contributors", "licence": "ODbL",
                "as_of": osm.get("osm3s", {}).get("timestamp_osm_base")}]
    merged = None
    if official_schools is not None:
        if bbox is not None:
            west, south, east, north = bbox
            official_schools = [r for r in official_schools
                                if _inside(r.get("lon"), r.get("lat"), west, south, east, north)]
        confirmed, added = merge_official_schools(features, official_schools)
        merged = {"confirmed": confirmed, "added": added, "match_m": OFFICIAL_MATCH_M,
                  "rule": "within match_m and sharing a distinctive name word"}
        sources.append({"name": OFFICIAL_SOURCE, "licence": "CC0", "as_of": "2017-05-02"})
    return {"schema": SCHEMA, "sources": sources, "official_schools": merged,
            "counts": dict(Counter(f["properties"]["facility"] for f in features)),
            "limits": "Mapped or listed locations only; current operation, capacity and opening hours are not verified. "
                      "Missing mapping means unknown, not absent.",
            "features": features}


def _inside(lon: Any, lat: Any, west: float, south: float, east: float, north: float) -> bool:
    try:
        return west <= float(lon) <= east and south <= float(lat) <= north
    except (TypeError, ValueError):
        return False


def crop_facilities(collection: dict, bounds: tuple[float, float, float, float]) -> dict:
    west, south, east, north = bounds
    features = [f for f in collection["features"] if _inside(*f["geometry"]["coordinates"], west, south, east, north)]
    return {**collection, "features": features,
            "counts": dict(Counter(f["properties"]["facility"] for f in features))}
