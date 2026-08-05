import pytest
from shapely.geometry import box

from dynacity_bbed.evaluation import evaluate_instances
from dynacity_bbed.models import Footprint


def test_instance_metrics_use_one_to_one_iou_matching():
    reference = [
        Footprint(0, box(0, 0, 2, 2), {}),
        Footprint(1, box(4, 0, 6, 2), {}),
    ]
    predicted = [
        Footprint(0, box(0, 0, 2, 2), {}),
        Footprint(1, box(10, 10, 11, 11), {}),
    ]
    report = evaluate_instances(reference, predicted, iou_threshold=0.5)

    assert report["true_positive"] == 1
    assert report["false_positive"] == 1
    assert report["false_negative"] == 1
    assert report["precision"] == pytest.approx(0.5)
    assert report["recall"] == pytest.approx(0.5)
    assert report["mean_iou"] == pytest.approx(1.0)

