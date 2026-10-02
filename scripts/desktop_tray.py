#!/usr/bin/env python3
"""pystray 托盘菜单：与 macOS 菜单栏同源（复用 wq.desktop_control 动作），面向 Windows/Linux。

依赖：python -m pip install pystray Pillow。菜单模型（menu_model）与渲染（render_menu）分离，
前者无第三方依赖、可单测；pystray 仅在真正运行时导入。文案语言取 settings.language
（config.json 的 ui.language；auto 跟随系统），切换入口在「设置 → 界面语言」。
"""
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading

def _bind_console(alloc=False):
    """--windowed exe 没有控制台：输出被管道/文件接走就用它；否则挂到已有或父进程控制台，alloc 时新开一个。

    从 cmd 启动时会继承 cmd 的控制台句柄：fstat 能通过，但本进程并未挂上该控制台，直接写会静默丢失；
    句柄也可能“存在但无效”（WinError 6）。所以只有管道/磁盘文件走原句柄，其余一律先挂控制台。"""
    import io
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetFileType.argtypes = (wintypes.HANDLE,)

    def file_type(fd):   # 1 磁盘文件，2 字符设备（控制台/NUL），3 管道；None 无效
        try:
            os.fstat(fd)
            return kernel32.GetFileType(msvcrt.get_osfhandle(fd))
        except OSError:
            return None

    def rebind_streams():
        for name, fd, mode in (('stdin', 0, 'r'), ('stdout', 1, 'w'), ('stderr', 2, 'w')):
            if file_type(fd) in (1, 3):
                setattr(sys, name, io.open(fd, mode, encoding='utf-8', errors='replace', closefd=False))

    def attach_ancestor():
        # PyInstaller 单文件运行时是“引导进程 → Python 进程”两层，父进程（引导进程）没有控制台，
        # AttachConsole(-1) 会失败；沿祖先链往上，挂到第一个有控制台的进程（cmd / PowerShell）。
        class Entry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                        ('heap', ctypes.c_void_p), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                        ('ppid', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                        ('exe', wintypes.WCHAR * 260)]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        parents = {}
        entry = Entry(); entry.size = ctypes.sizeof(Entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.pid] = entry.ppid
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        kernel32.CloseHandle(snap)
        pid = os.getpid()
        for _ in range(4):
            pid = parents.get(pid)
            if not pid:
                return False
            if kernel32.AttachConsole(pid):
                return True
        return False

    if file_type(1) in (1, 3):
        rebind_streams()
        return False
    # --console（向导/登录）总是自己开窗口，避免挂进祖先进程可能隐藏着的控制台里。
    if kernel32.GetConsoleWindow() or (kernel32.AllocConsole() if alloc else attach_ancestor()):
        sys.stdin = open('CONIN$', 'r', encoding='utf-8', errors='replace')
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8', errors='replace', buffering=1)
        sys.stderr = sys.stdout
        # getpass 只在 sys.stdin is sys.__stdin__ 时走不回显的 msvcrt 路径，否则退化为明文回显。
        sys.__stdin__, sys.__stdout__, sys.__stderr__ = sys.stdin, sys.stdout, sys.stderr
        return True
    rebind_streams()
    return False


def _pop_flag(name, value=False):
    if name not in sys.argv[1:]:
        return None
    i = sys.argv.index(name, 1)
    if value:
        result = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        del sys.argv[i:i + 2]
        return result
    del sys.argv[i]
    return True


def _default_workspace():
    """托盘模式的工作区：上次用过的 → ~/autowq。"""
    remembered = Path(os.environ.get('APPDATA') or Path.home()) / 'WorldQuant' / 'workspace.txt'
    try:
        value = remembered.read_text(encoding='utf-8').strip()
        if value and (Path(value) / 'config' / 'config.json').exists():
            return Path(value)
    except OSError:
        pass
    return Path.home() / 'autowq'


