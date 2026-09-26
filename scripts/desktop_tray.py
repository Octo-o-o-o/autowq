#!/usr/bin/env python3
"""pystray 托盘菜单：与 macOS 菜单栏同源（复用 wq.desktop_control 动作），面向 Windows/Linux。

依赖：python -m pip install pystray Pillow。菜单模型（menu_model）与渲染（render_menu）分离，
前者无第三方依赖、可单测；pystray 仅在真正运行时导入。
"""
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading

if getattr(sys, 'frozen', False):
    # PyInstaller 打包：__file__ 指向解包临时目录；工作区用 --workspace 指定，默认 cwd。
    argv_ws = None
    if '--workspace' in sys.argv:
        i = sys.argv.index('--workspace')
        if i + 1 < len(sys.argv):
            argv_ws = sys.argv[i + 1]
    ROOT = Path(argv_ws).resolve() if argv_ws else Path.cwd()
    BUNDLE = Path(sys._MEIPASS)
    os.chdir(ROOT)
    if len(sys.argv) > 1 and sys.argv[1] == '--engine':
        # frozen 下 sys.executable 是本 exe，无法当解释器跑 `-m wq`；
        # desktop_control.wq() 以 `exe --engine <args...>` 自代理到内置引擎。
        # --windowed 构建 stdout 可能为 None，父进程靠管道捕获输出，须显式重绑 fd。
        import io
        if sys.stdout is None:
            sys.stdout = io.open(1, 'w', closefd=False)
        if sys.stderr is None:
            sys.stderr = io.open(2, 'w', closefd=False)
        sys.path.insert(0, str(BUNDLE))
        from wq import cli as _cli
        raise SystemExit(_cli.main(sys.argv[2:]))
else:
    ROOT = Path(__file__).resolve().parents[1]
    BUNDLE = None
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'src'))

from wq import desktop_control as bridge  # noqa: E402

HISTORY_CAP = 30
INTERVALS = [(300, '5 分钟'), (900, '15 分钟'), (1800, '30 分钟'), (3600, '1 小时'), (7200, '2 小时'), (21600, '6 小时')]
DAILIES = [(10, '10 轮'), (20, '20 轮'), (40, '40 轮'), (80, '80 轮')]
TOTALS = [(50, '50 轮'), (100, '100 轮'), (150, '150 轮'), (300, '300 轮')]
BADGE_PREFIX = {'submitted': '★ '}


# ---------- 菜单模型（纯数据，无 pystray 依赖） ----------

def info(text):
    return {'kind': 'info', 'text': str(text)}


def sep():
    return {'kind': 'sep'}


def action(text, act, arg=None):
    return {'kind': 'action', 'text': text, 'action': act, 'arg': arg}


def check(text, act, arg, checked):
    return {'kind': 'check', 'text': text, 'action': act, 'arg': arg, 'checked': bool(checked)}


def radio(text, act, arg, checked):
    return {'kind': 'radio', 'text': text, 'action': act, 'arg': arg, 'checked': bool(checked)}


def submenu(text, items):
    return {'kind': 'submenu', 'text': text, 'items': items}


def history_items(hist):
    entries = (hist.get('entries') or [])[:HISTORY_CAP]
    if not entries:
        return [info('暂无记录')]
    rows = [info(f"共 {hist.get('count', len(entries))} 轮 · 北京时间")]
    for entry in entries:
        prefix = BADGE_PREFIX.get(entry.get('badge') or '', '')
        rows.append(info(prefix + str(entry.get('title', ''))))
    return rows


def submission_items(subs):
    entries = subs.get('entries') or []
    if not entries:
        return [info('暂无记录')]
    return [info(('★ ' if e.get('badge') == 'submitted' else '') + str(e.get('title', ''))) for e in entries]


def _current_note(pairs, value, unit):
    if value is not None and value not in dict(pairs):
        return [info(f'当前值 {value} {unit}')]
    return []


