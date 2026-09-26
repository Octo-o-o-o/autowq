"""本机官方 CLI 入口；不读取或复制登录凭证。由外层 sandbox-exec 启动。"""
import os
from pathlib import Path
import sys
import shutil

provider, workdir, prompt_file, model = sys.argv[1:]
prompt = Path(prompt_file).read_text()
if provider == 'cursor':
    binary = os.environ.get('WQ_CURSOR_BIN') or str(Path.home()/'.local/bin/cursor-agent')
    argv = [binary, '--workspace', workdir, '--model', model, '--print', '--force',
            '--trust', '--output-format', 'json', prompt]
elif provider == 'zcode':
    os.environ['ZCODE_BUILTIN_PROVIDER_CONFIG_FILE'] = '/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json'
    binary = os.environ.get('WQ_NODE_BIN') or shutil.which('node')
    if not binary:
        raise SystemExit('node not found; set WQ_NODE_BIN')
    argv = [binary, '/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs',
            '--cwd', workdir, '--mode', 'yolo', '--json', '--prompt', prompt,
            '--disallowed-tools', 'BrowserUse,WebSearch,WebFetch,Agent,Task']
else:
    raise SystemExit('unknown provider')
os.execv(binary, argv)