def _remember_workspace(root):
    remembered = Path(os.environ.get('APPDATA') or Path.home()) / 'WorldQuant' / 'workspace.txt'
    try:
        remembered.parent.mkdir(parents=True, exist_ok=True)
        remembered.write_text(str(root), encoding='utf-8')
    except OSError:
        pass


def _install_cli():
    """写一个 wq.cmd，让命令行（含 AI 代理）像普通 CLI 一样调用内置引擎；批处理会等 exe 结束。"""
    target = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'WorldQuant' / 'bin'
    target.mkdir(parents=True, exist_ok=True)
    script = target / 'wq.cmd'
    script.write_text(f'@echo off\r\n"{sys.executable}" --engine %*\r\n', encoding='utf-8')
    print(json.dumps({'wq': str(script), 'add_to_path': str(target)}, ensure_ascii=False))
    return 0


if getattr(sys, 'frozen', False):
    # PyInstaller 打包：一个 exe 同时是托盘、完整命令行和调度入口。
    #   --engine <wq 参数>       运行内置 CLI（desktop_control 与 wq.cmd 自代理到这里）
    #   --console                先新开控制台（交互向导 / 登录），结束后等回车
    #   --provider-runtime ...   API 渠道调用（providers.runtime_argv）
    #   --scheduled-run          任务计划每分钟一次：日志写 var/run 后跑 run-once
    #   --setup-windows ...      注册/移除调度与自启（wq.windows_setup）
    #   --install-cli            生成 %LOCALAPPDATA%\WorldQuant\bin\wq.cmd
    import json
    BUNDLE = Path(sys._MEIPASS)
    sys.path.insert(0, str(BUNDLE))
    argv_ws = _pop_flag('--workspace', value=True)
    console = _pop_flag('--console')
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode is None and not argv_ws:
        ROOT = _default_workspace()
    else:
        ROOT = Path(argv_ws).resolve() if argv_ws else Path.cwd()
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    if mode == '--engine':
        _bind_console(alloc=bool(console))
        from wq import cli as _cli
        try:
            code = _cli.main(sys.argv[2:])
        except SystemExit as exc:
            code = exc.code
        if console:
            input('\n[Enter] ')
        raise SystemExit(code)
    if mode == '--provider-runtime':
        _bind_console()
        import runpy
        sys.argv = ['provider_runtime', *sys.argv[2:]]
        runpy.run_module('wq.provider_runtime', run_name='__main__')
        raise SystemExit(0)
    if mode == '--scheduled-run':
        (ROOT / 'var' / 'run').mkdir(parents=True, exist_ok=True)
        sys.stdout = open(ROOT / 'var' / 'run' / 'runner.out.log', 'a', buffering=1, encoding='utf-8')
        sys.stderr = open(ROOT / 'var' / 'run' / 'runner.err.log', 'a', buffering=1, encoding='utf-8')
        from wq import cli as _cli
        raise SystemExit(_cli.main(['run-once', '--lease', '3600']))
    if mode == '--setup-windows':
        _bind_console()
        from wq import windows_setup as _win
        raise SystemExit(_win.main(['--root', str(ROOT), *sys.argv[2:]]))
    if mode == '--install-cli':
        _bind_console()
        raise SystemExit(_install_cli())
else:
    ROOT = Path(__file__).resolve().parents[1]
    BUNDLE = None
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'src'))

from wq import desktop_control as bridge  # noqa: E402
from wq.i18n import text  # noqa: E402

HISTORY_CAP = 30
# 托盘菜单无法输入数字，临时预设轮数用常用档位；任意轮数可用 CLI --cycles。
TEMP_CYCLE_CHOICES = (1, 3, 5, 10)
INTERVALS = [(300, ('5 分钟', '5 min')), (900, ('15 分钟', '15 min')), (1800, ('30 分钟', '30 min')),
             (3600, ('1 小时', '1 hour')), (7200, ('2 小时', '2 hours')), (21600, ('6 小时', '6 hours'))]
