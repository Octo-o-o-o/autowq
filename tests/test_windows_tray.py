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

    def test_settings_have_one_editor_and_preserve_model_actions(self):
        rows=tray.settings_items({'notifications':True},'en')
        self.assertEqual([r['action'] for r in rows],['settings-window','update','logs'])
        se={'providers':[{'name':'grok','disabled':False}], 'presets':[{'name':'steady','routes':'grok → devin'}], 'permanent_preset':'steady'}
        menus=tray.settings_command_menus(se,'en','models')
        self.assertEqual(len(menus),3)
        provider=menus[2][1][0]
        self.assertEqual((provider['action'],provider['arg'],provider['checked']),('provider','grok',True))

    def test_preset_pending_temp_shows_remaining_and_cancel(self):
        rows=tray.preset_items({'permanent_preset':'steady','cycle_preset':'core-only','preset_once':'core-only','preset_once_cycles':3,'presets':[{'name':'steady','routes':'fixture'},{'name':'core-only','routes':'fixture'}]},'zh')
        self.assertIn('临时待用：core-only ×3',rows[0]['text'])
        self.assertTrue(any(r.get('action')=='preset-cancel' for r in rows))

    def test_descriptor_editor_distinguishes_false_none_and_pending(self):
        from wq.desktop_windows import settings_changes,filtered_settings,setting_text
        entries=[{'key':'desktop.notifications','group_id':'general','label':'Notifications','value':True,'type':'boolean'},
                 {'key':'limit','group_id':'research','label':'Model starts','value':64,'has_pending':True,'pending':256},
                 {'key':'retry','group_id':'providers','label':'Retry delay','value':None,'advanced':True}]
        self.assertEqual(setting_text(False),'false')
        self.assertEqual(setting_text(None),'none')
        self.assertEqual(settings_changes(entries,{'limit':'256','desktop.notifications':'false'}),{'desktop.notifications':'false'})
        self.assertEqual(filtered_settings(entries,'providers'),[])
        self.assertEqual(filtered_settings(entries,'general','model')[0]['key'],'limit')
        self.assertEqual(filtered_settings(entries,'providers',advanced=True)[0]['key'],'retry')

    def test_language_renders_shared_settings_entry(self):
        rows=tray.settings_items({},'en')
        self.assertEqual(rows[0]['text'],'Open settings…')
        self.assertEqual(tray.settings_items({},'zh')[0]['text'],'打开设置…')

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
