#!/usr/bin/env python3
"""Render disabled local configuration and macOS launchers; never install or start jobs."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import sys


def render(root, runtime):
    root, runtime = Path(root).resolve(), Path(runtime).expanduser().resolve()
    if runtime.is_relative_to(root):
        raise ValueError('runtime must be outside the project (sandbox boundary)')
    targets = [root/'config/config.json', root/'config/profiles.json',
               root/'config/autopilot-policy.json']
    if any(p.exists() for p in targets) or runtime.exists():
        raise ValueError('Refusing to overwrite existing configuration/runtime')
    home = Path.home()
    private = home/'.worldquant-pilot'
    runtime.mkdir(parents=True, mode=0o700)
    launchers = runtime/'launchers';launchers.mkdir(mode=0o700)
    jobs = runtime/'jobs';jobs.mkdir(mode=0o700)
    entry = launchers/'provider_entry.py'
    shutil.copyfile(root/'scripts/provider_entry.py', entry)
    # This matches the project's targeted macOS isolation, not a whole-machine sandbox.
    quote = lambda p: json.dumps(str(p))
    denied = [root, private, home/'Downloads', home/'Library/Application Support/Google/Chrome']
    writes = [jobs, home/'.grok', home/'.local/share/devin', home/'.config/devin',
              home/'.cache', home/'.cursor', home/'.local/share/cursor-agent',
              home/'.zcode', home/'.zcode-ai', home/'.zai',
              home/'Library/Application Support/ZCode', home/'Library/Logs/ZCode',
              home/'Library/Caches/ZCode', home/'Library/Caches/cursor-compile-cache',
              Path('/private/tmp'), Path('/private/var/folders')]
    sb = '(version 1)\n(allow default)\n(deny file-write*)\n'
    sb += '(allow file-write* '+ ' '.join('(subpath '+quote(p)+')' for p in writes)
    sb += ' (literal "/dev/null") (literal "/dev/tty"))\n'
    sb += '(deny file-read* file-write* '+' '.join('(subpath '+quote(p)+')' for p in denied)+')\n'
    sandbox = runtime/'agents.sb';sandbox.write_text(sb)
    binaries = {'grok':home/'.grok/bin/grok', 'devin':home/'.local/bin/devin'}
    for name in ('grok','devin','cursor','zcode'):
        argv = ['/usr/bin/sandbox-exec','-f',str(sandbox)]
        argv += [str(binaries[name])] if name in binaries else [sys.executable,str(entry),name]
        f = launchers/name
        f.write_text('#!/bin/sh\nexec '+shlex.join(argv)+' "$@"\n');f.chmod(0o700)
    cfg = json.loads((root/'config/config.example.json').read_text())
    cfg['routing']['work_root'] = str(jobs)
    profiles = json.loads((root/'config/profiles.example.json').read_text().replace('{launcher_dir}',str(launchers)))
    for path, data in zip(targets[:2], (cfg, profiles)):
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    shutil.copyfile(root/'config/autopilot-policy.example.json',targets[2])
    run = root/'var/run';run.mkdir(parents=True,exist_ok=True)
    plist = {'Label':'com.worldquant.wq-runner', 'RunAtLoad':True,'StartInterval':60,
             'WorkingDirectory':str(root),'ProgramArguments':[str(root/'wq'),'run-once','--lease','3600'],
             'EnvironmentVariables':{'PATH':str(Path(sys.executable).parent)+':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
             'StandardOutPath':str(run/'launchd.out.log'),'StandardErrorPath':str(run/'launchd.err.log')}
    with (runtime/'com.worldquant.wq-runner.plist').open('wb') as f:plistlib.dump(plist,f)
    return runtime


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--runtime',type=Path,default=Path.home()/'.local/share/autowq-runtime')
    args=p.parse_args()
    try:dest=render(args.root,args.runtime)
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')
    print(f'Generated disabled config and launchers in {dest}. Nothing installed or started.')

if __name__=='__main__':main()
