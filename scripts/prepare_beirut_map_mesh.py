"""Convert the private overlay buffers to a compact georeferenced map mesh."""
import argparse,json
from pathlib import Path
import numpy as np
from pyproj import Transformer
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True,type=Path);args=p.parse_args();root=args.directory
meta=json.loads((root/'data.json').read_text());v=np.fromfile(root/'positions.bin',dtype='<f4').reshape(-1,3);idx=np.fromfile(root/'indices.bin',dtype='<u4').reshape(-1,3)
r=np.asarray(meta['registration']['rotation_matrix']);t=np.asarray(meta['registration']['translation_xy_m']);xy=np.column_stack((v[:,0],-v[:,2]))@r.T+t
lon,lat=Transformer.from_crs(32636,4326,always_xy=True).transform(xy[:,0],xy[:,1]);wx=(lon+180)/360*512;wy=(.5+np.log(np.tan(np.pi/4+np.radians(lat)/2))/(2*np.pi))*512
origin=[float(np.median(wx)),float(np.median(wy)),0.];centerlat=float(np.median(lat));scale=512/(40075016.68557849*np.cos(np.radians(centerlat)))
positions=np.column_stack((wx-origin[0],wy-origin[1],v[:,1])).astype('<f4');picking=np.zeros_like(positions);offset=0
for i,obj in enumerate(meta['objects']):
 count=obj['vertices'];positions[offset:offset+count,2]-=obj['bounds'][2]
 code=i+1;picking[offset:offset+count]=[(code&255)/255,((code>>8)&255)/255,0]
 c=np.mean(xy[offset:offset+count],axis=0);clon,clat=Transformer.from_crs(32636,4326,always_xy=True).transform(*c);obj['map_center']=[clon,clat];offset+=count
assert offset==len(v)
# Normals in common-world coordinates; geometry and triangle winding preserved.
normals=np.zeros_like(positions);metric=positions.copy();metric[:,:2]/=scale;face=np.cross(metric[idx[:,1]]-metric[idx[:,0]],metric[idx[:,2]]-metric[idx[:,0]])
for j in range(3):np.add.at(normals,idx[:,j],face)
normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-20)
for name,a in [('map-positions',positions),('map-normals',normals),('map-picking',picking)]:a.astype('<f4').tofile(root/(name+'.bin'))
meta['map_origin_common']=origin;meta['display_vertical_note']='Each model base shifted to map ground for display; source Z and unknown datum retained in information card.'
(root/'data.json').write_text(json.dumps(meta,separators=(',',':')));print('Prepared map mesh',len(v),'vertices',len(idx),'triangles')
