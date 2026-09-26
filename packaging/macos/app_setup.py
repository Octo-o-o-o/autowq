#!/usr/bin/env python3
"""First-run helper bundled inside WorldQuant.app (stdlib only, bounded JSON on stdout)."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

RESOURCES = Path(__file__).resolve().parent
APP_SUPPORT = Path.home() / 'Library/Application Support/WorldQuant'
VENV = APP_SUPPORT / 'venv'
PYTHON_CANDIDATES = ('/usr/bin/python3', '/opt/homebrew/bin/python3', '/usr/local/bin/python3')
RUNNER_LABEL = 'com.worldquant.wq-runner'
MENU_LABEL = 'com.worldquant.wq-menu'
PATH_ENV = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def emit(data):
    print(json.dumps(data, ensure_ascii=False))


def fail(message):
    emit({'error': str(message)[-1500:]})
    sys.exit(1)


def run(argv, timeout=300):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f'退出码 {result.returncode}')[-1500:])
    return result


def usable(python):
    try:
        return subprocess.run(
            [str(python), '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'],
            capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_python():
    seen = set()
    for candidate in PYTHON_CANDIDATES:
        if not os.access(candidate, os.X_OK):
            continue
        real = os.path.realpath(candidate)
        if real in seen:
            continue
        seen.add(real)
        if usable(candidate):
            return candidate
    return None


def ensure_venv(base_python):
    venv_python = VENV / 'bin/python'
    if not usable(venv_python):
        if VENV.exists():
            shutil.rmtree(VENV)
        VENV.parent.mkdir(parents=True, exist_ok=True)
        run([base_python, '-m', 'venv', str(VENV)], timeout=180)
    return venv_python


def check_python():
    python = find_python()
    if not python:
        fail('未找到 Python ≥ 3.11；请先安装 Xcode Command Line Tools：xcode-select --install')
    emit({'python': python})


def setup(workspace):
    workspace = Path(workspace).expanduser().resolve()
    base = find_python()
    if not base:
        fail('未找到 Python ≥ 3.11；请先安装 Xcode Command Line Tools：xcode-select --install')
    if not list(RESOURCES.glob('wq_pilot-*.whl')):
        fail('应用包缺少引擎 wheel（wq_pilot-*.whl），安装包不完整')
    venv_python = ensure_venv(base)
    run([str(venv_python), '-m', 'pip', 'install', '--no-index', '--find-links', str(RESOURCES),
         '--upgrade', 'wq-pilot'])
    run([str(venv_python), '-c', 'import wq'])
    (workspace / 'var/run').mkdir(parents=True, exist_ok=True)
    emit({'python': str(venv_python), 'workspace': str(workspace)})


def domain():
    return f'gui/{os.getuid()}'


def loaded(label):
    return subprocess.run(['/bin/launchctl', 'print', f'{domain()}/{label}'],
                          capture_output=True, timeout=10).returncode == 0


def bootout(label):
    if loaded(label):
        subprocess.run(['/bin/launchctl', 'bootout', f'{domain()}/{label}'],
                       capture_output=True, timeout=20)


def activate(workspace, app):
    workspace = Path(workspace).expanduser().resolve()
    binary = Path(app).resolve() / 'Contents/MacOS/WorldQuantMenu'
    if not binary.exists():
        fail(f'应用二进制不存在：{binary}')
    venv_python = VENV / 'bin/python'
    if not usable(venv_python):
        fail('运行时未安装，请先完成首次设置')
    (workspace / 'var/run').mkdir(parents=True, exist_ok=True)
    agents = Path.home() / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    runner = agents / f'{RUNNER_LABEL}.plist'
    if runner.exists():
        existing = plistlib.loads(runner.read_bytes())
        if existing.get('WorkingDirectory') != str(workspace):
            fail('现有 runner 属于另一工作区，拒绝加载第二份研究调度；'
                 '如需迁移请先运行 deactivate')
    runner.write_bytes(plistlib.dumps({
        'Label': RUNNER_LABEL,
        'ProgramArguments': [str(venv_python), '-m', 'wq', 'run-once', '--lease', '3600'],
        'WorkingDirectory': str(workspace),
        'EnvironmentVariables': {'PATH': PATH_ENV},
        'RunAtLoad': True, 'StartInterval': 60, 'ProcessType': 'Background',
        'StandardOutPath': str(workspace / 'var/run/launchd.out.log'),
        'StandardErrorPath': str(workspace / 'var/run/launchd.err.log')}))
    menu_plist = agents / f'{MENU_LABEL}.plist'
    if menu_plist.exists():
        old = plistlib.loads(menu_plist.read_bytes())
        if old.get('ProgramArguments') != [str(binary)]:
            fail('菜单栏 LaunchAgent 指向另一应用，拒绝覆盖；如需迁移请先运行 deactivate')
    menu_plist.write_bytes(plistlib.dumps({
        'Label': MENU_LABEL, 'ProgramArguments': [str(binary)],
        'RunAtLoad': True, 'ProcessType': 'Interactive', 'WorkingDirectory': str(workspace),
        'StandardOutPath': str(workspace / 'var/run/menubar.out.log'),
        'StandardErrorPath': str(workspace / 'var/run/menubar.err.log')}))
    for label, plist in ((RUNNER_LABEL, runner), (MENU_LABEL, menu_plist)):
        bootout(label)
        run(['/bin/launchctl', 'bootstrap', domain(), str(plist)], timeout=20)
    emit({'runner': str(runner), 'menubar': str(menu_plist)})


def deactivate():
    agents = Path.home() / 'Library/LaunchAgents'
    for label in (RUNNER_LABEL, MENU_LABEL):
        bootout(label)
        plist = agents / f'{label}.plist'
        if plist.exists():
            plist.unlink()
    emit({'removed': [RUNNER_LABEL, MENU_LABEL]})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='app_setup.py')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check-python')
    setup_parser = commands.add_parser('setup')
    setup_parser.add_argument('--workspace', required=True)
    activate_parser = commands.add_parser('activate')
    activate_parser.add_argument('--workspace', required=True)
    activate_parser.add_argument('--app', required=True)
    commands.add_parser('deactivate')
    args = parser.parse_args()
    try:
        if args.command == 'check-python':
            check_python()
        elif args.command == 'setup':
            setup(args.workspace)
        elif args.command == 'activate':
            activate(args.workspace, args.app)
        else:
            deactivate()
    except SystemExit:
        raise
    except Exception as exc:
        fail(exc)
