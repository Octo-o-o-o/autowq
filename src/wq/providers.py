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


def install_custom(cfg, spec, key=None):
    """把用户自己的 OpenAI / Anthropic 兼容服务写入已有配置。密钥只进私有文件。"""
    from . import util
    name = str(spec.get('name') or '').strip()
    protocol = str(spec.get('protocol') or '').strip()
    model = str(spec.get('model') or '').strip()
    base_url = str(spec.get('base_url') or '').strip()
    roles = spec.get('roles') or []
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', name):
        raise ValueError('名称须以字母开头，只含字母、数字、下划线和短横线')
    if name in CLI or name in PROTOCOLS:
        raise ValueError('这个名称留给内置渠道，请换一个')
    if spec.get('preset'):
        protocol = 'openai'
    if protocol not in ('openai', 'anthropic'):
        raise ValueError('协议只接受 openai 或 anthropic')
    if not isinstance(roles, list) or any(role not in ('research', 'review', 'engineering') for role in roles):
        raise ValueError('角色只接受 research、review、engineering')
    allow_no_key = spec.get('allow_no_key') is True
    key = (key or '').strip()
    if key and allow_no_key:
        raise ValueError('不需要密钥时不要再填写密钥')
    if not key and not allow_no_key:
        raise ValueError('填写 API Key，或标明这个服务不需要密钥')
    env_name = 'WQ_' + re.sub(r'[^A-Za-z0-9_]', '_', name).upper() + '_API_KEY'
    quota_reset = None
    if spec.get('preset'):
        # 免费预设：地址、模型、token 参数和额度重置规则来自 free_apis，用户只提供 Key。
        from . import free_apis
        api, quota_reset = free_apis.api_spec(spec['preset'], model or None, name)
        model = api['model']
    else:
        api = api_definition(protocol, model, base_url, env_name)
    if allow_no_key:
        api['allow_no_key'] = True
    path = Path(cfg.resolve(cfg.get('routing', 'profiles_file', default='config/profiles.json')))
    profiles = json.loads(path.read_text(encoding='utf-8'))
    providers = profiles.setdefault('providers', {})
    if name in providers:
        raise ValueError('已经有同名渠道')
    if key:
        api['api_key_file'] = save_key(cfg.ensure_private_dir(), name, key)
    providers[name] = definition(protocol, model, api=api)
    providers[name]['label'] = name + ' / ' + model
    if spec.get('preset'):
        providers[name]['timeout_s'] = api['timeout_s'] + 60
        providers[name]['free_preset'] = spec['preset']
        if quota_reset:
            providers[name]['quota_reset'] = quota_reset
    if not profiles.get('presets'):
        raise ValueError('还没有路由预设，请先完成初始化')
    util.write_json(str(path), profiles)
    path.chmod(0o600)
    cfg.data.setdefault('models', {})[name] = {'enabled': True, 'timeout_s': providers[name]['timeout_s']}
    cfg.data.setdefault('budgets', {})[name] = {
        'enabled': True, 'remaining': 10000, 'unit': 'calls', 'as_of': util.now_iso()}
    util.write_json(cfg.path, cfg.data)
    return {'name': name, 'roles': list(roles)}


def assign_role(cfg, conn, role, provider):
    """把一个已保存的模型放到当前预设里该角色的第一位。研究和审查的第一位不能相同。"""
    from . import routing, store, util
    if role not in ('research', 'review', 'engineering'):
        raise ValueError('角色只接受 research、review、engineering')
    path = Path(cfg.resolve(cfg.get('routing', 'profiles_file', default='config/profiles.json')))
    profiles = json.loads(path.read_text(encoding='utf-8'))
    if provider not in profiles.get('providers', {}):
        raise ValueError('还没有这个模型')
    preset_name = store.get_flag(conn, 'active_preset') or profiles.get('default')
    preset = (profiles.get('presets') or {}).get(preset_name)
    if not isinstance(preset, dict):
        raise ValueError('当前预设由流程文件固定，不能在菜单里改研究和审查的模型')
    routes = preset.setdefault('routes', {})
    other = {'research': 'review', 'review': 'research'}.get(role)
    if other:
        other_chain = routes.get(other) or []
        known = profiles.get('providers', {})
        if other_chain and routing.same_channel({'providers': known}, other_chain[0], provider):
            raise ValueError('研究和审查要使用不同的模型（同一服务地址或同一 CLI 算同一渠道）')
    chain = [provider] + [item for item in (routes.get(role) or []) if item != provider]
    routes[role] = chain
    util.write_json(str(path), profiles)
    path.chmod(0o600)
    routing.catalog(cfg)
    return {'preset': preset_name, 'role': role, 'provider': provider}


