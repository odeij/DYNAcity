import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('unreal_build_cache', Path(__file__).resolve().parents[1] / 'scripts/unreal_build_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def host(root, name='drive-0123456789abcdef'):
    path = root / name / 'HostProject'
    path.mkdir(parents=True)
    (path / 'HostProject.uproject').write_text(json.dumps(cache.EXPECTED_PROJECT))
    for folder in ('Intermediate', 'Plugins/DynaCityDrive/Intermediate', 'Content', 'Plugins/DynaCityDrive/Binaries'):
        target = path / folder
        target.mkdir(parents=True)
        (target / 'keep-or-cache').write_bytes(b'123')
    (path.parent / 'build.log').write_text('evidence')
    return path


def test_only_caches_removed_and_dry_run_preserves(tmp_path):
    path = host(tmp_path)
    assert sum(r['bytes'] for r in cache.cleanup(tmp_path)) == 6
    assert (path / 'Intermediate').exists()
    assert all(r['removed'] for r in cache.cleanup(tmp_path, True))
    assert not (path / 'Intermediate').exists()
    assert (path / 'Content/keep-or-cache').exists()
    assert (path / 'Plugins/DynaCityDrive/Binaries/keep-or-cache').exists()
    assert (path.parent / 'build.log').read_text() == 'evidence'
    assert cache.cleanup(tmp_path, True) == []


def test_invalid_host_aborts_before_any_delete(tmp_path):
    first = host(tmp_path)
    second = host(tmp_path, 'drive-fedcba9876543210')
    (second / 'HostProject.uproject').write_text('{}')
    with pytest.raises(ValueError):
        cache.cleanup(tmp_path, True)
    assert (first / 'Intermediate').exists()


def test_lock_excludes_concurrent_build_and_releases(tmp_path):
    with cache.output_lock(tmp_path):
        with pytest.raises(FileExistsError):
            with cache.output_lock(tmp_path):
                pass
    assert not (tmp_path / '.dynacity-build.lock').exists()


def test_run_filter_preserves_other_host(tmp_path):
    first = host(tmp_path)
    second = host(tmp_path, 'drive-fedcba9876543210')
    cache.cleanup(tmp_path, True, run_name=first.parent.name)
    assert not (first / 'Intermediate').exists()
    assert (second / 'Intermediate').exists()


def test_junction_rejected(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction test')
    path = host(tmp_path / 'builds')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'precious').write_text('keep')
    link = path / 'Intermediate/linked'
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
    try:
        with pytest.raises(ValueError):
            cache.cleanup(tmp_path / 'builds', True)
        assert (outside / 'precious').read_text() == 'keep'
    finally:
        link.rmdir()


@pytest.mark.parametrize('exit_code,keep', [(0, False), (1, False), (0, True)])
def test_builder_retention_on_success_and_failure(tmp_path, monkeypatch, exit_code, keep):
    import importlib
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    builder = importlib.import_module('build_unreal_drive')
    engine = tmp_path / 'UE'
    batch = engine / 'Engine/Build/BatchFiles/Build.bat'
    batch.parent.mkdir(parents=True)
    batch.write_text('fake')
    output = tmp_path / 'builds'

    def fake_build(command, **kwargs):
        project = Path(next(c.removeprefix('-Project=') for c in command if c.startswith('-Project=')))
        (project.parent / 'Intermediate').mkdir()
        (project.parent / 'Intermediate/cache').write_text('cache')
        binaries = project.parent / 'Plugins/DynaCityDrive/Binaries/Win64'
        binaries.mkdir(parents=True)
        (binaries / 'UnrealEditor-DynaCityDrive.dll').write_text('dll')
        return SimpleNamespace(returncode=exit_code, stdout='build evidence', stderr='')

    monkeypatch.setattr(builder.subprocess, 'run', fake_build)
    if exit_code:
        with pytest.raises(SystemExit):
            builder.build(engine, output, keep)
    else:
        builder.build(engine, output, keep)
    run = next(output.glob('drive-*'))
    assert (run / 'HostProject/Intermediate').exists() == keep
    assert (run / 'build.log').read_text().startswith('build evidence')
    assert json.loads((run / 'build-result.json').read_text())['exit_code'] == exit_code
    assert (run / 'HostProject/Plugins/DynaCityDrive/Binaries/Win64/UnrealEditor-DynaCityDrive.dll').exists()
