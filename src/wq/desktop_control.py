#!/usr/bin/env python3
"""Bounded menu-bar bridge; delegates all research permissions to the existing CLI.

语言：读 config.json 的 ui.language（zh/en/auto），auto 与未配置按环境检测；
所有返回给菜单栏/托盘的文案按该语言渲染，菜单可切换并写回。
"""
import datetime as dt
import json
import sqlite3
import os
from pathlib import Path
import subprocess
import sys
import webbrowser

ROOT = Path(os.getcwd())
LABEL = 'com.worldquant.wq-runner'
RUNNER_TASK = 'WorldQuantWQRunner'   # Windows 任务计划程序任务名
MACOS = sys.platform == 'darwin'
BRAIN_REGISTER_URL = 'https://platform.worldquantbrain.com/sign-up'
from wq import desktop, routing, store
from wq.config import Config
from wq.i18n import default_language, stored_language, text, translate, write_language

# 菜单栏可调的 autopilot 设置：键名 → (取值范围, 显示名)。max_cycles_total 允许 none=不限。
SETTINGS = {
    'interval_s': ((60, 86400), ('运行间隔', 'Run interval')),
    'max_cycles_per_day': ((1, 500), ('每日轮数上限', 'Daily cycle limit')),
    'max_cycles_total': ((1, 100000), ('累计轮数上限', 'Total cycle limit')),
}


UPDATE_FEED = 'https://autowq.octoooo.com/version.json'


def _version_tuple(value):
    parts = []
    for piece in str(value).strip().lstrip('v').split('.'):
        digits = ''.join(ch for ch in piece if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def check_update(lang='zh'):
    """对照官网 version.json。不下载安装包，只报告是否有更新。"""
    import urllib.request
    from wq import __version__
    current = __version__
    # Cloudflare 对 Python-urllib 默认标识返回 403；带上产品名即可。
    request = urllib.request.Request(UPDATE_FEED, headers={
        'User-Agent': f'autowq/{current}', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {'current': current, 'update': False,
                'message': text(lang, '检查更新失败：', 'Update check failed: ') + str(exc)[:160]}
    latest = str(payload.get('version') or '').strip()
    url = str(payload.get('dmg') or payload.get('url') or 'https://autowq.octoooo.com/#install')
    if not latest:
        return {'current': current, 'update': False,
                'message': text(lang, '官网版本信息缺少版本号。', 'The site version feed has no version number.')}
    newer = _version_tuple(latest) > _version_tuple(current)
    if newer:
        return {'current': current, 'latest': latest, 'update': True, 'url': url,
                'message': text(lang, f'有新版本 {latest}（当前 {current}）。', f'Version {latest} is available (you have {current}).')}
    return {'current': current, 'latest': latest, 'update': False,
            'message': text(lang, f'已是最新版本 {current}。', f'You are on the latest version {current}.')}


def ui_language(cfg):
    """生效界面语言：ui.language 显式值优先，'auto'/未配置按环境检测。"""
    pref = stored_language(cfg.path) if cfg.path else None
    return pref if pref in ('zh', 'en') else default_language()


def command(argv, check=True):
    # errors='replace'：schtasks 等系统命令输出本机编码（中文 Windows 为 GBK），
    # 在 UTF-8 模式 Python（PYTHONUTF8=1）下按 UTF-8 解码会崩读线程；这里只关心退出码。
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                            errors='replace', timeout=25)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f'退出码 {result.returncode}')[-2000:])
    return result


def wq(*args):
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包：sys.executable 是托盘 exe 自身，不能当解释器用 `-m wq`；
        # 用 `--engine` 让 exe 重新进入自身分发到内置 CLI（见 scripts/desktop_tray.py）。
        argv = [sys.executable, '--engine', *args]
        env = None
    else:
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT / 'src')
        argv = [sys.executable, '-m', 'wq', *args]
    result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True,
                            errors='replace', timeout=25)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-2000:])
    return json.loads(result.stdout)


def loaded():
    if MACOS:
        return command(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], False).returncode == 0
    return command(['schtasks', '/Query', '/TN', RUNNER_TASK], False).returncode == 0


def _engine_argv(*args):
    """菜单栏/托盘进程再次进入 CLI 的命令行；frozen 用 exe --engine 自代理。"""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--engine', *args]
    return [sys.executable, '-m', 'wq', *args]


def _child_env():
    env = os.environ.copy()
    src = ROOT / 'src'
    if (src / 'wq' / '__init__.py').exists():
        env['PYTHONPATH'] = str(src) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


IDENTITY_REFRESH_FLAG = 'brain_identity_refresh_at'
_IDENTITY_FIELDS = ('nickname', 'displayName', 'name', 'fullName', 'firstName', 'email', 'level', 'geniusLevel', 'stage', 'score')


def _identity_cache(cfg):
    return Path(cfg.private_dir) / 'brain-account' / 'menu-identity.json'


