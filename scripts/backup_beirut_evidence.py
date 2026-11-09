"""Create a checksum-verified snapshot of Beirut visual evidence and reports."""
import hashlib
import json
import shutil
import time
from pathlib import Path

ENGINE=Path('C:/private/dynacity-data/engine')
TARGET=Path('C:/private/dynacity-data/backups/beirut-evidence')
PROJECT=Path('C:/Users/PC/DYNAcity')
FOLDERS=['facade-batch-evidence-20261030','appearance-evidence-20261030',
         'appearance-citywide-20261030','facade-reference-20261030',
         'facade-coverage-20261030','appearance-plan-20261030','appearance-passes-20261030']


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def backup():
    TARGET.mkdir(parents=True,exist_ok=True);records=[];changed=[]
    sources=[(path,Path('engine')/name/path.relative_to(ENGINE/name))
             for name in FOLDERS if (ENGINE/name).exists()
             for path in (ENGINE/name).rglob('*') if path.is_file() and not path.name.endswith(('.lock','.tmp','.download'))]
    for path in [PROJECT/'docs/beirut-simulation-work-log.md',PROJECT/'docs/batch-facade-evidence.md',
                 PROJECT/'scripts/collect_batch_facade_evidence.py',PROJECT/'scripts/apply_batch_facade_reviews.py',
                 PROJECT/'scripts/update_simulation_work_page.py']:
        if path.is_file():sources.append((path,Path('project')/path.relative_to(PROJECT)))
    for source,relative in sources:
        dest=TARGET/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        try:
            before=digest(source)
            if not dest.exists() or digest(dest)!=before:
                temp=dest.with_name(dest.name+'.backup-tmp');shutil.copyfile(source,temp)
                if digest(temp)!=before or digest(source)!=before:
                    temp.unlink(missing_ok=True);changed.append(str(source));continue
                temp.replace(dest)
            records.append({'file':relative.as_posix(),'bytes':dest.stat().st_size,'sha256':before})
        except (FileNotFoundError,PermissionError):changed.append(str(source))
    report={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'snapshot_root':str(TARGET),'files_verified':len(records),'bytes_verified':sum(x['bytes'] for x in records),
            'changed_or_unavailable_during_copy':changed,'files':records,
            'limits':['This backup is on the same physical machine, not a separate disaster-recovery copy.',
                      'Some HTML links refer to original absolute paths; original folders remain authoritative.',
                      'Rerun after collection completes to capture new or changing files.']}
    path=TARGET/'backup-manifest.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(report,indent=2),encoding='utf-8');temp.replace(path)
    print(json.dumps({k:v for k,v in report.items() if k!='files'}))


if __name__=='__main__':backup()
