import unittest
import numpy as np
from dynacity.building_facade_modules import (plan_modules, source_walls,
    native_outward_sign, unreal_module_yaw)


def building():
    vertices = [[0,0,0],[12,0,0],[12,9,0],[0,9,0],
                [0,0,12],[12,0,12],[12,9,12],[0,9,12]]
    triangles = [[0,1,4],[1,5,4],[1,2,5],[2,6,5],
                 [2,3,6],[3,7,6],[3,0,7],[0,4,7],[4,5,6],[4,6,7]]
    return {'id':'source:1','vertices':vertices,'triangles':triangles}


class FacadeModulesTest(unittest.TestCase):
    def test_source_edges_and_outward_proper_rotations(self):
        b = building()
        self.assertEqual(len(source_walls(b['vertices'],b['triangles'])),4)
        result = plan_modules([b],bounds=(-1,-1,13,10))
        self.assertEqual(len(result['placements']),30)
        for placement in result['placements']:
            x,y,z = placement['position_m']
            self.assertTrue(x < 0 or x > 12 or y < 0 or y > 9)
            self.assertLessEqual(z+3,12)
            self.assertEqual(placement['scale'],[1,1,1])
            self.assertEqual(placement['source_id'],'source:1')
        self.assertEqual(result,plan_modules([b],bounds=(-1,-1,13,10)))

    def test_budget_selection_and_reversed_winding(self):
        b=building()
        normal=plan_modules([b],bounds=(-1,-1,13,10),max_instances=5)
        self.assertEqual(len(normal['placements']),5)
        b['triangles']=[list(reversed(t)) for t in b['triangles']]
        reverse=plan_modules([b],bounds=(-1,-1,13,10),max_instances=5)
        self.assertEqual([p['position_m'] for p in normal['placements']],
                         [p['position_m'] for p in reverse['placements']])
        self.assertFalse(plan_modules([b],bounds=(20,20,30,30))['placements'])

    def test_roof_and_sloped_faces_not_interpreted_as_walls(self):
        self.assertEqual(source_walls([[0,0,0],[12,0,0],[0,9,1]],[[0,1,2]]),[])
        with self.assertRaises(ValueError):
            plan_modules([building()],bounds=(0,0,0,0))

    def test_focus_selects_frontage_and_offset_clears_library_recess(self):
        result=plan_modules([building()],bounds=(-1,-1,13,10),
            focus_point=[6,-20],lift_m=.28)
        self.assertEqual(len(result['placements']),9)
        for placement in result['placements']:
            self.assertAlmostEqual(placement['position_m'][1],-.28)
            # The kit's deepest authored geometry is 24cm behind its wall.
            self.assertLess(placement['position_m'][1]+.24,0)

    def test_native_y_sign_and_proper_rotation_point_outward(self):
        self.assertEqual(native_outward_sign([-.24,.15],[-24,15]),1)
        self.assertEqual(native_outward_sign([-.24,.15],[-15,24]),-1)
        with self.assertRaises(ValueError):native_outward_sign([-.24,.15],[-20,19])
        for angle in [0,36,90,145,180,-45]:
            for sign in [-1,1]:
                phi=np.radians(unreal_module_yaw(angle,sign))
                outward=sign*np.array([-np.sin(phi),np.cos(phi)])
                theta=np.radians(angle)
                target=np.array([-np.sin(theta),-np.cos(theta)])
                np.testing.assert_allclose(outward,target,atol=1e-12)

    def test_incremental_camera_scope_excludes_existing_source_ids(self):
        first=plan_modules([building()],bounds=(-1,-1,13,10),
            focus_point=[20,4],max_instances=5)
        existing={item['id'] for item in first['placements']}
        second=plan_modules([building()],bounds=(-1,-1,13,10),
            focus_point=[20,4],max_instances=5,exclude_ids=existing)
        added={item['id'] for item in second['placements']}
        self.assertEqual(len(added),1)
        self.assertFalse(existing & added)
        reverse=plan_modules([building()],bounds=(-1,-1,13,10),
            focus_point=[-20,4],max_instances=5,exclude_ids=existing)
        self.assertEqual(len(reverse['placements']),5)
        self.assertFalse(existing & {item['id'] for item in reverse['placements']})


if __name__=='__main__':unittest.main()
