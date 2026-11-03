"""Offline: cached street photos -> appearance-library.json + building-appearance-jobs.json.

No network, Unreal or LLM calls. Outputs go to a private folder.
"""
import argparse
import json
from pathlib import Path

from dynacity.photo_facade_library import build_library, refresh_review_overlay

ROOT = Path(r"C:\private\dynacity-data\engine")
EVIDENCE = ROOT / "facade-batch-evidence-20261030"


def latest(directory: Path) -> Path:
    return Path(json.loads((directory / "latest-run.json").read_text(encoding="utf-8"))["run_directory"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=EVIDENCE / "all-batches" / "photo-catalog.json")
    parser.add_argument("--reviews", type=Path, default=EVIDENCE / "review-decisions.json")
    parser.add_argument("--review-report", type=Path, default=EVIDENCE / "reviewed" / "report.json")
    parser.add_argument("--retained", type=Path, default=EVIDENCE / "reviewed" / "retained-observations.json")
    parser.add_argument("--landmarks", type=Path, default=EVIDENCE / "reviewed" / "landmark-priority.json")
    parser.add_argument("--plan-dir", type=Path, default=latest(ROOT / "appearance-plan-20261030"))
    parser.add_argument("--passes-dir", type=Path, default=latest(ROOT / "appearance-passes-20261030"))
    parser.add_argument("--inspection-notes", type=Path, help="JSON recorded by the human/assistant inspection")
    parser.add_argument("--curated", type=Path, help="curated accepted clean-wall patches JSON")
    parser.add_argument("--cache", type=Path, help="analysis cache (reused when catalog and rules are unchanged)")
    parser.add_argument("--candidate-sheets", type=Path, help="only write numbered candidate sheets here, for curation")
    parser.add_argument("--overlay-root", type=Path, help="new folder for the refreshed review overlay "
                                                         "(default: <output>/review-overlay)")
    parser.add_argument("--no-overlay", action="store_true", help="use the stale plan candidates instead")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    notes = json.loads(args.inspection_notes.read_text(encoding="utf-8")) if args.inspection_notes else None
    pass_paths = sorted(args.passes_dir.glob("pass-0*.json"))
    overlay = None
    if not args.no_overlay:
        overlay = refresh_review_overlay(EVIDENCE, Path(__file__).with_name("apply_batch_facade_reviews.py"),
                                         args.overlay_root or args.output / "review-overlay")
    library, jobs, _ = build_library(
        args.catalog, pass_paths, args.reviews, args.review_report, args.plan_dir, args.passes_dir,
        args.retained, args.landmarks, args.output, inspection_notes=notes,
        extra_photo_dirs=[EVIDENCE / "all-batches" / "photos"], cache_path=args.cache, curated_path=args.curated,
        overlay_dir=overlay, collector_dir=EVIDENCE / "all-batches", candidate_sheet_dir=args.candidate_sheets)
    if jobs is None:
        print("candidate sheets written to", args.candidate_sheets)
        return
    photos = library["photos"]
    print(f"photos processed {photos['processed']} usable {photos['usable']} reviewed {photos['reviewed']}")
    print("families", {f["id"]: f["appearance_estimates"]["photo_statistics"]["tile_count"] for f in library["families"]})
    print("jobs", jobs["inventory"]["source_buildings"], jobs["counts_by_evidence_class"])
    print("by family", jobs["counts_by_family"])
    print("reconciliation", json.dumps(library["candidate_reconciliation"], indent=1))
    print("seconds", library["timing_seconds"])


if __name__ == "__main__":
    main()
