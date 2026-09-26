#!/usr/bin/env python3
"""Bounded menu-bar bridge; delegates all research permissions to the existing CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.getcwd())
LABEL = 'com.worldquant.wq-runner'
RUNNER_TASK = 'WorldQuantWQRunner'   # Windows 任务计划程序任务名
MACOS = sys.platform == 'darwin'
from wq import desktop, routing, store
from wq.config import Config

# 菜单栏可调的 autopilot 设置：键名 → (取值范围, 显示名)。max_cycles_total 允许 none=不限。
SETTINGS = {
    'interval_s': ((60, 86400), '运行间隔'),
    'max_cycles_per_day': ((1, 500), '每日轮数上限'),
    'max_cycles_total': ((1, 100000), '累计轮数上限'),
}


def command(argv, check=True):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=25)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f'退出码 {result.returncode}')[-2000:])
    return result


def wq(*args):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src')
    result = subprocess.run([sys.executable, '-m', 'wq', *args], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-2000:])
    return json.loads(result.stdout)


def loaded():
    if MACOS:
        return command(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], False).returncode == 0
    return command(['schtasks', '/Query', '/TN', RUNNER_TASK], False).returncode == 0


def ensure_runner():
    """start 前置：macOS 加载 LaunchAgent；Windows 只认 setup_windows.py 注册的任务。"""
    if MACOS:
        plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
        command(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)])
        return
    raise RuntimeError('Windows 调度任务未注册；请先运行 python scripts/setup_windows.py')


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


def settings_snapshot(cfg, conn):
    data = routing.catalog(cfg)
    presets = []
    for name, preset in data['presets'].items():
        routes = preset.get('routes', {})
        presets.append({'name': name, 'routes': '；'.join(
            f"{label}：{' → '.join(routes.get(role, []))}" for role, label in
            (('research', '研究'), ('engineering', '工程'), ('review', '审查')))})
    providers = [{'name': name, 'label': desktop.provider_label(name, definition),
                  'disabled': store.get_flag(conn, f'provider_disabled:{name}') == '1',
                  'reason': routing._unavailable(conn, cfg, name) or ''}
                 for name, definition in data['providers'].items()]
    return {'active_preset': routing.active_preset(conn, cfg, data),
            'permanent_preset': store.get_flag(conn, 'active_preset', data['default']),
            'preset_once': store.get_flag(conn, 'preset_once') or '',
            'cycle_preset': routing.cycle_preset(conn) or '',
            'presets': presets, 'providers': providers,
            'notifications': desktop.notifications_enabled(cfg),
            'interval_s': cfg.get('autopilot', 'interval_s', default=3600),
            'max_cycles_per_day': cfg.get('autopilot', 'max_cycles_per_day', default=4),
            'max_cycles_total': cfg.get('autopilot', 'max_cycles_total')}


def control(action, arg=None):
    if action in ('pause', 'quit'):
        wq('pause', '--graceful', '--reason', '菜单栏手动暂停' if action == 'pause' else '菜单栏退出')
        return {'message': '已暂停；已领取任务允许收尾'}
    if action in ('start', 'run-next'):
        if wq('status')['unknown_pending']:
            raise RuntimeError('存在 UNKNOWN 待对账，请先运行 ./wq reconcile；没有强制恢复。')
        if not loaded():
            ensure_runner()
        if action == 'run-next':
            wq('autopilot', 'run-next', '--json')
            kick_runner()
            return {'message': '已请求运行下一轮；仍受队列、授权与额度限制，不改变自动运行开关'}
        wq('autopilot', 'start', '--json')
        wq('resume')
        return {'message': '已开始自动运行，调度器按既有间隔推进'}
    if action == 'settings':
        cfg = _cfg()
        conn = desktop.connect_readonly(cfg)
        try:
            return settings_snapshot(cfg, conn)
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
            return {'message': f'已登记仅一轮预设 {arg}：下一个新建轮次使用，结束后自动恢复永久预设。'}
        return {'message': f'已切换到预设 {arg}；下一项任务领取时生效，在途任务保持原路由。'}
    if action == 'provider':
        from wq.db import connect
        if not arg:
            raise ValueError('缺少渠道名')
        cfg = _cfg()
        if arg not in routing.catalog(cfg)['providers']:
            raise ValueError(f'未知渠道 {arg}')
        conn = connect(cfg.db_path)
        try:
            was_disabled = store.get_flag(conn, f'provider_disabled:{arg}') == '1'
            store.set_flag(conn, f'provider_disabled:{arg}', '0' if was_disabled else '1')
        finally:
            conn.close()
        if was_disabled:
            return {'message': f'{arg} 已恢复；后续调用生效。'}
        return {'message': f'{arg} 已停用；不打断在途调用，后续领取生效。'}
    if action == 'notifications':
        # 无论开关状态都消费水位线：关闭期间的事件不回放，重新开启后不会弹历史积压。
        from wq.db import connect
        cfg = _cfg()
        conn = connect(cfg.db_path)
        try:
            items = desktop.pending_notifications(conn)
        finally:
            conn.close()
        return {'items': items if desktop.notifications_enabled(cfg) else []}
    if action == 'config':
        key, _, raw = (arg or '').partition('=')
        if key == 'notifications':
            if raw not in ('on', 'off'):
                raise ValueError('通知开关只接受 on/off')
            cfg = _cfg()
            if not cfg.path:
                raise RuntimeError('未找到 config/config.json，无法保存设置')
            write_config(cfg.path, 'notifications', raw == 'on', section='desktop')
            if raw == 'on':
                return {'message': '系统通知已开启；Alpha 提交成功或任务失败时提醒（需系统允许本应用通知）。'}
            return {'message': '系统通知已关闭；事件仍记录在账本，不再弹提醒。'}
        if key not in SETTINGS:
            raise ValueError('未知设置项')
        (low, high), label = SETTINGS[key]
        value = None if raw == 'none' else int(raw)
        if value is None and key != 'max_cycles_total':
            raise ValueError(f'{label} 不支持取消上限')
        if value is not None and not low <= value <= high:
            raise ValueError(f'{label} 需在 {low}–{high} 之间')
        cfg = _cfg()
        if not cfg.path:
            raise RuntimeError('未找到 config/config.json，无法保存设置')
        write_config(cfg.path, key, value)
        if key == 'interval_s':
            shown = f'{value // 60} 分钟' if value >= 60 else f'{value} 秒'
            return {'message': f'运行间隔已设为 {shown}；下一次调度起采用。'}
        if key == 'max_cycles_per_day':
            return {'message': f'每日轮数上限已设为 {value}；按 UTC 日计。'}
        return {'message': '已取消累计轮数上限。' if value is None else f'累计轮数上限已设为 {value}。'}
    if action in ('history', 'submissions'):
        cfg = _cfg()
        conn = desktop.connect_readonly(cfg)
        try:
            return desktop.history(conn, cfg) if action == 'history' else desktop.submitted(conn)
        finally:
            conn.close()
    if action != 'status':
        raise ValueError('未知操作')
    state = wq('status')
    auto = wq('autopilot', 'status', '--json')
    scheduler = loaded()
    if state['paused']:
        title = '已暂停（当前任务可收尾）'
    elif state['unknown_pending']:
        title = '待对账'
    elif not scheduler:
        title = '调度器未加载'
    elif auto.get('run_next_requested') and not auto['enabled']:
        title = '单轮运行已请求（自动运行未开启）'
    elif not auto['enabled']:
        title = '自动研究未启用'
    else:
        title = '自动研究已启用'
    cfg = _cfg()
    conn = desktop.connect_readonly(cfg)
    try:
        routes = desktop.next_models(conn, cfg)
    finally:
        conn.close()
    return {'title': title, 'paused': state['paused'], 'scheduler': scheduler,
            'message': desktop.localize_message(state.get('pause_reason') or auto['message']),
            'next_models': routes, 'next_at': desktop.beijing(auto.get('next_cycle_at'), '待当前任务完成／调度检查'),
            'cycles': f"累计 {auto['total_cycles']} / {auto.get('max_cycles_total') or '不限'} 轮",
            'last_tick': desktop.beijing(auto.get('last_tick_at')), 'unknown': state['unknown_pending']}


if __name__ == '__main__':
    try:
        print(json.dumps(control(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
