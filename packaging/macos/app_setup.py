#!/usr/bin/env python3
"""First-run helper bundled inside WorldQuant.app (stdlib only, bounded JSON on stdout)."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time

RESOURCES = Path(__file__).resolve().parent


def _lang():
    """引擎安装前按环境选择：zh* 中文，其余英文。"""
    import locale as _locale
    value = next((os.environ[k] for k in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG') if os.environ.get(k)), None)
    if value is None:
        try: value = _locale.getlocale()[0] or ''
        except (ValueError, _locale.Error): value = ''
    return 'zh' if value.split(':')[0].lower().replace('-', '_').startswith('zh') else 'en'


def _t(zh, en):
    return zh if _lang() == 'zh' else en
APP_SUPPORT = Path.home() / 'Library/Application Support/WorldQuant'
VENV = APP_SUPPORT / 'venv'
# 应用自带的 Python 运行时先复制到这里再建 venv：应用被替换或删除时，在跑的调度不受影响。
STAGED_PYTHON = APP_SUPPORT / 'python'
PYTHON_CANDIDATES = ('/usr/bin/python3', '/opt/homebrew/bin/python3', '/usr/local/bin/python3')
RUNNER_LABEL = 'com.worldquant.wq-runner'
MENU_LABEL = 'com.worldquant.wq-menu'
PATH_ENV = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def emit(data):
    print(json.dumps(data, ensure_ascii=False))


def fail(message, code=None):
    payload = {'error': str(message)[-1500:]}
    if code:
        payload['code'] = code
    emit(payload)
    sys.exit(1)


def run(argv, timeout=300):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or _t(f'退出码 {result.returncode}', f'exit code {result.returncode}'))[-1500:])
    return result


def usable(python):
    try:
        return subprocess.run(
            [str(python), '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'],
            capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def bundled_runtime():
    """应用包内与本机架构匹配的运行时目录；源码或旧版构建没有时返回 None。"""
    arch = 'arm64' if platform.machine() == 'arm64' or _sysctl_arm64() else 'x86_64'
    runtime = RESOURCES / 'runtime' / arch
    if (runtime / 'bin/python3').exists() and (runtime / 'WQ-RUNTIME-ID').exists():
        return runtime
    return None


def _sysctl_arm64():
    try:
        return subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.optional.arm64'], capture_output=True,
                              text=True, timeout=5).stdout.strip() == '1'
    except (OSError, subprocess.SubprocessError):
        return False


def stage_runtime(runtime):
    """把内置运行时复制到 Application Support；版本变化时连同旧 venv 一起替换。"""
    runtime_id = (runtime / 'WQ-RUNTIME-ID').read_text().strip()
    marker = STAGED_PYTHON / 'WQ-RUNTIME-ID'
    python = STAGED_PYTHON / 'bin/python3'
    if marker.exists() and marker.read_text().strip() == runtime_id and usable(python):
        return python
    for path in (VENV, STAGED_PYTHON):
        if path.exists():
            shutil.rmtree(path)
    STAGED_PYTHON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(runtime, STAGED_PYTHON, symlinks=True)
    return python


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
    runtime = bundled_runtime()
    python = str(runtime / 'bin/python3') if runtime else find_python()
    if not python:
        fail(_t('未找到 Python ≥ 3.11；请先安装 Xcode Command Line Tools：xcode-select --install',
               'Python >= 3.11 not found; install Xcode Command Line Tools: xcode-select --install'))
    emit({'python': python})


def run_dir_for(workspace):
    workspace = Path(workspace)
    config_path = workspace / 'config/config.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    run_dir = Path(config.get('paths', {}).get('run_dir', 'var/run')).expanduser()
    if not run_dir.is_absolute():
        run_dir = workspace / run_dir
    return run_dir


def db_path_for(workspace):
    workspace = Path(workspace)
    config_path = workspace / 'config/config.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    db = Path(config.get('paths', {}).get('db', 'var/wq.db')).expanduser()
    if not db.is_absolute():
        db = workspace / db
    return db


def lock_available(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def _flag(conn, key, value):
    conn.execute(
        'INSERT OR REPLACE INTO state_flags(key, value, updated_at) VALUES(?,?,?)',
        (key, value, dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')))


def mark_stop_after_cycle(workspace):
    """只写标志。下一轮调度若已是新引擎，会把当前轮走完再停。"""
    db = db_path_for(workspace)
    if not db.exists():
        fail(_t('还没有研究账本，无法登记停机', 'No research ledger yet; cannot record the stop'))
    conn = sqlite3.connect(db, timeout=30)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'state_flags' not in tables:
            fail(_t('研究账本还没初始化', 'The research ledger is not initialized'))
        open_cycle = 'research_cycles' in tables and conn.execute(
            "SELECT 1 FROM research_cycles WHERE state!='closed'").fetchone()
        _flag(conn, 'autopilot_run_next', '0')
        if open_cycle:
            _flag(conn, 'autopilot_stop_after_cycle', '1')
            deferred = True
        else:
            _flag(conn, 'autopilot_stop_after_cycle', '0')
            _flag(conn, 'paused', '1')
            _flag(conn, 'pause_origin', 'manual')
            _flag(conn, 'pause_reason', 'menu:stop-after-cycle')
            deferred = False
        conn.commit()
    finally:
        conn.close()
    return deferred


def setup(workspace, allow_busy=False):
    workspace = Path(workspace).expanduser().resolve()
    runtime = bundled_runtime()
    base = None if runtime else find_python()
    if not runtime and not base:
        fail(_t('未找到 Python ≥ 3.11；请先安装 Xcode Command Line Tools：xcode-select --install',
               'Python >= 3.11 not found; install Xcode Command Line Tools: xcode-select --install'))
    wheels = list(RESOURCES.glob('wq_pilot-*.whl'))
    if len(wheels) != 1:
        fail(_t('应用包必须包含一个引擎 wheel，安装包不完整', 'The app bundle must contain exactly one engine wheel; the installer is incomplete'))
    wheel = wheels[0]
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if runtime:
        digest += '+' + (runtime / 'WQ-RUNTIME-ID').read_text().strip()
    marker = APP_SUPPORT / 'engine-wheel.sha256'
    venv_python = VENV / 'bin/python'
    pending = False
    if not (marker.exists() and marker.read_text().strip() == digest and usable(venv_python)):
        run_dir = run_dir_for(workspace)
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_file = (run_dir / 'agent-runner.lock').open('a')
        try:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if allow_busy and usable(venv_python):
                    pending = True
                else:
                    fail(_t('研究任务仍在运行；可以先打开菜单，或退出时选择在本轮结束后停止、或立刻结束任务。不会擅自中断。',
                           'Research tasks are still running. Open the menu now, or quit and choose to stop after this cycle or stop all tasks now. Nothing is interrupted unless you choose that.'),
                         code='engine-busy')
            if not pending:
                if runtime:
                    base = str(stage_runtime(runtime))
                venv_python = ensure_venv(base)
                run([str(venv_python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                     '--force-reinstall', str(wheel)])
                run([str(venv_python), '-c', 'import wq.desktop_control, wq.autopilot'])
                marker.write_text(digest + '\n')
        finally:
            lock_file.close()
    (workspace / 'var/run').mkdir(parents=True, exist_ok=True)
    payload = {'python': str(venv_python), 'workspace': str(workspace)}
    if pending:
        payload['engine_pending'] = True
    emit(payload)


def _python_env(workspace):
    env = os.environ.copy()
    src = Path(workspace) / 'src'
    if (src / 'wq' / '__init__.py').exists():
        env['PYTHONPATH'] = str(src) + ((':' + env['PYTHONPATH']) if env.get('PYTHONPATH') else '')
    return env


def release(workspace, mode):
    """退出时的停机。now 会结束本地任务并等待调度锁放开，便于接着更换引擎。"""
    workspace = Path(workspace).expanduser().resolve()
    if mode == 'after-cycle':
        deferred = mark_stop_after_cycle(workspace)
        emit({'ok': True, 'mode': mode, 'deferred': deferred})
        return
    if mode != 'now':
        fail(_t('未知停机方式', 'Unknown stop mode'))
    python = VENV / 'bin/python'
    if not usable(python):
        fail(_t('运行时未安装，无法结束任务', 'Runtime is not installed, so tasks cannot be stopped'))
    env = _python_env(workspace)
    stopped = subprocess.run([str(python), '-m', 'wq.desktop_control', 'quit-now'],
                             cwd=workspace, env=env, capture_output=True, text=True, timeout=40)
    if stopped.returncode != 0:
        fallback = subprocess.run([str(python), '-m', 'wq', 'pause', '--reason', 'menu:stop-now'],
                                  cwd=workspace, env=env, capture_output=True, text=True, timeout=40)
        if fallback.returncode != 0:
            fail((fallback.stderr or fallback.stdout or stopped.stderr or stopped.stdout or _t('结束任务失败', 'Could not stop tasks'))[-1500:])
    lock_path = run_dir_for(workspace) / 'agent-runner.lock'
    for _ in range(20):
        if lock_available(lock_path):
            emit({'ok': True, 'mode': 'now'})
            return
        time.sleep(1)
    fail(_t('任务已要求停止，但调度锁还没放开。等几秒后重新打开应用。',
           'Stop was requested, but the scheduler still holds its lock. Reopen the app in a few seconds.'),
         code='engine-busy')


def domain():
    return f'gui/{os.getuid()}'


def loaded(label):
    return subprocess.run(['/bin/launchctl', 'print', f'{domain()}/{label}'],
                          capture_output=True, timeout=10).returncode == 0


def bootout(label):
    if loaded(label):
        subprocess.run(['/bin/launchctl', 'bootout', f'{domain()}/{label}'],
                       capture_output=True, timeout=20)


def activate(workspace, app, runner_only=False):
    """注册后台调度；runner_only 时不启用菜单栏（只用命令行 / AI 驱动的场景）。"""
    workspace = Path(workspace).expanduser().resolve()
    if not runner_only and not app:
        fail(_t('需要 --app，或用 --runner-only 只注册调度', 'Pass --app, or use --runner-only to register only the scheduler'))
    binary = Path(app).resolve() / 'Contents/MacOS/WorldQuantMenu' if app else None
    if binary is not None and not binary.exists():
        fail(_t(f'应用二进制不存在：{binary}', f'App binary not found: {binary}'))
    venv_python = VENV / 'bin/python'
    if not usable(venv_python):
        fail(_t('运行时未安装，请先完成首次设置', 'Runtime not installed; complete first-time setup first'))
    (workspace / 'var/run').mkdir(parents=True, exist_ok=True)
    agents = Path.home() / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    runner = agents / f'{RUNNER_LABEL}.plist'
    if runner.exists():
        existing = plistlib.loads(runner.read_bytes())
        if existing.get('WorkingDirectory') != str(workspace):
            fail(_t('现有 runner 属于另一工作区，拒绝加载第二份研究调度；如需迁移请先运行 deactivate',
                   'The existing runner belongs to another workspace; refusing to load a second research scheduler. Run deactivate first to migrate.'))
    runner.write_bytes(plistlib.dumps({
        'Label': RUNNER_LABEL,
        'ProgramArguments': [str(venv_python), '-m', 'wq', 'run-once', '--lease', '3600'],
        'WorkingDirectory': str(workspace),
        'EnvironmentVariables': {'PATH': PATH_ENV},
        'RunAtLoad': True, 'StartInterval': 60, 'ProcessType': 'Background',
        'StandardOutPath': str(workspace / 'var/run/launchd.out.log'),
        'StandardErrorPath': str(workspace / 'var/run/launchd.err.log')}))
    if runner_only:
        if not loaded(RUNNER_LABEL):
            run(['/bin/launchctl', 'bootstrap', domain(), str(runner)], timeout=20)
        emit({'runner': str(runner)})
        return
    menu_plist = agents / f'{MENU_LABEL}.plist'
    if menu_plist.exists():
        old = plistlib.loads(menu_plist.read_bytes())
        if old.get('ProgramArguments') != [str(binary)]:
            fail(_t('菜单栏 LaunchAgent 指向另一应用，拒绝覆盖；如需迁移请先运行 deactivate',
                   'The menu-bar LaunchAgent points to another app; refusing to overwrite. Run deactivate first to migrate.'))
    menu_plist.write_bytes(plistlib.dumps({
        'Label': MENU_LABEL, 'ProgramArguments': [str(binary)],
        'RunAtLoad': True, 'ProcessType': 'Interactive', 'WorkingDirectory': str(workspace),
        'StandardOutPath': str(workspace / 'var/run/menubar.out.log'),
        'StandardErrorPath': str(workspace / 'var/run/menubar.err.log')}))
    for label, plist in ((RUNNER_LABEL, runner), (MENU_LABEL, menu_plist)):
        if not loaded(label):
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
    setup_parser.add_argument('--allow-busy', action='store_true')
    release_parser = commands.add_parser('release')
    release_parser.add_argument('--workspace', required=True)
    release_parser.add_argument('--mode', choices=['after-cycle', 'now'], required=True)
    activate_parser = commands.add_parser('activate')
    activate_parser.add_argument('--workspace', required=True)
    activate_parser.add_argument('--app')
    activate_parser.add_argument('--runner-only', action='store_true')
    commands.add_parser('deactivate')
    args = parser.parse_args()
    try:
        if args.command == 'check-python':
            check_python()
        elif args.command == 'setup':
            setup(args.workspace, allow_busy=args.allow_busy)
        elif args.command == 'release':
            release(args.workspace, args.mode)
        elif args.command == 'activate':
            activate(args.workspace, args.app, runner_only=args.runner_only)
        else:
            deactivate()
    except SystemExit:
        raise
    except Exception as exc:
        fail(exc)