def settings_items(se):
    if not se:
        return [info('正在读取设置…')]
    notifications = se.get('notifications', True)
    preset_rows = [radio(p['name'], 'preset', p['name'], p['name'] == se.get('active_preset'))
                   for p in se.get('presets') or []]
    provider_rows = [check(p['name'] + ('（不可用）' if p.get('reason') else ''),
                           'provider', p['name'], not p.get('disabled'))
                     for p in se.get('providers') or []]
    return [
        check('系统通知（提交成功/任务失败）', 'config',
              'notifications=' + ('off' if notifications else 'on'), notifications),
        submenu('路由预设', preset_rows or [info('暂无预设')]),
        submenu('渠道', [info('勾选表示参与路由，点击切换')] + provider_rows),
        submenu('运行间隔', [radio(text, 'config', f'interval_s={value}', se.get('interval_s') == value)
                          for value, text in INTERVALS] + _current_note(INTERVALS, se.get('interval_s'), '秒')),
        submenu('每日轮数上限', [radio(text, 'config', f'max_cycles_per_day={value}', se.get('max_cycles_per_day') == value)
                             for value, text in DAILIES] + _current_note(DAILIES, se.get('max_cycles_per_day'), '轮')),
        submenu('累计轮数上限', [radio(text, 'config', f'max_cycles_total={value}', se.get('max_cycles_total') == value)
                             for value, text in TOTALS]
                + [radio('不限', 'config', 'max_cycles_total=none', se.get('max_cycles_total') is None)]
                + _current_note(TOTALS, se.get('max_cycles_total'), '轮')),
    ]


def menu_model(state):
    st = state.get('status') or {}
    hist = state.get('history') or {}
    subs = state.get('submissions') or {}
    items = [
        info(st.get('title') or '读取状态…'),
        info((st.get('message') or ' ')[:80] or ' '),
        info('最近调度：' + (st.get('last_tick') or '尚无记录')),
        info('下一轮：' + (st.get('next_at') or '待调度检查')),
    ]
    items += [info('下轮' + str(m.get('title', '未知'))) for m in st.get('next_models') or []]
    items += [
        sep(),
        submenu('WorldQuant 账号', [
            info('BRAIN 账号：' + ('已绑定（本地会话存在）' if st.get('brain_bound')
                                   else '未绑定；用下方「绑定 / 重新登录」')),
            action('绑定 / 重新登录…', 'brain-login'),
            action('核验会话（联网检查）', 'brain-check'),
            action('打开 BRAIN 注册页', 'brain-register'),
        ]),
        sep(),
        submenu(f"轮次历史（{hist.get('count', '…')}）", history_items(hist)),
        submenu(f"已提交 Alpha（{subs.get('count', '…')}）", submission_items(subs)),
        sep(),
        submenu('设置', settings_items(state.get('settings'))),
        sep(),
        action('立刻运行下一轮', 'run-next'),
        action('开始自动运行', 'start'),
        action('暂停（当前任务完成后）', 'pause'),
        action('打开运行日志', 'logs'),
        action('退出（暂停自动运行）', 'quit'),
    ]
    return items


def _callback(on_action, act, arg):
    # pystray 按参数个数包装回调：必须是恰好 (icon, item) 两个参数的普通函数。
    def cb(_icon, _item):
        on_action(act, arg)
    return cb


def render_menu(entries, on_action):
    """把菜单模型渲染成 pystray Menu；on_action(action, arg) 在回调线程触发。"""
    import pystray

    def one(entry):
        kind = entry['kind']
        if kind == 'sep':
            return pystray.Menu.SEPARATOR
        if kind == 'submenu':
            return pystray.MenuItem(entry['text'], render_menu(entry['items'], on_action))
        if kind == 'info':
            return pystray.MenuItem(entry['text'], None, enabled=False)
        cb = _callback(on_action, entry.get('action'), entry.get('arg'))
        if kind in ('check', 'radio'):
            return pystray.MenuItem(entry['text'], cb, checked=lambda i, v=entry.get('checked'): v,
                                    radio=(kind == 'radio'))
        return pystray.MenuItem(entry['text'], cb)

    return pystray.Menu(*[one(entry) for entry in entries])


