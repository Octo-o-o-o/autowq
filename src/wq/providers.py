"""Provider discovery and explicit configuration, without inference or credential scraping."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .provider_runtime import PROTOCOLS, api_request, validate_api

CLI = {
    'grok': {'bins': ('grok', '~/.grok/bin/grok'), 'models': ['models'], 'login': ['login']},
    'devin': {'bins': ('devin', '~/.local/bin/devin'), 'models': ['models','list'], 'login': ['auth', 'login']},
    'cursor': {'bins': ('cursor-agent', '~/.local/bin/cursor-agent'), 'models': ['models'], 'login': ['login']},
    'zcode': {'bins': ('/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs',), 'models': None, 'login': None},
    'claude': {'bins': ('claude',), 'models': None, 'login': ['auth', 'login']},
    'codex': {'bins': ('codex',), 'models': None, 'login': ['login']},
    'gemini': {'bins': ('gemini',), 'models': None, 'login': []},
    'copilot': {'bins': ('copilot',), 'models': None, 'login': ['login']},
    'qwen': {'bins': ('qwen',), 'models': None, 'login': []},
    'opencode': {'bins': ('opencode',), 'models': ['models'], 'login': ['auth', 'login']},
}
LEGACY = ('grok', 'devin', 'cursor', 'zcode')
NAMES = tuple(CLI) + PROTOCOLS


def discover():
    result = {}
    for name, info in CLI.items():
        for item in info['bins']:
            p = shutil.which(item) or os.path.expanduser(item)
            if Path(p).is_file() and (os.access(p, os.X_OK) or name == 'zcode'):
                result[name] = str(Path(p).absolute()); break
    return result


def inventory():
    found = discover()
    return [{'provider': n, 'transport': 'cli', 'binary': found.get(n),
             'installed': n in found, 'model_listing': 'command' if i['models'] else 'manual',
             'authentication': 'not_checked', 'model_access': 'not_verified'} for n, i in CLI.items()] + [
            {'provider': p, 'transport': 'api', 'model_listing': 'GET /models',
             'authentication': 'not_checked', 'model_access': 'not_verified'} for p in PROTOCOLS]


def models(name, item=None, binary=None):
    if item and item.get('kind') == 'api':
        obj = api_request(item, '/models')
        return {'models': [m['id'] for m in obj.get('data', []) if isinstance(m.get('id'), str)],
                'source': 'API /models', 'has_more': obj.get('has_more', False), 'model_access_verified': False}
    info = CLI.get(name)
    if not info or not info['models']:
        return {'models': [], 'source': 'manual', 'hint': 'Use vendor model picker (/model) and enter the exact ID / 在供应商模型菜单中查看并手填ID', 'model_access_verified': False}
    binary = binary or discover().get(name)
    if not binary:
        raise ValueError('CLI not installed / CLI未安装')
    try:
        r = subprocess.run([binary, *info['models']], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise ValueError('Model listing timed out; enter model ID manually') from None
    if r.returncode:
        raise ValueError('Model listing unavailable; login or enter model ID manually')
    return {'listing': r.stdout[:200000], 'listing_truncated':len(r.stdout)>200000, 'source': 'vendor CLI', 'model_access_verified': False}


def api_definition(protocol, model, base_url=None, key_env=None):
    item = {'kind': 'api', 'protocol': protocol, 'model': model,
            'base_url': base_url or ('https://api.anthropic.com/v1' if protocol == 'anthropic' else 'https://api.openai.com/v1'),
            'api_key_env': key_env or ('ANTHROPIC_API_KEY' if protocol == 'anthropic' else 'OPENAI_API_KEY'),
            'max_tokens': 4096, 'timeout_s': 120}
    if protocol == 'openai':
        item['token_parameter']='max_completion_tokens' if item['base_url'].rstrip('/')=='https://api.openai.com/v1' else 'max_tokens'
    validate_api(item)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item['api_key_env']):
        raise ValueError('Invalid environment variable name')
    return item


def save_key(private_dir, name, key):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not key.strip() or '\n' in key.strip():
        raise ValueError('Invalid provider/key')
    directory = Path(private_dir).expanduser() / 'provider-keys'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(key.strip())
    return str(path)


def definition(kind, model, binary=None, api=None):
    if kind in PROTOCOLS:
        result = api or api_definition(kind, model)
        validate_api(result)
        return {'label': kind + ' / ' + model, 'model': model, 'transport': result, 'timeout_s': 180}
    if kind not in CLI or kind in LEGACY:
        raise ValueError('Use onboarding for legacy CLI launchers')
    if not binary or not Path(binary).is_absolute() or not os.access(binary, os.X_OK):
        raise ValueError('CLI executable not found')
    return {'label': kind + ' / ' + model, 'model': model,
            'transport': {'kind': kind, 'binary': binary, 'model': model}, 'timeout_s': 900}


def runtime_argv(cfg, definition):
    """Freeze the selected transport and use existing isolation for new CLI adapters."""
    item = definition['transport']
    if item.get('kind') == 'api': validate_api(item)
    elif item.get('kind') not in CLI or item.get('kind') in LEGACY: raise ValueError('Unsupported transport kind')
    script = str(Path(__file__).with_name('provider_runtime.py'))
    argv = [sys.executable, script, json.dumps(item), '{prompt}']
    if item['kind'] != 'api':
        runtime = Path(cfg.get('onboarding', 'runtime', default='')).expanduser()
        if sys.platform != 'darwin' or not (runtime / 'agents.sb').is_file():
            raise ValueError('CLI adapter requires generated macOS sandbox; use API transport on Linux or configure a Docker CLI profile')
        script = runtime / 'launchers/provider_runtime.py'
        if not script.is_file():
            raise ValueError('Provider runtime missing; run providers refresh-runtime')
        argv = ['/usr/bin/sandbox-exec', '-f', str(runtime / 'agents.sb'), sys.executable,
                str(script), json.dumps(item), '{prompt}']
    return argv


def install_runtime(runtime):
    target = Path(runtime).expanduser() / 'launchers/provider_runtime.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name('provider_runtime.py'), target)
    target.chmod(0o700)


def command(args):
    from .config import Config
    from . import util
    from .i18n import text
    lang = getattr(args, 'lang', 'zh')
    cfg = Config.load(args.config, os.getcwd())
    if args.action == 'list':
        print(json.dumps(inventory(), ensure_ascii=False, indent=2)); return 0
    pp = Path(cfg.resolve(cfg.get('routing', 'profiles_file', default='config/profiles.json')))
    profiles = json.loads(pp.read_text()) if pp.exists() else {'providers':{}}
    if args.action not in ('models',) and not pp.exists(): raise ValueError('Run wq onboard first / 请先初始化')
    if args.action == 'refresh-runtime':
        install_runtime(cfg.get('onboarding', 'runtime'))
        print(text(lang, '运行适配器已更新', 'Runtime adapters updated')); return 0
    if args.action == 'models':
        d = profiles.get('providers', {}).get(args.provider, {})
        item = d.get('transport')
        print(json.dumps(models(args.provider, item, cfg.get('onboarding', 'binaries', args.provider)), ensure_ascii=False, indent=2)); return 0
    if args.action == 'add':
        name = args.name or args.provider
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name): raise ValueError('Invalid provider name')
        if name in profiles['providers']: raise ValueError('Provider already exists; edit model with providers select')
        api = api_definition(args.provider, args.model, args.base_url, args.key_env) if args.provider in PROTOCOLS else None
        if args.save_key:
            if not api or not sys.stdin.isatty(): raise ValueError('API key entry requires interactive terminal')
            import getpass
            api['api_key_file'] = save_key(cfg.ensure_private_dir(), name, getpass.getpass('API key（隐藏 / hidden）: '))
        d = definition(args.provider, args.model, args.binary or discover().get(args.provider), api)
        if not api:
            runtime = cfg.get('onboarding', 'runtime')
            if not runtime or sys.platform != 'darwin': raise ValueError('New CLI adapters require macOS generated runtime; Linux supports APIs')
            install_runtime(runtime)
        profiles['providers'][name] = d
        cfg.data.setdefault('models', {})[name] = {'enabled': False, 'timeout_s': d['timeout_s']}
        cfg.data.setdefault('budgets', {})[name] = {'enabled': False, 'remaining': None, 'unit': 'calls'}
        util.write_json(cfg.path, cfg.data)
    elif args.action == 'select':
        if args.provider not in profiles['providers']: raise ValueError('Provider not configured; use providers add / 渠道未配置，请先添加')
        d = profiles['providers'][args.provider]
        if not args.model:
            if not sys.stdin.isatty(): raise ValueError('Provide --model or use an interactive terminal')
            try:
                available = models(args.provider, d.get('transport'), cfg.get('onboarding','binaries',args.provider))
                print(json.dumps(available, ensure_ascii=False, indent=2))
            except ValueError as exc: print(str(exc))
            args.model = input('模型ID / Model ID: ').strip()
        if not args.model: raise ValueError('Explicit model required')
        d['model'] = args.model
        if 'transport' in d: d['transport']['model'] = args.model
        if args.provider in ('grok','devin'):
            model_cfg=cfg.data['models'][args.provider]
            extra=model_cfg.get('extra_args',[])[:]
            if '--model' in extra:
                i=extra.index('--model');extra[i+1]=args.model
            else: extra+=['--model',args.model]
            model_cfg['extra_args']=extra
            util.write_json(cfg.path,cfg.data)
        d['label'] = args.provider + ' / ' + args.model
    util.write_json(str(pp), profiles); pp.chmod(0o600)
    print(text(lang, '已保存，未启动推理', 'Saved; no inference started'))
    return 0


def add_parser(sub, lang='zh'):
    from .i18n import text
    p = sub.add_parser('providers', help=text(lang, '检测渠道、模型与 API', 'Discover providers, models and API setup'))
    s = p.add_subparsers(dest='action', required=True)
    for name in ('list', 'refresh-runtime'):
        s.add_parser(name).set_defaults(fn=command)
    m = s.add_parser('models'); m.add_argument('provider'); m.set_defaults(fn=command)
    m = s.add_parser('select'); m.add_argument('provider'); m.add_argument('--model'); m.set_defaults(fn=command)
    m = s.add_parser('add'); m.add_argument('provider', choices=tuple(n for n in NAMES if n not in LEGACY))
    m.add_argument('--name'); m.add_argument('--model', required=True); m.add_argument('--binary')
    m.add_argument('--base-url'); m.add_argument('--key-env'); m.add_argument('--save-key', action='store_true')
    m.set_defaults(fn=command)