DAILIES = [(10, ('10 轮', '10 cycles')), (20, ('20 轮', '20 cycles')), (40, ('40 轮', '40 cycles')), (80, ('80 轮', '80 cycles'))]
LANES = [(1, ('1 条（串行）', '1 (serial)')), (2, ('2 条', '2 lanes')), (3, ('3 条', '3 lanes')), (4, ('4 条', '4 lanes'))]
TOTALS = [(50, ('50 轮', '50 cycles')), (100, ('100 轮', '100 cycles')), (150, ('150 轮', '150 cycles')), (300, ('300 轮', '300 cycles'))]
LANGUAGES = [('auto', ('跟随系统（自动）', 'Follow system (auto)')),
             ('zh', ('中文', '中文')), ('en', ('English', 'English'))]
BADGE_PREFIX = {'submitted': '★ ', 'standby': '◇ '}


# ---------- 菜单模型（纯数据，无 pystray 依赖） ----------

def info(message):
    return {'kind': 'info', 'text': str(message)}


def sep():
    return {'kind': 'sep'}


def action(label, act, arg=None):
    return {'kind': 'action', 'text': str(label), 'action': act, 'arg': arg}


def check(label, act, arg, checked):
    return {'kind': 'check', 'text': str(label), 'action': act, 'arg': arg, 'checked': bool(checked)}


def radio(label, act, arg, checked):
    return {'kind': 'radio', 'text': str(label), 'action': act, 'arg': arg, 'checked': bool(checked)}


def submenu(label, items):
    return {'kind': 'submenu', 'text': str(label), 'items': items}


def history_items(hist, lang):
    import textwrap
    entries = (hist.get('entries') or [])[:HISTORY_CAP]
    if not entries:
        return [info(text(lang, '暂无记录', 'No records'))]
    rows = [info(text(lang, f"共 {hist.get('count', len(entries))} 轮 · 北京时间",
                          f"{hist.get('count', len(entries))} cycles · Beijing time"))]
    for entry in entries:
        prefix = BADGE_PREFIX.get(entry.get('badge') or '', '')
        details = entry.get('detail') or entry.get('lines') or []
        title = prefix + str(entry.get('title', ''))
        rows.append(submenu(title, [info(part) for line in details for part in textwrap.wrap(str(line), 90)]) if details else info(title))
    return rows


def submission_items(subs, lang):
    entries = subs.get('entries') or []
    if not entries:
        return [info(text(lang, '暂无记录', 'No records'))]
    marks = {'submitted': '★ ', 'standby': '◇ '}
    return [info(marks.get(e.get('badge') or '', '') + str(e.get('title', ''))) for e in entries]


def _current_note(pairs, value, unit, lang):
    if value is not None and value not in dict(pairs):
        return [info(text(lang, f'当前值 {value} {unit}', f'Current: {value} {unit}'))]
    return []


def _temp_label(n, lang):
    return text(lang, f'临时 {n} 轮', f'Temporary: {"1 cycle" if n == 1 else f"{n} cycles"}')