def open_path(path):
    if sys.platform == 'win32':
        os.startfile(path)  # noqa: S606
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


def acquire_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, 'a+', encoding='utf-8')
    try:
        if sys.platform == 'win32':
            import msvcrt
            if fh.seek(0, 2) == 0:
                fh.write('1'); fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


class Tray:
    def __init__(self):
        self.state = {}
        self.icon = None
        self.stop = threading.Event()

    def call(self, act, arg=None, quiet=False):
        try:
            result = bridge.control(act, arg)
            if act in ('status', 'settings', 'history', 'submissions'):
                self.state[act] = result
            return result
        except Exception as exc:
            logging.exception('动作失败 %s', act)
            if not quiet:
                self.notify('操作未完成', str(exc)[:200])
            return None

    def notify(self, title, body):
        if self.icon is None:
            return
        try:
            self.icon.notify(body, title)
        except Exception:
            logging.exception('通知失败')

    def on_action(self, act, arg):
        def worker():
            try:
                if act == 'logs':
                    open_path(bridge.runner_log())
                elif act == 'quit':
                    self.call('quit')
                    self.icon.stop()
                    return
                else:
                    result = self.call(act, arg)
                    if isinstance(result, dict) and result.get('message'):
                        self.notify('WorldQuant', str(result['message'])[:200])
                if act in ('preset', 'provider', 'config'):
                    self.call('settings', quiet=True)   # 立即反映新勾选状态，不等周期轮询
            finally:
                if act != 'quit':
                    self.refresh()
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        self.icon.menu = render_menu(menu_model(self.state), self.on_action)
        try:
            self.icon.update_menu()
        except Exception:
            logging.exception('刷新菜单失败')

    def check_notifications(self):
        result = self.call('notifications', quiet=True)
        for item in (result or {}).get('items') or []:
            self.notify(item.get('title', 'WorldQuant'), item.get('body', ''))

    def loop(self):
        self.call('status', quiet=True)
        self.refresh()
        ticks = 0
        while not self.stop.wait(15):
            ticks += 1
            try:
                self.call('status', quiet=True)
                if ticks % 4 == 0:
                    for act in ('history', 'submissions', 'settings'):
                        self.call(act, quiet=True)
                    self.check_notifications()
                self.refresh()
            except Exception:
                logging.exception('刷新循环异常')


def main():
    from PIL import Image
    import pystray

    log_path = ROOT / 'var/run/tray.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(log_path, encoding='utf-8')])
    lock = acquire_lock(ROOT / 'var/run/tray.lock')
    if lock is None:
        logging.error('已有托盘实例在运行，退出')
        return 0
    tray = Tray()
    tray.icon = pystray.Icon('WorldQuant', title='WorldQuant 自动研究',
                             menu=render_menu(menu_model({}), tray.on_action))
    candidates = [ROOT / 'packaging/assets/tray-icon.png']
    if BUNDLE is not None:
        # PyInstaller 6.x onefile 数据文件可能在 _MEIPASS 根或 _internal 子目录
        for sub in ('_internal', '.'):
            candidates.insert(0, BUNDLE / sub / 'tray-icon.png')
    candidates.append(ROOT / 'macos/Assets/ResearchIcon.png')
    for icon_path in candidates:
        try:
            tray.icon.icon = Image.open(icon_path)
            logging.info('托盘图标已加载: %s', icon_path)
            break
        except OSError as e:
            logging.warning('托盘图标加载失败 %s: %s', icon_path, e)
    else:
        if BUNDLE is not None and BUNDLE.is_dir():
            logging.warning('BUNDLE=%s；目录内 png: %s', BUNDLE,
                            sorted(p.name for p in BUNDLE.rglob('*.png')))
        tray.icon.icon = Image.new('RGB', (64, 64), (40, 90, 60))
    worker = threading.Thread(target=tray.loop, daemon=True)
    worker.start()
    tray.icon.run()
    tray.stop.set()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
