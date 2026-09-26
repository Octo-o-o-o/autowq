#!/usr/bin/env python3
"""Generate disabled Linux/WSL2 Docker-provider config and systemd user timer."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wq.setup_linux import main, render, systemd_quote

if __name__ == '__main__':
    if not any(a == '--root' or a.startswith('--root=') for a in sys.argv[1:]):
        sys.argv += ['--root', str(ROOT)]
    main()
