"""Local, bounded ANUGA job execution from self-contained documented ZIP packages."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile

import numpy as np

from .flood_solver import RunManifest, load_inputs


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_TRIANGLES = 20000
MAX_FRAMES = 240


class JobError(ValueError):
    pass


def package_inputs(manifest_path: str | Path, destination: str | Path) -> Path:
    """Package existing validated inputs without modifying any physical arrays."""
    path = Path(manifest_path).resolve()
    m, _, _, _ = load_inputs(path)
    if m.purpose != "real_data":
        raise JobError("Benchmark inputs cannot be packaged for the city interface.")
    destination = Path(destination).resolve()
    repo = Path(__file__).resolve().parents[2]
    if destination == repo or repo in destination.parents:
        raise JobError("Input packages must be saved outside the repository.")
    manifest = m.model_dump(mode="json")
    files: dict[Path, str] = {}
    def include(value):
        file = (path.parent / value).resolve()
        if file not in files:
            files[file] = f"inputs/{len(files)}-{file.name}"
        return files[file]
    manifest["mesh"] = include(m.mesh)
    manifest["forcing"] = include(m.forcing)
    if m.drainage:
        manifest['drainage']['network'] = include(m.drainage.network)
    for name, source in m.sources.items():
        manifest["sources"][name]["path"] = include(source.path)
    if sum(file.stat().st_size for file in files) > MAX_EXPANDED_BYTES:
        raise JobError("Sources exceed the supported package size.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        for file, name in files.items():
            archive.write(file, name)
    if len(buffer.getvalue()) > MAX_UPLOAD_BYTES:
        raise JobError("Compressed package exceeds 50 MB.")
    with destination.open("xb") as stream:
        stream.write(buffer.getvalue())
    return destination


def _relative(value: str) -> str:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value or ":" in value:
        raise JobError("Package paths must be relative and stay inside the ZIP.")
    return str(p)


def _extract(payload: bytes, directory: Path):
    if len(payload) > MAX_UPLOAD_BYTES:
        raise JobError("Input package exceeds 50 MB.")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
            raise JobError("Input package expands beyond the supported limit.")
        names = set()
        for entry in entries:
            name = _relative(entry.filename)
            if name in names or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                raise JobError("Duplicate paths, symlinks and encrypted entries are unsupported.")
            names.add(name)
            target = directory / name
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as src, target.open("xb") as dest:
                    shutil.copyfileobj(src, dest)
    if not (directory / "manifest.json").is_file():
        raise JobError("ZIP must contain manifest.json at its root, with mesh, forcing and source files.")


@dataclass
class Job:
    job_id: str
    directory: Path
    manifest: RunManifest
    hashes: dict
    triangles: int
    frames: int
    duration_s: float
    state: str = "ready"
    process: subprocess.Popen | None = None
    started: float | None = None
    error: str | None = None


class FloodJobs:
    """One active subprocess per local server; no arbitrary server file paths."""

    def __init__(self, root: str | Path, *, timeout_s: float = 900):
        self.root = Path(root).resolve()
        repo = Path(__file__).resolve().parents[2]
        if self.root == repo or repo in self.root.parents:
            raise JobError("Flood run storage must be outside the repository.")
        if timeout_s <= 0 or not np.isfinite(timeout_s):
            raise JobError("A finite positive solver timeout is required.")
        self.timeout_s = timeout_s
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()

    def prepare(self, payload: bytes) -> dict:
        with self.lock:
            if len(self.jobs) >= 20:
                raise JobError("This session already holds 20 input packages; restart the session to accept more.")
            self.root.mkdir(parents=True, exist_ok=True)
            directory = self.root / uuid.uuid4().hex
            inputs = directory / "inputs"
            inputs.mkdir(parents=True)
            try:
                _extract(payload, inputs)
                manifest_path = inputs / "manifest.json"
                declared = json.loads(manifest_path.read_text())
                if not isinstance(declared, dict):
                    raise JobError("manifest.json must contain a run description object.")
                if declared.get("schema") == "dynacity.real-input-acquisition.v1":
                    raise JobError("This package is a data inventory, not a simulation. It has no hydraulic mesh or matched event forcing.")
                m = RunManifest.model_validate_json(manifest_path.read_text())
                if m.purpose != "real_data":
                    raise JobError("Numerical benchmarks cannot be run or mapped as city floods.")
                for value in [m.mesh, m.forcing, *(s.path for s in m.sources.values())]:
                    _relative(value)
                if m.drainage:
                    _relative(m.drainage.network)
                expanded_arrays = 0
                for value in (m.mesh, m.forcing):
                    with zipfile.ZipFile(inputs / value) as npz:
                        expanded_arrays += sum(e.file_size for e in npz.infolist())
                if expanded_arrays > MAX_EXPANDED_BYTES:
                    raise JobError("Numerical array files expand beyond the supported limit.")
                m, mesh, forcing, hashes = load_inputs(manifest_path)
                n, k = len(mesh["triangles"]), len(forcing["times_s"])
                if n > MAX_TRIANGLES or k > MAX_FRAMES:
                    raise JobError(f"Interactive runs support at most {MAX_TRIANGLES} triangles and {MAX_FRAMES} frames.")
                # Check package/runtime independently. No missing runtime gets a fake fallback.
                import anuga  # noqa: F401
                if m.drainage:
                    from .flood_coupling import runtime_path
                    runtime_path()
                job = Job(directory.name, directory, m, hashes, n, k, float(forcing["times_s"][-1]))
                self.jobs[job.job_id] = job
                return self.status(job.job_id)
            except Exception:
                shutil.rmtree(directory)
                raise

    def _job(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise KeyError("Unknown flood run in this session.")
        return self.jobs[job_id]

    def _refresh(self, job: Job):
        if job.state != "running":
            return
        code = job.process.poll()
        if code is None and time.monotonic() - job.started > self.timeout_s:
            job.process.kill()
            job.process.wait()
            job.state, job.error = "failed", "Solver exceeded the run time limit; no results accepted."
        elif code is not None:
            audit_path = job.directory / "output" / "audit.json"
            try:
                audit = json.loads(audit_path.read_text()) if audit_path.exists() else {}
            except (ValueError, OSError):
                job.state, job.error = "failed", "Solver audit is incomplete; no results accepted."
                return
            if code == 0 and audit.get("status") == "complete" and audit.get("purpose") == "real_data" and job.manifest.purpose == "real_data":
                artifact = job.directory / "output" / "surface.npz"
                if artifact.exists() and hashlib.sha256(artifact.read_bytes()).hexdigest() == audit.get("output_sha256"):
                    job.state = "complete"
                else:
                    job.state, job.error = "failed", "Computed output is missing or failed its checksum."
            else:
                job.state = "failed"
                job.error = ("Benchmark outputs cannot be mapped as city floods." if audit.get("purpose") == "numerical_benchmark"
                             else audit.get("error", "Solver failed before producing accepted results. See the local solver log."))

    def status(self, job_id: str) -> dict:
        with self.lock:
            job = self._job(job_id)
            self._refresh(job)
            progress = job.directory / "output" / "progress.json"
            return {"job_id": job.job_id, "status": job.state, "run_id": job.manifest.run_id,
                    "triangles": job.triangles, "frames": job.frames, "duration_s": job.duration_s,
                    "scope": job.manifest.scope, "calibration": "not_established", "error": job.error,
                    "dynamic_drainage_coupling":job.manifest.drainage is not None,
                    "progress": json.loads(progress.read_text()) if progress.exists() else None,
                    "sources": {k: {"evidence":v.evidence, "description":v.description}
                                for k,v in job.manifest.sources.items()}}

    def start(self, job_id: str) -> dict:
        with self.lock:
            job = self._job(job_id)
            if job.state != "ready":
                raise JobError("Only a checked input package can start; each package runs once.")
            if job.manifest.purpose != "real_data":
                raise JobError("Benchmark jobs cannot be started from the city interface.")
            for other in self.jobs.values():
                self._refresh(other)
                if other.state == "running":
                    raise JobError("Another flood calculation is running. Wait or cancel it first.")
            _, _, _, hashes = load_inputs(job.directory / "inputs" / "manifest.json")
            if hashes != job.hashes:
                raise JobError("Inputs changed after checking; load the original package again.")
            env = os.environ.copy()
            env.update(OMP_NUM_THREADS="1", MPLCONFIGDIR=str(job.directory / "matplotlib-cache"))
            with (job.directory / "solver.log").open("wb") as log:
                job.process = subprocess.Popen(
                    [sys.executable, "-m", "dynacity.flood_solver", "--manifest",
                     str(job.directory / "inputs" / "manifest.json"), "--output-dir", str(job.directory / "output")],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                    cwd=Path(__file__).resolve().parents[2])
            job.started, job.state = time.monotonic(), "running"
            # Deadline enforcement continues even when a browser closes.
            threading.Thread(target=self._deadline, args=(job,), daemon=True).start()
            return self.status(job_id)

    def _deadline(self, job: Job):
        try:
            job.process.wait(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            pass
        with self.lock:
            self._refresh(job)

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self._job(job_id)
            self._refresh(job)
            if job.state == "running":
                job.process.terminate()
                try:
                    job.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    job.process.kill(); job.process.wait()
                job.state = "cancelled"
            return self.status(job_id)

    def _result(self, job_id: str):
        job = self._job(job_id)
        self._refresh(job)
        if job.state != "complete":
            raise JobError("Computed results are available only after a successful run.")
        artifact = job.directory / "output" / "surface.npz"
        audit = json.loads((artifact.parent / "audit.json").read_text())
        if audit.get("purpose") != "real_data" or job.manifest.purpose != "real_data":
            raise JobError("Benchmark outputs cannot be mapped as city floods.")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != audit.get("output_sha256"):
            raise JobError("Output checksum mismatch; results refused.")
        return job, artifact, audit

    def geometry(self, job_id: str) -> dict:
        with self.lock:
            job, artifact, audit = self._result(job_id)
            from pyproj import Transformer
            with np.load(artifact, allow_pickle=False) as data:
                points, triangles = data["points_m"], data["triangles"]
                lon, lat = Transformer.from_crs(job.manifest.crs, "EPSG:4326", always_xy=True).transform(points[:,0], points[:,1])
                coordinates = np.column_stack([lon,lat])[triangles]
                if not np.isfinite(coordinates).all():
                    raise JobError("Result coordinates cannot be mapped.")
                return {"polygons": coordinates.tolist(), "timestamps": audit["timestamps"],
                        "times_s": data["times_s"].tolist(), "volumes_m3": data["volume_m3"].tolist(),
                        "scope": job.manifest.scope, "engine": audit["engine"], "version": audit["version"],
                        "mass_residual_m3": audit["max_mass_residual_m3"], "warnings": audit["warnings"],
                        "dynamic_drainage_coupling":audit.get('dynamic_drainage_coupling',False),
                        "drainage_engine":audit.get('drainage_engine'),
                        "combined_mass_residual_m3":audit.get('max_combined_mass_residual_m3'),
                        "render_reference": "Computed depth colors on native triangles; no absolute water-surface elevation is inferred."}

    def frame(self, job_id: str, index: int) -> dict:
        with self.lock:
            job, artifact, _ = self._result(job_id)
            if index < 0 or index >= job.frames:
                raise JobError("Frame outside computed output times.")
            with np.load(artifact, allow_pickle=False) as data:
                depth, velocity = data["depth_m"][index], data["velocity_m_s"][index]
                def finite(values):
                    return [float(v) if np.isfinite(v) else None for v in values]
                return {"index": index, "time_s": float(data["times_s"][index]),
                        "depth_m": finite(depth), "speed_m_s": finite(np.linalg.norm(velocity,axis=1)),
                        "velocity_m_s":[finite(row) for row in velocity]}

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                if job.state == "running":
                    self.cancel(job.job_id)
