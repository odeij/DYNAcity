"""Job boundaries use analytical test packages, never invented Beirut inputs."""
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from dynacity.flood_jobs import FloodJobs, Job, JobError, _extract, package_inputs
from dynacity.flood_solver import load_inputs
from dynacity.service import create_app
from test_flood_solver import benchmark, requires_engine


def zip_bytes(entries):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        for name,content in entries.items(): z.writestr(name,content)
    return stream.getvalue()


def benchmark_package(tmp_path,**kwargs):
    path,m=benchmark(tmp_path,**kwargs)
    m['mesh']='mesh.npz';m['forcing']='forcing.npz'
    for source in m['sources'].values():source['path']='analytical-reference.txt'
    entries={name:(tmp_path/name).read_bytes() for name in ('mesh.npz','forcing.npz','analytical-reference.txt')}
    entries['manifest.json']=json.dumps(m).encode()
    return zip_bytes(entries)


def test_city_job_rejects_benchmark_without_writing_a_run(tmp_path):
    manager=FloodJobs(tmp_path/'jobs')
    with pytest.raises(JobError,match='benchmarks'):
        manager.prepare(benchmark_package(tmp_path))
    assert manager.jobs=={}
    assert list(manager.root.iterdir())==[]


def test_packager_cannot_promote_benchmark_to_city_inputs(tmp_path):
    path,_=benchmark(tmp_path)
    with pytest.raises(JobError,match='Benchmark'):
        package_inputs(path,tmp_path/'must-not-exist.zip')
    assert not (tmp_path/'must-not-exist.zip').exists()


@pytest.mark.parametrize('entry',['../escape.txt','/tmp/escape.txt','a/../../escape.txt','a\\escape.txt'])
def test_zip_cannot_write_outside_package(tmp_path,entry):
    manager=FloodJobs(tmp_path/'jobs')
    with pytest.raises(JobError,match='relative'):
        manager.prepare(zip_bytes({entry:'not data'}))
    assert not (tmp_path/'escape.txt').exists()


def test_symlink_and_duplicate_archive_paths_are_rejected(tmp_path):
    for mode in ('symlink','duplicate'):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            if mode=='symlink':
                info=zipfile.ZipInfo('source');info.external_attr=(stat.S_IFLNK|0o777)<<16
                z.writestr(info,'/tmp/other')
            else:
                z.writestr('manifest.json','{}');z.writestr('./manifest.json','{}')
        with pytest.raises(JobError):FloodJobs(tmp_path/'jobs').prepare(stream.getvalue())


def test_inventory_is_not_misrepresented_as_a_simulation(tmp_path):
    package=zip_bytes({'manifest.json':json.dumps({'schema':'dynacity.real-input-acquisition.v1','planning_ready':False})})
    with pytest.raises(JobError,match='data inventory'):
        FloodJobs(tmp_path/'jobs').prepare(package)


def test_missing_input_manifest_is_reported_and_package_removed(tmp_path):
    manager=FloodJobs(tmp_path/'jobs')
    with pytest.raises(JobError,match='manifest.json'):
        manager.prepare(zip_bytes({'notes.txt':'No numerical inputs supplied.'}))
    assert list(manager.root.iterdir())==[]


def test_api_exposes_checks_and_refuses_incomplete_package(tmp_path):
    manager=FloodJobs(tmp_path/'jobs')
    with TestClient(create_app(object(),flood_jobs=manager)) as client:
        assert client.get('/v1/flood/solver/inputs').json()['execution_enabled'] is True
        response=client.post('/v1/flood/solver/packages',content=zip_bytes({'manifest.json':'{}'}))
        assert response.status_code==422
        assert any('mesh' in issue for issue in response.json()['detail']['issues'])
        assert client.get('/v1/flood/solver/jobs/unknown').status_code==404
        assert client.post('/v1/flood/solver/jobs/unknown/start').status_code==404
    assert manager.jobs=={}


def test_api_rejects_remote_execution_and_disabled_runtime(tmp_path):
    client=TestClient(create_app(object()))
    assert client.get('/v1/flood/solver/inputs').json()['execution_enabled'] is False
    assert client.post('/v1/flood/solver/packages',content=b'').status_code==503
    remote=TestClient(create_app(object(),flood_jobs=FloodJobs(tmp_path/'jobs')),client=('192.0.2.1',80))
    assert remote.post('/v1/flood/solver/packages',content=b'').status_code==403


