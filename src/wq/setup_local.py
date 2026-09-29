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

from .assets import path as asset_path


def write_runtime_files(runtime, binaries=None, verified_versions=None, root=None, work_dirs=()):
    """（重新）生成 launchers、agents.sb 与 provider_entry.py。幂等：`wq providers
    refresh-runtime` 在应用更新或源码修复后调用，保证部署 runtime 与源码一致，
    不触碰 jobs/ 与既有配置。root 为项目根（沙箱禁读边界），缺省取当前目录。
    work_dirs 为配置中实际使用的任务目录（routing.work_root、models.*.workdir）；
    不放行就写不出 result.json。位于禁读边界内的目录无法放行，由 doctor 报错。"""
    runtime = Path(runtime).expanduser().resolve()
    launchers = runtime / 'launchers'
    launchers.mkdir(parents=True, exist_ok=True, mode=0o700)
    jobs = runtime / 'jobs'
    jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = launchers / 'provider_entry.py'
    shutil.copyfile(asset_path('provider_entry.py'), entry)
    entry.chmod(0o700)
    home = Path.home()
    private = home/'.worldquant-pilot'
    denied = [Path(root).resolve() if root else Path.cwd(), private,
              home/'Downloads', home/'Library/Application Support/Google/Chrome']
    # 只放行任务目录，不放行 launchers：沙箱内进程不得改写启动器自身。
    tasks = [jobs]
    for d in work_dirs:
        d = Path(d).expanduser().resolve()
        # runtime 本身或其祖先会连带放行 launchers/agents.sb，同样拒绝。
        if (d not in tasks and not d.is_relative_to(launchers) and not runtime.is_relative_to(d)
                and not any(d.is_relative_to(x) for x in denied)):
            tasks.append(d)
    writes = [*tasks, home/'.grok', home/'.local/share/devin', home/'.config/devin',
              home/'.claude', home/'.claude.json', home/'.codex', home/'.gemini', home/'.copilot', home/'.qwen', home/'.config/opencode', home/'.local/share/opencode',
              home/'.cache', home/'.cursor', home/'.local/share/cursor-agent',
              home/'.zcode', home/'.zcode-ai', home/'.zai',
              home/'Library/Application Support/ZCode', home/'Library/Logs/ZCode',
              home/'Library/Caches/ZCode', home/'Library/Caches/cursor-compile-cache',
              Path('/private/tmp'), Path('/private/var/folders')]
    sb = '(version 1)\n(allow default)\n(deny file-write*)\n'
    sb += '(allow file-write* '+ ' '.join('(subpath '+json.dumps(str(p))+')' for p in writes)
    sb += ' (literal "/dev/null") (literal "/dev/tty"))\n'
    sb += '(deny file-read* file-write* '+' '.join('(subpath '+json.dumps(str(p))+')' for p in denied)+')\n'
    sandbox = runtime/'agents.sb'
    sandbox.write_text(sb)
    binaries = {**{'grok':home/'.grok/bin/grok', 'devin':home/'.local/bin/devin'}, **(binaries or {})}
    verified = str((verified_versions or {}).get('zcode', '') or '')
    for name in ('grok','devin','cursor','zcode'):
        argv = ['/usr/bin/sandbox-exec','-f',str(sandbox)]
        argv += [str(binaries[name])] if name in ('grok','devin') else [sys.executable,str(entry),name]
        if name=='cursor' and binaries.get('cursor'):
            argv=['/usr/bin/env','WQ_CURSOR_BIN='+str(binaries['cursor'])]+argv
        if name=='zcode' and verified:
            # 版本核对在入口脚本内完成；应用更新后版本不符会在发调用前阻断。
            argv=['/usr/bin/env','WQ_ZCODE_VERIFIED_VERSION='+verified]+argv
        f = launchers/name
        f.write_text('#!/bin/sh\nexec '+shlex.join(argv)+' "$@"\n');f.chmod(0o700)


def render(root, runtime, binaries=None):
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
    # This matches the project's targeted macOS isolation, not a whole-machine sandbox.
    write_runtime_files(runtime, binaries, root=root)
    cfg = json.loads(asset_path('config.example.json').read_text())
    cfg['routing']['work_root'] = str(runtime/'jobs')
    profiles = json.loads(asset_path('profiles.example.json').read_text().replace('{launcher_dir}',str(runtime/'launchers')))
    (root/'config').mkdir(parents=True,exist_ok=True)
    for path, data in zip(targets[:2], (cfg, profiles)):
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    shutil.copyfile(asset_path('autopilot-policy.example.json'),targets[2])
    run = root/'var/run';run.mkdir(parents=True,exist_ok=True)
    # pipx/brew/DMG 安装的工作区没有 ./wq shim：统一用当前解释器 -m wq；源码检出额外给 PYTHONPATH。
    env = {'PATH':str(Path(sys.executable).parent)+':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
    if (root/'src'/'wq'/'__init__.py').exists(): env['PYTHONPATH'] = str(root/'src')
    plist = {'Label':'com.worldquant.wq-runner', 'RunAtLoad':True,'StartInterval':60,
             'WorkingDirectory':str(root),'ProgramArguments':[sys.executable,'-m','wq','run-once','--lease','3600'],
             'EnvironmentVariables':env,
             'StandardOutPath':str(run/'launchd.out.log'),'StandardErrorPath':str(run/'launchd.err.log')}
    with (runtime/'com.worldquant.wq-runner.plist').open('wb') as f:plistlib.dump(plist,f)
    return runtime


def main():
    if sys.platform.startswith("linux"):
        from .setup_linux import main as linux_main
        return linux_main()
    if sys.platform != "darwin":
        raise SystemExit("On Windows, run this project inside WSL2 Ubuntu.")
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--runtime',type=Path,default=Path.home()/'.local/share/autowq-runtime')
    args=p.parse_args()
    try:dest=render(args.root,args.runtime)
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')
    from .i18n import default_language, text
    lang = default_language()
    print(text(lang, f'已在 {dest} 生成默认关闭的配置与启动入口；未安装、未启动任何服务。',
                 f'Generated disabled config and launchers in {dest}. Nothing installed or started.'))

if __name__=='__main__':main()
