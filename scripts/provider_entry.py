#!/usr/bin/env python3
"""本机官方 CLI 入口；不读取或复制登录凭证。由外层 sandbox-exec 启动。"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wq.assets import path as asset_path

runpy.run_path(str(asset_path('provider_entry.py')), run_name='__main__')
