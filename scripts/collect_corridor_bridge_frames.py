#!/usr/bin/env python3
"""
scripts/collect_corridor_bridge_frames.py

Collect original bridge frames from KartaView sequence 3555101 for the time
interval shotDate 2018-07-21 06:42:33..37 inclusive.

Bounds and Constraints:
- Reference-geometry role. Offline evidence collection only.
- Reads local cached sequence pages first:
    C:/private/dynacity-data/engine/appearance-evidence-20261030/street-sequence-3555101-page-{1,2,3}.json
- Bounded public API fallback if cached pages miss interval (max 3 GETs).
- Downloads ONLY public official cdn.kartaview.org URLs from exact row imageProcUrl.
- Max 8 photos / 40 MB total limit enforced.
- Private output:
    C:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/bridge-frames/
- Preserves raw JPEG bytes bit-for-bit, computes SHA256, verifies JPEG decode and dimensions with PIL.
- Emits per-photo JSON, photo-catalog.json, photo-catalog.md, and PIL contact-sheet.jpg (Segoe UI font).
- No calibration or geographic identity claims; current presence unverified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image, ImageDraw, ImageFont

DEFAULT_CACHE_DIR = Path(r"C:/private/dynacity-data/engine/appearance-evidence-20261030")
DEFAULT_OUTPUT_DIR = Path(r"D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/bridge-frames")
DEFAULT_SEQUENCE_ID = "3555101"
DEFAULT_START_TIME = "2018-07-21 06:42:33"
DEFAULT_END_TIME = "2018-07-21 06:42:37"
MAX_PHOTOS = 8
MAX_TOTAL_MB = 40.0
FONT_PATH = r"C:/Windows/Fonts/segoeui.ttf"
FONT_BOLD_PATH = r"C:/Windows/Fonts/segoeuib.ttf"
ATTRIBUTION = "Grab and KartaView Contributors"
LICENSE_NAME = "CC BY-SA 4.0"
TERMS_URL = "https://kartaview.org/terms"
DISCLAIMER_TEXT = (
    "NO pose calibration claimed. Intrinsics uncalibrated. "
    "No geographic identity claims; current presence unverified. "
    "GPS and heading are reported sensor data, not ground truth."
)


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 digest of a binary file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_cached_sequence_records(cache_dir: Path, sequence_id: str) -> Dict[str, Dict[str, Any]]:
    """
    Read cached sequence JSON files, filtering for sequenceId and public visibility,
    deduplicating by photo ID.
    """
    cached_files = [
        cache_dir / f"street-sequence-{sequence_id}-page-1.json",
        cache_dir / f"street-sequence-{sequence_id}-page-2.json",
        cache_dir / f"street-sequence-{sequence_id}-page-3.json",
        cache_dir / f"street-sequence-{sequence_id}.json",
    ]

    records: Dict[str, Dict[str, Any]] = {}
    found_files = 0

    for path in cached_files:
        if not path.is_file():
            continue
        found_files += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Accommodate both response.result.data and result.data
            rows = (
                data.get("response", {}).get("result", {}).get("data")
                or data.get("result", {}).get("data")
                or []
            )
            for row in rows:
                row_seq = str(row.get("sequenceId") or row.get("sequence_id") or "")
                row_vis = str(row.get("visibility") or "").strip().lower()
                row_id = str(row.get("id") or "").strip()
                if row_seq == sequence_id and row_vis == "public" and row_id.isdigit():
                    records[row_id] = row
        except Exception as e:
            print(f"[WARN] Error reading {path}: {e}", file=sys.stderr)

    print(f"[INFO] Scanned {found_files} cache files; loaded {len(records)} unique sequence {sequence_id} records.")
    return records


def filter_interval_records(
    records: Dict[str, Dict[str, Any]],
    start_time: str,
    end_time: str,
) -> List[Dict[str, Any]]:
    """Filter records whose shotDate is within start_time and end_time (inclusive)."""
    matching = []
    # End time pattern: allow fractional seconds up to .999999
    start_str = start_time
    end_str = end_time + ".999999"

    for r in records.values():
        sdate = str(r.get("shotDate") or "")
        if start_str <= sdate <= end_str:
            matching.append(r)

    # Sort deterministically by sequenceIndex if available, else shotDate
    matching.sort(key=lambda x: (int(x.get("sequenceIndex") or 0), str(x.get("shotDate") or "")))
    return matching


def fetch_metadata_page(sequence_id: str, page: int) -> Tuple[int, List[Dict[str, Any]]]:
    """Fetch one page of sequence photo metadata from the official KartaView API."""
    api_url = "https://api.kartaview.org/2.0/photo/"
    params = {
        "sequenceId": sequence_id,
        "itemsPerPage": "100",
        "page": str(page),
    }
    headers = {"User-Agent": "DYNAcity-Corridor-Bridge-Collector/1.0"}
    try:
        with httpx.Client(timeout=20.0, headers=headers) as client:
            resp = client.get(api_url, params=params)
            status = resp.status_code
            if status == 200:
                body = resp.json()
                rows = (
                    body.get("response", {}).get("result", {}).get("data")
                    or body.get("result", {}).get("data")
                    or []
                )
                return status, rows
            return status, []
    except Exception as e:
        print(f"[ERROR] API GET failed for page {page}: {e}", file=sys.stderr)
        return -1, []


def download_image_file(
    url: str,
    dest_path: Path,
    max_bytes: int,
    rate_limit_sec: float = 1.5,
) -> Tuple[bool, Optional[str], int]:
    """
    Download image from official cdn.kartaview.org URL.
    Preserves raw JPEG bytes bit-for-bit without transcode.
    """
    if not url.startswith("https://cdn.kartaview.org/"):
        return False, f"Prohibited host in URL: {url}", 0

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp_download")

    headers = {"User-Agent": "DYNAcity-Corridor-Bridge-Collector/1.0"}
    try:
        time.sleep(rate_limit_sec)
        with httpx.Client(timeout=30.0, headers=headers) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code} from CDN", 0
                total_bytes = 0
                with open(temp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            temp_path.unlink(missing_ok=True)
                            return False, f"Exceeded maximum file size limit ({max_bytes} bytes)", total_bytes
                        f.write(chunk)

        if temp_path.exists():
            temp_path.replace(dest_path)
            return True, None, total_bytes
        return False, "Temporary file not found after download", 0
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False, f"Download exception: {e}", 0


def verify_image(path: Path) -> Tuple[bool, Optional[Tuple[int, int]], Optional[str]]:
    """Decode and verify image dimensions and format with PIL."""
    try:
        with Image.open(path) as img:
            img_format = img.format
            img.load()
            dims = img.size  # (width, height)
            if img_format != "JPEG":
                return False, dims, f"Format is {img_format}, expected JPEG"
            return True, dims, None
    except Exception as e:
        return False, None, f"PIL decode error: {e}"


def get_font(path: str, size: int) -> ImageFont.ImageFont:
    """Load TrueType font or fallback to default."""
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    return ImageFont.load_default()


def generate_contact_sheet(
    photos_meta: List[Dict[str, Any]],
    output_path: Path,
    sequence_id: str,
    target_window: str,
) -> bool:
    """
    Generate PIL contact sheet for downloaded bridge photos.
    Uses Segoe UI font.
    """
    n = len(photos_meta)
    if n == 0:
        return False

    thumb_w = 720
    thumb_h = 540  # 4:3 aspect ratio
    card_padding = 24
    card_info_h = 160
    card_w = thumb_w
    card_h = thumb_h + card_info_h

    cols = min(n, 3)
    rows = (n + cols - 1) // cols

    sheet_pad = 30
    header_h = 120
    gap = 24

    total_w = sheet_pad * 2 + cols * card_w + (cols - 1) * gap
    total_h = header_h + sheet_pad * 2 + rows * card_h + (rows - 1) * gap

    # Canvas
    canvas = Image.new("RGB", (total_w, total_h), (245, 246, 248))
    draw = ImageDraw.Draw(canvas)

    title_font = get_font(FONT_BOLD_PATH, 24)
    subtitle_font = get_font(FONT_PATH, 15)
    body_bold_font = get_font(FONT_BOLD_PATH, 14)
    body_font = get_font(FONT_PATH, 13)
    mono_font = get_font(FONT_PATH, 11)

    # Header
    draw.rectangle([0, 0, total_w, header_h], fill=(30, 41, 59))
    draw.text((sheet_pad, 20), f"KartaView Sequence {sequence_id} — Bridge Corridor Originals", fill=(255, 255, 255), font=title_font)
    header_sub = (
        f"Window: {target_window} UTC | CC BY-SA 4.0 Grab / KartaView | "
        f"Retrieved: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    draw.text((sheet_pad, 54), header_sub, fill=(203, 213, 225), font=subtitle_font)
    disclaimer_short = "Bounded feasibility originals. Uncalibrated sensor data; current presence unverified."
    draw.text((sheet_pad, 82), disclaimer_short, fill=(148, 163, 184), font=subtitle_font)

    # Draw photo cards
    for idx, p in enumerate(photos_meta):
        col = idx % cols
        row = idx // cols
        x = sheet_pad + col * (card_w + gap)
        y = header_h + sheet_pad + row * (card_h + gap)

        # Card background
        draw.rectangle([x, y, x + card_w, y + card_h], fill=(255, 255, 255), outline=(226, 232, 240), width=1)

        # Thumbnail
        img_path = Path(p["local_image_path"])
        if img_path.is_file():
            try:
                with Image.open(img_path) as im:
                    im_thumb = im.copy()
                    im_thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                    # Center thumbnail in card top area
                    tx = x + (thumb_w - im_thumb.width) // 2
                    ty = y + (thumb_h - im_thumb.height) // 2
                    canvas.paste(im_thumb, (tx, ty))
            except Exception as e:
                draw.text((x + 20, y + 20), f"Failed to render image: {e}", fill=(200, 0, 0), font=body_font)

        # Card text area
        text_y = y + thumb_h + 10
        photo_id = p.get("photo_id")
        seq_idx = p.get("sequence_index")
        shot_date = p.get("shot_date")
        dims = p.get("dimensions", [0, 0])
        file_bytes = p.get("bytes", 0)
        sha_prefix = p.get("sha256", "")[:16] + "..."

        draw.text((x + 16, text_y), f"Photo ID: {photo_id}  (SeqIndex: {seq_idx})", fill=(15, 23, 42), font=body_bold_font)
        text_y += 24
        draw.text((x + 16, text_y), f"Reported Shot Date: {shot_date} (timezone unverified)", fill=(51, 65, 85), font=body_font)
        text_y += 20
        draw.text((x + 16, text_y), f"Resolution: {dims[0]}x{dims[1]} px | Size: {file_bytes/1024:.1f} KB", fill=(51, 65, 85), font=body_font)
        text_y += 20
        draw.text((x + 16, text_y), f"SHA-256: {sha_prefix}", fill=(100, 116, 139), font=mono_font)
        text_y += 18
        gps_acc = p.get("gps_accuracy_reported", "N/A")
        heading = p.get("heading_reported_deg", "N/A")
        draw.text((x + 16, text_y), f"Reported GPS Acc: {gps_acc} m | Heading: {heading}°", fill=(100, 116, 139), font=mono_font)
        text_y += 18
        draw.text((x + 16, text_y), f"License: {LICENSE_NAME} | Attribution: {ATTRIBUTION}", fill=(148, 163, 184), font=mono_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "JPEG", quality=92)
    print(f"[INFO] Saved contact sheet: {output_path}")
    return True


def build_catalog_markdown(
    catalog: Dict[str, Any],
    photos_meta: List[Dict[str, Any]],
) -> str:
    """Build markdown documentation for bridge photos catalog."""
    md = []
    md.append(f"# KartaView Corridor Bridge Photos Catalog (Sequence {catalog['sequence_id']})")
    md.append("")
    md.append(f"**Target Window:** `{catalog['target_shot_date_window']}`  ")
    md.append(f"**Retrieval Timestamp:** `{catalog['retrieval_timestamp_utc']}`  ")
    md.append(f"**License:** [{LICENSE_NAME}]({TERMS_URL}) — {ATTRIBUTION}  ")
    md.append("")
    md.append("## Summary and Bounds")
    md.append("")
    md.append(f"- **Total bridge photos collected:** {len(photos_meta)} (limit: {catalog['limits_applied']['max_photos']})")
    md.append(f"- **Total download size:** {catalog['total_bytes_downloaded']} bytes ({catalog['total_bytes_downloaded'] / (1024*1024):.2f} MB; limit: {catalog['limits_applied']['max_total_mb']} MB)")
    md.append(f"- **Cached sequence records inspected:** {catalog['cached_coverage_summary']['total_cached_records']}")
    md.append(f"- **API metadata calls made:** {catalog['api_calls_made']}")
    md.append(f"- **Continuous Sequence Indices:** `{catalog['cached_coverage_summary']['sequence_indices']}`")
    md.append(f"- **Continuity Context:** {catalog['cached_coverage_summary']['continuity_note']}")
    md.append("")
    md.append("### Important Legal & Engineering Disclaimers")
    md.append(f"- **Calibration Claim:** {DISCLAIMER_TEXT}")
    md.append("- **Sensor Data:** GPS accuracy and heading values are reported device telemetry from 2018 metadata, not independently surveyed ground truth.")
    md.append("- **Preservation:** Raw JPEG bytes downloaded directly from official `cdn.kartaview.org` URLs without recompression or modification.")
    md.append("")
    md.append("## Coverage & Gap Analysis")
    md.append("")
    md.append("| Target Timestamp | Photo ID | Sequence Index | Status | Reason / Note |")
    md.append("| :--- | :--- | :--- | :--- | :--- |")

    # 33..37 seconds
    seconds = [
        "2018-07-21 06:42:33",
        "2018-07-21 06:42:34",
        "2018-07-21 06:42:35",
        "2018-07-21 06:42:36",
        "2018-07-21 06:42:37",
    ]
    photos_by_sec = {}
    for p in photos_meta:
        s = p["shot_date"][:19]
        photos_by_sec[s] = p

    for s in seconds:
        if s in photos_by_sec:
            p = photos_by_sec[s]
            md.append(f"| `{s}` | `{p['photo_id']}` | `{p['sequence_index']}` | **COLLECTED** | File: `{p['filename']}` ({p['dimensions'][0]}x{p['dimensions'][1]}) |")
        else:
            md.append(f"| `{s}` | *None* | *N/A* | **NOT FOUND** | No public frame observed in the inspected metadata at this second |")

    md.append("")
    md.append("## Bridge Photo Records")
    md.append("")

    for p in photos_meta:
        md.append(f"### Photo `{p['photo_id']}` (Sequence Index {p['sequence_index']})")
        md.append("")
        md.append(f"- **File:** [{p['filename']}]({p['filename']})  ")
        md.append(f"- **Metadata JSON:** [{p['photo_id']}.json]({p['photo_id']}.json)  ")
        md.append(f"- **Shot Date:** `{p['shot_date']}` (Capture year: 2018; Retrieved: 2026-11-03)  ")
        md.append(f"- **Source CDN URL:** [{p['source_url']}]({p['source_url']})  ")
        md.append(f"- **SHA-256:** `{p['sha256']}`  ")
        md.append(f"- **File Size:** {p['bytes']} bytes ({p['bytes'] / 1024:.1f} KB)  ")
        md.append(f"- **Resolution:** {p['dimensions'][0]} x {p['dimensions'][1]} px (format: JPEG)  ")
        md.append(f"- **Reported Coordinates:** Lat {p['lat_reported']}, Lng {p['lng_reported']}  ")
        md.append(f"- **Reported Heading:** {p['heading_reported_deg']}° | **Reported GPS Accuracy:** {p['gps_accuracy_reported']} m  ")
        md.append(f"- **Quality Warning:** `{p.get('quality_warnings', 'None reported in metadata')}`  ")
        md.append(f"- **License & Attribution:** {p['license']} — {p['attribution']}  ")
        md.append("")

    md.append("## Contact Sheet")
    md.append("")
    md.append("![Bridge Contact Sheet](contact-sheet.jpg)")
    md.append("")
    return "\n".join(md)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect corridor bridge originals from KartaView sequence 3555101.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Path to cached sequence json directory")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Private output directory for bridge frames")
    parser.add_argument("--sequence-id", type=str, default=DEFAULT_SEQUENCE_ID, help="Sequence ID")
    parser.add_argument("--start-time", type=str, default=DEFAULT_START_TIME, help="Start shotDate (inclusive)")
    parser.add_argument("--end-time", type=str, default=DEFAULT_END_TIME, help="End shotDate (inclusive)")
    parser.add_argument("--max-photos", type=int, default=MAX_PHOTOS, help="Maximum photos to download")
    parser.add_argument("--max-total-mb", type=float, default=MAX_TOTAL_MB, help="Maximum total download size in MB")
    parser.add_argument("--rate-limit", type=float, default=1.5, help="Polite rate limit sleep between downloads (seconds)")
    args = parser.parse_args()
    args.max_photos=min(MAX_PHOTOS,max(0,args.max_photos))
    args.max_total_mb=min(MAX_TOTAL_MB,max(0.,args.max_total_mb))
    if shutil.disk_usage(args.output_dir.anchor).free < 100*1024*1024:raise RuntimeError("Insufficient output-drive space for bounded collection")

    print("[INFO] Starting corridor bridge frame collection...")
    print(f"[INFO] Sequence ID: {args.sequence_id}")
    print(f"[INFO] Target window: {args.start_time} .. {args.end_time} inclusive")
    print(f"[INFO] Output directory: {args.output_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Read cached sequence pages
    records = load_cached_sequence_records(args.cache_dir, args.sequence_id)
    matching_records = filter_interval_records(records, args.start_time, args.end_time)

    api_calls_made = 0
    # 2. Check if cached records missed the interval
    if not matching_records:
        print("[WARN] No matching records found in local cache. Attempting up to 3 bounded API GETs...")
        for page in range(1, 4):
            api_calls_made += 1
            print(f"[INFO] Querying KartaView API page {page}...")
            status, rows = fetch_metadata_page(args.sequence_id, page)
            if status != 200:
                print(f"[WARN] API returned status {status} on page {page}")
                break
            for row in rows:
                row_seq = str(row.get("sequenceId") or row.get("sequence_id") or "")
                row_vis = str(row.get("visibility") or "").strip().lower()
                row_id = str(row.get("id") or "").strip()
                if row_seq == args.sequence_id and row_vis == "public" and row_id.isdigit():
                    records[row_id] = row
            matching_records = filter_interval_records(records, args.start_time, args.end_time)
            if matching_records:
                print(f"[INFO] Found {len(matching_records)} matching records after querying page {page}")
                break
            time.sleep(2.0)

    print(f"[INFO] Filtered {len(matching_records)} records in interval {args.start_time} .. {args.end_time}")

    if not matching_records:
        print("[ERROR] Exact coverage gap: no frames found for sequence 3555101 in interval 06:42:33..37.", file=sys.stderr)
        # Record coverage gap report and exit
        gap_report = {
            "sequence_id": args.sequence_id,
            "target_window": f"{args.start_time}..{args.end_time}",
            "records_found": 0,
            "coverage_gap": "No public frames found in inspected cached/API metadata; capture completeness unknown",
            "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        with open(args.output_dir / "coverage-gap.json", "w", encoding="utf-8") as f:
            json.dump(gap_report, f, indent=2)
        return 1

    # 3. Enforce maximum photos limit
    if len(matching_records) > args.max_photos:
        print(f"[WARN] Capping records from {len(matching_records)} to {args.max_photos} per limit constraint.")
        matching_records = matching_records[:args.max_photos]

    # 4. Download photos and verify
    photos_meta: List[Dict[str, Any]] = []
    total_bytes_downloaded = 0
    max_total_bytes = int(args.max_total_mb * 1024 * 1024)

    for r in matching_records:
        photo_id = str(r.get("id"))
        if not photo_id.isdigit():continue
        seq_idx = int(r.get("sequenceIndex") or 0)
        shot_date = str(r.get("shotDate") or "")
        image_url = str(r.get("imageProcUrl") or "").strip()

        if not image_url or not image_url.startswith("https://cdn.kartaview.org/"):
            print(f"[WARN] Skipping photo {photo_id}: invalid or unauthorized CDN URL '{image_url}'")
            continue

        dest_filename = f"{photo_id}.jpg"
        dest_path = args.output_dir / dest_filename
        json_filename = f"{photo_id}.json"
        json_path = args.output_dir / json_filename

        print(f"[INFO] Downloading photo {photo_id} (seqIndex: {seq_idx}, shotDate: {shot_date})...")
        remaining_budget = max_total_bytes - total_bytes_downloaded
        if remaining_budget <= 0:
            print(f"[WARN] Reached maximum download budget of {args.max_total_mb} MB. Stopping further downloads.")
            break

        # Check if already downloaded and valid
        if dest_path.is_file() and dest_path.stat().st_size > 0:
            print(f"[INFO] Photo {dest_filename} already exists locally; verifying integrity...")
            valid, dims, err = verify_image(dest_path)
            if valid and dims:
                sha256_hash = compute_sha256(dest_path)
                file_size = dest_path.stat().st_size
                total_bytes_downloaded += file_size
            else:
                dest_path.unlink()
                ok, err_msg, bytes_got = download_image_file(image_url, dest_path, remaining_budget, args.rate_limit)
                if not ok:
                    print(f"[ERROR] Failed to download {dest_filename}: {err_msg}", file=sys.stderr)
                    continue
                valid, dims, err = verify_image(dest_path)
                sha256_hash = compute_sha256(dest_path)
                file_size = dest_path.stat().st_size
                total_bytes_downloaded += file_size
        else:
            ok, err_msg, bytes_got = download_image_file(image_url, dest_path, remaining_budget, args.rate_limit)
            if not ok:
                print(f"[ERROR] Failed to download {dest_filename}: {err_msg}", file=sys.stderr)
                continue
            valid, dims, err = verify_image(dest_path)
            if not valid or not dims:
                print(f"[ERROR] Failed to verify downloaded image {dest_filename}: {err}", file=sys.stderr)
                dest_path.unlink(missing_ok=True)
                continue
            sha256_hash = compute_sha256(dest_path)
            file_size = dest_path.stat().st_size
            total_bytes_downloaded += file_size

        print(f"[INFO] Verified {dest_filename}: {dims[0]}x{dims[1]} px, {file_size} bytes, SHA256: {sha256_hash[:16]}...")

        # Construct per-photo metadata record
        now_utc = datetime.now(timezone.utc).isoformat()
        photo_entry = {
            "photo_id": photo_id,
            "filename": dest_filename,
            "local_image_path": str(dest_path),
            "sequence_id": args.sequence_id,
            "sequence_index": seq_idx,
            "shot_date": shot_date,
            "capture_date_year": int(shot_date[:4]),
            "capture_timezone": "unverified; provider timestamp preserved",
            "provider_image_kind": "processed public JPEG, download bytes unmodified; not raw sensor data",
            "retrieval_date_utc": now_utc,
            "source_url": image_url,
            "sha256": sha256_hash,
            "bytes": file_size,
            "dimensions": [dims[0], dims[1]],
            "format": "JPEG",
            "gps_accuracy_reported": str(r.get("gpsAccuracy") or "N/A"),
            "gps_accuracy_notice": "REPORTED by device telemetry; NOT independently surveyed ground truth",
            "heading_reported_deg": str(r.get("heading") or "N/A"),
            "lat_reported": str(r.get("lat") or "N/A"),
            "lng_reported": str(r.get("lng") or "N/A"),
            "quality_warnings": r.get("qualityDetails") or "None reported",
            "license": LICENSE_NAME,
            "attribution": ATTRIBUTION,
            "terms_url": TERMS_URL,
            "disclaimers": {
                "calibration_claim": "NO pose calibration claimed. Intrinsics uncalibrated; bounded bridge frame collection only.",
                "geographic_identity": "Current presence unverified; geographic coordinates are reported sensor readings.",
            },
            "raw_public_source_record": r,
        }

        # Write per-photo JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(photo_entry, f, indent=2)

        photos_meta.append(photo_entry)

    # 5. Build summary catalog
    missing_dates = []
    expected_seconds = [
        "2018-07-21 06:42:33",
        "2018-07-21 06:42:34",
        "2018-07-21 06:42:35",
        "2018-07-21 06:42:36",
        "2018-07-21 06:42:37",
    ]
    present_seconds = {p["shot_date"][:19] for p in photos_meta}
    for sec in expected_seconds:
        if sec not in present_seconds:
            missing_dates.append({
                "timestamp_sec": sec,
                "reason": "No downloaded public frame at this second; cached metadata does not establish what the camera captured or why a frame is absent.",
            })

    catalog_data = {
        "metadata_cache_pages": [{"path":str(f),"sha256":compute_sha256(f)} for f in sorted(args.cache_dir.glob("street-sequence-"+args.sequence_id+"*.json"))],
        "metadata_cache_retrieval_time": "not independently verified; catalog retrieval timestamp refers to this collection run",
        "sequence_id": args.sequence_id,
        "target_shot_date_window": f"{args.start_time}..{args.end_time} inclusive",
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_photos_collected": len(photos_meta),
        "total_bytes_downloaded": total_bytes_downloaded,
        "limits_applied": {
            "max_photos": args.max_photos,
            "max_total_mb": args.max_total_mb,
            "within_limits": (len(photos_meta) <= args.max_photos and total_bytes_downloaded <= max_total_bytes),
        },
        "cached_coverage_summary": {
            "total_cached_records": len(records),
            "matching_frames_found": len(photos_meta),
            "sequence_indices": [p["sequence_index"] for p in photos_meta],
            "continuity_note": (
                "Observed public sequence indices are listed above; completeness of camera capture is unverified. "
                "Anchor photo1288770821 is titlematch1 at06:42:32; photo1288771293 is titlematch3 at06:42:38."
            ),
        },
        "api_calls_made": api_calls_made,
        "missing_timestamps_in_window": missing_dates,
        "license": LICENSE_NAME,
        "attribution": ATTRIBUTION,
        "terms_url": TERMS_URL,
        "disclaimer": DISCLAIMER_TEXT,
        "photos": photos_meta,
    }

    # Save photo-catalog.json
    catalog_json_path = args.output_dir / "photo-catalog.json"
    with open(catalog_json_path, "w", encoding="utf-8") as f:
        json.dump(catalog_data, f, indent=2)
    print(f"[INFO] Saved JSON catalog: {catalog_json_path}")

    # Save photo-catalog.md
    catalog_md_path = args.output_dir / "photo-catalog.md"
    md_content = build_catalog_markdown(catalog_data, photos_meta)
    with open(catalog_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"[INFO] Saved Markdown catalog: {catalog_md_path}")

    # 6. Generate contact sheet
    contact_sheet_path = args.output_dir / "contact-sheet.jpg"
    generate_contact_sheet(
        photos_meta,
        contact_sheet_path,
        args.sequence_id,
        f"{args.start_time}..{args.end_time}",
    )

    print("\n[COLLECTED] Bridge collection finished; inspect catalog for missing/failed frames.")
    print(f"  Photos collected: {len(photos_meta)}")
    print(f"  Total bytes: {total_bytes_downloaded} ({total_bytes_downloaded / (1024*1024):.2f} MB)")
    print(f"  Catalog JSON: {catalog_json_path}")
    print(f"  Catalog Markdown: {catalog_md_path}")
    print(f"  Contact Sheet: {contact_sheet_path}")
    return 0 if photos_meta else 1


if __name__ == "__main__":
    sys.exit(main())
