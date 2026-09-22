#!/usr/bin/env python3
"""First-run configuration wizard. Interactive login, explicit models and disabled execution gates."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from wq.i18n import default_language, preferred_language

from wq.providers import NAMES, CLI, LEGACY, PROTOCOLS, discover, models as list_models, api_definition, definition, install_runtime, save_key, inventory


def assignments(items, providers):
    result = {}
    for item in items:
        key, sep, value = item.partition('=')
        if not sep or key not in providers or key in result or not value.strip() or '\n' in value:
            raise ValueError('Expected one PROVIDER=VALUE per selected provider: '+key)
        result[key] = value.strip()
    return result


def configure(root, runtime, selected, models, binaries, roles, efforts, platform=None, apis=None):
    platform = platform or sys.platform
    apis = apis or {}
    for name in selected:
        if name in PROTOCOLS:
            apis.setdefault(name, api_definition(name, models.get(name,'')))
            from wq.provider_runtime import validate_api
            validate_api(apis[name])
    if platform not in ('darwin', 'linux'): raise ValueError('Use macOS, Linux or Windows WSL2.')
    if not selected or len(set(selected)) != len(selected) or any(n not in NAMES for n in selected):
        raise ValueError('Choose supported, non-duplicate providers.')
    if platform == 'linux' and any(n not in ('grok','devin','cursor',*PROTOCOLS) for n in selected):
        raise ValueError('Linux wizard supports grok/devin/cursor Docker and standard APIs; other CLI adapters currently require macOS.')
    if any(n not in selected for n in roles.values()): raise ValueError('Role provider must be selected.')
    if len(selected)>1 and roles['research']==roles['review']:
        raise ValueError('Choose a different provider for review.')
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
        from setup_local import render
        render(root,runtime,binaries=binaries)
    else:
        from setup_linux import render
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
    if platform=='darwin': install_runtime(runtime)
    for name in selected:
        if name not in LEGACY:
            profiles['providers'][name]=definition(name,models[name],binaries.get(name),apis.get(name))
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
PROVIDER_URLS = {'grok':'https://grok.com/', 'devin':'https://app.devin.ai/',
                 'cursor':'https://cursor.com/', 'zcode':'https://z.ai/',
                 'claude':'https://claude.ai/', 'codex':'https://chatgpt.com/',
                 'gemini':'https://geminicli.com/', 'copilot':'https://github.com/features/copilot',
                 'qwen':'https://qwenlm.github.io/qwen-code-docs/', 'opencode':'https://opencode.ai/'}


def login_flow(root, lang):
    root=Path(root).resolve();path=root/'config/config.json'
    cfg=json.loads(path.read_text());info=cfg.get('onboarding',{})
    providers=info.get('providers',list(cfg.get('models',{})))
    say=lambda zh,en: print(zh if lang=='zh' else en)
    ask=lambda zh,en: input(zh if lang=='zh' else en).strip().lower()
    say('步骤：账号注册与登录（不会启动研究或付费推理）','Step: account registration and login (no research or paid inference)')
    print('WorldQuant BRAIN: '+BRAIN_REGISTER_URL)
    say('请在浏览器自行注册、验证邮箱并接受条款；已有账号可直接登录。','Register, verify your email and accept terms yourself in the browser, or sign in with your existing account.')
    status=info.get('login',{})
    for name in providers:
        if name in PROTOCOLS:
            say(name+'：使用API Key，非订阅登录；环境变量或私有文件。',name+': API key, separate from subscription login; environment variable or private file.');continue
        print(name+': '+PROVIDER_URLS.get(name,''))
        if ask(f'现在登录 {name}？[y/N]：',f'Sign in to {name} now? [y/N]: ')!='y':
            status.setdefault(name,'pending');continue
        if info.get('platform')=='linux':
            runtime=info.get('runtime')
            if not runtime: say('缺少runtime；请查看部署指南。','Missing runtime; see the deployment guide.');continue
            cmd=[sys.executable,str(root/'scripts/provider_login.py'),name,'--config',str(Path(runtime)/'containers.json')]
            say('使用已构建的Docker镜像；缺镜像时先按文档构建。','Uses the configured Docker image; build it first if missing.')
        elif name in CLI and CLI[name]['login'] is not None:
            binary=info.get('binaries',{}).get(name) or discover().get(name)
            if not binary: say('未找到CLI，先安装后用 --login-only 重试。','CLI missing. Install it and retry with --login-only.');status[name]='pending';continue
            cmd=[binary]+CLI[name]['login']
        else:
            say('请在供应商应用中完成登录；此向导不能验证该应用会话。','Sign in inside the vendor app; this wizard cannot verify its session.');status[name]='manual_unverified';continue
        try: code=subprocess.call(cmd,cwd=root)
        except OSError: code=1
        status[name]='login_command_succeeded_unverified' if code==0 else 'failed'
        say('登录命令结束；模型权限需后续单轮验证。' if code==0 else '登录未完成，可稍后继续。',
            'Login command finished; verify model access in a later cycle.' if code==0 else 'Login did not complete; you can resume later.')
    if ask('现在登录BRAIN？[Y/n]：','Sign in to BRAIN now? [Y/n]: ') not in ('n','no'):
        code=subprocess.call([str(root/'wq'),'--config',str(path),'--lang',lang,'brain','login'],cwd=root)
        status['brain']='authenticated' if code==0 else 'failed'
    else: status.setdefault('brain','pending')
    # Re-read: the login command may have updated configuration in a future version.
    cfg=json.loads(path.read_text());cfg.setdefault('onboarding',{})['login']=status
    cfg['onboarding']['brain_authentication_verified']=status.get('brain')=='authenticated'
    cfg['onboarding']['authentication_verified']=False
    cfg['onboarding']['model_access_verified']=False
    cfg.setdefault('ui',{})['language']=lang
    path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    say('登录进度已保存；未完成项可用 ./wq onboard --login-only 继续。','Login progress saved. Resume unfinished steps with ./wq onboard --login-only.')
    print(json.dumps(status,ensure_ascii=False,indent=2))
    return 0 if status.get('brain')=='authenticated' else 3


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lang',choices=['zh','en'],default=None)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--runtime',type=Path,default=Path.home()/'.local/share/autowq-runtime')
    p.add_argument('--login-only',action='store_true',help='Resume login without regenerating config')
    p.add_argument('--skip-login',action='store_true',help='Skip interactive login and leave it pending')
    p.add_argument('--list',action='store_true',help='Detect host executables only; no writes or model calls')
    p.add_argument('--non-interactive',action='store_true')
    p.add_argument('--providers',help='Comma-separated: '+','.join(NAMES))
    p.add_argument('--base-url',action='append',default=[],metavar='PROVIDER=URL')
    p.add_argument('--key-env',action='append',default=[],metavar='PROVIDER=ENV_VAR')
    p.add_argument('--model',action='append',default=[],metavar='PROVIDER=MODEL_ID')
    p.add_argument('--binary',action='append',default=[],metavar='PROVIDER=/ABS/PATH')
    p.add_argument('--reasoning-effort',action='append',default=[],metavar='grok=xhigh')
    for role in ('research','review','engineering'): p.add_argument('--'+role,choices=NAMES)
    a=p.parse_args(argv);found=discover()
    if a.lang is None:
        a.lang=preferred_language(a.root/'config/config.json') if a.login_only else default_language()
        if not a.non_interactive and not a.list and sys.stdin.isatty():
            choice=input(f'语言 / Language [zh/en] ({a.lang}): ').strip().lower()
            if choice not in ('','zh','en'): p.exit(2,'请选择 zh/en / Choose zh or en\n')
            a.lang=choice or a.lang
    zh=a.lang=='zh'
    say=lambda cn,en: print(cn if zh else en)
    ask=lambda cn,en: input(cn if zh else en).strip()
    if a.list: print(json.dumps({'host_executables':found,'supported_providers':inventory(),'model_access_verified':False},ensure_ascii=False,indent=2));return 0
    try:
        if a.login_only:
            if a.non_interactive or not sys.stdin.isatty(): raise ValueError('Login requires an interactive terminal / 登录需要交互终端')
            return login_flow(a.root,a.lang)
        if (a.root/'config/config.json').exists(): raise ValueError('Existing deployment: refusing to overwrite config/config.json. See docs/onboarding.md.')
        if not a.non_interactive and not sys.stdin.isatty(): raise ValueError('Interactive terminal required, or use --non-interactive with explicit choices.')
        say('步骤：选择模型渠道。BRAIN注册入口：'+BRAIN_REGISTER_URL,'Step: select model providers. BRAIN registration: '+BRAIN_REGISTER_URL)
        say('检测到的宿主CLI（不代表已登录或模型有权限）：','Detected host CLIs (not proof of login/model access):');print(json.dumps(found,ensure_ascii=False))
        if sys.platform.startswith('linux'):
            say('Linux/WSL使用隔离Docker镜像；宿主CLI不会直接执行。后续需构建镜像并登录。','Linux/WSL uses isolated Docker images, not host CLIs. Build images and sign in afterward.')
        chosen=a.providers
        if not chosen and not a.non_interactive:chosen=ask('选择渠道，逗号分隔（'+','.join(NAMES)+'）：','Providers, comma separated ('+','.join(NAMES)+'): ')
        selected=[n.strip() for n in (chosen or '').split(',') if n.strip()]
        if not selected: raise ValueError('Select at least one provider.')
        models=assignments(a.model,selected);binaries=assignments(a.binary,selected);efforts=assignments(a.reasoning_effort,selected)
        binaries={**found,**binaries}
        urls=assignments(a.base_url,selected);key_envs=assignments(a.key_env,selected);apis={}
        for name in selected:
            if name not in NAMES:raise ValueError('Unsupported provider: '+name)
            if name!='zcode' and name not in models and not a.non_interactive:
                if name in CLI:
                    try: print(json.dumps(list_models(name,binary=binaries.get(name)),ensure_ascii=False))
                    except ValueError as exc: print(str(exc))
                models[name]=ask(name+' 模型ID（使用本人CLI列出的可用ID）：',name+' model ID (as listed by your CLI): ')
            if sys.platform=='darwin' and name in CLI and name!='zcode' and name not in binaries and not a.non_interactive:
                binaries[name]=os.path.expanduser(ask(name+' 可执行文件绝对路径：',name+' executable absolute path: '))
            if name in PROTOCOLS:
                if not a.non_interactive:
                    urls[name]=urls.get(name) or ask(name+' Base URL（回车官方默认，包含/v1）：',name+' Base URL (Enter for official default, include /v1): ')
                    key_envs[name]=key_envs.get(name) or ask(name+' Key环境变量名（回车默认）：',name+' key environment variable (Enter for default): ')
                apis[name]=api_definition(name,models.get(name,''),urls.get(name),key_envs.get(name))
        if 'grok' in selected and 'grok' not in efforts and not a.non_interactive:
            value=ask('Grok思考强度 low/medium/high/xhigh（回车使用CLI默认）：','Grok effort low/medium/high/xhigh (Enter for CLI default): ')
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
        if apis and not a.non_interactive:
            import getpass
            pp=a.root/'config/profiles.json';profiles=json.loads(pp.read_text())
            for name in apis:
                if ask(name+' 现在隐藏输入API Key并存到仓库外？[y/N]：',name+' enter hidden API key and save outside repository? [y/N]: ').lower()=='y':
                    key_path=save_key(cfg['paths']['private_dir'],name,getpass.getpass('API key: '))
                    profiles['providers'][name]['transport']['api_key_file']=key_path
            pp.write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+'\n')
        cfg['ui']={'language':a.lang}
        (a.root/'config/config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
        say('WorldQuant BRAIN 注册：'+BRAIN_REGISTER_URL,'WorldQuant BRAIN registration: '+BRAIN_REGISTER_URL)
        login_code=0
        if not a.non_interactive and not a.skip_login:
            login_code=login_flow(a.root,a.lang)
        else:
            say('登录待完成：./wq onboard --login-only','Login pending: ./wq onboard --login-only')
        say('配置已保存。下一步：完成待办登录 → doctor → 离线导入 → 预算/授权 → 真实单轮核验 → 开启调度。','Configuration saved. Next: finish pending logins → doctor → offline import → budgets/authorization → verify one real cycle → enable scheduling.')
        if len(selected)==1:say('仅一个渠道：可做离线/单模型工作；自动研究要求不同渠道审查，暂不具备条件。','One provider: offline/single-model use only; autopilot requires a distinct review provider.')
        say('完整操作单：docs/onboarding.md；未发起付费推理或安装调度服务。','Full checklist: docs/onboarding.md. No paid inference or scheduler installation performed.')
        return login_code
    except (ValueError,OSError,EOFError,KeyboardInterrupt) as e:
        p.exit(1,str(e)+'\n')

if __name__=='__main__':raise SystemExit(main())