def preset_items(se, lang):
    presets = se.get('presets') or []
    if not presets:
        return [info(text(lang, '暂无预设', 'No presets'))]
    active = se.get('active_preset') or ''
    permanent = se.get('permanent_preset') or active
    once = se.get('preset_once') or ''
    left = se.get('preset_once_cycles') or 1
    cycle_preset = se.get('cycle_preset') or ''
    head = text(lang, '永久预设：', 'Permanent preset: ') + str(permanent)
    if cycle_preset:
        head += text(lang, '｜本轮临时：', ' | this cycle: ') + str(cycle_preset)
    if once:
        head += text(lang, f'｜临时待用：{once} ×{left}', f' | pending: {once} ×{left}')
    rows = [info(head),
            info(text(lang, '点击预设选择永久或临时切换；临时用完后自动恢复永久预设，在途任务保持原路由',
                      'Click a preset to switch permanently or temporarily; a temporary preset reverts to the permanent one when used up. In-flight tasks keep their routes'))]
    if once:
        rows.append(action(text(lang, f'取消临时切换（{once} ×{left}）',
                                f'Cancel temporary switch ({once} ×{left})'), 'preset-cancel'))
    rows.append(sep())
    for preset in presets:
        name = str(preset.get('name', ''))
        title = ('● ' if name == active else '') + name
        research = preset.get('research') or ''
        review = preset.get('review') or ''
        if research or review:
            title += '\n' + text(lang, '研究  ', 'Research  ') + str(research)
            title += '\n' + text(lang, '审查  ', 'Review  ') + str(review)
        rows.append(submenu(title,
                            [action(text(lang, '永久切换', 'Switch permanently'), 'preset', name)]
                            + [action(_temp_label(n, lang), 'preset-once', f'{name}:{n}') for n in TEMP_CYCLE_CHOICES]))
    return rows


def _spend_items(se, lang):
    known = se.get('spend_known_usd') or 0
    unknown = se.get('spend_unknown_calls') or 0
    cap = se.get('spend_cap_usd')
    rows = [info(text(lang, f'自本次设置起已知花费 ${known:.4f}', f'Known spend since this cap was set: ${known:.4f}'))]
    if unknown:
        rows.append(info(text(lang, f'另有 {unknown} 次调用金额未知，未计入，也不记成 $0',
                              f'{unknown} calls have an unknown price; they are omitted and not treated as $0')))
    rows.append(radio(text(lang, '不限', 'No cap'), 'config', 'model_spend_cap_usd=none', cap is None))
    current_shown = False
    for value in se.get('spend_presets') or [5, 10, 20, 50, 100]:
        matched = isinstance(cap, (int, float)) and abs(float(cap) - float(value)) < 0.001
        if matched:
            current_shown = True
        rows.append(radio(f'${value}', 'config', f'model_spend_cap_usd={value}', matched))
    if isinstance(cap, (int, float)) and not current_shown:
        rows.append(info(text(lang, f'当前上限 ${float(cap):.4f}', f'Current cap ${float(cap):.4f}')))
    return rows


