"""Offline place search and map-link parsing; standard library only, no Unreal import."""
import math
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlsplit

PAIR = r'(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)'


def coordinates(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid latitude or longitude.')
    return {'lat': lat, 'lon': lon, 'name': f'{lat:.6f}, {lon:.6f}', 'kind': 'coordinates'}


def google_host(host):
    # Explicit trusted hosts only; used for parsing and optional short-link redirects.
    return host in {'google.com', 'www.google.com', 'maps.google.com', 'google.com.lb',
                    'www.google.com.lb', 'maps.google.com.lb', 'maps.app.goo.gl', 'goo.gl'}


def parse_location(text):
    """Return coordinates, a local OSM ID, a short link, or a place-name query.

    Google place pins take priority over viewport centres. Never scrape HTML.
    """
    text = text.strip()
    if not text or len(text) > 8192:
        raise ValueError('Enter a place name, map link, or latitude, longitude.')
    direct = re.fullmatch(PAIR, text)
    if direct:
        return coordinates(*direct.groups())
    if text.startswith('geo:'):
        direct = re.match(PAIR, text[4:])
        if not direct: raise ValueError('Invalid geo coordinates.')
        return coordinates(*direct.groups())
    if re.match(r'^(www\.|maps\.|openstreetmap\.)', text):
        text = 'https://' + text
    if '://' not in text:
        return {'query': text}
    url = urlsplit(text)
    host = (url.hostname or '').lower()
    if url.scheme != 'https' or url.port not in (None, 443) or url.username or url.password:
        raise ValueError('Use an HTTPS Google Maps or OpenStreetMap link.')
    osm = host in {'openstreetmap.org', 'www.openstreetmap.org'}
    google = google_host(host)
    if not osm and not google:
        raise ValueError('This link is not a supported Google Maps or OpenStreetMap URL.')
    if host == 'maps.app.goo.gl' or (host == 'goo.gl' and url.path.startswith('/maps/')):
        return {'short_link': text}
    query = parse_qs(url.query)
    if osm:
        if 'mlat' in query and 'mlon' in query:
            return coordinates(query['mlat'][0], query['mlon'][0])
        match = re.search(r'(?:^|&)map=\d+(?:\.\d+)?/(-?[\d.]+)/(-?[\d.]+)', url.fragment)
        # Object links identify a feature, not necessarily the map centre.
        obj = re.fullmatch(r'/(node|way|relation)/(\d+)/?', url.path)
        if obj: return {'osm_id': '/'.join(obj.groups())}
        if match: return coordinates(*match.groups())
        if query.get('query'): return {'query': query['query'][0]}
    else:
        data = unquote(url.path + '?' + url.query)
        # Selected place coordinates are distinct from the /@ camera centre.
        pin = re.search(r'!3d(-?[\d.]+)!4d(-?[\d.]+)', data)
        if pin: return coordinates(*pin.groups())
        for key in ('destination', 'query', 'q', 'center', 'll'):
            if key not in query: continue
            match = re.fullmatch(PAIR, query[key][0])
            if match: return coordinates(*match.groups())
            if key in ('destination', 'query', 'q'):
                return {'query': query[key][0]}
        match = re.search(r'@(-?[\d.]+),(-?[\d.]+)', data)
        if match: return coordinates(*match.groups())
        place = re.search(r'/maps/(?:place|search)/([^/]+)', url.path)
        if place: return {'query': unquote(place[1]).replace('+', ' ')}
    raise ValueError('No usable location in this link. Paste a pin link or latitude, longitude.')


def normalize(text):
    text = unicodedata.normalize('NFKD', text).casefold()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text
                            if not unicodedata.combining(c)).split())


def search(index, parsed, limit=12):
    if 'lat' in parsed: return [parsed]
    if 'osm_id' in parsed:
        return [r for r in index['places'] if r['id'] == parsed['osm_id']][:limit]
    query = normalize(parsed['query'])
    if not query: return []
    found = []
    for row in index['places']:
        names = [normalize(n) for n in [row['name'], *row.get('aliases', [])]]
        ranks = [0 if n == query else 1 if n.startswith(query) else 2
                 for n in names if all(word in n for word in query.split())]
        if ranks: found.append((min(ranks), len(row['name']), row['id'], row))
    return [item[-1] for item in sorted(found, key=lambda item: item[:3])[:limit]]


def nearest_roads(index, local, max_distance=200, limit=12):
    """Project onto source segments, retaining Z and heading; never snap unbounded distances."""
    x, y = local[:2]
    found = []
    for road in index['roads']:
        west, south, east, north = road['bounds']
        if not west-max_distance <= x <= east+max_distance or not south-max_distance <= y <= north+max_distance:
            continue
        best = None
        for a, b in zip(road['path'], road['path'][1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            length2 = dx*dx + dy*dy
            if length2 < .01: continue
            t = max(0, min(1, ((x-a[0])*dx+(y-a[1])*dy)/length2))
            point = [a[j]+t*(b[j]-a[j]) for j in range(3)]
            distance = math.hypot(point[0]-x, point[1]-y)
            if distance <= max_distance and (best is None or distance < best['distance_m']):
                best = {'position': point, 'distance_m': distance,
                        'direction': [dx, dy], 'road_id': road['id'], 'road_name': road.get('name', '')}
        if best: found.append(best)
    return sorted(found, key=lambda r: (r['distance_m'], str(r['road_id'])))[:limit]


def lonlat_to_utm(lon: float, lat: float, zone: int = 36, north: bool = True) -> tuple[float, float]:
    """Forward transverse Mercator on WGS84 (Snyder 1987, eqs. 8-9 to 8-10).

    The inverse of `utm_to_lonlat`, for turning a point dropped on the map
    back into snapshot metres.
    """

    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi = math.radians(lat)
    sin_phi, cos_phi, tan_phi = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1 - e2 * sin_phi**2)
    t, c = tan_phi**2, ep2 * cos_phi**2
    big_a = cos_phi * math.radians(lon - (zone * 6 - 183))
    m = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
        - (35 * e2**3 / 3072) * math.sin(6 * phi)
    )
    x = k0 * n * (
        big_a
        + (1 - t + c) * big_a**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * big_a**5 / 120
    ) + 500000.0
    y = k0 * (m + n * tan_phi * (
        big_a**2 / 2
        + (5 - t + 9 * c + 4 * c**2) * big_a**4 / 24
        + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * big_a**6 / 720
    ))
    return x, y if north else y + 10000000.0



def utm_to_lonlat(x: float, y: float, zone: int = 36, north: bool = True) -> tuple[float, float]:
    """Inverse transverse Mercator on WGS84 (Snyder 1987, eqs. 8-12 to 8-25).

    Sub-metre across a UTM zone, which is ample for drawing footprints on a
    basemap. A metric offset from the centroid would not do: Beirut sits 2.5°
    east of zone 36's central meridian, where grid north is rotated ~1.4° from
    true north, enough to slide footprints off their streets.
    """

    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x -= 500000.0
    if not north:
        y -= 10000000.0
    mu = (y / k0) / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * sin1**2)
    r1 = a * (1 - e2) / (1 - e2 * sin1**2) ** 1.5
    t1, c1 = tan1**2, ep2 * cos1**2
    d = x / (n1 * k0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / cos1
    return math.degrees(lon) + (zone * 6 - 183), math.degrees(lat)

