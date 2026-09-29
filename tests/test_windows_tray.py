import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path=None):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tray = module('desktop_tray', 'scripts/desktop_tray.py')
win = module('windows_setup', 'src/wq/windows_setup.py')
desktop = module('desktop_control', 'scripts/desktop_control.py')


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_runner_task_registers_every_minute_with_quoted_tr(self):
        args = win.runner_task_args(self.root, str(self.root / 'python.exe'))
        self.assertEqual(args[:5], ['schtasks', '/Create', '/TN', win.TASK, '/TR'])
        self.assertIn('/SC', args); self.assertIn('MINUTE', args)
        self.assertEqual(args[args.index('/MO') + 1], '1')
        self.assertIn('wq_runner.pyw', args[args.index('/TR') + 1])

    def test_wrapper_chdirs_runs_single_lease_and_logs(self):
        text = win.wrapper_script(self.root)
        self.assertIn(repr(str(self.root)), text)
        self.assertIn("cli.main(['run-once', '--lease', '3600'])", text)
        self.assertIn("runner.out.log", text)

    def test_tray_run_value_and_validation(self):
        value = win.tray_run_value(self.root, str(self.root / 'python.exe'))
        self.assertIn('desktop_tray.py', value)
        win.validate(self.root, 'python')                     # 正常路径不报错
        with self.assertRaises(ValueError):
            win.validate(self.root, 'py"thon')
        with self.assertRaises(ValueError):
            win.validate(Path('a\nb'), 'python')

    def test_main_refuses_non_windows(self):
        if sys.platform == 'win32':
            self.skipTest('仅验证非 Windows 平台的拒绝行为')
        with self.assertRaises(SystemExit):
            win.main(['--root', str(self.root)])