def settings_items(se, lang):
    if not se:
        return [info(text(lang, '正在读取设置…', 'Loading settings…'))]
    notifications = se.get('notifications', True)
    provider_rows = [check(p['name'] + (text(lang, '（不可用）', ' (unavailable)') if p.get('reason') else ''),
                           'provider', p['name'], not p.get('disabled'))
                     for p in se.get('providers') or []]
    language_rows = [radio(text(lang, zh, en), 'config', f'language={value}',
                           se.get('language_setting', 'auto') == value)
                     for value, (zh, en) in LANGUAGES]
    submission_on = bool(se.get('submission_enabled'))
    launch_on = se.get('launch_research', True) is not False
    return [
        check(text(lang, '系统通知（提交成功/任务失败）', 'System notifications (submission accepted / task failed)'),
              'config', 'notifications=' + ('off' if notifications else 'on'), notifications),
        check(text(lang, '提交队列（打开后，内部通过且授权仍有效的 Alpha 会自动入队）',
                   'Submission queue (a passing Alpha is queued automatically while authorization is valid)'),
              'config', 'submission=' + ('off' if submission_on else 'on'), submission_on),
        check(text(lang, '启动时开始自动研究', 'Start automatic research on launch'),
              'config', 'launch_research=' + ('off' if launch_on else 'on'), launch_on),
        submenu(text(lang, '界面语言', 'Interface language'), language_rows),
        submenu(text(lang, '模型', 'Models'), model_items(se, lang)),
        submenu(text(lang, '模型花费上限', 'Model spend cap'), _spend_items(se, lang)),
        submenu(text(lang, '路由预设', 'Routing presets'), preset_items(se, lang)),
        submenu(text(lang, '渠道', 'Providers'),
                [info(text(lang, '勾选表示参与路由，点击切换', 'Checked = used for routing; click to toggle'))] + provider_rows),
        submenu(text(lang, '运行间隔', 'Run interval'),
                [radio(text(lang, zh, en), 'config', f'interval_s={value}', se.get('interval_s') == value)
                 for value, (zh, en) in INTERVALS]
                + _current_note([(v, 0) for v, _ in INTERVALS], se.get('interval_s'), text(lang, '秒', 's'), lang)),
        submenu(text(lang, '并行泳道', 'Parallel lanes'),
                [info(text(lang, '每条泳道是一轮完整的研究→审查→模拟；研究与审查必须不同渠道，'
                               '同一渠道同一时刻只有一个调用，模拟与提交仍串行。对在途轮次不打断，下一轮起生效。',
                               'Each lane is a full research→review→simulate cycle; research and review must use different channels, '
                               'one call per channel at a time, and simulation/submission stay serial. Applies to the next cycles.'))]
                + [radio(text(lang, zh, en), 'config', f'concurrent_lanes={value}', se.get('concurrent_lanes') == value)
                   for value, (zh, en) in LANES]
                + _current_note([(v, 0) for v, _ in LANES], se.get('concurrent_lanes'), text(lang, '条', 'lanes'), lang)),
        submenu(text(lang, '每日轮数上限', 'Daily cycle limit'),
                [radio(text(lang, zh, en), 'config', f'max_cycles_per_day={value}', se.get('max_cycles_per_day') == value)
                 for value, (zh, en) in DAILIES]
                + _current_note([(v, 0) for v, _ in DAILIES], se.get('max_cycles_per_day'), text(lang, '轮', 'cycles'), lang)),
        submenu(text(lang, '累计轮数上限', 'Total cycle limit'),
                [radio(text(lang, zh, en), 'config', f'max_cycles_total={value}', se.get('max_cycles_total') == value)
                 for value, (zh, en) in TOTALS]
                + [radio(text(lang, '不限', 'Unlimited'), 'config', 'max_cycles_total=none', se.get('max_cycles_total') is None)]
                + _current_note([(v, 0) for v, _ in TOTALS], se.get('max_cycles_total'), text(lang, '轮', 'cycles'), lang)),
        action(text(lang, '检查更新', 'Check for updates'), 'update'),
        action(text(lang, '打开运行日志', 'Open run log'), 'logs'),
    ]


def model_items(se, lang):
    providers = se.get('providers') or []
    rows = [action(text(lang, '添加自定义模型…', 'Add a custom model…'), 'provider-add'),
            info(text(lang, '保存成模板后，在下面分别指定研究和审查。两者必须不同，从下一轮生效。',
                      'After you save a template, choose research and review below. They must differ, and the choice applies from the next cycle.'))]
    for role, head_key, zh, en in (
            ('research', 'research_provider', '研究模型', 'Research model'),
            ('review', 'review_provider', '审查模型', 'Review model')):
        rows.append(sep())
        rows.append(info(text(lang, zh, en)))
        head = se.get(head_key) or ''
        for provider in providers:
            name = provider.get('name') or ''
            rows.append(radio(provider.get('label') or name, 'provider-role', f'{role}:{name}', name == head))
    return rows


