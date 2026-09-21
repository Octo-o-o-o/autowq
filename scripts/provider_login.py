#!/usr/bin/env python3
"""Interactive login into one provider's isolated Docker home; no BRAIN data mounted."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('provider',choices=('grok','devin','cursor'))
    p.add_argument('--config',type=Path,default=Path.home()/'.local/share/autowq-runtime/containers.json')
    a=p.parse_args()
    if not sys.stdin.isatty():p.exit(1,'Login requires your interactive terminal.\n')
    cfg=json.loads(a.config.read_text());item=cfg['providers'][a.provider]
    volume=item['home_volume']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]+',volume):p.exit(1,'Invalid volume name\n')
    commands={'grok':['grok','login'],'devin':['devin','auth','login'],'cursor':['cursor-agent','login']}
    argv=[cfg.get('docker_bin','docker'),'run','--rm','-it','--init','--pull=never',
          '--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
          '--user',f'{os.getuid()}:{os.getgid()}',
          '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
          '--mount',f'type=volume,source={volume},target=/home/agent',
          '--env','HOME=/home/agent',item['image'],*commands[a.provider]]
    return subprocess.call(argv)

if __name__=='__main__':sys.exit(main())
