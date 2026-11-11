"""Combine a reviewed evidence overlay with the reproducible candidate dataset."""
import argparse
import json
from pathlib import Path

from dynacity.appearance_observations import apply_reviewed_observations


def apply(root):
    root = Path(root)
    source = json.loads((root/'evidence.json').read_text())
    review = json.loads((root/'reviewed-observations.json').read_text())
    result = apply_reviewed_observations(source, review, root/'street-photos')
    target = root/'appearance-evidence-reviewed.json'
    target.write_text(json.dumps(result, indent=2))
    print(json.dumps(result['review_summary']))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    apply(parser.parse_args().output)
