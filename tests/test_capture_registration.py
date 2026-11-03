import numpy as np
import pytest

from dynacity.capture_registration import coarse_candidates,refine,voxel_sample,yaw_matrix


def asymmetric_surface():
    rng=np.random.default_rng(81)
    xy=rng.uniform([0,0],[15,9],(6000,2))
    z=3+.14*xy[:,0]+np.where(xy[:,0]<5,4,0)+np.where(xy[:,1]>6,2,0)
    roof=np.column_stack([xy,z])
    # Include a vertical facade so a roof-only fit cannot explain the fixture.
    facade=np.column_stack([rng.uniform(0,15,2000),np.zeros(2000),rng.uniform(0,7,2000)])
    return np.concatenate([roof,facade])


def test_refinement_recovers_rigid_pose_on_held_out_points():
    source=asymmetric_surface();original=source.copy();rotation=yaw_matrix(35)
    translation=np.array([70.,-40.,17.])
    target=source@rotation.T+translation
    result=refine(source,target,{'yaw_degrees':33,'translation':[70.4,-40.3,17.2]})
    matrix=np.asarray(result['transform'])
    assert np.allclose(matrix[:3,:3],rotation,atol=.01)
    assert np.allclose(matrix[:3,3],translation,atol=.05)
    assert result['within_05m']>.99
    assert not result['identity_verified']
    assert result['held_out_samples']>0
    assert np.array_equal(source,original)
    assert np.linalg.det(matrix[:3,:3])==pytest.approx(1)


def test_height_search_finds_translated_surface_with_clutter():
    source=asymmetric_surface();rng=np.random.default_rng(93)
    rotation=yaw_matrix(90);translation=np.array([31,19,24])
    target=source@rotation.T+translation
    clutter=rng.uniform([-10,-5,0],[5,10,5],(1000,3))
    seeds=coarse_candidates(source,np.concatenate([target,clutter]),angle_step=90,keep=12)
    refined=[refine(source,target,seed) for seed in seeds]
    best=max(refined,key=lambda row:row['within_05m'])
    assert best['within_05m']>.99
    assert np.allclose(np.asarray(best['transform'])[:3,3],translation,atol=.1)


def test_voxels_validate_input_and_retain_original_points():
    points=np.array([[0,0,0],[.1,.1,.1],[1,0,0.]])
    assert voxel_sample(points,cell=.5).tolist()==[0,2]
    with pytest.raises(ValueError):voxel_sample([[0,0,np.nan]])
    with pytest.raises(ValueError):voxel_sample(points,cell=0)
