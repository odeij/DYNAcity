"""Keep legacy painted crossings while respecting explicit no-paint tags."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('mapped_crossings',
    Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/mapped_crossings.py')
crossings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crossings)


@pytest.mark.parametrize('tags,expected', [
    ({'crossing':'uncontrolled'}, True),
    ({'crossing':'unmarked'}, False),
    ({'crossing':'marked','crossing:markings':'no'}, False),
    ({'crossing':'unmarked','crossing:markings':'yes'}, False),
    ({}, False),
    ({'crossing:markings':'zebra'}, True),
    ({'crossing':'traffic_signals'}, True),
])
def test_mapped_paint_filter(tags, expected):
    assert crossings.is_painted({'kind':'crossing','tags':tags}) is expected