def run_actions(st, lang):
    """开始/停止互斥；有进行中的轮次时用取消替换立刻运行；多泳道时按轮次取消。"""
    paused = bool(st.get('paused'))
    enabled = bool(st.get('enabled'))
    cycle_open = bool(st.get('cycle_open'))
    lanes = [item for item in (st.get('lanes') or []) if item.get('cycle_id')]
    rows = []
    if len(lanes) > 1:
        rows.append(submenu(text(lang, '取消轮次…', 'Cancel cycle…'),
                            [action(text(lang, f"泳道{(item.get('lane') or 0)+1} · 第{item.get('cycle_id')}轮 · {item.get('state_label') or item.get('state')}",
                                          f"Lane {(item.get('lane') or 0)+1} · cycle {item.get('cycle_id')} · {item.get('state_label') or item.get('state')}"),
                                    'cancel-cycle', str(item.get('cycle_id'))) for item in lanes]))
    elif cycle_open:
        rows.append(action(text(lang, '取消当前轮次', 'Cancel current cycle'), 'cancel-cycle'))
    elif not paused:
        rows.append(action(text(lang, '立刻运行下一轮', 'Run next cycle now'), 'run-next'))
    if (not paused) and enabled:
        rows.append(action(text(lang, '停止自动研究', 'Stop automatic research'), 'pause'))
    else:
        rows.append(action(text(lang, '开始自动研究', 'Start automatic research'), 'start'))
    return rows


