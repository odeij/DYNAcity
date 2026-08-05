from __future__ import annotations

import math
from typing import Any

import numpy as np
from shapely import STRtree

from .models import Footprint


def evaluate_instances(
    reference: list[Footprint],
    predicted: list[Footprint],
    *,
    iou_threshold: float = 0.1,
) -> dict[str, Any]:
    """Greedily match predicted instances to BBED footprints by polygon IoU."""

    if not 0 <= iou_threshold <= 1:
        raise ValueError("IoU threshold must be between 0 and 1")
    tree = STRtree([feature.geometry for feature in reference])
    candidates: list[tuple[float, int, int]] = []
    for predicted_index, instance in enumerate(predicted):
        for reference_index in tree.query(instance.geometry, predicate="intersects"):
            target = reference[int(reference_index)].geometry
            intersection = instance.geometry.intersection(target).area
            union = instance.geometry.union(target).area
            iou = intersection / union if union else 0.0
            if iou >= iou_threshold:
                candidates.append((iou, int(reference_index), predicted_index))

    matched_reference: set[int] = set()
    matched_predicted: set[int] = set()
    matches: list[dict[str, Any]] = []
    for iou, reference_index, predicted_index in sorted(candidates, reverse=True):
        if reference_index in matched_reference or predicted_index in matched_predicted:
            continue
        matched_reference.add(reference_index)
        matched_predicted.add(predicted_index)
        matches.append(
            {
                "reference_match_id": reference[reference_index].match_id,
                "predicted_match_id": predicted[predicted_index].match_id,
                "iou": iou,
            }
        )

    true_positive = len(matches)
    false_positive = len(predicted) - true_positive
    false_negative = len(reference) - true_positive
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(reference) if reference else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ious = [item["iou"] for item in matches]
    return {
        "iou_threshold": iou_threshold,
        "reference_count": len(reference),
        "predicted_count": len(predicted),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_iou": float(np.mean(ious)) if ious else math.nan,
        "median_iou": float(np.median(ious)) if ious else math.nan,
        "matches": matches,
    }

