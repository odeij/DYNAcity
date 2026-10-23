"""Infer a rigid XY alignment for the private Beirut 001 overlay pilot.

Centroid correlation and trimmed nearest-pair refinement are exploratory,
not a surveyed datum transformation. All resulting links remain provisional.
"""
import argparse
import rhino3dm as r
import json,numpy as np
from pathlib import Path
from shapely.geometry import shape
from scipy.signal import fftconvolve
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree
parser=argparse.ArgumentParser(description=__doc__)
for flag in ['model','bbed','output']: parser.add_argument('--'+flag,type=Path,required=True)
args=parser.parse_args()
out=args.output;out.mkdir(parents=True,exist_ok=True)
m=r.File3dm.Read(str(args.model))
rows=[]
for o in m.Objects:
 if m.Layers[o.Attributes.LayerIndex].Name!='05_BUILDINGS' or type(o.Geometry).__name__=='Text':continue
 b=o.Geometry.GetBoundingBox();rows.append({'id':str(o.Attributes.Id),'name':o.Attributes.Name,'kind':type(o.Geometry).__name__,'bounds':[b.Min.X,b.Min.Y,b.Min.Z,b.Max.X,b.Max.Y,b.Max.Z]})
(out/'model-objects.json').write_text(json.dumps(rows))
rows=json.load(open(out/'model-objects.json'));a=np.array([[(r['bounds'][0]+r['bounds'][3])/2,(r['bounds'][1]+r['bounds'][4])/2] for r in rows]);b=np.array([[p.centroid.x,p.centroid.y] for p in [shape(f['geometry']) for f in json.load(open(args.bbed))['features'] if f.get('geometry')]])
step=10.;bmin=b.min(0)-20
ib=np.floor((b-bmin)/step).astype(int);sb=ib.max(0)+3;B=np.zeros(sb,dtype=np.float32);np.add.at(B,(ib[:,0],ib[:,1]),1);B=gaussian_filter(np.minimum(B,1),.7)
def evaluate(deg):
 r=np.array([[np.cos(np.radians(deg)),-np.sin(np.radians(deg))],[np.sin(np.radians(deg)),np.cos(np.radians(deg))]])
 xy=a@r.T;amin=xy.min(0)-20;ia=np.floor((xy-amin)/step).astype(int);sa=ia.max(0)+3;A=np.zeros(sa,dtype=np.float32);np.add.at(A,(ia[:,0],ia[:,1]),1);A=gaussian_filter(np.minimum(A,1),.7)
 c=fftconvolve(B,A[::-1,::-1],mode='full');peak=np.array(np.unravel_index(c.argmax(),c.shape));shift=(peak-(sa-1))*step+bmin-amin
 return float(c.max()),deg,shift,r
scores=sorted([evaluate(d) for d in range(-180,180,5)],key=lambda x:x[0],reverse=True)
print('coarse',[(s[0],s[1]) for s in scores[:5]],flush=True)
scores=sorted([evaluate(d) for d in np.arange(scores[0][1]-4,scores[0][1]+4.01,.25)],key=lambda x:x[0],reverse=True)
_,deg,shift,r=scores[0]
# Keep metric scale; fit rigid registration using trimmed BBED-to-model pairs.
for it in range(30):
 xy=a@r.T+shift;tree=cKDTree(xy);dist,ids=tree.query(b);good=dist<12
 aa=a[ids[good]];bb=b[good];ca=aa.mean(0);cb=bb.mean(0);u,_,vt=np.linalg.svd((aa-ca).T@(bb-cb));rr=vt.T@u.T
 if np.linalg.det(rr)<0:break
 tt=cb-ca@rr.T
 if np.linalg.norm(tt-shift)<.001 and np.linalg.norm(rr-r)<1e-6:break
 r,shift=rr,tt
xy=a@r.T+shift;dist,_=cKDTree(xy).query(b)
report={'method':'rigid centroid-pattern registration, inferred not surveyed','rotation_degrees':float(np.degrees(np.arctan2(r[1,0],r[0,0]))),'rotation_matrix':r.tolist(),'translation_xy_m':shift.tolist(),'source_objects':len(a),'bbed_features':len(b),'bbed_to_model_percentiles_m':np.percentile(dist,[10,25,50,75,90]).tolist(),'bbed_within_5m':int((dist<5).sum()),'bbed_within_10m':int((dist<10).sum())};(out/'registration.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
