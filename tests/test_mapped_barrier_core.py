import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python'))
from mapped_barrier_core import replacement_plan


def fixture():
    placement = {'source_id':'way/1', 'source_instance_id':'way/1#0',
                 'position_local_m':[1, 2, 3], 'heading_degrees':45, 'scale_x':.9}
    manifest = {'schema':'dynacity-mapped-barrier-placements/1', 'origin_utm_m':[0, 0, 0],
                'placements':[placement], 'omitted':[]}
    covers = {'origin_utm_m':[0,0,0], 'pending':None, 'replaced_actors':[],
              'packages':[{'kind':'jersey_barrier','key':'one',
                           'objects':[{'source_id':'way/1','instance_id':'way/1#0'}]}],
              'completed':{'one':{'actor':'actor/one'}}}
    return manifest, covers


def test_only_exact_matching_source_packages_are_replaced():
    manifest, covers = fixture()
    covers['packages'].append({'kind':'wall','key':'wall','objects':[]})
    assert replacement_plan(manifest, covers, [0,0,0]) == [
        {'actor':'actor/one','source_ids':[('way/1','way/1#0')]}]


def test_partial_packages_and_incomplete_sources_are_rejected():
    manifest, covers = fixture()
    covers['packages'][0]['objects'].append({'source_id':'way/2','instance_id':'way/2#0'})
    with pytest.raises(ValueError, match='unmatched'):
        replacement_plan(manifest, covers, [0,0,0])
    manifest, covers = fixture()
    manifest['omitted'] = [{'source_id':'way/1'}]
    with pytest.raises(ValueError, match='omitted'):
        replacement_plan(manifest, covers, [0,0,0])
