#!/usr/bin/env python3
"""Bounded menu-bar bridge; delegates all research permissions to the existing CLI.

语言：读 config.json 的 ui.language（zh/en/auto），auto 与未配置按环境检测；
所有返回给菜单栏/托盘的文案按该语言渲染，菜单可切换并写回。
"""
import json
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


def brain_bound():
    """本地判定 BRAIN 会话文件是否存在；不代表平台权限，联网核验用 brain-check。"""
    cfg = _cfg()
    private = cfg.resolve(cfg.get('paths', 'private_dir', default='~/.worldquant-pilot'))
    return (Path(private) / 'brain-session.cookies').exists()


def spawn_login_terminal():
    """在独立交互终端窗口运行 `wq brain login`：getpass 需要本人 tty，不能在菜单进程内代输。"""
    argv = _engine_argv('brain', 'login')
    env = _child_env()
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
    """start 前置：macOS 加载 LaunchAgent；Windows 只认 setup_windows.py 注册的任务。"""
    if MACOS:
        plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
        command(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)])
        return
    raise RuntimeError('Windows 调度任务未注册；请先运行 python scripts/setup_windows.py / '
                       'Windows scheduler task not registered; run python scripts/setup_windows.py first')


def kick_runner():
    """立即触发一次 runner（run-next 请求后让调度器马上领取）。"""
    if MACOS:
        command(['/bin/launchctl', 'kickstart', f'gui/{os.getuid()}/{LABEL}'])
    else:
        command(['schtasks', '/Run', '/TN', RUNNER_TASK])


def runner_log():
    return ROOT / ('var/run/launchd.out.log' if MACOS else 'var/run/runner.out.log')


def _cfg():
    return Config.load(None, str(ROOT))


def write_config(path, key, value, section='autopilot'):
    """原子改写 config.json 的单个设置；runner 下次读取时生效。"""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data.setdefault(section, {})[key] = value
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def settings_snapshot(cfg, conn, lang='zh'):
    data = routing.catalog(cfg)
    presets = []
    role_labels = (('research', '研究', 'Research'), ('engineering', '工程', 'Engineering'), ('review', '审查', 'Review'))
    for name, preset in data['presets'].items():
        routes = preset.get('routes', {})
        presets.append({'name': name, 'routes': '；'.join(
            f"{text(lang, zh, en)}：{' → '.join(routes.get(role, []))}" for role, zh, en in role_labels)})
    providers = [{'name': name, 'label': desktop.provider_label(name, definition, lang),
                  'disabled': store.get_flag(conn, f'provider_disabled:{name}') == '1',
                  'reason': routing._unavailable(conn, cfg, name) or ''}
                 for name, definition in data['providers'].items()]
    return {'active_preset': routing.active_preset(conn, cfg, data),
            'permanent_preset': store.get_flag(conn, 'active_preset', data['default']),
            'preset_once': store.get_flag(conn, 'preset_once') or '',
            'cycle_preset': routing.cycle_preset(conn) or '',
            'presets': presets, 'providers': providers,
            'notifications': desktop.notifications_enabled(cfg),
            'language': lang,
            'language_setting': (stored_language(cfg.path) if cfg.path else None) or 'auto',
            'interval_s': cfg.get('autopilot', 'interval_s', default=3600),
            'max_cycles_per_day': cfg.get('autopilot', 'max_cycles_per_day', default=4),
            'max_cycles_total': cfg.get('autopilot', 'max_cycles_total')}


def control(action, arg=None):
    lang = ui_language(_cfg())
    if action in ('pause', 'quit'):
        wq('pause', '--graceful', '--reason', '菜单栏手动暂停' if action == 'pause' else '菜单栏退出')
        return {'message': text(lang, '已暂停；已领取任务允许收尾', 'Paused; claimed tasks may finish')}
    if action in ('start', 'run-next'):
        if wq('status')['unknown_pending']:
            raise RuntimeError(text(lang, '存在 UNKNOWN 待对账，请先运行 ./wq reconcile；没有强制恢复。',
                                    'UNKNOWN items pending reconciliation; run ./wq reconcile first. No forced resume.'))
        if not loaded():
            ensure_runner()
        if action == 'run-next':
            wq('autopilot', 'run-next', '--json')
            kick_runner()
            return {'message': text(lang, '已请求运行下一轮；仍受队列、授权与额度限制，不改变自动运行开关',
                                    'Next cycle requested; still limited by queue, authorization and quota. The auto-run switch is unchanged.')}
        wq('autopilot', 'start', '--json')
        wq('resume')
        return {'message': text(lang, '已开始自动运行，调度器按既有间隔推进', 'Automatic research started; the scheduler follows the configured interval')}
    if action == 'settings':
        cfg = _cfg()
        conn = desktop.connect_readonly(cfg)
        try:
            return settings_snapshot(cfg, conn, lang)
        finally:
            conn.close()
    if action in ('preset', 'preset-once'):
        from wq.db import connect
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            routing.choose_preset(conn, cfg, arg, once=(action == 'preset-once'))
        finally:
            conn.close()
        if action == 'preset-once':
            return {'message': text(lang, f'已登记仅一轮预设 {arg}：下一个新建轮次使用，结束后自动恢复永久预设。',
                                    f'One-cycle preset {arg} registered: used by the next new cycle, then the permanent preset resumes automatically.')}
        return {'message': text(lang, f'已切换到预设 {arg}；下一项任务领取时生效，在途任务保持原路由。',
                                    f'Switched to preset {arg}; applies to the next claimed task. In-flight tasks keep their routes.')}
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
    if action in ('history', 'submissions'):
        cfg = _cfg()
        conn = desktop.connect_readonly(cfg)
        try:
            return desktop.history(conn, cfg, lang) if action == 'history' else desktop.submitted(conn, lang)
        finally:
            conn.close()
    if action == 'brain-login':
        if not spawn_login_terminal():
            raise RuntimeError(text(lang, '未找到可用终端；请在终端手动运行 wq brain login',
                                    'No usable terminal found; run wq brain login manually'))
        return {'message': text(lang, '已在终端打开 BRAIN 登录：按提示输入邮箱与密码（密码不保存）。完成后回到菜单用「核验会话」确认。',
                                    'BRAIN login opened in a terminal: enter your email and password when prompted (the password is not saved). Confirm afterwards with "Verify session".')}
    if action == 'brain-check':
        try:
            result = wq('brain', 'check')
            return {'message': text(lang, f"BRAIN 会话核验通过（HTTP {result.get('http_status')}）；模拟选项已缓存到本地私有目录。",
                                    f"BRAIN session verified (HTTP {result.get('http_status')}); simulation options cached in the local private directory.")}
        except RuntimeError as exc:
            return {'message': text(lang, 'BRAIN 会话核验未通过：', 'BRAIN session verification failed: ') + str(exc)[:160] +
                               text(lang, '；可用「绑定 / 重新登录」后重试。', '; retry after "Bind / re-login".')}
    if action == 'brain-register':
        webbrowser.open(BRAIN_REGISTER_URL)
        return {'message': text(lang, '已在浏览器打开 WorldQuant BRAIN 注册页。', 'WorldQuant BRAIN registration page opened in the browser.')}
    if action != 'status':
        raise ValueError(text(lang, '未知操作', 'Unknown action'))
    state = wq('status')
    auto = wq('autopilot', 'status', '--json')
    scheduler = loaded()
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
    return {'title': title, 'paused': state['paused'], 'scheduler': scheduler,
            'brain_bound': brain_bound(),
            'message': desktop.localize_message(translate(state.get('pause_reason') or auto['message'], lang), lang),
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
