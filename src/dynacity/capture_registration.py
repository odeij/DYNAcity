"""Candidate rigid registrations for existing building captures.

No deformation, guessed georeference, city mutation or automatic acceptance.
Search is constrained to Z-up, metre units and yaw plus translation.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree


def voxel_sample(points, cell=.4):
    points=np.asarray(points,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():
        raise ValueError('Expected finite XYZ points')
    if not math.isfinite(cell) or cell<=0:raise ValueError('Positive voxel size required')
    _,indices=np.unique(np.floor(points/cell).astype(np.int64),axis=0,return_index=True)
    return np.sort(indices)


def top_grid(points, cell=1.):
    points=np.asarray(points,dtype=float);origin=np.floor(points[:,:2].min(axis=0)/cell)*cell
    ij=np.floor((points[:,:2]-origin)/cell).astype(int)
    dimensions=ij.max(axis=0)+1
    grid=np.full((dimensions[1],dimensions[0]),-np.inf)
    np.maximum.at(grid,(ij[:,1],ij[:,0]),points[:,2])
    return grid,origin


def yaw_matrix(degrees):
    angle=math.radians(degrees);c,s=math.cos(angle),math.sin(angle)
    return np.array([[c,-s,0],[s,c,0],[0,0,1.]])


def coarse_candidates(source,target,*,cell=1.,angle_step=5,keep=24):
    """Height-pattern correlation with an independently estimated Z translation.

    Flat rooftops and missing context can produce false matches. Returned
    candidates must undergo independent 3D and identity checks.
    """
    target_grid,target_origin=top_grid(target,cell)
    finite=np.isfinite(target_grid);reference=float(np.median(target_grid[finite]))
    t=np.where(finite,target_grid-reference,0);valid=finite.astype(float)
    rows=[]
    def corr(a,b):return fftconvolve(a,b[::-1,::-1],mode='valid')
    for angle in range(0,360,angle_step):
        rotation=yaw_matrix(angle);rotated=source@rotation.T
        grid,origin=top_grid(rotated,cell)
        if any(a>b for a,b in zip(grid.shape,t.shape)):continue
        mask=np.isfinite(grid).astype(float)
        if mask.sum()<25:continue
        z0=float(np.median(grid[np.isfinite(grid)]));z=np.where(mask,grid-z0,0)
        count=corr(valid,mask);safe=np.maximum(count,1)
        a=corr(t,mask);b=corr(valid,z)
        delta=(a-b)/safe
        mse=np.maximum(0,(corr(t*t,mask)+corr(valid,z*z)-2*corr(t,z))/safe-delta*delta)
        coverage=count/mask.sum()
        score=np.sqrt(mse)+8*(1-coverage)
        score[coverage<.85]=np.inf
        # Keep independent local minima rather than adjacent pixels of one pose.
        for _ in range(3):
            flat=int(np.argmin(score));y,x=np.unravel_index(flat,score.shape)
            if not np.isfinite(score[y,x]):break
            translation=[target_origin[0]+x*cell-origin[0],target_origin[1]+y*cell-origin[1],
                         float(delta[y,x]+reference-z0)]
            rows.append({'yaw_degrees':angle,'translation':translation,'coarse_score':float(score[y,x]),
                         'coarse_coverage':float(coverage[y,x])})
            score[max(0,y-8):y+9,max(0,x-8):x+9]=np.inf
    chosen=[]
    for row in sorted(rows,key=lambda r:r['coarse_score']):
        if any(np.linalg.norm(np.asarray(row['translation'])[:2]-np.asarray(r['translation'])[:2])<5 and
               abs((row['yaw_degrees']-r['yaw_degrees']+180)%360-180)<20 for r in chosen):continue
        chosen.append(row)
        if len(chosen)>=keep:break
    return chosen


def refine(source,target,seed,*,iterations=100):
    """Trimmed yaw/translation ICP, with separate fitting and held-out samples."""
    source=np.asarray(source,dtype=float);target=np.asarray(target,dtype=float)
    rng=np.random.default_rng(137)
    order=rng.permutation(len(source));training=source[order[:min(4000,len(source)//2)]]
    testing=source[order[min(4000,len(source)//2):]][:12000]
    if len(testing)<10:raise ValueError('At least twenty source samples required')
    tree=cKDTree(target)
    rotation=yaw_matrix(seed['yaw_degrees']);translation=np.array(seed['translation'],dtype=float)
    for iteration in range(iterations):
        moved=training@rotation.T+translation
        distances,indices=tree.query(moved,workers=2)
        threshold=max(.6,2.5*(1-iteration/iterations))
        # Keep facade/roof edges: a global quantile trim can discard the entire
        # smaller vertical facade and make yaw unobservable on a flat roof.
        good=distances<threshold
        if good.sum()<30:break
        a=moved[good];b=target[indices[good]]
        ca=a.mean(axis=0);cb=b.mean(axis=0)
        u,_,vt=np.linalg.svd((a[:,:2]-ca[:2]).T@(b[:,:2]-cb[:2]))
        step2=vt.T@u.T
        if np.linalg.det(step2)<0:vt[-1]*=-1;step2=vt.T@u.T
        step=np.eye(3);step[:2,:2]=step2
        shift=cb-ca@step.T
        rotation=step@rotation;translation=translation@step.T+shift
        if np.linalg.norm(shift)<1e-5 and abs(step2[0,1])<1e-6:break
    moved=testing@rotation.T+translation
    distances,nearest=tree.query(moved,workers=2)
    # Source to target alone accepts a tiny scan contained inside a big roof.
    # Report spatially distributed support and independent held-out distances.
    support=moved[distances<.5]
    support_cells=len(np.unique(np.floor(support[:,:2]/2).astype(int),axis=0)) if len(support) else 0
    transform=np.eye(4);transform[:3,:3]=rotation;transform[:3,3]=translation
    return {**seed,'transform':transform.tolist(),
            'held_out_samples':len(testing),'median_m':float(np.median(distances)),
            'p90_m':float(np.quantile(distances,.9)),'within_025m':float(np.mean(distances<.25)),
            'within_05m':float(np.mean(distances<.5)),'within_1m':float(np.mean(distances<1)),
            'support_cells_2m':support_cells,'identity_verified':False,
            'status':'candidate_only_requires_visual_identity_and_control_check'}
