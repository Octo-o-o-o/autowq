#!/usr/bin/env python3
"""First-run configuration wizard. Interactive login, explicit models and disabled execution gates."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wq.onboard import BRAIN_REGISTER_URL, PROVIDER_URLS, assignments, configure, intro_flow, login_flow, main

if __name__ == '__main__':
    if not any(a == '--root' or a.startswith('--root=') for a in sys.argv[1:]):
        sys.argv += ['--root', str(ROOT)]
    raise SystemExit(main())
