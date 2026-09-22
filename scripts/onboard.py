#!/usr/bin/env python3
"""First-run configuration wizard. No login, inference, platform POST or scheduler installation."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

NAMES = ('grok', 'devin', 'cursor', 'zcode')
CANDIDATES = {
    'grok': ('grok', '~/.grok/bin/grok'),
    'devin': ('devin', '~/.local/bin/devin'),
    'cursor': ('cursor-agent', '~/.local/bin/cursor-agent'),
    'zcode': ('/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs',),
}


def discover():
    result = {}
    for name, paths in CANDIDATES.items():
        for item in paths:
            p = shutil.which(item) or os.path.expanduser(item)
            if Path(p).is_file() and (os.access(p, os.X_OK) or name == 'zcode'):
                result[name] = str(Path(p).absolute()); break
    return result


def assignments(items, providers):
    result = {}
    for item in items:
        key, sep, value = item.partition('=')
        if not sep or key not in providers or key in result or not value.strip() or '\n' in value:
            raise ValueError('Expected one PROVIDER=VALUE per selected provider: '+key)
        result[key] = value.strip()
    return result


def configure(root, runtime, selected, models, binaries, roles, efforts, platform=None):
    platform = platform or sys.platform
    if platform not in ('darwin', 'linux'): raise ValueError('Use macOS, Linux or Windows WSL2.')
    if not selected or len(set(selected)) != len(selected) or any(n not in NAMES for n in selected):
        raise ValueError('Choose supported, non-duplicate providers.')
    if platform == 'linux' and 'zcode' in selected: raise ValueError('ZCode has no supported Linux launcher.')
    if any(n not in selected for n in roles.values()): raise ValueError('Role provider must be selected.')
    if len(selected)>1 and roles['research']==roles['review']:
        raise ValueError('Choose a different provider for review.')
    for name in selected:
        if name != 'zcode' and not models.get(name): raise ValueError('Explicit model ID required: '+name)
        if name=='zcode' and models.get(name): raise ValueError('ZCode uses its app configuration; model override is unsupported.')
        if platform=='darwin' and name!='zcode':
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
    for name in selected:
        definition=profiles['providers'][name]
        if name in models: definition['model']=models[name]
        if name in ('grok','devin'):
            argv=containers['providers'][name]['argv'] if containers else definition['argv']
            argv.extend(['--model','{model}'])
            if name in efforts: argv.extend(['--reasoning-effort',efforts[name]])
        definition['label']=name+' / '+models.get(name,'app-configured model')
        cfg['models'][name]['enabled']=False
        if platform=='darwin' and name in binaries: cfg['models'][name]['bin']=str(runtime/'launchers'/name)
        if name in ('grok','devin'):
            cfg['models'][name]['extra_args']=['--model',models[name]]
            if name in efforts: cfg['models'][name]['extra_args']+=['--reasoning-effort',efforts[name]]
    cfg['onboarding']={'version':1,'providers':selected,'models':models,
        'runtime':str(runtime),'platform':platform,'authentication_verified':False,
        'single_provider':len(selected)==1}
    cfg['autopilot']['max_cycles_total']=4
    for path,data in [(cp,cfg),(pp,profiles)]:
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
    if containers:
        containers['providers']={k:v for k,v in containers['providers'].items() if k in selected}
        (runtime/'containers.json').write_text(json.dumps(containers,ensure_ascii=False,indent=2)+'\n')
    return cfg


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lang',choices=['zh','en'],default='zh')
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--runtime',type=Path,default=Path.home()/'.local/share/autowq-runtime')
    p.add_argument('--list',action='store_true',help='Detect host executables only; no writes or model calls')
    p.add_argument('--non-interactive',action='store_true')
    p.add_argument('--providers',help='Comma-separated: grok,devin,cursor,zcode (macOS only)')
    p.add_argument('--model',action='append',default=[],metavar='PROVIDER=MODEL_ID')
    p.add_argument('--binary',action='append',default=[],metavar='PROVIDER=/ABS/PATH')
    p.add_argument('--reasoning-effort',action='append',default=[],metavar='grok=xhigh')
    for role in ('research','review','engineering'): p.add_argument('--'+role,choices=NAMES)
    a=p.parse_args(argv);found=discover();zh=a.lang=='zh'
    say=lambda cn,en: print(cn if zh else en)
    ask=lambda cn,en: input(cn if zh else en).strip()
    if a.list: print(json.dumps({'host_executables':found,'model_access_verified':False},ensure_ascii=False,indent=2));return 0
    try:
        if (a.root/'config/config.json').exists(): raise ValueError('Existing deployment: refusing to overwrite config/config.json. See docs/onboarding.md.')
        if not a.non_interactive and not sys.stdin.isatty(): raise ValueError('Interactive terminal required, or use --non-interactive with explicit choices.')
        say('检测到的宿主CLI（不代表已登录或模型有权限）：','Detected host CLIs (not proof of login/model access):');print(json.dumps(found,ensure_ascii=False))
        if sys.platform.startswith('linux'):
            say('Linux/WSL使用隔离Docker镜像；宿主CLI不会直接执行。后续需构建镜像并登录。','Linux/WSL uses isolated Docker images, not host CLIs. Build images and sign in afterward.')
        chosen=a.providers
        if not chosen and not a.non_interactive:chosen=ask('选择渠道，逗号分隔（grok,devin,cursor；Mac另支持zcode）：','Providers, comma separated (grok,devin,cursor; zcode on Mac): ')
        selected=[n.strip() for n in (chosen or '').split(',') if n.strip()]
        if not selected: raise ValueError('Select at least one provider.')
        models=assignments(a.model,selected);binaries=assignments(a.binary,selected);efforts=assignments(a.reasoning_effort,selected)
        binaries={**found,**binaries}
        for name in selected:
            if name not in NAMES:raise ValueError('Unsupported provider: '+name)
            if name!='zcode' and name not in models and not a.non_interactive:
                models[name]=ask(name+' 模型ID（使用本人CLI列出的可用ID）：',name+' model ID (as listed by your CLI): ')
            if sys.platform=='darwin' and name!='zcode' and name not in binaries and not a.non_interactive:
                binaries[name]=os.path.expanduser(ask(name+' 可执行文件绝对路径：',name+' executable absolute path: '))
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
        configure(a.root,a.runtime,selected,models,binaries,roles,efforts)
        say('初始化完成。下一步：本人登录CLI → doctor → 离线导入 → 设置预算/授权 → 真实单轮核验 → 开启调度。','Configured. Next: sign in to CLIs → doctor → offline import → budgets/authorization → verify one real cycle → enable scheduling.')
        if len(selected)==1:say('仅一个渠道：可做离线/单模型工作；自动研究要求不同渠道审查，暂不具备条件。','One provider: offline/single-model use only; autopilot requires a distinct review provider.')
        say('完整操作单：docs/onboarding.md；未执行登录、付费请求或安装服务。','Full checklist: docs/onboarding.md. No login, paid requests or service installation performed.')
        return 0
    except (ValueError,OSError,EOFError,KeyboardInterrupt) as e:
        p.exit(1,str(e)+'\n')

if __name__=='__main__':raise SystemExit(main())
