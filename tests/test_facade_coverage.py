import unittest
from dynacity.facade_coverage import fitted_building, admit_buildings
from test_building_facade_modules import building


class CoverageTest(unittest.TestCase):
    def test_short_building_and_fractional_roofline_have_no_residual(self):
        b=building()
        for v in b['vertices']:
            if v[2]:v[2]=5.7
        plan=fitted_building(b)
        self.assertEqual(len(plan['wall_coverage']),4)
        for wall in plan['wall_coverage']:
            self.assertEqual(wall['rows'],2)
            self.assertAlmostEqual(wall['residual_height_m'],0)
            self.assertAlmostEqual(wall['residual_width_m'],0)
        for p in plan['placements']:self.assertAlmostEqual(p['scale'][2],.95)

    def test_budget_defers_entire_building_and_reruns_are_stable(self):
        b=building();plan=fitted_building(b)
        actual,coverage,deferred=admit_buildings([b],len(plan['placements'])-1)
        self.assertEqual(actual,[]);self.assertEqual(coverage,[]);self.assertEqual(len(deferred),1)
        self.assertEqual(plan,fitted_building(b))
        self.assertEqual(len({p['id'] for p in plan['placements']}),len(plan['placements']))


if __name__=='__main__':unittest.main()
