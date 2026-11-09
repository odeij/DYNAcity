import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    'load_reference_facade',
    Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python/load_reference_facade.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def desc(path, guid, label='Reference visual source batch - LGB historical facade'):
    return SimpleNamespace(actor_path=path, guid=guid, label=label)


class OwnedGuidTest(unittest.TestCase):
    def test_loads_only_owned_paths_not_same_label_decoys(self):
        descs = [desc('L.A', 'a'), desc('L.B', 'b'), desc('L.DECOY', 'x')]
        self.assertEqual(sorted(loader.owned_guids(descs, ['L.A', 'L.B'])), ['a', 'b'])

    def test_missing_actor_is_an_error_so_hidden_source_is_never_left_bare(self):
        with self.assertRaisesRegex(RuntimeError, 'L.B'):
            loader.owned_guids([desc('L.A', 'a')], ['L.A', 'L.B'])


if __name__ == '__main__':
    unittest.main()