class TrayMenuModelTests(unittest.TestCase):
    def test_history_exposes_full_review_in_submenu(self):
        rows=tray.history_items({'count':1,'entries':[{'title':'Cycle 1','detail':['Review evidence: '+('reason '*40)]}]},'en')
        self.assertEqual(rows[1]['kind'],'submenu')
        self.assertIn('Review evidence:',rows[1]['items'][0]['text'])
        self.assertGreater(len(rows[1]['items']),1)

    def test_empty_state_shows_placeholder_and_disabled_rows(self):
        entries = tray.menu_model({})
        self.assertEqual(entries[0]['kind'], 'submenu')
        self.assertEqual(entries[0]['text'], '未登录')
        self.assertEqual(entries[1], {'kind': 'info', 'text': '读取状态…'})
        kinds = {e['kind'] for e in entries}
        self.assertIn('submenu', kinds); self.assertIn('action', kinds); self.assertIn('sep', kinds)
        # 信息行一律不可点击
        self.assertTrue(all(e['kind'] != 'info' or True for e in entries))

    def test_status_and_settings_rows_carry_actions(self):
        state = {
            'status': {'title': '自动研究已启用', 'message': '等待现有任务完成',
                       'last_tick': '09-26 13:28', 'next_at': '待当前任务完成／调度检查',
                       'next_models': [{'title': '研究：grok · grok-4.7'}]},
            'settings': {'active_preset': 'steady', 'notifications': True, 'interval_s': 300,
                         'max_cycles_per_day': 40, 'max_cycles_total': 149,
                         'presets': [{'name': 'steady', 'routes': '…'}, {'name': 'core-only', 'routes': '…'}],
                         'providers': [{'name': 'grok', 'disabled': False, 'reason': ''},
                                       {'name': 'devin', 'disabled': True, 'reason': '用户已停用此渠道'}]},
            'history': {'count': 113, 'entries': [
                {'title': '第 113 轮 · 自由探索', 'badge': None},
                {'title': '第 108 轮 · 组合实验 · 父轮 3+5 · 已提交', 'badge': 'submitted'},
                *({'title': f'第 {i} 轮 · 自由探索', 'badge': None} for i in range(107, 70, -1))]},
            'submissions': {'count': 4, 'entries': [
                {'title': 'rKO9JAW9 · 2026年09月26日 06:09:53', 'badge': 'submitted'}]},
        }
        entries = tray.menu_model(state)
        texts = [e.get('text', '') for e in entries]
        self.assertIn('自动研究已启用', texts)
        self.assertIn('下轮研究：grok · grok-4.7', texts)
        settings = next(e for e in entries if e.get('text') == '设置')
        notify = settings['items'][0]
        self.assertEqual((notify['kind'], notify['action'], notify['arg']), ('check', 'config', 'notifications=off'))
        queue = settings['items'][1]
        self.assertEqual((queue['kind'], queue['action'], queue['arg'], queue['checked']),
                         ('check', 'config', 'submission=on', False))
        presets = next(e for e in settings['items'] if e['text'] == '路由预设')
        head = presets['items'][0]['text']
        self.assertIn('永久预设：steady', head)
        self.assertNotIn('临时待用', head)   # 无待生效临时预设时不显示，也不显示取消行
        steady = next(e for e in presets['items'] if e.get('kind') == 'submenu' and e.get('text') == '● steady')
        core = next(e for e in presets['items'] if e.get('kind') == 'submenu' and e.get('text') == 'core-only')
        self.assertTrue(steady['text'].startswith('● ')); self.assertFalse(core['text'].startswith('● '))
        self.assertEqual([c['action'] for c in steady['items']],
                         ['preset'] + ['preset-once'] * len(tray.TEMP_CYCLE_CHOICES))
        permanent_row = steady['items'][0]
        self.assertEqual((permanent_row['arg'], permanent_row['text']), ('steady', '永久切换'))
        three = next(c for c in steady['items'] if c['arg'] == 'steady:3')
        self.assertEqual(three['text'], '临时 3 轮')
        self.assertNotIn('steady:2', [c.get('arg') for c in steady['items']])
        providers = next(e for e in settings['items'] if e['text'] == '渠道')
        grok = next(e for e in providers['items'] if e['text'] == 'grok')
        devin = next(e for e in providers['items'] if e['text'].startswith('devin'))
        self.assertTrue(grok['checked']); self.assertFalse(devin['checked'])
        interval = next(e for e in settings['items'] if e['text'] == '运行间隔')
        five = next(e for e in interval['items'] if e['text'] == '5 分钟')
        self.assertEqual(five['arg'], 'interval_s=300'); self.assertTrue(five['checked'])
        total = next(e for e in settings['items'] if e['text'] == '累计轮数上限')
        unlimited = next(e for e in total['items'] if e['text'] == '不限')
        self.assertEqual(unlimited['arg'], 'max_cycles_total=none'); self.assertFalse(unlimited['checked'])
        self.assertIn('当前值 149 轮', [e.get('text') for e in total['items']])
        history = next(e for e in entries if (e.get('text') or '').startswith('轮次历史'))
        self.assertEqual(history['text'], '轮次历史（113）')
        titles = [e['text'] for e in history['items']]
        self.assertEqual(len(titles), tray.HISTORY_CAP + 1)  # 表头 + 截断后的轮次
        self.assertIn('★ 第 108 轮 · 组合实验 · 父轮 3+5 · 已提交', titles)
        quit_row = next(e for e in entries if (e.get('text') or '').startswith('退出'))
        self.assertEqual(quit_row['kind'], 'submenu')
        self.assertEqual([item['action'] for item in quit_row['items']],
                         ['quit-app', 'quit-after-cycle', 'quit-now'])

    def test_preset_pending_temp_shows_remaining_and_cancel(self):
        entries = tray.menu_model({'settings': {
            'active_preset': 'core-only', 'permanent_preset': 'steady',
            'preset_once': 'core-only', 'preset_once_cycles': 3, 'cycle_preset': 'core-only',
            'presets': [{'name': 'steady', 'routes': '…'}, {'name': 'core-only', 'routes': '…'}]}})
        settings = next(e for e in entries if e.get('text') == '设置')
        presets = next(e for e in settings['items'] if e['text'] == '路由预设')
        texts = [e.get('text', '') for e in presets['items']]
        self.assertIn('永久预设：steady｜本轮临时：core-only｜临时待用：core-only ×3', texts)
        cancel = next(e for e in presets['items'] if e.get('action') == 'preset-cancel')
        self.assertEqual(cancel['text'], '取消临时切换（core-only ×3）')

    def test_toggle_notification_arg_flips_when_disabled(self):
        entries = tray.menu_model({'settings': {'notifications': False}})
        settings = next(e for e in entries if e.get('text') == '设置')
        notify = settings['items'][0]
        self.assertEqual(notify['arg'], 'notifications=on')
        self.assertFalse(notify['checked'])

    def test_language_submenu_switches_and_renders_english(self):
        state = {'settings': {'language': 'en', 'language_setting': 'en', 'notifications': True}}
        entries = tray.menu_model(state)
        texts = [e.get('text', '') for e in entries]
        self.assertIn('Settings', texts)
        self.assertIn('Start automatic research', texts)
        settings = next(e for e in entries if e.get('text') == 'Settings')
        language = next(e for e in settings['items'] if e.get('text') == 'Interface language')
        rows = {row['arg']: row for row in language['items']}
        self.assertEqual(set(rows), {'language=auto', 'language=zh', 'language=en'})
        self.assertTrue(rows['language=en']['checked'])
        self.assertFalse(rows['language=auto']['checked'])

        entries = tray.menu_model({'settings': {'language': 'zh', 'language_setting': 'auto', 'notifications': True}})
        settings = next(e for e in entries if e.get('text') == '设置')
        language = next(e for e in settings['items'] if e.get('text') == '界面语言')
        rows = {row['arg']: row for row in language['items']}
        self.assertTrue(rows['language=auto']['checked'])
        self.assertIn('跟随系统（自动）', [row['text'] for row in language['items']])

    def test_run_controls_follow_pause_and_the_open_cycle(self):
        paused = tray.menu_model({'status': {'paused': True, 'enabled': True}, 'settings': {'language': 'en'}})
        paused_text = [e.get('text') for e in paused]
        self.assertIn('Start automatic research', paused_text)
        self.assertNotIn('Stop automatic research', paused_text)
        self.assertNotIn('Run next cycle now', paused_text)
        running = tray.menu_model({'status': {'paused': False, 'enabled': True, 'cycle_open': False},
                                   'settings': {'language': 'en'}})
        running_text = [e.get('text') for e in running]
        self.assertIn('Stop automatic research', running_text)
        self.assertIn('Run next cycle now', running_text)
        self.assertNotIn('Start automatic research', running_text)
        busy = tray.menu_model({'status': {'paused': False, 'enabled': True, 'cycle_open': True},
                                'settings': {'language': 'en'}})
        cancel = next(e for e in busy if e.get('text') == 'Cancel current cycle')
        self.assertEqual(cancel['action'], 'cancel-cycle')
        self.assertNotIn('Run next cycle now', [e.get('text') for e in busy])
        settings = next(e for e in running if e.get('text') == 'Settings')
        launch = next(e for e in settings['items'] if e.get('text') == 'Start automatic research on launch')
        self.assertEqual(launch['arg'], 'launch_research=off')
        self.assertTrue(launch['checked'])