def _read_identity_cache(cfg):
    cache = _identity_cache(cfg)
    if not cache.is_file() or cache.is_symlink():
        return None
    try:
        data = json.loads(cache.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _write_identity_cache(cfg, kept):
    cache = _identity_cache(cfg)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.parent.chmod(0o700)
    tmp = cache.with_name(cache.name + '.tmp')
    tmp.write_text(json.dumps(kept, ensure_ascii=False), encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, cache)


def _format_score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            return None
        if value.is_integer():
            value = int(value)
    return str(value)


def _leaderboard_score(data):
    """Challenge 榜上的 score。没有 Challenge 时用第一条带分数的竞赛。"""
    results = data.get('results') if isinstance(data, dict) else None
    if not isinstance(results, list):
        return None
    fallback = None
    for row in results:
        if not isinstance(row, dict) or not isinstance(row.get('leaderboard'), dict):
            continue
        score = _format_score(row['leaderboard'].get('score'))
        if score is None:
            continue
        if row.get('id') == 'challenge' or row.get('scoring') == 'CHALLENGE':
            return score
        fallback = fallback or score
    return fallback


def _last_local_eight(now):
    """now 必须带当地时区。返回最近一个已经到达的当地 8:00。"""
    eight = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now < eight:
        eight -= dt.timedelta(days=1)
    return eight


def identity_due(cached, now, flag_at=None):
    """缓存够用时不联网。缺缓存、跨过当地 8:00、或提交成功标记新于缓存时才读。"""
    from wq import util
    if not isinstance(cached, dict) or not cached.get('queried_at'):
        return True
    try:
        queried = util.parse_iso(cached['queried_at'])
    except (TypeError, ValueError):
        return True
    if queried < _last_local_eight(now):
        return True
    if flag_at:
        try:
            mark = util.parse_iso(flag_at)
        except (TypeError, ValueError):
            mark = None
        if mark and mark > queried:
            return True
    return False


def _identity_flag(cfg):
    try:
        conn = desktop.connect_readonly(cfg)
    except (OSError, sqlite3.Error):
        return None
    try:
        return store.get_flag(conn, IDENTITY_REFRESH_FLAG)
    except (OSError, TypeError, sqlite3.Error):
        # 只是“是否该刷新身份”的提示标记；库被锁、缺失或已关闭时按没有标记处理，不影响菜单状态。
        return None
    finally:
        conn.close()


def _fetch_identity(cfg, cached):
    from wq import util
    from wq.brain_client import BrainClient
    from wq.brain_jobs import get_with_reauth
    from wq.errors import AdapterError
    client = BrainClient(cfg.private_dir)
    # 会话大约数小时过期。自动登录已开启时，这里和模拟预检一样用 Keychain 重新登录后再读。
    status, _, data = get_with_reauth(client, cfg, '/users/self')
    if status != 200 or not isinstance(data, dict):
        return None
    kept = {'bound': True, 'queried_at': util.now_iso()}
    for key in _IDENTITY_FIELDS:
        value = data.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            kept[key] = value
    score = None
    try:
        cstatus, _, comp = get_with_reauth(client, cfg, '/users/self/competitions')
        if cstatus == 200:
            score = _leaderboard_score(comp)
    except (AdapterError, OSError, ValueError):
        score = None
    if score:
        kept['score'] = score
    elif isinstance(cached, dict) and cached.get('score'):
        kept['score'] = cached['score']
    return kept


def menu_identity(cfg, lang='zh', refresh=False):
    """菜单顶部的账号称呼。身份信息只进私有目录，不进研究账本。

    不按状态轮询去打平台。refresh=True 用于打开应用和手动刷新；
    其余时刻只在跨过当地 8:00，或提交成功标记新于缓存时读取。
    """
    cached = _read_identity_cache(cfg)
    bound = (Path(cfg.private_dir) / 'brain-session.cookies').is_file()
    due = refresh or identity_due(cached, dt.datetime.now().astimezone(), None if refresh else _identity_flag(cfg))
    if not bound:
        return _identity_view(cached if cached and cached.get('bound') else {'bound': False}, lang), False
    if not due:
        return _identity_view(cached or {'bound': True}, lang), False
    from wq.errors import AdapterError
    try:
        kept = _fetch_identity(cfg, cached)
    except (AdapterError, OSError, ValueError):
        kept = None
    if not kept:
        return _identity_view(cached or {'bound': True}, lang), False
    _write_identity_cache(cfg, kept)
    return _identity_view(kept, lang), True


def _identity_view(raw, lang):
    if not raw or not raw.get('bound', True):
        return {'bound': False, 'title': text(lang, '未登录', 'Not signed in'), 'detail': ''}
    name = next((str(raw[key]) for key in ('nickname', 'displayName', 'name', 'fullName', 'firstName') if raw.get(key)), '')
    email = str(raw.get('email') or '')
    level = str(raw.get('level') or raw.get('stage') or '')
    genius = raw.get('geniusLevel')
    score = _format_score(raw.get('score')) if not isinstance(raw.get('score'), str) else (str(raw.get('score')).strip() or None)
    who = name or email or text(lang, '已登录', 'Signed in')
    parts = [who]
    if level:
        parts.append(level)
    if genius not in (None, '') and str(genius) not in level:
        parts.append('Genius ' + str(genius))
    if score:
        parts.append(text(lang, '分数 ', 'Score ') + score)
    detail_bits = []
    if email and email != who:
        detail_bits.append(email)
    if level:
        detail_bits.append(text(lang, '等级 ', 'Level ') + level)
    if genius not in (None, ''):
        detail_bits.append('Genius ' + str(genius))
    if score:
        detail_bits.append(text(lang, '分数 ', 'Score ') + score)
    return {'bound': True, 'title': ' · '.join(parts), 'detail': ' · '.join(detail_bits)}


def brain_bound():
    """本地判定 BRAIN 会话文件是否存在；不代表平台权限，联网核验用 brain-check。"""
    cfg = _cfg()
    private = cfg.resolve(cfg.get('paths', 'private_dir', default='~/.worldquant-pilot'))
    return (Path(private) / 'brain-session.cookies').exists()


def spawn_login_terminal():
    """在独立交互终端窗口运行 `wq brain login`：getpass 需要本人 tty，不能在菜单进程内代输。"""
    argv = _engine_argv('brain', 'login')
    env = _child_env()
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        # 打包 exe 是窗口程序：--console 让子进程自己开控制台，getpass 才有本人的终端。
        subprocess.Popen([sys.executable, '--console', '--engine', 'brain', 'login'], cwd=ROOT, env=env)
        return True
    if sys.platform == 'win32':
        # cmd /k 保持窗口：登录完成后仍可查看结果；GUI 父进程下显式新开控制台。
        subprocess.Popen(['cmd', '/k', subprocess.list2cmdline(argv)], cwd=ROOT, env=env,
                         creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        return True
    if sys.platform == 'darwin':
        import tempfile
        import uuid
        q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"
        script = (f"cd {q(ROOT)} && " + ' '.join(q(a) for a in argv) + "; echo; read -n1\n")
        path = Path(tempfile.gettempdir()) / f'wq-brain-login-{uuid.uuid4().hex}.command'
        path.write_text(script, encoding='utf-8')
        path.chmod(0o755)
        subprocess.Popen(['open', '-a', 'Terminal', str(path)])
        return True
    for terminal in (['x-terminal-emulator', '-e'], ['gnome-terminal', '--'],
                     ['konsole', '-e'], ['xterm', '-e']):
        try:
            subprocess.Popen(terminal + [subprocess.list2cmdline(argv)], cwd=ROOT, env=env)
            return True
        except OSError:
            continue
    return False


def ensure_runner():
    """start 前置：macOS 加载 LaunchAgent；Windows 打包 exe 直接注册任务计划，源码方式仍需 setup_windows.py。"""
    if MACOS:
        plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
        command(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)])
        return
    if getattr(sys, 'frozen', False):
        from wq.windows_setup import register_runner
        register_runner(ROOT)
        return
    raise RuntimeError('Windows 调度任务未注册；请先运行 python scripts/setup_windows.py / '
                       'Windows scheduler task not registered; run python scripts/setup_windows.py first')


