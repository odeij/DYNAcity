'use strict';
// WGS84 / UTM zone 36 north. Local coordinates are metres east/north of the export origin.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./vendor/proj4/proj4.js'));
  else root.cityMapCoordinates = factory(root.proj4);
})(typeof window !== 'undefined' ? window : globalThis, function (proj4) {
  const projection = proj4('EPSG:4326', '+proj=utm +zone=36 +datum=WGS84 +units=m +no_defs');
  const pair = value => Array.isArray(value) && value.length >= 2 && value.slice(0, 2).every(Number.isFinite);
  function forward(lonlat) {
    if (!pair(lonlat) || lonlat[0] < 30 || lonlat[0] > 36 || lonlat[1] < 0 || lonlat[1] > 84) throw Error('Expected WGS84 longitude/latitude in UTM zone 36N.');
    return projection.forward(lonlat.slice(0, 2));
  }
  function inverse(utm) {
    if (!pair(utm)) throw Error('Expected finite UTM easting/northing.');
    return projection.inverse(utm.slice(0, 2));
  }
  function reference(payload) {
    const ref = payload.map_reference;
    if (!ref || ref.crs !== 'EPSG:32636') throw Error('Waiting for map_reference with CRS EPSG:32636.');
    // Prefer the exact exported UTM origin; lon/lat alone is a supported reference fallback.
    const origin = pair(payload.origin_utm_m) ? payload.origin_utm_m.slice(0, 2) : forward(ref.origin_lonlat);
    if (origin[0] < 100000 || origin[0] > 900000 || origin[1] < 0 || origin[1] > 9400000) throw Error('Invalid UTM36N export origin.');
    return {
      localToLatLng(local) {
        if (!pair(local)) throw Error('Expected finite local east/north metres.');
        const lonlat = inverse([origin[0] + local[0], origin[1] + local[1]]);
        return [lonlat[1], lonlat[0]];
      },
      latLngToLocal(latlng) {
        const utm = forward([latlng.lng, latlng.lat]);
        return {x: utm[0] - origin[0], y: utm[1] - origin[1]};
      }
    };
  }
  return {forward, inverse, reference};
});