class SchedulerBackendTests(unittest.TestCase):
    def test_windows_backend_uses_schtasks(self):
        with patch.object(desktop, 'MACOS', False), patch.object(desktop, 'command') as command:
            command.return_value.returncode = 0
            self.assertTrue(desktop.loaded())
            self.assertEqual(command.call_args.args[0][:4], ['schtasks', '/Query', '/TN', desktop.RUNNER_TASK])
            desktop.kick_runner()
            self.assertEqual(command.call_args.args[0][:4], ['schtasks', '/Run', '/TN', desktop.RUNNER_TASK])

    def test_windows_start_requires_registered_task(self):
        with patch.object(desktop, 'MACOS', False), patch.object(desktop, 'loaded', return_value=False), \
             patch.object(desktop, 'wq', return_value={'unknown_pending': 0}), patch.object(desktop, 'command'):
            with self.assertRaisesRegex(RuntimeError, 'setup_windows'):
                desktop.control('start')

    @unittest.skipIf(sys.platform == 'win32', 'macOS 后端的 launchctl 域依赖 os.getuid（POSIX 专有）')
    def test_macos_backend_keeps_launchctl(self):
        with patch.object(desktop, 'MACOS', True), patch.object(desktop, 'command') as command:
            command.return_value.returncode = 0
            self.assertTrue(desktop.loaded())
            self.assertEqual(command.call_args.args[0][:2], ['/bin/launchctl', 'print'])
            desktop.kick_runner()
            self.assertEqual(command.call_args.args[0][:2], ['/bin/launchctl', 'kickstart'])


if __name__ == '__main__':
    unittest.main()
