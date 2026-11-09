"""Run the Tari'ak temporal-holdout climatology baseline and write its evidence folder.

Writes observation-baseline.json (deterministic, no timestamps), results.md, run-metadata.json
(append-only run history: times, elapsed, peak memory) and manifest.json. Other files in the folder
(for example a hand-written handoff.md or run-log.txt) are not written or changed by this script.
A folder that already holds a completed result is only rewritten by a run with the same source,
configuration and way set; anything else is refused (exit 3) so use a new --output folder.
Exit codes: 0 completed, 1 unreadable input or bad configuration, 2 blocked, 3 refused to overwrite.
"""
import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dynacity.observation_baseline import (
    BaselineConfig, BaselineInputError, canonical_json, evaluate, load_road_domain, sha256_file,
)

DEFAULT_VELOCITIES = Path("C:/private/dynacity-data/traffic/tariak/velocities.csv")
DEFAULT_OUTPUT = Path("C:/private/dynacity-data/engine/goal-20261031/02-baseline")


def peak_memory():
    """Peak process memory from the OS high-water mark, or an explicit 'unavailable'."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {"available": True, "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
                    "peak_pagefile_bytes": int(counters.PeakPagefileUsage),
                    "method": "Windows GetProcessMemoryInfo PeakWorkingSetSize / PeakPagefileUsage "
                              "(OS-maintained high-water marks for this process, read once at exit)"}
    else:
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return {"available": True, "peak_working_set_bytes": int(peak * (1 if sys.platform == "darwin" else 1024)),
                    "method": "resource.getrusage ru_maxrss (OS-maintained peak resident set)"}
        except ImportError:
            pass
    return {"available": False, "method": "no OS peak-memory counter available on this platform"}


def fmt(value, digits=3):
    return "n/a" if value is None else f"{value:.{digits}f}"


def domain_line(d, rows):
    if d["mode"] == "source_file_wide":
        return ("Domain: **whole source file** (Lebanon dataset per its data package; geographic footprint of the file unverified). "
                "File-wide scores are not Beirut targets.")
    return (f"Domain: **{d['unique_way_ids']:,} unique way IDs of `{d['model_file_name']}`** (model sha256 `{d['model_sha256'][:16]}...`, "
            f"way-ID-set sha256 `{d['way_id_set_sha256'][:16]}...`; IDs only). {d['selected_valid_rows']:,} valid rows are on those ways, "
            f"{d['valid_rows_outside_domain']:,} valid rows on other ways are excluded; {d['selected_unique_ways_with_valid_rows']:,} of the "
            f"selected ways have valid rows. Not a whole-city or national statement.")


def results_markdown(report):
    r, cfg = report["results"], report["config"]
    lines = ["# Tari'ak temporal-holdout baseline - results", "",
             f"Status: **{report['status']}**" + (f" ({report['blocked_reason']})" if report["blocked_reason"] else ""),
             "", "**Historical climatology baseline. Not a simulation, not SUMO validation, not an accepted accuracy target.**", "",
             domain_line(report["domain"], report["rows"]), "",
             f"Calibration years {cfg['calibration_years']}, held-out years {cfg['holdout_years']}, min count {cfg['min_count']}, "
             f"speed cap {cfg['speed_cap']} (config hash `{cfg['config_sha256'][:16]}`).",
             f"Source `{report['source']['file_name']}` sha256 `{report['source']['sha256'][:16]}...`; "
             f"valid rows {report['rows']['valid_rows']:,} of {report['rows']['total_data_rows']:,} "
             f"({report['rows']['rejected_rows']:,} rejected).", ""]
    if r and "baselines" in r:
        lines += [f"Held-out cells: {r['heldout_cells_total']:,} total, {r['heldout_cells_eligible']:,} eligible "
                  f"(>= {cfg['min_count']} readings); {r['heldout_cells_below_min_count']:,} below the minimum and not scored "
                  f"({r['heldout_readings_in_below_min_count_cells']:,} readings). Cells scored by every baseline: {r['common_scored_cells']:,}.",
                  "Speeds are in the file's own numeric units (unit unresolved; see JSON).", "",
                  "| Baseline | Scored / eligible | MAE | Median ratio | Cell medians in p10-p90 | Readings in p10-p90 | MAE on common cells |",
                  "|---|---|---|---|---|---|---|"]
        for name, b in r["baselines"].items():
            m, band = b["metrics"], b["calibration_band_p10_p90"]
            lines.append(f"| {name} | {b['scored_cells']:,} / {r['heldout_cells_eligible']:,} | {fmt(m.get('mae'))} | "
                         f"{fmt(m.get('median_ratio_pred_over_obs'))} | {fmt(band['heldout_cell_median_inside'])} | "
                         f"{fmt(band['heldout_readings_inside'])} | {fmt(b['metrics_on_common_cells'].get('mae'))} |")
    lines += ["", "## Limitations", *[f"- {t}" for t in report["limitations"]], ""]
    return "\n".join(lines)


def atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def identity(report):
    """Stable identity of a result: source file hash plus configuration (and way-set) hash."""
    return report["source"]["sha256"], report["config"]["config_sha256"]


def overwrite_refusal(path: Path, report):
    """Why an existing completed result must not be replaced by this run, or None."""
    if not path.exists():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        old = identity(existing)
    except (OSError, ValueError, KeyError, TypeError):
        return "the existing observation-baseline.json is unreadable or from an unknown schema."
    if existing.get("status") != "completed" or old == identity(report):
        return None
    return ("it holds a completed result for a different source file, configuration or way set "
            f"(existing source {old[0][:12]} config {old[1][:12]}; this run source {identity(report)[0][:12]} "
            f"config {identity(report)[1][:12]}).")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--velocities", type=Path, default=DEFAULT_VELOCITIES)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--calibration-years", type=int, nargs="+", default=list(BaselineConfig().calibration_years))
    p.add_argument("--holdout-years", type=int, nargs="+", default=list(BaselineConfig().holdout_years))
    p.add_argument("--min-count", type=int, default=BaselineConfig().min_count)
    p.add_argument("--speed-cap", type=float, default=BaselineConfig().speed_cap)
    p.add_argument("--tolerance-absolute", type=float, default=None, help="illustrative setting; not an accepted target")
    p.add_argument("--tolerance-relative", type=float, default=None, help="illustrative setting; not an accepted target")
    p.add_argument("--traffic-model", type=Path, default=None,
                   help="optional: restrict to the unique roads[*].way IDs of this traffic model (IDs only; its speeds are never read)")
    p.add_argument("--session-id", default=None, help="coordinator session id recorded in run-metadata and manifest")
    p.add_argument("--chunk-rows", type=int, default=200_000)
    args = p.parse_args(argv)

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    config = BaselineConfig(tuple(args.calibration_years), tuple(args.holdout_years), args.min_count, args.speed_cap,
                            args.tolerance_absolute, args.tolerance_relative)
    try:
        t_hash = time.perf_counter()
        digest = sha256_file(args.velocities)
        hash_s = time.perf_counter() - t_hash
        domain = load_road_domain(args.traffic_model) if args.traffic_model else None
        report = evaluate(args.velocities, config, args.chunk_rows, source_sha256=digest, domain=domain)
    except (BaselineInputError, OSError) as exc:
        print(f"error: cannot evaluate, nothing was written: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - t0
    out = args.output
    refusal = overwrite_refusal(out / "observation-baseline.json", report)
    if refusal:
        print(f"error: refusing to overwrite {out}: {refusal} Use a new --output folder; nothing was written.", file=sys.stderr)
        return 3
    out.mkdir(parents=True, exist_ok=True)
    body = canonical_json(report)
    atomic_write(out / "observation-baseline.json", body)
    json_hash = sha256_file(out / "observation-baseline.json")
    atomic_write(out / "results.md", results_markdown(report))

    meta_path = out / "run-metadata.json"
    history = json.loads(meta_path.read_text(encoding="utf-8"))["runs"] if meta_path.exists() else []
    memory = peak_memory()
    history.append({
        "started_utc": started.isoformat(timespec="seconds"),
        "elapsed_seconds_total": round(elapsed, 2), "elapsed_seconds_source_hash": round(hash_s, 2),
        "peak_memory": memory, "chunk_rows": args.chunk_rows, "session_id": args.session_id,
        "command": [sys.executable, *sys.argv],
        "python": platform.python_version(), "platform": platform.platform(),
        "observation_baseline_json_sha256": json_hash, "status": report["status"],
        "note": "Elapsed time includes a separate source-hash pass; both runs used the same code/environment unless module_sha256 differs.",
    })
    atomic_write(meta_path, canonical_json({"runs": history}))
    artifacts = {name: sha256_file(out / name) for name in ("observation-baseline.json", "results.md", "run-metadata.json")}
    atomic_write(out / "manifest.json", canonical_json({
        "session": "tariak-temporal-holdout-baseline", "session_id": args.session_id,
        "domain_mode": report["domain"]["mode"], "output_folder": str(out),
        "artifact_sha256": artifacts,
        "inputs": {"velocities_sha256": digest, "module_sha256": report["lineage"]["module_sha256"]},
        "deterministic_artifact": "observation-baseline.json",
        "note": "run-metadata.json and manifest.json change per run (timestamps/history); observation-baseline.json must not.",
    }))
    print(f"{report['status']}: {out / 'observation-baseline.json'} sha256={json_hash}")
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