def kick_runner():
    """尽量立刻唤醒一次 runner。服务已在运行时不算失败：请求已写入账本，当前任务结束后会被看到。"""
    if MACOS:
        result = command(['/bin/launchctl', 'kickstart', f'gui/{os.getuid()}/{LABEL}'], check=False)
    else:
        result = command(['schtasks', '/Run', '/TN', RUNNER_TASK], check=False)
    if result.returncode == 0:
        return
    err = (result.stderr or result.stdout or '')
    if any(word in err.lower() for word in ('running', 'already', 'in progress')):
        return
    raise RuntimeError((err or f'退出码 {result.returncode}')[-500:])


def runner_log():
    return ROOT / ('var/run/launchd.out.log' if MACOS else 'var/run/runner.out.log')


def _cfg():
    return Config.load(None, str(ROOT))


def write_config(path, key, value, section='autopilot'):
    """原子改写 config.json 的单个设置；runner 下次读取时生效。"""
    _write_config_values(path, section, {key: value})


def _write_config_values(path, section, values):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    bucket = data.setdefault(section, {})
    bucket.update(values)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


SPEND_PRESETS = (5, 10, 20, 50, 100)


def launch_research_enabled(cfg):
    """未配置时默认开启：打开菜单栏/托盘即开始自动研究。"""
    value = cfg.get('desktop', 'launch_research', default=None)
    if value is None:
        return True
    return value is True


def pause_detail(reason, lang):
    """暂停原因按界面语言显示。历史账本里的中文原因和新的稳定代码都认。"""
    known = {
        '菜单栏退出': ('从菜单栏退出后暂停', 'Paused because the menu bar app quit'),
        '菜单栏手动暂停': ('已从菜单栏停止', 'Stopped from the menu bar'),
        'menu:quit': ('从菜单栏退出后暂停', 'Paused because the menu bar app quit'),
        'menu:pause': ('已从菜单栏停止', 'Stopped from the menu bar'),
        'menu:stop-now': ('已立刻停止所有任务', 'All tasks stopped immediately'),
        'menu:stop-after-cycle': ('当前轮次结束后已停止', 'Stopped after the current cycle'),
    }
    if not reason:
        return text(lang, '已暂停', 'Paused')
    pair = known.get(reason)
    if pair:
        return text(lang, pair[0], pair[1])
    return translate(reason, lang)


