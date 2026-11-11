"""Verify supplementary drapes preserve terrain planes and polygon holes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np
from shapely.geometry import Polygon
from dynacity.engine_street_context import TerrainSurface

spec = importlib.util.spec_from_file_location('prepare_overture', Path(__file__).parents[1]/'scripts/prepare_overture_unreal.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OvertureContextTests(unittest.TestCase):
    def test_drape_matches_original_planes_and_holes(self):
        ground = SimpleNamespace(ground=np.array([[1., 2., 4.], [2., 6., 5.], [4., 5., 9.]]), x0=0., y0=3., cell_m=1.)
        surface = TerrainSurface(ground)
        polygon = Polygon([(0.5, .5), (2.5, .5), (2.5, 2.5), (.5, 2.5)],
                          [[(1., 1.), (1.7, 1.), (1.7, 1.7), (1., 1.7)]])
        meshes = [surface.drape(polygon, [0, 0, 0]), module.drape_fast(surface, polygon, [0, 0, 0])]
        areas = []
        for vertices, faces, missing in meshes:
            area = 0.
            for face in faces:
                triangle = np.asarray([vertices[i] for i in face])
                footprint = Polygon(triangle[:, :2])
                self.assertLess(footprint.difference(polygon).area, 1e-6)
                for x, y, z in triangle:
                    # Check the source plane by sampling just inside its grid extent.
                    sampled = surface.sample(min(x, 2.499999), max(y, .500001))
                    self.assertAlmostEqual(z, sampled+.03, places=4)
                area += footprint.area
            areas.append(area)
        self.assertAlmostEqual(areas[0], areas[1], places=5)
        self.assertAlmostEqual(areas[1], polygon.area, places=5)

    def test_osm_identity_retained(self):
        self.assertEqual(module.source_ids({'sources': [{'dataset': 'OpenStreetMap', 'record_id': 'w123@7'}]}), ['way/123'])

    def test_place_merge_is_repeatable_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root/'location-index.json'
            original = {'schema': 'dynacity-locations/1', 'places': [{'id': 'node/1', 'name': 'Cafe', 'lon': 35.5, 'lat': 33.88}], 'roads': []}
            index.write_text(json.dumps(original), encoding='utf-8')
            features = [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': coord},
                         'properties': {'id': identifier, 'names': {'primary': name}}}
                        for identifier, name, coord in [('a', 'Cafe', [35.50001, 33.88]), ('b', 'Museum', [35.52, 33.89])]]
            places = root/'places.geojson'
            places.write_text(json.dumps({'features': features}), encoding='utf-8')
            module.merge_locations(index, places)
            once = json.loads(index.read_text(encoding='utf-8'))
            module.merge_locations(index, places)
            twice = json.loads(index.read_text(encoding='utf-8'))
            self.assertEqual(once, twice)
            self.assertEqual(twice['places'][0], original['places'][0])
            self.assertEqual([r['id'] for r in twice['places']], ['node/1', 'overture/b'])
            self.assertEqual(json.loads((root/'location-index.before-overture.json').read_text()), original)


if __name__ == '__main__': unittest.main()
