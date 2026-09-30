#!/usr/bin/env python3
"""First-run configuration wizard. Interactive login, explicit models and disabled execution gates."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess

from .i18n import default_language, preferred_language
from .providers import NAMES, CLI, LEGACY, PROTOCOLS, discover, models as list_models, api_definition, definition, install_runtime, save_key, inventory


def assignments(items, providers):
    result = {}
    for item in items:
        key, sep, value = item.partition('=')
        if not sep or key not in providers or key in result or not value.strip() or '\n' in value:
            raise ValueError('Expected one PROVIDER=VALUE per selected provider: '+key)
        result[key] = value.strip()
    return result


def _same_service(a, b, apis):
    """两个 API 渠道指向同一服务地址时视为同一渠道（CLI 名称本身唯一）。"""
    from .routing import channel_id
    apis = apis or {}
    if a in apis and b in apis:
        return channel_id({'transport': apis[a]}) == channel_id({'transport': apis[b]})
    return False


def configure(root, runtime, selected, models, binaries, roles, efforts, platform=None, apis=None):
    platform = platform or sys.platform
    apis = apis or {}
    for name in selected:
        if name in PROTOCOLS:
            apis.setdefault(name, api_definition(name, models.get(name,'')))
            from wq.provider_runtime import validate_api
            validate_api(apis[name])
    if platform not in ('darwin', 'linux', 'win32'): raise ValueError('Use macOS, Linux or Windows.')
    if not selected or len(set(selected)) != len(selected):
        raise ValueError('Choose supported, non-duplicate providers.')
    custom = [n for n in selected if n not in NAMES]
    if any(n not in apis for n in custom):
        raise ValueError('Custom endpoint missing API definition.')
    import re
    if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', n) or n in CLI or n in PROTOCOLS for n in custom):
        raise ValueError('Custom endpoint name is invalid or reserved.')
    if platform == 'win32' and any(n not in apis for n in selected):
        raise ValueError('Native Windows supports API providers only (OpenAI/Anthropic protocols, free presets, custom endpoints); '
                         'CLI providers need macOS, or Linux/WSL2 with Docker.')
    if platform == 'linux' and any(n not in ('grok','devin','cursor',*PROTOCOLS) and n not in custom for n in selected):
        raise ValueError('Linux wizard supports grok/devin/cursor Docker and standard APIs; other CLI adapters currently require macOS.')
    if any(n not in selected for n in roles.values()): raise ValueError('Role provider must be selected.')
    if len(selected)>1 and (roles['research']==roles['review'] or _same_service(roles['research'], roles['review'], apis)):
        raise ValueError('Choose a different provider for review (two names for one service count as one).')
    for name in selected:
        if name != 'zcode' and not models.get(name): raise ValueError('Explicit model ID required: '+name)
        if name=='zcode' and models.get(name): raise ValueError('ZCode uses its app configuration; model override is unsupported.')
        if platform=='darwin' and name in CLI and name!='zcode':
            binary=Path(binaries.get(name,''))
            if not binary.is_absolute() or not binary.is_file() or not os.access(binary,os.X_OK):
                raise ValueError('Executable not found; install CLI or supply --binary: '+name)
    if any(name!='grok' or value not in ('low','medium','high','xhigh') for name,value in efforts.items()):
        raise ValueError('Only Grok reasoning effort low/medium/high/xhigh is supported here.')
    # Validate all user choices before the existing renderer writes anything.
    if platform=='darwin':
        from .setup_local import render
        render(root,runtime,binaries=binaries)
    elif platform=='win32':
        from .windows_setup import render
        render(root,runtime)
    else:
        from .setup_linux import render
        render(root,runtime)
    root=Path(root);runtime=Path(runtime).expanduser().resolve()
    cp=root/'config/config.json';pp=root/'config/profiles.json'
    cfg=json.loads(cp.read_text());profiles=json.loads(pp.read_text())
    profiles['providers']={k:v for k,v in profiles['providers'].items() if k in selected}
    routes={role:[roles[role]] for role in ('research','review','engineering')}
    profiles['presets']={'local':{'description':'User-selected providers; no implicit fallback',
        'routes':routes,'retries':3,'retry_delays_s':[30,60,120]}}
    profiles['default']='local'
    containers=json.loads((runtime/'containers.json').read_text()) if platform=='linux' else None
    if platform=='darwin': install_runtime(runtime, binaries, root=root)
    for name in selected:
        if name not in LEGACY:
            kind = apis[name]['protocol'] if name in apis and name not in PROTOCOLS else name
            profiles['providers'][name]=definition(kind,models[name],binaries.get(name),apis.get(name))
            cfg['models'][name]={'enabled':False,'timeout_s':profiles['providers'][name]['timeout_s']}
            cfg['budgets'][name]={'enabled':False,'remaining':None,'unit':'calls'}
        provider_def=profiles['providers'][name]
        if name in models: provider_def['model']=models[name]
        if name in ('grok','devin'):
            argv=containers['providers'][name]['argv'] if containers else provider_def['argv']
            argv.extend(['--model','{model}'])
            if name in efforts: argv.extend(['--reasoning-effort',efforts[name]])
        provider_def['label']=name+' / '+models.get(name,'app-configured model')
        cfg['models'][name]['enabled']=False
        if platform=='darwin' and name in LEGACY and name in binaries: cfg['models'][name]['bin']=str(runtime/'launchers'/name)
        if name in ('grok','devin'):
            cfg['models'][name]['extra_args']=['--model',models[name]]
            if name in efforts: cfg['models'][name]['extra_args']+=['--reasoning-effort',efforts[name]]
    cfg['onboarding']={'version':1,'providers':selected,'models':models,
        'runtime':str(runtime),'platform':platform,'authentication_verified':False,
        'binaries':{n:binaries[n] for n in selected if n in binaries},
        'single_provider':len(selected)==1}
    cfg['autopilot']['max_cycles_total']=4
    for path,data in [(cp,cfg),(pp,profiles)]:
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    if containers:
        containers['providers']={k:v for k,v in containers['providers'].items() if k in selected}
        (runtime/'containers.json').write_text(json.dumps(containers,ensure_ascii=False,indent=2)+'\n')
    return cfg


BRAIN_REGISTER_URL = 'https://platform.worldquantbrain.com/sign-up'
BRAIN_PLATFORM_URL = 'https://worldquantbrain.com'
PROVIDER_URLS = {'grok':'https://grok.com/', 'devin':'https://app.devin.ai/',
                 'cursor':'https://cursor.com/', 'zcode':'https://z.ai/',
                 'claude':'https://claude.ai/', 'codex':'https://chatgpt.com/',
                 'gemini':'https://geminicli.com/', 'copilot':'https://github.com/features/copilot',
                 'qwen':'https://qwenlm.github.io/qwen-code-docs/', 'opencode':'https://opencode.ai/'}


def intro_flow(lang):
    """First-run welcome: what autowq does, the road to Gold and beyond, and where you start.

    Returns 'fresh' (new to BRAIN / not yet Gold) or 'gold' (already Gold or consultant).
    The choice only tailors guidance copy; BRAIN login is still required either way,
    because the research loop runs under the user's own account.
    """
    zh = lang == 'zh'
    color = sys.stdout.isatty() and not os.environ.get('NO_COLOR')
    if color and sys.platform == 'win32':
        os.system('')  # enable VT escape processing on legacy conhost
    amber = '\x1b[38;5;214m' if color else ''
    hot = '\x1b[38;5;220m' if color else ''
    dim = '\x1b[38;5;245m' if color else ''
    bold = '\x1b[1m' if color else ''
    rst = '\x1b[0m' if color else ''
    say = lambda cn, en: print(cn if zh else en)
    ask = lambda cn, en: input(cn if zh else en).strip().lower()

    def screen(no, title):
        print()
        print(amber + '─' * 66 + rst)
        print(hot + ('● AUTOWQ · 首次使用引导    %d/3' % no if zh else '● AUTOWQ · FIRST-RUN INTRO    %d/3' % no) + rst)
        print(bold + title + rst)
        print(amber + '─' * 66 + rst)

    screen(1, '把沉睡的 token，变成你的研究算力。' if zh else 'Turn idle tokens into research compute.')
    say('autowq 跑在你已订阅的 Agent（Grok、Cursor、Claude Code、Codex 等）用不完的',
        'autowq runs on the surplus tokens of the agent subscriptions you already pay for')
    say('token 富余量里——官方免费 API、自己部署的模型同样可以。',
        '(Grok, Cursor, Claude Code, Codex …) — official free APIs and self-hosted models work too.')
    say('它把这些算力组织成一条受控的 WorldQuant BRAIN 研究回路：',
        'It turns that idle compute into a gated WorldQuant BRAIN research loop:')
    say('假设 → 异渠道审查 → 有限回测 → 提交前验收，日夜自动循环。',
        'hypothesis → cross-provider review → bounded simulation → gated submission, on repeat.')
    say('软件开源免费，不多花一分钱；研究成果与报酬来自 WorldQuant 平台，规则以官方为准。',
        'Free and open source — not a cent extra. Results and rewards come from the WorldQuant platform.')
    print(dim + ('平台本身由官方自己介绍：按 o 打开 WorldQuant 官网，这里不越俎代庖。' if zh
                 else 'Under its official rules. The platform speaks for itself — press o to open their site.') + rst)
    while True:
        pick = ask('[Enter 继续 · o 了解 WorldQuant] ', '[Enter to continue · o for worldquantbrain.com] ')
        if pick != 'o':
            break
        say('正在打开 ' + BRAIN_PLATFORM_URL + ' ……', 'Opening ' + BRAIN_PLATFORM_URL + ' ...')
        try:
            import webbrowser
            webbrowser.open(BRAIN_PLATFORM_URL)
        except Exception:
            pass

    screen(2, '这条路，分三段走。' if zh else 'The road has three legs.')
    say('① 第一周 · 从注册到金牌 —— 受控循环把「提出—验证—复盘」缩到最短。',
        '① Week one · sign-up to Gold — the gated loop shrinks every propose-verify-review cycle.')
    say('   作者实测：装上第 5 天账号升上金牌（个人案例，速度不作承诺）。',
        '   In the author\'s own run the account reached Gold on day 5 (one person\'s history, not a promise).')
    say('② 金牌之后 · 等待官方邀请期间 —— 持续自动研究，让每一枚 Alpha 都比上一枚更有依据。',
        '② After Gold · while the official invitation is pending — keep researching, so each alpha')
    say('   邀请、签约与激活以你所在地区的官方通知为准。',
        '   stands on better evidence than the last. Invitation and contracting follow official notice.')
    say('③ 成为顾问 · 冲向 Grandmaster —— WorldQuant 官方写明：Grandmaster 级顾问',
        '③ Consultant · on toward Grandmaster — WorldQuant states Grandmaster-level consultants')
    say('   每季度报酬可达 8,000 美元以上。持续提高研究质量，就是朝这条线走。',
        '   can earn upwards of $8,000 per quarter. Better research is how you walk that line.')
    ask('[Enter 继续] ', '[Enter to continue] ')

    screen(3, '你从哪里开始？' if zh else 'Where do you start?')
    say('1) 全新开始 —— 还没有 BRAIN 账号，或还没到金牌。向导会带你走完注册指引、',
        '1) Fresh start — no BRAIN account yet, or not yet Gold. The wizard guides you through')
    say('   登录与第一次研究设置。', '   registration pointers, login and the first research setup.')
    say('2) 我已是金牌或顾问 —— 跳过新手引导，直接进入持续研究的设置；',
        '2) I am already Gold / a consultant — skip the newcomer guidance and go straight to the')
    say('   BRAIN 登录仍然需要，因为回路跑的正是你的账号。',
        '   continuous-research setup; BRAIN login is still required, since the loop runs as you.')
    choice = ask('选择 [1]: ', 'Choose [1]: ')
    if choice in ('2', 'gold', 'g'):
        say('好的——直接进入持续研究设置。', 'Got it — straight to the continuous-research setup.')
        return 'gold'
    say('好的——从第一段开始。', 'Got it — starting from leg one.')
    return 'fresh'


def login_flow(root, lang, persist=None):
    root=Path(root).resolve();path=root/'config/config.json'
    cfg=json.loads(path.read_text());info=cfg.get('onboarding',{})
    providers=info.get('providers',list(cfg.get('models',{})))
    say=lambda zh,en: print(zh if lang=='zh' else en)
    ask=lambda zh,en: input(zh if lang=='zh' else en).strip().lower()
    status=info.get('login',{})
    stage=info.get('user_stage','fresh')
    if stage=='gold':
        say('步骤：账号登录（不会启动研究或付费推理）','Step: account login (no research or paid inference)')
        say('你已是金牌或顾问——BRAIN 登录会把账号绑定到本地工作区，研究才能以你的身份进行。',
            'You are already Gold or a consultant — signing in binds your BRAIN account to this workspace so research runs as you.')
    else:
        say('步骤：账号注册与登录（不会启动研究或付费推理）','Step: account registration and login (no research or paid inference)')
        print('WorldQuant BRAIN: '+BRAIN_REGISTER_URL)
        say('请在浏览器自行注册、验证邮箱并接受条款；已有账号可直接登录。','Register, verify your email and accept terms yourself in the browser, or sign in with your existing account.')
    for name in providers:
        login_env=None
        if name in PROTOCOLS:
            say(name+'：使用API Key，非订阅登录；环境变量或私有文件。',name+': API key, separate from subscription login; environment variable or private file.');continue
        print(name+': '+PROVIDER_URLS.get(name,''))
        if ask(f'现在登录 {name}？[y/N]：',f'Sign in to {name} now? [y/N]: ')!='y':
            status.setdefault(name,'pending');continue
        if info.get('platform')=='linux':
            runtime=info.get('runtime')
            if not runtime: say('缺少runtime；请查看部署指南。','Missing runtime; see the deployment guide.');continue
            from .assets import path as asset_path
            cmd=[sys.executable,str(asset_path('provider_login.py')),name,'--config',str(Path(runtime)/'containers.json')]
            say('使用已构建的Docker镜像；缺镜像时先按文档构建。','Uses the configured Docker image; build it first if missing.')
        elif name=='zcode':
            # ZCode CLI 自带 OAuth 登录子命令，但入口是 node 脚本，不能直接 exec。
            import shutil as _shutil
            node=os.environ.get('WQ_NODE_BIN') or _shutil.which('node')
            binary=info.get('binaries',{}).get('zcode') or discover().get('zcode')
            if not node or not binary:
                say('缺少 node 或 ZCode 应用 CLI，先安装后用 --login-only 重试。','Missing node or the ZCode app CLI. Install them and retry with --login-only.');status[name]='pending';continue
            cmd=[node,binary,'login']
            # 与 provider_entry 运行时相同的内置 provider 配置，登录态才对应实际调用渠道。
            from .assets.provider_entry import ZCODE_PROVIDER_CONFIG
            login_env={**os.environ,'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE':ZCODE_PROVIDER_CONFIG}
        elif name in CLI and CLI[name]['login'] is not None:
            binary=info.get('binaries',{}).get(name) or discover().get(name)
            if not binary: say('未找到CLI，先安装后用 --login-only 重试。','CLI missing. Install it and retry with --login-only.');status[name]='pending';continue
            cmd=[binary]+CLI[name]['login']
        else:
            say('请在供应商应用中完成登录；此向导不能验证该应用会话。','Sign in inside the vendor app; this wizard cannot verify its session.');status[name]='manual_unverified';continue
        try: code=subprocess.call(cmd,cwd=root,env=login_env)
        except OSError: code=1
        status[name]='login_command_succeeded_unverified' if code==0 else 'failed'
        say('登录命令结束；模型权限需后续单轮验证。' if code==0 else '登录未完成，可稍后继续。',
            'Login command finished; verify model access in a later cycle.' if code==0 else 'Login did not complete; you can resume later.')
    if ask('现在登录BRAIN？[Y/n]：','Sign in to BRAIN now? [Y/n]: ') not in ('n','no'):
        engine=[sys.executable,'--engine'] if getattr(sys,'frozen',False) else [sys.executable,'-m','wq']
        code=subprocess.call(engine+['--config',str(path),'--lang',lang,'brain','login'],cwd=root)
        status['brain']='authenticated' if code==0 else 'failed'
    else: status.setdefault('brain','pending')
    # Re-read: the login command may have updated configuration in a future version.
    cfg=json.loads(path.read_text());cfg.setdefault('onboarding',{})['login']=status
    cfg['onboarding']['brain_authentication_verified']=status.get('brain')=='authenticated'
    cfg['onboarding']['authentication_verified']=False
    cfg['onboarding']['model_access_verified']=False
    cfg.setdefault('ui',{})['language']=persist or lang   # 允许持久化 'auto'（跟随系统）
    path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    say('登录进度已保存；未完成项可用 wq onboard --login-only 继续。','Login progress saved. Resume unfinished steps with wq onboard --login-only.')
    print(json.dumps(status,ensure_ascii=False,indent=2))
    return 0 if status.get('brain')=='authenticated' else 3


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lang',choices=['zh','en','auto'],default=None)
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--runtime',type=Path,default=Path.home()/'.local/share/autowq-runtime')
    p.add_argument('--login-only',action='store_true',help='Resume login without regenerating config')
    p.add_argument('--skip-login',action='store_true',help='Skip interactive login and leave it pending')
    p.add_argument('--skip-intro',action='store_true',help='Skip the first-run welcome screens')
    p.add_argument('--stage',choices=['fresh','gold'],help='Starting point: fresh (new to BRAIN) or gold (already Gold or consultant)')
    p.add_argument('--list',action='store_true',help='Detect host executables only; no writes or model calls')
    p.add_argument('--non-interactive',action='store_true')
    p.add_argument('--providers',help='Comma-separated: '+','.join(NAMES))
    p.add_argument('--custom',action='append',default=[],metavar='NAME=openai|anthropic')
    p.add_argument('--allow-no-key',action='append',default=[],metavar='NAME')
    p.add_argument('--base-url',action='append',default=[],metavar='PROVIDER=URL')
    p.add_argument('--key-env',action='append',default=[],metavar='PROVIDER=ENV_VAR')
    p.add_argument('--model',action='append',default=[],metavar='PROVIDER=MODEL_ID')
    p.add_argument('--binary',action='append',default=[],metavar='PROVIDER=/ABS/PATH')
    p.add_argument('--reasoning-effort',action='append',default=[],metavar='grok=xhigh')
    p.add_argument('--free',action='append',default=[],metavar='PRESET',help='Official free API preset, see `wq providers free`')
    for role in ('research','review','engineering'): p.add_argument('--'+role)
    a=p.parse_args(argv);found=discover()
    if a.lang is None:
        a.lang=preferred_language(a.root/'config/config.json') if a.login_only else default_language()
        if not a.non_interactive and not a.list and sys.stdin.isatty():
            choice=input(f'语言 / Language [zh/en/auto] ({a.lang}): ').strip().lower()
            if choice not in ('','zh','en','auto'): p.exit(2,'请选择 zh/en/auto / Choose zh, en or auto\n')
            a.lang=choice or a.lang
    a.lang_pref=a.lang                                  # 持久化原值，'auto' 表示跟随系统
    a.lang=default_language() if a.lang=='auto' else a.lang
    zh=a.lang=='zh'
    say=lambda cn,en: print(cn if zh else en)
    ask=lambda cn,en: input(cn if zh else en).strip()
    if a.list: print(json.dumps({'host_executables':found,'supported_providers':inventory(),'model_access_verified':False},ensure_ascii=False,indent=2));return 0
    try:
        if a.login_only:
            if a.non_interactive or not sys.stdin.isatty(): raise ValueError('Login requires an interactive terminal / 登录需要交互终端')
            return login_flow(a.root,a.lang,a.lang_pref)
        if (a.root/'config/config.json').exists(): raise ValueError('Existing deployment: refusing to overwrite config/config.json. See docs/onboarding.md.')
        if not a.non_interactive and not sys.stdin.isatty(): raise ValueError('Interactive terminal required, or use --non-interactive with explicit choices.')
        stage=a.stage
        if stage is None and not a.skip_intro and not a.non_interactive and sys.stdin.isatty():
            stage=intro_flow(a.lang)
        stage=stage or 'fresh'
        if stage=='gold':
            say('步骤：选择模型渠道。','Step: select model providers.')
        else:
            say('步骤：选择模型渠道。BRAIN注册入口：'+BRAIN_REGISTER_URL,'Step: select model providers. BRAIN registration: '+BRAIN_REGISTER_URL)
        say('检测到的宿主CLI（不代表已登录或模型有权限）：','Detected host CLIs (not proof of login/model access):');print(json.dumps(found,ensure_ascii=False))
        if sys.platform=='win32':
            say('原生 Windows 只支持 API 渠道：官方免费预设、OpenAI/Anthropic 协议或自建兼容接口；模型 CLI 需在 macOS 或 WSL2 使用。',
                'Native Windows supports API providers only: free presets, OpenAI/Anthropic protocols or self-hosted compatible endpoints. Model CLIs need macOS or WSL2.')
        if sys.platform.startswith('linux'):
            say('Linux/WSL使用隔离Docker镜像；宿主CLI不会直接执行。后续需构建镜像并登录。','Linux/WSL uses isolated Docker images, not host CLIs. Build images and sign in afterward.')
        chosen=a.providers
        if not chosen and not a.non_interactive:chosen=ask('选择渠道，逗号分隔（'+','.join(NAMES)+'）：','Providers, comma separated ('+','.join(NAMES)+'): ')
        selected=[n.strip() for n in (chosen or '').split(',') if n.strip()]
        from . import free_apis
        free=[n.strip() for n in a.free if n.strip()]
        if not a.non_interactive and not a.free and not a.providers and sys.stdin.isatty():
            say('没有订阅或 API Key？下面是官方免费 API 预设，只需注册拿一个 Key：','No subscription or API key? These official free API presets only need one key:')
            for row in free_apis.listing(lang='zh' if zh else 'en'):
                print(f"  {row['preset']:<18}[{row['region']}] {row['label']}：{row['limits']}")
            extra=ask('添加免费预设，逗号分隔（回车跳过）：','Add free presets, comma separated (Enter to skip): ')
            free=[n.strip() for n in extra.split(',') if n.strip()]
        for name in free:
            if name not in free_apis.PRESETS or name in selected: raise ValueError('Unknown or duplicate free preset: '+name)
            selected.append(name)
        custom=[(name,'openai') for name in free]
        for item in a.custom:
            cname, _, cprotocol = item.partition('=')
            cname, cprotocol = cname.strip(), cprotocol.strip()
            if not cname or cprotocol not in ('openai','anthropic') or cname in selected:
                raise ValueError('Expected NAME=openai or NAME=anthropic for each --custom')
            custom.append((cname, cprotocol))
            selected.append(cname)
        if not selected: raise ValueError('Select at least one provider.')
        if not a.non_interactive and not a.providers and sys.stdin.isatty():
            while True:
                extra=ask('再添加一个自建兼容接口的名称（回车跳过）：','Add a self-hosted compatible endpoint name (Enter to skip): ')
                if not extra: break
                protocol=ask(extra+' 协议 openai 或 anthropic：',extra+' protocol, openai or anthropic: ')
                if protocol not in ('openai','anthropic'): raise ValueError('Protocol must be openai or anthropic')
                if extra in selected: raise ValueError('Duplicate provider: '+extra)
                custom.append((extra, protocol))
                selected.append(extra)
        models=assignments(a.model,selected);binaries=assignments(a.binary,selected);efforts=assignments(a.reasoning_effort,selected)
        binaries={**found,**binaries}
        urls=assignments(a.base_url,selected);key_envs=assignments(a.key_env,selected);apis={}
        for name in free:
            item=free_apis.PRESETS[name]
            models.setdefault(name,item['model']);urls.setdefault(name,item['base_url']);key_envs.setdefault(name,free_apis.key_env(name))
            say(f"{name}：{item['caveats'][0 if zh else 1]}｜申请 Key：{item['signup']}",f"{name}: {item['caveats'][1]} | Get a key: {item['signup']}")
        custom_protocol=dict(custom)
        no_key=set(a.allow_no_key)
        for name in selected:
            if name not in NAMES and name not in custom_protocol:raise ValueError('Unsupported provider: '+name)
            if name!='zcode' and name not in models and not a.non_interactive:
                if name in CLI:
                    try: print(json.dumps(list_models(name,binary=binaries.get(name)),ensure_ascii=False))
                    except ValueError as exc: print(str(exc))
                models[name]=ask(name+' 模型ID（使用本人CLI列出的可用ID）：',name+' model ID (as listed by your CLI): ')
            if sys.platform=='darwin' and name in CLI and name!='zcode' and name not in binaries and not a.non_interactive:
                binaries[name]=os.path.expanduser(ask(name+' 可执行文件绝对路径：',name+' executable absolute path: '))
            if name in PROTOCOLS or name in custom_protocol:
                protocol=custom_protocol.get(name, name)
                if not a.non_interactive:
                    urls[name]=urls.get(name) or ask(name+' Base URL（回车官方默认；自建地址请含 /v1。本机和内网可用 http）：',name+' Base URL (Enter for official default; include /v1. http is allowed on localhost and private networks): ')
                    if name in custom_protocol and name not in free and ask(name+' 这个服务不需要 API Key？[y/N]：',name+' does this service need no API key? [y/N]: ').lower()=='y':
                        no_key.add(name)
                    elif name not in no_key:
                        key_envs[name]=key_envs.get(name) or ask(name+' Key环境变量名（回车默认）：',name+' key environment variable (Enter for default): ')
                apis[name]=api_definition(protocol,models.get(name,''),urls.get(name) or None,key_envs.get(name))
                if name in free:
                    spec,_=free_apis.api_spec(name,models.get(name))
                    apis[name].update({k:spec[k] for k in ('token_parameter','max_tokens','timeout_s')})
                if name in no_key:
                    apis[name]['allow_no_key']=True
                    from .provider_runtime import validate_api
                    validate_api(apis[name])
        if 'grok' in selected and 'grok' not in efforts and not a.non_interactive:
            value=ask('Grok 思考强度 low/medium/high/xhigh（回车使用 CLI 默认）：','Grok effort low/medium/high/xhigh (Enter for CLI default): ')
            if value:efforts['grok']=value
        roles={}
        for role in ('research','review','engineering'):
            default=selected[1] if role!='research' and len(selected)>1 else selected[0]
            value=getattr(a,role)
            if value is None and not a.non_interactive:
                value=ask(f'{role}渠道 [{default}]：',f'{role} provider [{default}]: ') or default
            roles[role]=value or default
        say('将生成配置，所有模型、API、调度和提交默认关闭。','Will generate configuration with models, API, scheduling and submission disabled.')
        print(json.dumps({'providers':selected,'models':models,'roles':roles,'effort':efforts,'runtime':str(a.runtime)},ensure_ascii=False,indent=2))
        if not a.non_interactive and ask('写入？[y/N]：','Write configuration? [y/N]: ').lower()!='y':
            say('已取消，没有写入。','Cancelled; no files written.');return 0
        cfg=configure(a.root,a.runtime,selected,models,binaries,roles,efforts,apis=apis)
        cfg['onboarding']['user_stage']=stage
        if free:
            # 免费预设的额度重置规则写在渠道层，额度暂停据此等到重置时刻。
            pp=a.root/'config/profiles.json';profiles=json.loads(pp.read_text())
            for name in free:
                item=free_apis.PRESETS[name];entry=profiles['providers'][name]
                entry['free_preset']=name;entry['timeout_s']=item['timeout_s']+60
                cfg['models'][name]['timeout_s']=entry['timeout_s']
                if item.get('quota_reset'): entry['quota_reset']=item['quota_reset']
            pp.write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+'\n')
        if apis and not a.non_interactive:
            import getpass
            pp=a.root/'config/profiles.json';profiles=json.loads(pp.read_text())
            for name in apis:
                if apis[name].get('allow_no_key'): continue
                if ask(name+' 现在隐藏输入API Key并存到仓库外？[y/N]：',name+' enter hidden API key and save outside repository? [y/N]: ').lower()=='y':
                    key_path=save_key(cfg['paths']['private_dir'],name,getpass.getpass('API key: '))
                    profiles['providers'][name]['transport']['api_key_file']=key_path
            pp.write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+'\n')
        cfg['ui']={'language':a.lang_pref}
        (a.root/'config/config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
        if stage!='gold':
            say('WorldQuant BRAIN 注册：'+BRAIN_REGISTER_URL,'WorldQuant BRAIN registration: '+BRAIN_REGISTER_URL)
        login_code=0
        if not a.non_interactive and not a.skip_login:
            login_code=login_flow(a.root,a.lang,a.lang_pref)
        else:
            say('登录待完成：wq onboard --login-only','Login pending: wq onboard --login-only')
        say('配置已保存。下一步：完成待办登录 → doctor → 离线导入 → 预算/授权 → 真实单轮核验 → 开启调度。','Configuration saved. Next: finish pending logins → doctor → offline import → budgets/authorization → verify one real cycle → enable scheduling.')
        if stage=='gold':
            say('你已是金牌或顾问：登录完成后，托盘「研究进展」与持续研究功能就是你的主场。','Already Gold or a consultant: after login, Research progress in the tray and the continuous-research features are your home turf.')
        if len(selected)==1:say('仅一个渠道：可做离线/单模型工作；自动研究要求不同渠道审查，暂不具备条件。','One provider: offline/single-model use only; autopilot requires a distinct review provider.')
        say('完整操作单：docs/onboarding.md；未发起付费推理或安装调度服务。','Full checklist: docs/onboarding.md. No paid inference or scheduler installation performed.')
        return login_code
    except (ValueError,OSError,EOFError,KeyboardInterrupt) as e:
        p.exit(1,str(e)+'\n')

if __name__=='__main__':raise SystemExit(main())