def settings_snapshot(cfg, conn, lang='zh'):
    data = routing.catalog(cfg)
    labels = {name: desktop.provider_label(name, definition, lang) for name, definition in data['providers'].items()}
    presets = []
    role_labels = (('research', '研究', 'Research'), ('engineering', '工程', 'Engineering'), ('review', '审查', 'Review'))
    def route_head(routes, role):
        chain = routes.get(role) or []
        if not chain:
            return text(lang, '未设置', 'not set')
        return labels.get(chain[0], chain[0])
    for name, preset in data['presets'].items():
        routes = preset.get('routes', {})
        presets.append({'name': name,
                        'research': route_head(routes, 'research'),
                        'review': route_head(routes, 'review'),
                        'routes': '；'.join(
            f"{text(lang, zh, en)}：{' → '.join(routes.get(role, []))}" for role, zh, en in role_labels)})
    providers = [{'name': name, 'label': labels[name],
                  'disabled': store.get_flag(conn, f'provider_disabled:{name}') == '1',
                  'quota_paused_until': routing.quota_paused_until(conn, name) or '',
                  'reason': routing._unavailable(conn, cfg, name) or ''}
                 for name, definition in data['providers'].items()]
    active = routing.active_preset(conn, cfg, data)
    routes = (data['presets'].get(active) or {}).get('routes') or {}
    return {'active_preset': active,
            'research_provider': (routes.get('research') or [''])[0],
            'review_provider': (routes.get('review') or [''])[0],
            'permanent_preset': store.get_flag(conn, 'active_preset', data['default']),
            'preset_once': store.get_flag(conn, 'preset_once') or '',
            'preset_once_cycles': (int(v) if (v := store.get_flag(conn, 'preset_once_cycles') or '').isdigit() else 1),
            'cycle_preset': routing.cycle_preset(conn) or '',
            'presets': presets, 'providers': providers,
            'notifications': desktop.notifications_enabled(cfg),
            'language': lang,
            'language_setting': (stored_language(cfg.path) if cfg.path else None) or 'auto',
            'interval_s': cfg.get('autopilot', 'interval_s', default=3600),
            'max_cycles_per_day': cfg.get('autopilot', 'max_cycles_per_day', default=4),
            'max_cycles_total': cfg.get('autopilot', 'max_cycles_total'),
            'submission_enabled': cfg.get('brain_submission', 'enabled') is True,
            'launch_research': launch_research_enabled(cfg),
            'automatic_submission': False,
            **_spend_snapshot(cfg, conn)}


def _spend_snapshot(cfg, conn):
    from wq import usage
    try:
        cap = usage.spend_cap(cfg)
    except ValueError:
        cap = None
    since = cfg.get('limits', 'model_spend_as_of', default='') or ''
    spend = usage.known_spend(conn, since)
    return {'spend_cap_usd': cap, 'spend_known_usd': spend['known_usd'],
            'spend_unknown_calls': spend['unknown_calls'],
            'spend_presets': list(SPEND_PRESETS)}


# 这些暂停是用户明确要求停住的。启动时自动研究不会把它们翻回来。
EXPLICIT_STOPS = {'menu:pause', 'menu:stop-now', 'menu:stop-after-cycle', '菜单栏手动暂停'}


def _begin_research(lang, automatic=False):
    state = wq('status')
    if state['unknown_pending']:
        raise RuntimeError(text(lang, '存在 UNKNOWN 待对账，请先运行 ./wq reconcile；没有强制恢复。',
                                'UNKNOWN items pending reconciliation; run ./wq reconcile first. No forced resume.'))
    reason = state.get('pause_reason') or ''
    if automatic and state.get('paused') and (reason in EXPLICIT_STOPS or str(reason).startswith('auth:')):
        return {'started': False, 'message': text(lang, '保持停止，直到手动开始。',
                                                  'Staying stopped until you start it.')}
    if automatic:
        latest = None
        if not state.get('live_agent_calls'):
            auto = wq('autopilot', 'status', '--json')
            latest = (auto.get('latest_cycle') or {}).get('state')
        if state.get('live_agent_calls') or latest not in (None, '', 'closed'):
            return {'started': False, 'message': text(lang, '后台任务继续，只打开了菜单。',
                                                      'Background work continues; only the menu was opened.')}
    if not loaded():
        ensure_runner()
    wq('autopilot', 'start', '--json')
    wq('resume')
    return {'started': True, 'message': text(lang, '已开始自动运行，调度器按既有间隔推进',
                                             'Automatic research started; the scheduler follows the configured interval')}


