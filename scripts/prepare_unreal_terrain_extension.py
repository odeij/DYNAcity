"""Extend visual terrain with attributed Copernicus ground estimates; preserve source samples."""
import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt

from dynacity.terrain import GroundModel, fetch_ground_model
from dynacity.beirut001_pack import Beirut001Pack
from dynacity.engine_city_surface import terrain_mesh
from dynacity.engine_asset import export_glb
from dynacity.engine_street_assets import _colour_glb


def merge_ground(primary, fallback, bounds):
    if primary.crs != fallback.crs: raise ValueError('Terrain coordinate systems differ')
    cell = primary.cell_m
    west,south,east,north = bounds
    c0 = min(0, math.floor((west-primary.x0)/cell)-2)
    c1 = max(primary.ground.shape[1], math.ceil((east-primary.x0)/cell)+2)
    r0 = min(0, math.floor((primary.y0-north)/cell)-2)
    r1 = max(primary.ground.shape[0], math.ceil((primary.y0-south)/cell)+2)
    xx,yy = np.meshgrid(primary.x0+(np.arange(c0,c1)+.5)*cell,
                         primary.y0-(np.arange(r0,r1)+.5)*cell)
    base = np.full(xx.shape,np.nan)
    base[-r0:-r0+primary.ground.shape[0],-c0:-c0+primary.ground.shape[1]] = primary.ground
    estimate = fallback.sample(xx.ravel(),yy.ravel()).reshape(xx.shape)
    known = np.isfinite(base)
    overlap = known & np.isfinite(estimate)
    if overlap.sum()<100: raise ValueError('Insufficient elevation overlap to align the extension')
    residual = base[overlap]-estimate[overlap]
    offset = float(np.median(residual))
    if abs(offset)>20: raise ValueError('Elevation alignment differs by more than 20 m; inspect sources first')
    estimate += offset
    # Fade local edge residuals into the coarse estimate to join retained source edges.
    distances,nearest = distance_transform_edt(~overlap,sampling=cell,return_indices=True)
    corrections = np.where(overlap,base-estimate,0)[tuple(nearest)]
    estimate += corrections*np.exp(-distances/60)
    merged = np.where(known,base,estimate)
    source = primary.source+' Extension only: '+fallback.source+' Approximate filtered surface model; alignment inferred.'
    ground = GroundModel(primary.crs,primary.x0+c0*cell,primary.y0-r0*cell,cell,merged,source)
    original_cells = known[:-1,:-1]&known[1:,:-1]&known[:-1,1:]&known[1:,1:]
    valid = np.isfinite(merged)
    valid_cells = valid[:-1,:-1]&valid[1:,:-1]&valid[:-1,1:]&valid[1:,1:]
    return ground, original_cells, {'source_samples_preserved':int(known.sum()),
        'estimated_samples_added':int((~known&valid).sum()), 'extension_cells':int((valid_cells&~original_cells).sum()),
        'overlap_samples':int(overlap.sum()),'median_alignment_offset_m':offset,
        'overlap_absolute_residual_p90_m':float(np.percentile(abs(residual-offset),90)),
        'native_dem_resolution_m_approx':30,'resampled_grid_m':cell,
        'vertical_reference':'Copernicus EGM2008 aligned to undeclared source model datum; unverified',
        'limits':'Visual terrain estimate only. Unknown sea/nodata stays absent. No surveyed ground accuracy inferred.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pack',required=True);p.add_argument('--city',required=True)
    p.add_argument('--bbox',nargs=4,type=float,required=True);p.add_argument('--output',required=True)
    args=p.parse_args();output=Path(args.output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]): p.error('Keep terrain assets outside Git')
    output.mkdir(parents=True,exist_ok=True)
    project=Transformer.from_crs(4326,32636,always_xy=True)
    west,south,east,north=args.bbox
    corners=[project.transform(lon,lat) for lon in (west,east) for lat in (south,north)]
    fallback_path=output/'copernicus-visual-ground.npz'
    if not fallback_path.exists():
        snapshot=SimpleNamespace(crs='EPSG:32636',objects=[SimpleNamespace(centroid_x_m=x,centroid_y_m=y) for x,y in corners])
        fallback=fetch_ground_model(snapshot,cell_m=10,margin_m=300)
        fallback.save(fallback_path)
    else: fallback=GroundModel.load(fallback_path)
    primary=Beirut001Pack.load(args.pack).ground
    bounds=[min(x for x,y in corners)-150,min(y for x,y in corners)-150,
            max(x for x,y in corners)+150,max(y for x,y in corners)+150]
    merged,original_cells,report=merge_ground(primary,fallback,bounds)
    merged.save(output/'extended-visual-ground.npz')
    city=json.loads(Path(args.city).read_text(encoding='utf-8'));origin=city['origin_utm_m']
    mesh=terrain_mesh(merged,origin)
    # terrain_mesh emits each valid cell's two faces in row-major order.
    valid=np.isfinite(merged.ground)
    good=valid[:-1,:-1]&valid[1:,:-1]&valid[:-1,1:]&valid[1:,1:]
    keep=np.repeat(~original_cells[good],2)
    mesh['triangles']=np.asarray(mesh['triangles'])[keep].tolist()
    mesh['id']='visual-terrain-extension';mesh['metadata'].update(report)
    path=output/'TerrainExtension.glb'
    export_glb({'coordinates':{'origin_utm_m':origin},'meshes':[mesh], 'trees':[], 'roads':[],
                'bounds_lonlat':args.bbox,'limitations':[report['limits']]},path,include_calibration=False)
    _colour_glb(path,(.49,.48,.43))
    report.update(ground=str(output/'extended-visual-ground.npz'),asset=str(path),triangles=len(mesh['triangles']),
                  original_pack=str(Path(args.pack).resolve()),fallback=str(fallback_path),origin_utm_m=origin)
    (output/'terrain-extension.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
