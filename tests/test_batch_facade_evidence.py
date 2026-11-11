import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect_batch_facade_evidence import (Fetcher,cached_query,greedy_frames,
    normalize_camera,prime_sequence_cache,publish,response_hash,wait_rate,write_json)
from pyproj import Geod,Transformer


class BatchEvidenceTest(unittest.TestCase):
    def test_greedy_reuses_cached_and_prefers_new_building_coverage(self):
        frames={'1':[{'source_id':'a'}],'2':[{'source_id':'a'},{'source_id':'b'}],
                '3':[{'source_id':'c'},{'source_id':'d'}]}
        self.assertEqual(greedy_frames(frames,{'1':{}},1),['1','3'])
        self.assertEqual(greedy_frames(frames,{},0),[])

    def test_cache_never_reuses_different_page_or_failed_api_response(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'page.json';params={'sequenceId':'123','page':1,'itemsPerPage':100}
            write_json(path,{'params':params,'response':{'status':{'apiCode':600},'result':{'data':[]}}})
            self.assertIsNotNone(cached_query(path,params))
            self.assertIsNone(cached_query(path,{**params,'page':2}))
            data=json.loads(path.read_text());data['response']['status']['apiCode']=500;write_json(path,data)
            self.assertIsNone(cached_query(path,params))

    def test_offline_fetcher_cannot_make_network_requests(self):
        args=SimpleNamespace(offline=True,max_queries=200,max_photos=500)
        with tempfile.TemporaryDirectory() as folder:
            fetcher=Fetcher(args,Path(folder))
            with patch.object(fetcher.client,'get',side_effect=AssertionError('Unexpected network')):
                self.assertIsNone(fetcher.query(Path(folder)/'missing.json',{'page':1}))
            self.assertEqual(fetcher.requests,0);fetcher.client.close()

    def test_sequence_pages_are_primed_without_changing_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pilot=root/'pilot';pilot.mkdir();target=root/'out'
            params={'sequenceId':'123','itemsPerPage':100,'page':2}
            write_json(pilot/'street-sequence-123-page-2.json',{'params':params,'response':{'status':{'apiCode':600},'result':{'data':[]}}})
            prime_sequence_cache(pilot,target)
            self.assertIsNotNone(cached_query(target/'queries/sequence-123-page-2.json',params))

    def test_invalid_camera_metadata_is_rejected(self):
        convert=Transformer.from_crs('EPSG:4326','EPSG:32636',always_xy=True);geod=Geod(ellps='WGS84')
        for heading in [None,'nan','inf']:
            self.assertIsNone(normalize_camera({'projection':'PLANE','lng':35.5,'lat':33.8,'heading':heading},convert,geod))

    def test_rate_timestamp_is_persisted_before_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rate.json'
            with patch('collect_batch_facade_evidence.time.time',return_value=1000):wait_rate(path,60)
            self.assertEqual(json.loads(path.read_text())['time'],1000)

    def test_empty_evidence_is_reported_as_a_gap_not_a_match(self):
        with tempfile.TemporaryDirectory() as folder:
            assignment={'owner_tile':'0:0','district':'Example','roof':{'status':'unknown'},
                        'facade':{'status':'estimated','observation':None}}
            members={name:{'pass':1,'assignment':assignment} for name in ['a','b']}
            report=publish(Path(folder),[1],members,[],{},{},{},
                           SimpleNamespace(requests=0,downloads=0,bytes=0),'offline',[])
            self.assertEqual(report['buildings_with_geometry_candidates'],0)
            self.assertEqual(report['automatic_confirmations'],0)
            self.assertEqual(report['passes'][0]['counts'],{'no_usable_view_found':2})

    def test_changed_successful_response_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'query.json';params={'page':1}
            response={'status':{'apiCode':600},'result':{'data':[]}}
            fingerprint=response_hash(response);response['result']['data']=[{'id':'unexpected'}]
            write_json(path,{'params':params,'response':response,'response_sha256':fingerprint})
            with self.assertRaises(RuntimeError):cached_query(path,params)


if __name__=='__main__':unittest.main()
