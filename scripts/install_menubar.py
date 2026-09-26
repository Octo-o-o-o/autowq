#!/usr/bin/env python3
"""Build/install the native macOS menu; preserve an existing runner's environment."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'com.worldquant.wq-menu'


def install(destination, activate):
    app = destination / 'WorldQuant.app'
    info = app / 'Contents/Info.plist'
    if app.exists():
        if not info.exists() or plistlib.loads(info.read_bytes()).get('CFBundleIdentifier') != LABEL:
            raise RuntimeError('目标应用已存在且不属于此项目，拒绝覆盖')
    macos = app / 'Contents/MacOS'
    macos.mkdir(parents=True, exist_ok=True)
    (ROOT / 'var/run').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / 'WorldQuantMenu'
        subprocess.run(['/usr/bin/swiftc', str(ROOT / 'macos/WorldQuantMenu.swift'),
                        '-o', str(binary), '-framework', 'AppKit'], check=True, timeout=180)
        os.replace(binary, macos / 'WorldQuantMenu')
    resources = app / 'Contents/Resources'
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'macos/Assets/ResearchIcon.png', resources / 'ResearchIcon.png')
    shutil.copy2(ROOT / 'packaging/macos/app_setup.py', resources / 'app_setup.py')
    info.write_bytes(plistlib.dumps({'CFBundleExecutable': 'WorldQuantMenu',
        'CFBundleIdentifier': LABEL, 'CFBundleName': 'WorldQuant',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': '3',
        'LSUIElement': True, 'LSMinimumSystemVersion': '12.0'}))
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True, timeout=30)
    for key, value in (('WQWorkspace', str(ROOT)), ('WQPython', sys.executable)):
        subprocess.run(['/usr/bin/defaults', 'write', LABEL, key, value], check=True, timeout=10)
    if not activate:
        print(app); return
    agents = Path.home() / 'Library/LaunchAgents'; agents.mkdir(parents=True, exist_ok=True)
    runner = agents / 'com.worldquant.wq-runner.plist'
    if runner.exists():
        existing = plistlib.loads(runner.read_bytes())
        if existing.get('WorkingDirectory') != str(ROOT):
            raise RuntimeError('现有 runner 属于另一目录，拒绝加载第二份研究调度')
    else:
        runner.write_bytes(plistlib.dumps({'Label': 'com.worldquant.wq-runner',
            'ProgramArguments': [sys.executable, '-m', 'wq', 'run-once', '--lease', '3600'],
            'WorkingDirectory': str(ROOT), 'EnvironmentVariables': {'PYTHONPATH': str(ROOT / 'src'),
                'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
            'RunAtLoad': True, 'StartInterval': 60, 'ProcessType': 'Background',
            'StandardOutPath': str(ROOT / 'var/run/launchd.out.log'),
            'StandardErrorPath': str(ROOT / 'var/run/launchd.err.log')}))
    domain = f'gui/{os.getuid()}'
    def loaded(label):
        return subprocess.run(['/bin/launchctl', 'print', domain + '/' + label],
                              capture_output=True, timeout=10).returncode == 0
    menu_plist = agents / (LABEL + '.plist')
    if menu_plist.exists():
        old = plistlib.loads(menu_plist.read_bytes())
        if old.get('ProgramArguments') != [str(macos / 'WorldQuantMenu')]:
            raise RuntimeError('菜单栏 LaunchAgent 路径不匹配，拒绝覆盖')
    if loaded(LABEL):
        subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + LABEL], check=True, timeout=20)
    menu_plist.write_bytes(plistlib.dumps({'Label': LABEL,
        'ProgramArguments': [str(macos / 'WorldQuantMenu')], 'RunAtLoad': True,
        'ProcessType': 'Interactive', 'WorkingDirectory': str(ROOT),
        'StandardOutPath': str(ROOT / 'var/run/menubar.out.log'),
        'StandardErrorPath': str(ROOT / 'var/run/menubar.err.log')}))
    if not loaded('com.worldquant.wq-runner'):
        subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(runner)], check=True, timeout=20)
    subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(menu_plist)], check=True, timeout=20)
    print(f'已安装并启用登录自启：{app}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, default=Path.home() / 'Applications')
    parser.add_argument('--activate', action='store_true', help='安装用户 LaunchAgents 并立即显示图标')
    args = parser.parse_args()
    install(args.destination.expanduser().resolve(), args.activate)