class WaitingProcess:
    def __init__(self):self.code=None;self.terminated=False;self.killed=False
    def poll(self):return self.code
    def terminate(self):self.terminated=True;self.code=-15
    def kill(self):self.killed=True;self.code=-9
    def wait(self,timeout=None):return self.code


def registered_benchmark(tmp_path,manager,**kwargs):
    directory=manager.root/'analytical-check';inputs=directory/'inputs';inputs.mkdir(parents=True)
    _extract(benchmark_package(tmp_path,**kwargs),inputs)
    m,mesh,forcing,hashes=load_inputs(inputs/'manifest.json')
    job=Job('analytical-check',directory,m,hashes,len(mesh['triangles']),len(forcing['times_s']),float(forcing['times_s'][-1]))
    manager.jobs[job.job_id]=job
    return job


def test_cancel_and_timeout_do_not_accept_partial_results(tmp_path):
    manager=FloodJobs(tmp_path/'jobs',timeout_s=1)
    job=registered_benchmark(tmp_path,manager)
    job.process=WaitingProcess();job.started=time.monotonic();job.state='running'
    assert manager.cancel(job.job_id)['status']=='cancelled'
    assert job.process.terminated
    with pytest.raises(JobError,match='successful'):manager.geometry(job.job_id)
    job.process=WaitingProcess();job.started=time.monotonic()-2;job.state='running'
    assert manager.status(job.job_id)['status']=='failed'
    assert job.process.killed


@requires_engine
def test_actual_worker_computes_benchmark_but_cannot_publish_it_as_city_flood(tmp_path):
    manager=FloodJobs(tmp_path/'jobs')
    job=registered_benchmark(tmp_path,manager)
    # Exercise the real worker process directly as a mathematical test; the city
    # start endpoint correctly rejects this purpose and cannot create this job.
    job.process=subprocess.Popen([sys.executable,'-m','dynacity.flood_solver','--manifest',
        str(job.directory/'inputs'/'manifest.json'),'--output-dir',str(job.directory/'output')],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    job.started=time.monotonic();job.state='running'
    assert job.process.wait(timeout=30)==0
    assert json.loads((job.directory/'output'/'audit.json').read_text())['status']=='complete'
    result=manager.status(job.job_id)
    assert result['status']=='failed' and 'Benchmark' in result['error']
    with pytest.raises(JobError):manager.geometry(job.job_id)


@requires_engine
def test_native_export_preserves_computed_triangles_times_depth_and_unknown_velocity(tmp_path,monkeypatch):
    from dynacity.flood_solver import run_surface
    import numpy as np
    manager=FloodJobs(tmp_path/'jobs');job=registered_benchmark(tmp_path,manager,dam=True)
    artifact=run_surface(job.directory/'inputs'/'manifest.json',job.directory/'output')
    audit=json.loads((artifact.parent/'audit.json').read_text())
    # Unit-test only the export transformation against genuinely computed
    # mathematical output. The separate tests above enforce the production
    # purpose gate; no benchmark is ever exposed in the running city app.
    monkeypatch.setattr(manager,'_result',lambda job_id:(job,artifact,audit))
    geometry=manager.geometry(job.job_id)
    frame=manager.frame(job.job_id,0)
    with np.load(artifact,allow_pickle=False) as native:
        assert geometry['timestamps']==audit['timestamps']
        assert len(geometry['polygons'])==len(native['triangles'])
        assert all(len(poly)==3 and all(len(p)==2 for p in poly) for poly in geometry['polygons'])
        np.testing.assert_equal(frame['depth_m'],native['depth_m'][0])
        assert frame['time_s']==native['times_s'][0]
        assert all(speed is None for depth,speed in zip(frame['depth_m'],frame['speed_m_s']) if depth==0)
        for exported,computed in zip(frame['velocity_m_s'],native['velocity_m_s'][0]):
            assert exported == [float(v) if np.isfinite(v) else None for v in computed]
    with pytest.raises(JobError,match='Frame'):manager.frame(job.job_id,-1)