def control(action, arg=None):
    lang = ui_language(_cfg())
    if action == 'pause':
        wq('pause', '--graceful', '--reason', 'menu:pause')
        return {'message': text(lang, '已停止自动研究；进行中的这一轮可以收尾',
                                'Automatic research stopped; the current cycle may finish')}
    if action == 'quit-app':
        return {'message': text(lang, '只关闭菜单栏，研究继续。', 'Menu bar closed; research continues.')}
    if action == 'quit-after-cycle':
        from wq.db import connect
        from wq import autopilot
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            result = autopilot.stop_after_cycle(conn)
        finally:
            conn.close()
        if result.get('deferred'):
            return {'message': text(lang, '当前轮次结束后停止。菜单栏现在退出，研究调度会把这一轮走完。',
                                    'Research stops when this cycle ends. The menu closes now; the scheduler finishes this cycle.')}
        return {'message': text(lang, '没有进行中的轮次，已停止自动研究。',
                                'No cycle is running; automatic research is stopped.')}
    if action == 'quit-now':
        from wq.db import connect
        from wq import autopilot
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            result = autopilot.stop_now(conn, cfg)
        finally:
            conn.close()
        if result.get('remote_left'):
            return {'message': text(lang, '已停止。已经发到平台的模拟会把结果记完，不会撤回，也不会再开新轮。',
                                    'Stopped. A simulation already sent to the platform will record its result and is not withdrawn. No new cycle will start.')}
        return {'message': text(lang, '已立刻结束当前任务。', 'Current tasks stopped.')}
    if action == 'launch':
        if not launch_research_enabled(_cfg()):
            return {'started': False, 'message': text(lang, '启动时自动研究已关闭',
                                                      'Automatic research on launch is off')}
        return _begin_research(lang, automatic=True)
    if action == 'cancel-cycle':
        from wq.db import connect
        from wq import autopilot
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            result = autopilot.cancel_open_cycle(conn, cfg)
        finally:
            conn.close()
        if result.get('cancelled'):
            return {'cancelled': True, 'message': text(
                lang, '已取消当前轮次。还没发出的本地任务已停止；自动研究保持开启，下一轮仍按间隔开始。',
                'Current cycle cancelled. Local work that had not been sent is stopped; automatic research stays on and the next cycle follows the interval.')}
        if result.get('reason') == 'remote':
            return {'cancelled': False, 'message': text(
                lang, '这一轮的平台模拟已经发出，不能撤回。可以停止自动研究，等它收尾。',
                'This cycle’s platform simulation has already been sent and cannot be withdrawn. Stop automatic research if you want to wait it out.')}
        return {'cancelled': False, 'message': text(lang, '当前没有进行中的轮次。', 'No cycle is running.')}
    if action in ('start', 'run-next'):
        if action == 'run-next':
            if wq('status')['unknown_pending']:
                raise RuntimeError(text(lang, '存在 UNKNOWN 待对账，请先运行 ./wq reconcile；没有强制恢复。',
                                        'UNKNOWN items pending reconciliation; run ./wq reconcile first. No forced resume.'))
            if not loaded():
                ensure_runner()
            result = wq('autopilot', 'run-next', '--json')
            try:
                kick_runner()
            except RuntimeError as exc:
                note = text(lang, '请求已记下，但调度器没能马上启动：', 'The request is saved, but the scheduler did not start immediately: ')
                return {'message': translate(result.get('message') or '', lang) + ' ' + note + str(exc)[:160]}
            return {'message': translate(result.get('message') or '', lang)}
        return _begin_research(lang)
    if action == 'settings':
        cfg = _cfg()
        conn = desktop.connect_readonly(cfg)
        try:
            return settings_snapshot(cfg, conn, lang)
        finally:
            conn.close()
    if action in ('preset', 'preset-once', 'preset-cancel'):
        from wq.db import connect
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            if action == 'preset-cancel':
                pending = store.get_flag(conn, 'preset_once') or ''
                left = store.get_flag(conn, 'preset_once_cycles') or '1'
                routing.cancel_once(conn)
                permanent = store.get_flag(conn, 'active_preset') or routing.catalog(cfg)['default']
            else:
                name, cycles = arg, 1
                if action == 'preset-once' and ':' in arg:
                    head, _, tail = arg.rpartition(':')
                    if tail.isdigit():
                        name, cycles = head, int(tail)
                routing.choose_preset(conn, cfg, name, once=(action == 'preset-once'), cycles=cycles)
                permanent = store.get_flag(conn, 'active_preset') or routing.catalog(cfg)['default']
        finally:
            conn.close()
        if action == 'preset-cancel':
            if not pending:
                return {'message': text(lang, '当前没有待生效的临时预设。', 'No temporary preset is pending.')}
            return {'message': text(lang, f'已取消临时预设 {pending}（原定再用于 {left} 个新轮次）；之后的新轮次使用永久预设 {permanent}，本轮已绑定的预设不变。',
                                    f'Temporary preset {pending} cancelled (was set for {left} more new cycles); new cycles use the permanent preset {permanent}. The current cycle keeps its preset.')}
        if action == 'preset-once':
            if cycles <= 1:
                return {'message': text(lang, f'已登记仅一轮预设 {arg}：下一个新建轮次使用，结束后自动恢复永久预设。',
                                        f'One-cycle preset {arg} registered: used by the next new cycle, then the permanent preset resumes automatically.')}
            return {'message': text(lang, f'已登记临时预设 {name}：接下来 {cycles} 个新建轮次使用，用完后自动恢复永久预设 {permanent}。',
                                    f'Temporary preset {name} registered: used by the next {cycles} new cycles, then the permanent preset resumes automatically.')}
        return {'message': text(lang, f'已切换到预设 {arg}；下一项任务领取时生效，在途任务保持原路由。',
                                f'Switched to preset {arg}; applies to the next claimed task. In-flight tasks keep their routes.')}
    if action == 'provider-add':
        spec = json.loads(arg or '')
        if not isinstance(spec, dict):
            raise ValueError(text(lang, '自定义模型参数无效', 'Invalid custom-model parameters'))
        from wq import providers
        key = os.environ.get('WQ_PROVIDER_KEY', '')
        added = providers.install_custom(_cfg(), spec, key)
        return {'message': text(lang, f"已保存模型 {added['name']}。到「设置 → 模型」里分别选择研究和审查；这两个位置必须是不同的模型。金额未知的调用不记成 $0。",
                                f"Saved model {added['name']}. Choose research and review separately under Settings → Models; those two slots must be different models. Calls with an unknown price are not treated as $0.")}
    if action == 'provider-role':
        role, _, name = (arg or '').partition(':')
        from wq.db import connect
        from wq import providers
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            assigned = providers.assign_role(cfg, conn, role, name)
        finally:
            conn.close()
        role_name = text(lang, {'research': '研究', 'review': '审查', 'engineering': '工程'}.get(assigned['role'], assigned['role']),
                         assigned['role'])
        return {'message': text(lang, f"下一轮{role_name}优先使用 {assigned['provider']}。当前这一轮保持原路由。",
                                f"The next {role_name} cycle prefers {assigned['provider']}. The current cycle keeps its route.")}
    if action == 'provider':
        from wq.db import connect
        if not arg:
            raise ValueError(text(lang, '缺少渠道名', 'Missing provider name'))
        cfg = _cfg()
        if arg not in routing.catalog(cfg)['providers']:
            raise ValueError(text(lang, f'未知渠道：{arg}', f'Unknown provider: {arg}'))
        conn = connect(cfg.db_path)
        try:
            was_disabled = store.get_flag(conn, f'provider_disabled:{arg}') == '1'
            store.set_flag(conn, f'provider_disabled:{arg}', '0' if was_disabled else '1')
        finally:
            conn.close()
        if was_disabled:
            return {'message': text(lang, f'{arg} 已恢复；后续任务恢复路由到该渠道。',
                                    f'{arg} re-enabled; new tasks may route to it again.')}
        return {'message': text(lang, f'{arg} 已停用；不打断在途调用，后续任务不再路由到该渠道，预算闸门保留。',
                                    f'{arg} disabled; in-flight calls are unaffected, new tasks no longer route to it, budget gates remain.')}
    if action == 'notifications':
        # 无论开关状态都消费水位线：关闭期间的事件不回放，重新开启后不会弹历史积压。
        from wq.db import connect
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            items = desktop.pending_notifications(conn, lang)
        finally:
            conn.close()
        return {'items': items if desktop.notifications_enabled(cfg) else []}
    if action == 'config':
        key, _, raw = (arg or '').partition('=')
        cfg = _cfg()
        if key == 'language':
            if raw not in ('zh', 'en', 'auto'):
                raise ValueError(text(lang, '语言设置只接受 zh/en/auto', 'Language accepts zh/en/auto only'))
            value = raw
            if not cfg.path or not write_language(cfg.path, value):
                raise RuntimeError(text(lang, '未找到 config/config.json，无法保存语言设置', 'config/config.json not found; cannot save the language setting'))
            return {'message': text(lang, {'zh': '界面语言已设为中文。', 'en': 'Interface language set to English.',
                                            'auto': '界面语言已设为跟随系统（中文环境中文，其余英文）。'}[value],
                                    {'zh': 'Interface language set to Chinese.',
                                     'en': 'Interface language set to English.',
                                     'auto': 'Language follows the system locale (Chinese for zh*, English otherwise).'}[value]),
                    'language': value if value in ('zh', 'en') else default_language(),
                    'language_setting': value}
        if key == 'launch_research':
            if raw not in ('on', 'off'):
                raise ValueError(text(lang, '启动时自动研究只接受 on/off', 'Start-on-launch accepts on/off only'))
            if not cfg.path:
                raise RuntimeError(text(lang, '未找到 config/config.json，无法保存设置', 'config/config.json not found; cannot save settings'))
            write_config(cfg.path, 'launch_research', raw == 'on', section='desktop')
            if raw == 'on':
                return {'message': text(lang, '已打开：下次启动菜单栏应用时开始自动研究。',
                                        'On: the next time this app launches, automatic research starts.')}
            return {'message': text(lang, '已关闭：下次启动会保持退出时的暂停，直到手动开始。',
                                    'Off: the next launch stays paused until you start it.')}
        if key == 'notifications':
            if raw not in ('on', 'off'):
                raise ValueError(text(lang, '通知开关只接受 on/off', 'The notifications switch accepts on/off only'))
            if not cfg.path:
                raise RuntimeError(text(lang, '未找到 config/config.json，无法保存设置', 'config/config.json not found; cannot save settings'))
            write_config(cfg.path, 'notifications', raw == 'on', section='desktop')
            if raw == 'on':
                return {'message': text(lang, '系统通知已开启；Alpha 提交成功或任务失败时提醒（需系统允许本应用通知）。',
                                        'System notifications on: alerts when an Alpha is accepted or a task fails (requires system permission for this app).')}
            return {'message': text(lang, '系统通知已关闭；事件仍记录在账本，不再弹提醒。',
                                    'System notifications off; events are still recorded in the ledger without pop-ups.')}
        if key == 'submission':
            if raw not in ('on', 'off'):
                raise ValueError(text(lang, '提交队列开关只接受 on/off', 'The submission-queue switch accepts on/off only'))
            if not cfg.path:
                raise RuntimeError(text(lang, '未找到 config/config.json，无法保存设置', 'config/config.json not found; cannot save settings'))
            write_config(cfg.path, 'enabled', raw == 'on', section='brain_submission')
            if raw == 'on':
                return {'message': text(lang, '提交队列已打开。达标的 Alpha 不会自动提交；仍须逐个准备验收后执行 wq brain submit。授权期限与 24 小时次数上限保持原值。',
                                        'Submission queue on. A passing Alpha is not submitted automatically; each one still needs its own review via wq brain submit. The authorization window and 24-hour cap are unchanged.')}
            return {'message': text(lang, '提交队列已关闭。不会入队，也不会 POST。已在队列中的任务仍按原状态收尾。',
                                    'Submission queue off. Nothing new is queued or posted. Tasks already in the queue keep their current state.')}
        if key == 'model_spend_cap_usd':
            from wq import util
            if not cfg.path:
                raise RuntimeError(text(lang, '未找到 config/config.json，无法保存设置', 'config/config.json not found; cannot save settings'))
            if raw in ('none', 'off', ''):
                _write_config_values(cfg.path, 'limits', {'model_spend_cap_usd': None})
                return {'message': text(lang, '已取消模型花费上限。之后的模型调用不再按美元合计拦截。',
                                        'Model spend cap removed. Later model calls are no longer blocked by a dollar total.')}
            try:
                cap = float(raw)
            except ValueError:
                cap = float('nan')
            if cap != cap or cap < 0 or cap > 100000:
                raise ValueError(text(lang, '花费上限需为 0–100000 的美元数，或 none',
                                      'The spend cap must be a dollar amount from 0 to 100000, or none'))
            _write_config_values(cfg.path, 'limits', {'model_spend_cap_usd': cap, 'model_spend_as_of': util.now_iso()})
            return {'message': text(lang, f'模型花费上限已设为 ${cap:g}。从现在起计算已知金额，达到后停止新的模型调用。金额未知的调用不记成 $0，也不计入这个上限。',
                                    f'Model spend cap set to ${cap:g}. Known dollars count from now; new model calls stop when the cap is reached. Calls with an unknown price are not treated as $0 and are not added to this total.')}
        if key not in SETTINGS:
            raise ValueError(text(lang, '未知设置项', 'Unknown setting'))
        (low, high), (label_zh, label_en) = SETTINGS[key]
        label = text(lang, label_zh, label_en)
        value = None if raw == 'none' else int(raw)
        if value is None and key != 'max_cycles_total':
            raise ValueError(text(lang, f'{label} 不支持取消上限', f'{label} cannot be unlimited'))
        if value is not None and not low <= value <= high:
            raise ValueError(text(lang, f'{label} 需在 {low}–{high} 之间', f'{label} must be between {low} and {high}'))
        if not cfg.path:
            raise RuntimeError(text(lang, '未找到 config/config.json，无法保存设置', 'config/config.json not found; cannot save settings'))
        write_config(cfg.path, key, value)
        if key == 'interval_s':
            shown = text(lang, f'{value // 60} 分钟', f'{value // 60} minutes') if value >= 60 else text(lang, f'{value} 秒', f'{value} seconds')
            return {'message': text(lang, f'运行间隔已设为 {shown}；下一次调度起采用。', f'Run interval set to {shown}; applies from the next scheduling tick.')}
        if key == 'max_cycles_per_day':
            return {'message': text(lang, f'每日轮数上限已设为 {value}；按 UTC 日计。', f'Daily cycle limit set to {value} (UTC days).')}
        return {'message': text(lang, '已取消累计轮数上限。', 'Total cycle limit removed.') if value is None
                else text(lang, f'累计轮数上限已设为 {value}。', f'Total cycle limit set to {value}.')}
    if action in ('history', 'submissions', 'standby', 'research'):
        cfg = _cfg()
        conn = desktop.connect_readonly(cfg)
        try:
            if action == 'research':
                return desktop.research(conn, cfg, lang)
            if action == 'history':
                return desktop.history(conn, cfg, lang)
            if action == 'standby':
                return desktop.standby(conn, lang)
            return desktop.submitted(conn, lang)
        finally:
            conn.close()
    if action == 'update':
        return check_update(lang)
    if action == 'brain-login':
        if not spawn_login_terminal():
            raise RuntimeError(text(lang, '未找到可用终端；请在终端手动运行 wq brain login',
                                    'No usable terminal found; run wq brain login manually'))
        return {'message': text(lang, '已在终端打开 BRAIN 登录：按提示输入邮箱与密码（密码不保存）。完成后回到菜单用「核验会话」确认。',
                                    'BRAIN login opened in a terminal: enter your email and password when prompted (the password is not saved). Confirm afterwards with "Verify session".')}
    if action == 'brain-check':
        try:
            result = wq('brain', 'check')
            cache = Path(_cfg().private_dir) / 'brain-account' / 'menu-identity.json'
            if cache.is_file() and not cache.is_symlink():
                cache.unlink()
            return {'message': text(lang, f"BRAIN 会话核验通过（HTTP {result.get('http_status')}）；模拟选项已缓存到本地私有目录。",
                                    f"BRAIN session verified (HTTP {result.get('http_status')}); simulation options cached in the local private directory.")}
        except RuntimeError as exc:
            return {'message': text(lang, 'BRAIN 会话核验未通过：', 'BRAIN session verification failed: ') + str(exc)[:160] +
                               text(lang, '；可用「绑定 / 重新登录」后重试。', '; retry after "Bind / re-login".')}
    if action == 'identity-refresh':
        identity, refreshed = menu_identity(_cfg(), lang, refresh=True)
        out = {'identity': identity, 'refreshed': refreshed}
        if arg == 'manual' and not refreshed:
            out['message'] = text(lang, '账号信息没有刷新。BRAIN 会话可能已过期，自动重新登录也没完成；请用「绑定 / 重新登录」。仍显示上次记录。',
                                  'Account info was not refreshed. The BRAIN session may have expired and automatic sign-in did not finish; use “Bind / re-login”. Showing the last record.')
        return out
    if action == 'brain-register':
        webbrowser.open(BRAIN_REGISTER_URL)
        return {'message': text(lang, '已在浏览器打开 WorldQuant BRAIN 注册页。', 'WorldQuant BRAIN registration page opened in the browser.')}
    if action != 'status':
        raise ValueError(text(lang, '未知操作', 'Unknown action'))
    state = wq('status')
    auto = wq('autopilot', 'status', '--json')
    scheduler = loaded()
    latest = auto.get('latest_cycle') or {}
    cycle_open = latest.get('state') not in (None, '', 'closed')
    if state['paused']:
        title = text(lang, '已暂停（当前任务可收尾）', 'Paused (running tasks may finish)')
    elif state['unknown_pending']:
        title = text(lang, '待对账', 'Reconciliation pending')
    elif not scheduler:
        title = text(lang, '调度器未加载', 'Scheduler not loaded')
    elif auto.get('run_next_requested') and not auto['enabled']:
        title = text(lang, '单轮运行已请求（自动运行未开启）', 'One-off run requested (auto-run off)')
    elif not auto['enabled']:
        title = text(lang, '自动研究未启用', 'Automatic research off')
    else:
        title = text(lang, '自动研究已启用', 'Automatic research on')
    cfg = _cfg()
    conn = desktop.connect_readonly(cfg)
    try:
        routes = desktop.next_models(conn, cfg, lang)
    finally:
        conn.close()
    unlimited = text(lang, '不限', 'unlimited')
    detail = pause_detail(state.get('pause_reason') or '', lang) if state['paused'] else translate(auto.get('message') or '', lang)
    try:
        identity, _refreshed = menu_identity(cfg, lang)
    except (OSError, ValueError, TypeError):
        identity = {'bound': brain_bound(), 'title': text(lang, 'WorldQuant 账号', 'WorldQuant account'), 'detail': ''}
    return {'title': title, 'paused': state['paused'], 'enabled': bool(auto.get('enabled')),
            'identity': identity,
            'cycle_open': cycle_open, 'cycle_state': latest.get('state') or '',
            'scheduler': scheduler,
            'brain_bound': brain_bound(),
            'message': desktop.localize_message(detail, lang),
            'next_models': routes,
            'next_at': desktop.beijing(auto.get('next_cycle_at'), text(lang, '待当前任务完成／调度检查', 'awaiting current task / scheduler check'), lang=lang),
            'cycles': text(lang, f"累计 {auto['total_cycles']} / {auto.get('max_cycles_total') or unlimited} 轮",
                                  f"{auto['total_cycles']} / {auto.get('max_cycles_total') or unlimited} cycles total"),
            'last_tick': desktop.beijing(auto.get('last_tick_at'), lang=lang), 'unknown': state['unknown_pending'],
            'language': lang}


if __name__ == '__main__':
    try:
        print(json.dumps(control(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), ensure_ascii=False))
    except Exception as exc:
        # 子进程错误同样按界面语言在显示层翻译（App 弹窗直接展示该文本）。
        try:
            lang = ui_language(_cfg())
        except Exception:
            lang = 'zh'
        print(json.dumps({'error': translate(str(exc), lang)}, ensure_ascii=False))
        sys.exit(1)
