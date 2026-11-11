"""Estimate weekday PM-peak pedestrian volumes per street segment in Beirut with UNA.

Run in an environment with the MIT-licensed `urban_network_analysis` package (City Form Lab), on
layers from `scripts/prepare_pedestrian_inputs.py`. Method after Sevtsuk et al. (2024), Cities 149,
104927: four UNA flows combined with that paper's PM-peak OLS coefficients (Table 2):

  ped/h = 61.62 + 3.49 F(amenities->amenities)
                + 0.08 F(jobs->homes, high-amenity-access origins)
                + 0.08 F(parks->amenities, high-amenity-access origins)
                + 0.02 F(homes->places of worship, low-amenity-access origins)
                + 9.77 (mean of the Mon-Thu day effects)

High-amenity-access origins have exponential-gravity access to amenities above the 90th percentile.
The paper's own inputs, radii and decay parameters were not published (supplementary material), so
this run uses UNA defaults close to its description (800 m radius, exponential decay). Our homes and
jobs layers are proxies, so the coefficients do not transfer exactly: treat results as a modelled
pattern, not observations, until checked against the 2021-22 counts.
"""
import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import urban_network_analysis

COEFFICIENTS = {'amenities_amenities': 3.49, 'jobs_homes': 0.08, 'parks_amenities': 0.08, 'homes_worship': 0.02}
INTERCEPT = 61.62 + np.mean([0.0, 13.02, 7.8, 18.27])
RADIUS_M = 800
BETA = 0.002


def project(folder, network, origins, destinations, origin_weight, destination_weight, out, name):
    una = urban_network_analysis.UNA()
    s = una.settings
    s.data_folder = str(folder)
    s.network_file, s.origins_file, s.destinations_file = network, origins, destinations
    s.origin_weight_column, s.destination_weight_column = origin_weight, destination_weight
    s.search_radius, s.gravity_beta = RADIUS_M, BETA
    s.output_folder, s.output_file_name = str(out), name
    s.output_feather, s.output_geojson = False, True
    return una


def high_access(folder, out, layer, weight):
    """Split an origin layer by gravity access to amenities (90th percentile)."""
    una = project(folder, 'network.geojson', layer, 'amenities.geojson', weight, 'weight', out, 'access_' + layer[:-8])
    s = una.settings
    s.calculate_reach = s.calculate_logistic_gravity = s.calculate_knn_access = False
    s.calculate_exponential_gravity = True
    una.RunAccessibility()
    result = sorted(Path(out).rglob('access_' + layer[:-8] + '*.geojson'))[-1]
    gdf = gpd.read_file(result)
    column = next(c for c in gdf.columns if 'grav' in c.lower() or 'exp' in c.lower())
    cut = gdf[column].quantile(0.9)
    for flag, subset in (('high', gdf[gdf[column] > cut]), ('low', gdf[gdf[column] <= cut])):
        subset.to_file(Path(folder) / f'{layer[:-8]}_{flag}.geojson', driver='GeoJSON')
    return {'column': column, 'p90': float(cut), 'high': int((gdf[column] > cut).sum()), 'total': len(gdf)}


def flow(folder, out, origins, weight, destinations, dest_weight, name):
    una = project(folder, 'network.geojson', origins, destinations, weight, dest_weight, out, name)
    s = una.settings
    s.flow_decay, s.flow_decay_curve, s.flow_decay_method = True, 'exponential', 'gravity_cap'
    s.result_prefix = name + '_'
    una.RunFlow()
    result = sorted(Path(out).rglob(name + '*.geojson'))[-1]
    return gpd.read_file(result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs', required=True, help='Folder written by prepare_pedestrian_inputs.py')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    folder, out = Path(a.inputs), Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    report = {'access': {
        'homes': high_access(folder, out, 'homes.geojson', 'residents_est'),
        'jobs': high_access(folder, out, 'jobs.geojson', 'jobs_proxy'),
        'parks': high_access(folder, out, 'parks.geojson', 'weight')}}
    runs = {
        'amenities_amenities': ('amenities.geojson', 'weight', 'amenities.geojson', 'weight'),
        'jobs_homes': ('jobs_high.geojson', 'jobs_proxy', 'homes.geojson', 'residents_est'),
        'parks_amenities': ('parks_high.geojson', 'weight', 'amenities.geojson', 'weight'),
        'homes_worship': ('homes_low.geojson', 'residents_est', 'worship.geojson', 'weight')}
    edges = None
    for name, (o, ow, d, dw) in runs.items():
        gdf = flow(folder, out, o, ow, d, dw, name)
        column = name + '_flow'
        if edges is None:
            edges = gdf[[c for c in gdf.columns if c == 'geometry' or not c.endswith('_flow')]].copy()
        edges[column] = gdf[column].to_numpy()
    edges['ped_per_hour_pm'] = INTERCEPT + sum(c * edges[n + '_flow'] for n, c in COEFFICIENTS.items())
    edges.to_file(out / 'pedestrian-flows-pm.geojson', driver='GeoJSON')
    v = edges['ped_per_hour_pm']
    report.update(segments=len(edges), intercept=INTERCEPT, coefficients=COEFFICIENTS,
                  ped_per_hour=dict(min=float(v.min()), median=float(v.median()), p90=float(v.quantile(.9)),
                                    max=float(v.max())),
                  status='modelled with proxy inputs; not observed counts')
    (out / 'run-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