def runtime_argv(cfg, definition):
    """Freeze the selected transport and use existing isolation for new CLI adapters."""
    item = definition['transport']
    if item.get('kind') == 'api': validate_api(item)
    elif item.get('kind') not in CLI or item.get('kind') in LEGACY: raise ValueError('Unsupported transport kind')
    script = str(Path(__file__).with_name('provider_runtime.py'))
    if getattr(sys, 'frozen', False):
        # 打包的 Windows exe：sys.executable 是 exe 自身，用 --provider-runtime 分发到内置模块。
        argv = [sys.executable, '--provider-runtime', json.dumps(item), '{prompt}']
    else:
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


def sandbox_work_dirs(cfg):
    """配置里真实会作为调用 cwd 的目录：路由 work_root 与 models.*.workdir。"""
    dirs = [cfg.resolve(cfg.get('routing', 'work_root', default='var/jobs'))]
    for item in (cfg.get('models', default={}) or {}).values():
        if isinstance(item, dict) and item.get('workdir'):
            dirs.append(cfg.resolve(item['workdir']))
    return dirs


def install_runtime(runtime, binaries=None, verified_versions=None, root=None, work_dirs=()):
    """刷新部署 runtime：provider_entry.py、launchers、agents.sb 与推理适配器。
    幂等，用于应用更新或源码修复后执行 wq providers refresh-runtime。"""
    if not runtime:
        raise ValueError('onboarding.runtime missing; run wq onboard first / 未记录 runtime，请先初始化')
    runtime = Path(runtime).expanduser()
    if sys.platform == 'darwin':
        from .setup_local import write_runtime_files
        write_runtime_files(runtime, binaries, verified_versions, root=root, work_dirs=work_dirs)
    elif (runtime / 'docker_provider.py').is_file():
        # Linux/WSL2 走 Docker 适配器；sandbox-exec 启动器在此无意义，不生成。
        from .assets import path as asset_path
        shutil.copyfile(asset_path('docker_provider.py'), runtime / 'docker_provider.py')
    target = runtime / 'launchers/provider_runtime.py'
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
    if args.action == 'free':
        from . import free_apis
        rows = free_apis.listing(args.region, 'zh' if lang == 'zh' else 'en')
        for row in rows:
            print(f"{row['preset']}  [{row['region']}]  {row['label']}" + (text(lang, '（限时）', ' (trial)') if row['trial'] else ''))
            print('  ' + text(lang, '默认模型：', 'Default model: ') + row['model'] + '  ' + text(lang, '可选：', 'alternatives: ') + ', '.join(row['models']))
            print('  ' + text(lang, '额度：', 'Limits: ') + row['limits'])
            print('  ' + text(lang, '注意：', 'Note: ') + row['caveats'])
            print('  ' + text(lang, '申请 Key：', 'Get a key: ') + row['signup'])
        print(text(lang, f'以上按 {free_apis.VERIFIED_AT} 的官方文档整理，供应商可能随时调整；添加：wq providers add-free <预设> --save-key [--roles research]',
                         f'Compiled from official docs on {free_apis.VERIFIED_AT}; vendors may change terms. Add: wq providers add-free <preset> --save-key [--roles research]'))
        return 0
    pp = Path(cfg.resolve(cfg.get('routing', 'profiles_file', default='config/profiles.json')))
    profiles = json.loads(pp.read_text()) if pp.exists() else {'providers':{}}
    if args.action not in ('models',) and not pp.exists(): raise ValueError('Run wq onboard first / 请先初始化')
    if args.action == 'add-free':
        from . import free_apis
        roles = [r.strip() for r in (args.roles or '').split(',') if r.strip()]
        key = os.environ.get('WQ_PROVIDER_KEY', '')
        if args.save_key:
            if not sys.stdin.isatty(): raise ValueError('API key entry requires interactive terminal')
            import getpass
            print(free_apis.PRESETS[args.preset]['signup'] if args.preset in free_apis.PRESETS else '')
            key = getpass.getpass('API key（隐藏 / hidden）: ')
        if not key:
            raise ValueError(text(lang, '需要 API Key：加 --save-key 隐藏输入，或设置 WQ_PROVIDER_KEY 后运行',
                                        'API key required: pass --save-key for hidden entry, or set WQ_PROVIDER_KEY'))
        added = install_custom(cfg, {'name': args.name or args.preset, 'preset': args.preset,
                                     'model': args.model or '', 'roles': roles}, key)
        if roles:
            from .db import connect
            conn = connect(cfg.db_path)
            try:
                for role in roles:
                    assign_role(cfg, conn, role, added['name'])
            finally:
                conn.close()
        item = free_apis.PRESETS[args.preset]
        print(text(lang, f"已添加 {added['name']}（{item['label']}），已启用；额度用尽时自动暂停到重置时刻，恢复后自动继续。",
                         f"Added {added['name']} ({item['label']}) and enabled it; when its quota runs out it pauses until reset and resumes automatically."))
        print(text(lang, '注意：', 'Note: ') + item['caveats'][0 if lang == 'zh' else 1])
        return 0
    if args.action == 'refresh-runtime':
        install_runtime(cfg.get('onboarding', 'runtime'),
                        cfg.get('onboarding', 'binaries', default={}) or {},
                        cfg.get('onboarding', 'verified_versions', default={}) or {},
                        root=cfg.root, work_dirs=sandbox_work_dirs(cfg))
        print(text(lang, '运行适配器已更新（entry/launchers/沙箱/推理适配器）', 'Runtime adapters updated (entry/launchers/sandbox/inference adapter)')); return 0
    if args.action == 'models':
        d = profiles.get('providers', {}).get(args.provider, {})
        item = d.get('transport')
        print(json.dumps(models(args.provider, item, cfg.get('onboarding', 'binaries', args.provider)), ensure_ascii=False, indent=2)); return 0
    if args.action == 'add':
        name = args.name or args.provider
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name): raise ValueError('Invalid provider name')
        if name in profiles['providers']: raise ValueError('Provider already exists; edit model with providers select')
        api = api_definition(args.provider, args.model, args.base_url, args.key_env) if args.provider in PROTOCOLS else None
        if api is not None and args.allow_no_key:
            api['allow_no_key'] = True
            validate_api(api)
        if args.save_key:
            if not api or not sys.stdin.isatty(): raise ValueError('API key entry requires interactive terminal')
            import getpass
            api['api_key_file'] = save_key(cfg.ensure_private_dir(), name, getpass.getpass('API key（隐藏 / hidden）: '))
        d = definition(args.provider, args.model, args.binary or discover().get(args.provider), api)
        if not api:
            runtime = cfg.get('onboarding', 'runtime')
            if not runtime or sys.platform != 'darwin': raise ValueError('New CLI adapters require macOS generated runtime; Linux supports APIs')
            install_runtime(runtime, cfg.get('onboarding', 'binaries', default={}) or {},
                            cfg.get('onboarding', 'verified_versions', default={}) or {}, root=cfg.root,
                            work_dirs=sandbox_work_dirs(cfg))
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
    m.add_argument('--allow-no-key', action='store_true')
    m.set_defaults(fn=command)
    from .free_apis import PRESETS
    m = s.add_parser('free', help=text(lang, '列出官方免费 API 预设（海外与中国大陆）', 'List official free API presets (global and mainland China)'))
    m.add_argument('--region', choices=['global', 'cn']); m.set_defaults(fn=command)
    m = s.add_parser('add-free', help=text(lang, '用免费预设添加渠道，只需一个 API Key', 'Add a provider from a free preset; only an API key is needed'))
    m.add_argument('preset', choices=tuple(PRESETS)); m.add_argument('--name'); m.add_argument('--model')
    m.add_argument('--roles', help='research,review,engineering'); m.add_argument('--save-key', action='store_true')
    m.set_defaults(fn=command)
