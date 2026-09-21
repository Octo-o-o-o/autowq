#!/usr/bin/env python3
"""Generate disabled Linux/WSL2 Docker-provider config and systemd user timer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


def systemd_quote(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def render(root,runtime):
    root=Path(root).resolve();runtime=Path(runtime).expanduser().resolve()
    if runtime.is_relative_to(root) or root.is_relative_to(runtime):
        raise ValueError('Runtime and project must be separate directories')
    if any(c in str(root)+str(runtime)+sys.executable for c in ('\n','\r','$')):
        raise ValueError('Paths cannot contain line breaks or dollar signs')
    targets=[root/'config'/n for n in ('config.json','profiles.json','autopilot-policy.json')]
    if runtime.exists() or any(p.exists() for p in targets):raise ValueError('Refusing to overwrite existing deployment')
    runtime.mkdir(parents=True,mode=0o700);jobs=runtime/'jobs';jobs.mkdir(mode=0o700)
    script=runtime/'docker_provider.py';shutil.copyfile(root/'scripts/docker_provider.py',script)
    cfg=json.loads((root/'config/config.example.json').read_text())
    cfg['routing']['work_root']=str(jobs)
    profiles=json.loads((root/'config/profiles.example.json').read_text())
    profiles['default']='core-only'
    names=('grok','devin','cursor')
    providers={}
    prefix='autowq-'+hashlib.sha256(str(runtime).encode()).hexdigest()[:10]
    for name in names:
        definition=profiles['providers'][name]
        if name=='grok':argv=['grok','--cwd','{cwd}','--no-subagents','--max-turns','12','--output-format','json','--verbatim','--always-approve','--prompt-file','{prompt}']
        elif name=='devin':argv=['devin','-p','--prompt-file','{prompt}','--permission-mode','dangerous','--respect-workspace-trust','false']
        else:argv=['cursor-agent','--workspace','{cwd}','--model','{model}','--print','--force','--trust','--output-format','json','{prompt}']
        # Cursor accepts prompt text, not a file: a small image-side Python adapter reads it.
        if name=='cursor':
            argv=['python3','-c','import os,sys;from pathlib import Path;p=Path(sys.argv[1]).read_text();os.execvp("cursor-agent",["cursor-agent","--workspace","/work","--model",sys.argv[2],"--print","--force","--trust","--output-format","json",p])','{prompt}','{model}']
        providers[name]={'image':'autowq-'+name+':local','home_volume':prefix+'-'+name,'argv':argv,'timeout_s':definition['timeout_s']-45}
        definition['argv']=[sys.executable,str(script),'--config',str(runtime/'containers.json'),name,'{cwd}','{prompt}','{model}']
    # ZCode's bundled macOS app path has no verified Linux counterpart here.
    del profiles['providers']['zcode'];profiles['presets'].pop('zcode-rich',None)
    for preset in profiles['presets'].values():
        for role,chain in preset['routes'].items():preset['routes'][role]=[n for n in chain if n in names]
    containers={'docker_bin':shutil.which('docker') or '/usr/bin/docker','jobs_root':str(jobs),'providers':providers}
    for path,data in [(targets[0],cfg),(targets[1],profiles),(runtime/'containers.json',containers)]:
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    shutil.copyfile(root/'config/autopilot-policy.example.json',targets[2])
    service='\n'.join(['[Unit]','Description=autowq single research queue tick','After=network-online.target','Wants=network-online.target','','[Service]','Type=oneshot','WorkingDirectory='+str(root).replace('%','%%'),
        'ExecStart='+systemd_quote(sys.executable)+' -m wq run-once --lease 3600',
        'Environment='+systemd_quote('PYTHONPATH='+str(root/'src')),'Environment=PYTHONUNBUFFERED=1',
        'TimeoutStartSec=3700','TimeoutStopSec=15','KillMode=control-group','UMask=0077','SuccessExitStatus=3 6',''])
    (runtime/'autowq.service').write_text(service)
    (runtime/'autowq.timer').write_text('[Unit]\nDescription=autowq research queue timer\n\n[Timer]\nOnBootSec=60\nOnUnitInactiveSec=60\nAccuracySec=5\nUnit=autowq.service\n\n[Install]\nWantedBy=timers.target\n')
    return runtime


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--runtime',type=Path,default=Path.home()/'.local/share/autowq-runtime')
    a=p.parse_args()
    if not sys.platform.startswith('linux'):p.exit(1,'Run this generator inside Linux or WSL2. Existing macOS deployment is unchanged.\n')
    if os.getuid()==0:p.exit(1,'Run as a dedicated non-root user.\n')
    try:r=render(a.root,a.runtime)
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')
    print(f'Generated disabled Docker configuration and systemd units: {r}. Nothing installed or started.')

if __name__=='__main__':main()
