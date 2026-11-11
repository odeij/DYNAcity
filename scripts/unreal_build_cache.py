"""Remove only generated Intermediate caches from validated private build hosts."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

BUILD_ROOTS = ('drive-build', 'drive-builds', 'minimap-builds', 'mobility-builds',
               'asset-quality-builds', 'minimap-quality-builds', 'ambient-builds',
               'title-menu-builds', 'controls-builds')
EXPECTED_PROJECT = {'FileVersion': 3, 'Plugins': [{'Name': 'DynaCityDrive', 'Enabled': True}]}


@contextmanager
def output_lock(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.dynacity-build.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        lock.unlink()


def cache_targets(output):
    """Resolve and validate every target before the caller removes anything."""
    output = Path(output).absolute()
    # Reject links/junctions at every level, including ancestors of the supplied root.
    def checked(path):
        for part in (path, *path.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ValueError(f'Refusing linked path: {part}')
        resolved = path.resolve()
        if not resolved.is_relative_to(output.resolve()):
            raise ValueError(f'Target escapes build output: {resolved}')
        return resolved

    checked(output)
    targets = []
    for run in sorted(output.glob('drive-*')):
        if not re.fullmatch(r'drive-[0-9a-f]{16}', run.name):
            continue
        host = run / 'HostProject'
        checked(host)
        descriptor = host / 'HostProject.uproject'
        if not descriptor.is_file():
            continue
        checked(descriptor)
        if json.loads(descriptor.read_text(encoding='utf-8')) != EXPECTED_PROJECT:
            raise ValueError(f'Unexpected host descriptor: {descriptor}')
        for target in (host / 'Intermediate', host / 'Plugins' / 'DynaCityDrive' / 'Intermediate'):
            checked(target)
            if target.is_dir():
                # shutil must never traverse a nested reparse point either.
                for base, dirs, files in os.walk(target, followlinks=False):
                    for name in dirs + files:
                        checked(Path(base) / name)
                targets.append(target)
    return targets


def cleanup(output, apply=False, run_name=None):
    records = []
    targets = cache_targets(output)
    if run_name is not None:
        targets = [p for p in targets if p.relative_to(Path(output).resolve()).parts[0] == run_name]
    for target in targets:
        size = sum(p.stat().st_size for p in target.rglob('*') if p.is_file())
        record = {'path': str(target), 'bytes': size, 'removed': False}
        records.append(record)
        if apply:
            shutil.rmtree(target)
            record['removed'] = True
    return records


def assert_no_builds(engine_root):
    """Fail closed when Windows process state cannot be inspected."""
    if os.name != 'nt':
        raise RuntimeError('Manual cleanup process guard currently requires Windows')
    command = "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('cl.exe','MSBuild.exe','UnrealBuildTool.exe','UnrealEditor.exe','UnrealEditor-Cmd.exe','dotnet.exe') } | Select-Object Name,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                            capture_output=True, text=True, check=True)
    processes = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(processes, dict):
        processes = [processes]
    for process in processes:
        name = process['Name'].lower()
        cmd = (process.get('CommandLine') or '').lower().replace('/', '\\')
        build = name in ('cl.exe', 'msbuild.exe', 'unrealbuildtool.exe') or (name == 'dotnet.exe' and 'unrealbuildtool' in cmd)
        temporary_editor = name.startswith('unrealeditor') and (not cmd or 'hostproject' in cmd)
        if build or temporary_editor:
            raise RuntimeError('Build or temporary host editor may be active; retry after it exits')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Delete caches; default is dry run')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    assert_no_builds(args.engine_root)
    records = []
    for name in BUILD_ROOTS:
        output = args.engine_root / name
        if output.is_dir():
            # Validate before creating a lock under the supplied path.
            cache_targets(output)
            with output_lock(output):
                records.extend(cleanup(output, args.apply))
    report = {'applied': args.apply, 'bytes': sum(r['bytes'] for r in records), 'targets': records}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
