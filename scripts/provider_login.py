#!/usr/bin/env python3
"""Interactive login into one provider's isolated Docker home; no BRAIN data mounted."""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wq.assets import path as asset_path

runpy.run_path(str(asset_path('provider_login.py')), run_name='__main__')
