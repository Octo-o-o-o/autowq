#!/usr/bin/env python3
"""Register Windows Task Scheduler runner + tray autostart; remove with --remove."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wq.windows_setup import main

if __name__ == '__main__':
    if not any(a == '--root' or a.startswith('--root=') for a in sys.argv[1:]):
        sys.argv += ['--root', str(ROOT)]
    sys.exit(main())
