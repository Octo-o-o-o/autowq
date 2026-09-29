"""原生 Windows：只支持 API 渠道；打包 exe 自代理调度、API 调用与 CLI。平台用参数或 patch 模拟。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wq import onboard, providers, windows_setup  # noqa: E402
from wq import desktop_control  # noqa: E402

EXE = r'C:\Program Files\WorldQuant\WorldQuantTray.exe'


class WindowsOnboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / 'ws'; self.root.mkdir()
        self.runtime = base / 'runtime'

    def test_api_only_configuration(self):
        cfg = onboard.configure(self.root, self.runtime, ['openai', 'anthropic'],
                                {'openai': 'gpt-x', 'anthropic': 'claude-x'}, {},
                                {'research': 'openai', 'review': 'anthropic', 'engineering': 'anthropic'},
                                {}, 'win32')
        profiles = json.loads((self.root / 'config/profiles.json').read_text())
        self.assertEqual(set(profiles['providers']), {'openai', 'anthropic'})
        self.assertTrue(all(p['transport']['kind'] == 'api' for p in profiles['providers'].values()))
        self.assertEqual(cfg['onboarding']['platform'], 'win32')
        self.assertEqual(cfg['routing']['work_root'], str((self.runtime / 'jobs').resolve()))
        self.assertTrue((self.root / 'config/autopilot-policy.json').exists())
        self.assertFalse((self.runtime / 'launchers').exists())

    def test_cli_provider_is_refused_before_writing(self):
        with self.assertRaisesRegex(ValueError, 'API providers only'):
            onboard.configure(self.root, self.runtime, ['claude', 'openai'], {'claude': 'x', 'openai': 'y'},
                              {'claude': sys.executable},
                              {'research': 'claude', 'review': 'openai', 'engineering': 'openai'}, {}, 'win32')
        self.assertFalse((self.root / 'config/config.json').exists())
        self.assertFalse(self.runtime.exists())


class FrozenExeTests(unittest.TestCase):
    def setUp(self):
        for target, name, value in [(sys, 'frozen', True), (sys, 'executable', EXE)]:
            p = patch.object(target, name, value, create=True); p.start(); self.addCleanup(p.stop)
        self.root = Path(tempfile.mkdtemp()); self.addCleanup(lambda: __import__('shutil').rmtree(self.root))

    def test_api_transport_dispatches_through_exe(self):
        item = {'transport': providers.api_definition('openai', 'gpt-x')}
        argv = providers.runtime_argv(None, item)
        self.assertEqual(argv[:2], [EXE, '--provider-runtime'])
        self.assertEqual(argv[-1], '{prompt}')

    def test_scheduler_and_autostart_point_at_exe(self):
        tr = windows_setup.runner_task_args(self.root, EXE)
        tr = tr[tr.index('/TR') + 1]
        self.assertEqual(tr, f'"{EXE}" --workspace "{self.root}" --scheduled-run')
        self.assertEqual(windows_setup.tray_run_value(self.root, EXE), f'"{EXE}" --workspace "{self.root}"')

    def test_register_runner_skips_python_wrapper(self):
        with patch.object(windows_setup.subprocess, 'run') as run:
            run.return_value.returncode = 0
            windows_setup.register_runner(self.root, EXE)
        self.assertFalse((self.root / 'var/run/wq_runner.pyw').exists())
        self.assertIn('--scheduled-run', run.call_args.args[0][5])

    def test_start_registers_task_instead_of_asking_for_source_setup(self):
        with patch.object(desktop_control, 'MACOS', False), patch.object(desktop_control, 'ROOT', self.root), \
             patch('wq.windows_setup.register_runner') as register:
            desktop_control.ensure_runner()
        register.assert_called_once_with(self.root)



class WindowsLanguageTests(unittest.TestCase):
    def test_windows_locale_name_counts_as_chinese(self):
        from wq import i18n
        with patch.object(i18n.sys, 'platform', 'linux'), \
             patch.object(i18n.locale, 'getlocale', return_value=('Chinese (Simplified)_China', '936')):
            self.assertEqual(i18n.default_language({}), 'zh')


if __name__ == '__main__':
    unittest.main()
