"""Compile the driving plugin in a private host project without enabling it.

Unreal's supported-toolchain checks remain authoritative. This script never
patches compiler bans or changes the user's running project.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',required=True,help='Unreal installation root, containing Engine/')
    parser.add_argument('--output',required=True,help='Private build/cache directory')
    args=parser.parse_args()
    engine=Path(args.engine).resolve();output=Path(args.output).resolve()
    for path in (engine,output):
        if any(char in str(path) for char in '"&|<>^%!\r\n'):
            raise ValueError('Build paths contain unsupported command-shell characters')
    source=Path(__file__).resolve().parents[1]/'engine'/'unreal'/'Plugins'/'DynaCityDrive'
    batch=engine/'Engine'/'Build'/'BatchFiles'/'Build.bat'
    if not batch.is_file():raise FileNotFoundError(batch)
    digest=hashlib.sha256()
    files=[p for p in source.rglob('*') if p.is_file() and (p.suffix in ('.h','.cpp','.cs','.uplugin'))]
    for path in sorted(files):digest.update(str(path.relative_to(source)).encode());digest.update(path.read_bytes())
    signature=digest.hexdigest();host=output/('drive-'+signature[:16])/'HostProject'
    target=host/'Plugins'/'DynaCityDrive'
    target.mkdir(parents=True,exist_ok=True)
    for path in files:
        destination=target/path.relative_to(source);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,destination)
    project=host/'HostProject.uproject'
    expected={'FileVersion':3,'Plugins':[{'Name':'DynaCityDrive','Enabled':True}]}
    if project.exists() and json.loads(project.read_text())!=expected:
        raise RuntimeError('Host project differs from generated build; refusing to overwrite it')
    project.write_text(json.dumps(expected,indent=2),encoding='utf-8')
    command=[str(batch),'UnrealEditor','Win64','Development','-Project='+str(project),
        '-Plugin='+str(target/'DynaCityDrive.uplugin'),'-NoHotReloadFromIDE',
        '-NoUBTMakefiles','-MaxParallelActions=2','-NoXGE']
    result=subprocess.run(command,check=False,capture_output=True,text=True,errors='replace')
    log=host.parent/'build.log';log.write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    binaries=list((target/'Binaries'/'Win64').glob('UnrealEditor-DynaCityDrive*.dll'))
    summary={'source_signature':signature,'exit_code':result.returncode,'host_project':str(project),
             'plugin':str(target),'editor_binaries':[str(p) for p in binaries],'log':str(log),
             'compiled':result.returncode==0 and bool(binaries),'runtime_verified':False}
    (host.parent/'build-result.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
    if not summary['compiled']:raise SystemExit(result.returncode or 1)


if __name__=='__main__':main()
