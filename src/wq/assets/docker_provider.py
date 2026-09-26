#!/usr/bin/env python3
"""Run one owned provider container, with bounded lifetime and no host credentials mount."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


def command(config, provider, workdir, prompt, model):
    work = Path(workdir).resolve()
    jobs = Path(config['jobs_root']).resolve()
    source = Path(prompt)
    if not work.is_relative_to(jobs) or work == jobs or source.is_symlink():
        raise ValueError('Only a single screened task directory may be mounted')
    source = source.resolve()
    if not source.is_relative_to(work) or not source.is_file():
        raise ValueError('Prompt must be a regular file inside the task directory')
    if any(p.is_symlink() for p in work.rglob('*')):
        raise ValueError('Task directory contains symlinks')
    if ',' in str(work):
        raise ValueError('Docker mount source must not contain commas')
    item = config['providers'][provider]
    image, volume = item['image'], item['home_volume']
    if not image or image.startswith('-') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]+', volume):
        raise ValueError('Invalid image or isolated home volume')
    ttl = item.get('timeout_s', 900)
    if type(ttl) is not int or not 1 <= ttl <= 3600:
        raise ValueError('Container timeout must be 1..3600 seconds')
    name = 'autowq-' + hashlib.sha256((str(work)+provider).encode()).hexdigest()[:24]
    substitutions = {'{cwd}':'/work', '{prompt}':'/work/'+source.relative_to(work).as_posix(), '{model}':model}
    argv = item['argv']
    if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv):
        raise ValueError('Provider argv must be a nonempty string array')
    for a,b in substitutions.items(): argv=[x.replace(a,b) for x in argv]
    cmd = [config.get('docker_bin','docker'),'run','--rm','--init','--pull=never','--name',name,
           '--label','autowq.managed=true','--read-only','--cap-drop=ALL',
           '--security-opt=no-new-privileges','--pids-limit=256','--memory=2g','--cpus=2',
           '--user',f'{os.getuid()}:{os.getgid()}',
           '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
           '--mount',f'type=bind,source={work},target=/work',
           '--mount',f'type=volume,source={volume},target=/home/agent',
           '--workdir','/work','--env','HOME=/home/agent',
           '--entrypoint','/usr/bin/timeout',image,'--signal=TERM','--kill-after=5',str(ttl),*argv]
    return cmd, name, ttl


def run(config, provider, workdir, prompt, model=''):
    argv, name, ttl = command(config,provider,workdir,prompt,model)
    docker=argv[0]
    # A surviving container belongs to the old attempt. Never replace/restart it.
    existing=subprocess.run([docker,'container','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
    if existing.returncode==0:
        raise ValueError('Previous container exists; reconcile it before retrying')
    proc=None
    previous={}
    def stop(signum, frame):
        raise SystemExit(128+signum)
    try:
        for sig in (signal.SIGTERM,signal.SIGINT):previous[sig]=signal.signal(sig,stop)
        proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL)
        try:return proc.wait(timeout=ttl+30)
        except subprocess.TimeoutExpired:return 124
    finally:
        # Only the deterministic name owned by this newly launched attempt is removed.
        if proc is not None:
            try:subprocess.run([docker,'rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
            except (OSError,subprocess.TimeoutExpired):pass
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=1)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for sig,handler in previous.items():signal.signal(sig,handler)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('provider');p.add_argument('workdir');p.add_argument('prompt');p.add_argument('model',nargs='?',default='')
    a=p.parse_args()
    try:
        config=json.loads(Path(a.config).read_text())
        return run(config,a.provider,a.workdir,a.prompt,a.model)
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as exc:
        print('Container runner: '+str(exc),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