def menu_model(state):
    settings = state.get('settings') or {}
    lang = settings.get('language') or 'zh'
    st = state.get('status') or {}
    hist = state.get('history') or {}
    subs = state.get('submissions') or {}
    identity = st.get('identity') or {}
    account_title = identity.get('title') or text(lang, '未登录', 'Not signed in')
    items = [
        submenu(account_title, [
            info(identity.get('detail') or text(lang, '登录后这里显示昵称或邮箱，以及等级和分数。',
                                               'After you sign in, this shows your name or email, plus level and score.')),
            action(text(lang, '刷新账号信息', 'Refresh account'), 'identity-refresh', 'manual'),
            action(text(lang, '绑定 / 重新登录…', 'Bind / re-login…'), 'brain-login'),
            action(text(lang, '核验会话（联网检查）', 'Verify session (online check)'), 'brain-check'),
            action(text(lang, '打开 BRAIN 注册页', 'Open BRAIN registration page'), 'brain-register'),
        ]),
        info(st.get('title') or text(lang, '读取状态…', 'Reading status…')),
        info((st.get('message') or ' ')[:80] or ' '),
        info(text(lang, '最近调度：', 'Last tick: ') + (st.get('last_tick') or text(lang, '尚无记录', 'No records yet'))),
        info(text(lang, '下一轮：', 'Next cycle: ') + (st.get('next_at') or text(lang, '待当前任务完成／调度检查', 'awaiting current task / scheduler check'))),
    ]
    items += [info(text(lang, '下轮', 'Next: ') + str(m.get('title', text(lang, '未知', 'unknown')))) for m in st.get('next_models') or []]
    for item in st.get('lanes') or []:
        pair = ' → '.join(x for x in (item.get('research'), item.get('review')) if x)
        label = item.get('state_label') or item.get('state') or ''
        if item.get('cycle_id'):
            label = text(lang, f"第{item.get('cycle_id')}轮 · ", f"cycle {item.get('cycle_id')} · ") + label
        items.append(info(text(lang, f"泳道{(item.get('lane') or 0)+1} · {label}" + (f"（{pair}）" if pair else ''),
                               f"Lane {(item.get('lane') or 0)+1} · {label}" + (f" ({pair})" if pair else ''))))
    items += [
        sep(),
        submenu(text(lang, f"轮次历史（{hist.get('count', '…')}）", f"Cycle history ({hist.get('count', '…')})"),
                history_items(hist, lang)),
        submenu(text(lang, f"已提交 Alpha（{subs.get('count', '…')}）", f"Submitted Alphas ({subs.get('count', '…')})"),
                submission_items(subs, lang)),
        submenu(text(lang, f"备选 Alpha（{state.get('standby', {}).get('count', '…')}）",
                     f"Standby Alphas ({state.get('standby', {}).get('count', '…')})"),
                submission_items(state.get('standby') or {}, lang)),
        sep(),
        submenu(text(lang, '研究进展', 'Research progress'), submission_items(state.get('research') or {}, lang)),
        submenu(text(lang, '设置', 'Settings'), settings_items(settings, lang)),
        sep(),
        *run_actions(st, lang),
        submenu(text(lang, '退出…', 'Quit…'), [
            action(text(lang, '仅退出', 'Quit menu only'), 'quit-app'),
            action(text(lang, '本轮结束后停止并退出', 'Stop after this cycle and quit'), 'quit-after-cycle'),
            action(text(lang, '立刻结束所有任务并退出', 'Stop all tasks and quit'), 'quit-now'),
        ]),
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
            if act in ('status', 'settings', 'history', 'submissions', 'standby', 'research'):
                self.state[act] = result
            if act == 'update' and isinstance(result, dict) and result.get('update') and result.get('url'):
                import webbrowser
                webbrowser.open(str(result['url']))
            return result
        except Exception as exc:
            logging.exception('动作失败 %s', act)
            if not quiet:
                lang = (self.state.get('settings') or {}).get('language') or 'zh'
                self.notify(text(lang, '操作未完成', 'Action failed'), str(exc)[:200])
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
                elif act in ('quit-app', 'quit-after-cycle', 'quit-now'):
                    if act != 'quit-app':
                        self.call(act)
                    self.icon.stop()
                    return
                else:
                    result = self.call(act, arg)
                    quiet = act in ('start', 'pause', 'run-next', 'cancel-cycle', 'config', 'launch',
                                    'preset', 'preset-once', 'preset-cancel', 'provider', 'provider-role')
                    if isinstance(result, dict) and result.get('message') and not quiet:
                        self.notify('WorldQuant', str(result['message'])[:200])
                if act in ('preset', 'preset-once', 'preset-cancel', 'provider', 'config'):
                    self.call('settings', quiet=True)   # 立即反映新勾选状态，不等周期轮询
            finally:
                if act not in ('quit-app', 'quit-after-cycle', 'quit-now'):
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
        self.call('identity-refresh', quiet=True)
        self.call('launch', quiet=True)
        self.call('status', quiet=True)
        self.call('settings', quiet=True)
        self.refresh()
        ticks = 0
        while not self.stop.wait(15):
            ticks += 1
            try:
                self.call('status', quiet=True)
                if ticks % 4 == 0:
                    for act in ('history', 'submissions', 'standby', 'research', 'settings'):
                        self.call(act, quiet=True)
                    self.check_notifications()
                self.refresh()
            except Exception:
                logging.exception('刷新循环异常')


def first_run(root):
    """打包 exe 首次打开：工作区没有配置时，新开控制台跑 onboard 向导，结束后再进托盘。"""
    if (root / 'config' / 'config.json').exists():
        return True
    if not getattr(sys, 'frozen', False):
        return True   # 源码方式沿用原行为：由用户先在终端跑 wq onboard
    subprocess.call([sys.executable, '--console', '--workspace', str(root), '--engine', 'onboard'], cwd=str(root))
    if (root / 'config' / 'config.json').exists():
        return True
    if sys.platform == 'win32':
        from wq.i18n import default_language
        import ctypes
        lang = default_language()
        ctypes.windll.user32.MessageBoxW(None, text(lang, f'初始化向导没有完成，工作区 {root} 还没有配置。重新打开 WorldQuant 即可继续。',
                                                    f'The setup wizard did not finish, so {root} has no configuration yet. Open WorldQuant again to continue.'),
                                         'WorldQuant', 0x40)
    return False


def main():
    from PIL import Image
    import pystray

    if not first_run(ROOT):
        return 0
    if getattr(sys, 'frozen', False):
        _remember_workspace(ROOT)
    log_path = ROOT / 'var/run/tray.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(log_path, encoding='utf-8')])
    lock = acquire_lock(ROOT / 'var/run/tray.lock')
    if lock is None:
        logging.error('已有托盘实例在运行，退出')
        return 0
    tray = Tray()
    tray.icon = pystray.Icon('WorldQuant', title='WorldQuant',
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
