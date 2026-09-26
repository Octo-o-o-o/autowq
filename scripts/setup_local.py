#!/usr/bin/env python3
"""Render disabled local configuration and macOS launchers; never install or start jobs."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wq.setup_local import main, render

if __name__ == '__main__':
    if not any(a == '--root' or a.startswith('--root=') for a in sys.argv[1:]):
        sys.argv += ['--root', str(ROOT)]
    main()
