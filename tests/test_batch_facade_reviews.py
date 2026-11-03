import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from apply_batch_facade_reviews import validate_decision


class ReviewTest(unittest.TestCase):
    def test_changed_photo_cannot_reuse_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.jpg';path.write_bytes(b'original')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            photo={'path':str(path),'sha256':digest}
            review={'status':'rejected_non_facade','photo_sha256':digest,'reason':'Tunnel'}
            validate_decision(review,photo)
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):validate_decision(review,photo)

    def test_mismatched_landmark_cannot_approve_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.jpg';path.write_bytes(b'photo');digest=hashlib.sha256(path.read_bytes()).hexdigest()
            review={'status':'corroborated','photo_sha256':digest,'reason':'Bank',
                    'source_id':'example','visible_features':['grey panels'],
                    'corroboration':{'visible_sign_matches_anchor':False}}
            with self.assertRaisesRegex(ValueError,'Unmatched landmark'):
                validate_decision(review,{'path':str(path),'sha256':digest})


if __name__=='__main__':unittest.main()
