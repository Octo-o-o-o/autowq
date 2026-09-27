import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import cli, store

spec = importlib.util.spec_from_file_location('desktop_control', Path(__file__).resolve().parents[1] / 'scripts/desktop_control.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopTests(unittest.TestCase):
    def test_graceful_pause_persists_and_prevents_claim_without_killing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, conn = make_env(tmp)
            tid, _ = store.enqueue_task(conn, 'agent_call', {}, 'queued')
            conn.commit()
            with patch('wq.cli.os.killpg') as kill, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--config', cfg.path, 'pause', '--graceful']), 0)
            kill.assert_not_called()
            self.assertTrue(store.is_paused(conn))
            self.assertIsNone(store.claim_task(conn, 'test'))
            self.assertEqual(conn.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0], 'queued')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--config',cfg.path,'resume']),0)
            self.assertEqual(store.claim_task(conn, 'test')['task_id'], tid)
            conn.close()

    def test_start_unknown_never_enables_or_loads_scheduler(self):
        with patch.object(desktop, 'wq', return_value={'unknown_pending': 1}) as call, patch.object(desktop, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
                desktop.control('start')
            self.assertEqual(call.call_count, 1)
            command.assert_not_called()

    def test_quit_pauses_and_does_not_unload_inflight_runner(self):
        with patch.object(desktop, 'wq', return_value={}) as call, patch.object(desktop, 'command') as command:
            desktop.control('quit')
            self.assertEqual(call.call_args.args[:2], ('pause','--graceful'))
            command.assert_not_called()

    def test_start_reuses_loaded_runner_without_kickstart(self):
        with patch.object(desktop,'loaded',return_value=True), patch.object(desktop,'wq',return_value={'unknown_pending':0}) as call, patch.object(desktop,'command') as command:
            desktop.control('start')
            self.assertEqual([x.args for x in call.call_args_list], [('status',),('autopilot','start','--json'),('resume',)])
            command.assert_not_called()


class SettingsControlTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.cfg, self.c = make_env(tmp.name); self.addCleanup(self.c.close)
        self.pin_language('zh')   # 文案断言固定中文，避免依赖测试机的系统语言
        self.data = {'default': 'p', 'providers': {'a': {'model': 'A'}, 'b': {'model': 'B'}},
                     'presets': {'p': {'routes': {'research': ['a', 'b'], 'review': ['b', 'a'], 'engineering': ['a']}},
                                 'q': {'routes': {'research': ['b'], 'review': ['a'], 'engineering': ['a']}}}}

    def pin_language(self, value):
        data = json.loads(Path(self.cfg.path).read_text())
        data.setdefault('ui', {})['language'] = value
        Path(self.cfg.path).write_text(json.dumps(data, ensure_ascii=False))

    def test_settings_snapshot_reports_routing_and_limits(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing._unavailable', return_value=None):
            result = desktop.control('settings')
        self.assertEqual(result['active_preset'], 'p')
        self.assertEqual([p['name'] for p in result['presets']], ['p', 'q'])
        self.assertIn('研究：a → b', result['presets'][0]['routes'])
        self.assertEqual(result['interval_s'], 3600)
        self.assertTrue(all(p['reason'] == '' for p in result['providers']))
        self.assertTrue(result['notifications'])                       # 默认开启

    def fresh_cfg(self):
        # 生产中每次调用都是新进程重新读盘；测试用 side_effect 复现该语义。
        from wq.config import Config
        return Config.load(self.cfg.path, os.path.dirname(os.path.dirname(self.cfg.path)))

    def test_notifications_action_consumes_events_and_respects_switch(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg):
            self.assertEqual(desktop.control('notifications'), {'items': []})   # 基线
        for key in ('desktop_notified_submissions', 'desktop_notified_failures'):
            store.set_flag(self.c, key, '2020-01-01T00:00:00.000+00:00')
        tid, _ = store.enqueue_task(self.c, 'agent_call', {})
        self.c.execute("UPDATE tasks SET status='failed',last_error='boom' WHERE task_id=?", (tid,))
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg):
            self.assertEqual(len(desktop.control('notifications')['items']), 1)
            desktop.control('config', 'notifications=off')
            tid2, _ = store.enqueue_task(self.c, 'reconcile', {})
            self.c.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (tid2,))
            self.assertEqual(desktop.control('notifications'), {'items': []})   # 关闭：不弹但不积压
            desktop.control('config', 'notifications=on')
            self.assertEqual(desktop.control('notifications'), {'items': []})   # 重开：不回放已消费事件

    def test_config_notifications_toggle_persists_and_validates(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing._unavailable', return_value=None):
            desktop.control('config', 'notifications=off')
            self.assertFalse(desktop.control('settings')['notifications'])
            desktop.control('config', 'notifications=on')
            self.assertTrue(desktop.control('settings')['notifications'])
            with self.assertRaises(ValueError):
                desktop.control('config', 'notifications=maybe')
        data = json.loads(Path(self.cfg.path).read_text())
        self.assertIs(data['desktop']['notifications'], True)

    def test_preset_switch_writes_flag(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            result = desktop.control('preset', 'q')
        self.assertIn('q', result['message'])
        self.assertEqual(store.get_flag(self.c, 'active_preset'), 'q')
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            with self.assertRaises(ValueError):
                desktop.control('preset', 'missing')

    def test_provider_toggle_round_trip(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            desktop.control('provider', 'a')
            self.assertEqual(store.get_flag(self.c, 'provider_disabled:a'), '1')
            desktop.control('provider', 'a')
            self.assertEqual(store.get_flag(self.c, 'provider_disabled:a'), '0')
            with self.assertRaises(ValueError):
                desktop.control('provider', 'missing')

    def test_config_updates_file_atomically_and_validates(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg):
            desktop.control('config', 'interval_s=900')
            desktop.control('config', 'max_cycles_total=none')
        data = json.loads(Path(self.cfg.path).read_text())
        self.assertEqual(data['autopilot']['interval_s'], 900)
        self.assertIsNone(data['autopilot']['max_cycles_total'])
        self.assertFalse(Path(str(self.cfg.path) + '.tmp').exists())
        with patch.object(desktop, '_cfg', return_value=self.cfg):
            for bad in ('interval_s=10', 'evil=1', 'interval_s=none', 'max_cycles_per_day=abc'):
                with self.assertRaises(ValueError):
                    desktop.control('config', bad)


class BrainAccountTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.cfg, self.c = make_env(tmp.name); self.addCleanup(self.c.close)
        data = json.loads(Path(self.cfg.path).read_text())
        data.setdefault('ui', {})['language'] = 'zh'   # 文案断言固定中文
        Path(self.cfg.path).write_text(json.dumps(data, ensure_ascii=False))

    def test_brain_bound_follows_session_file(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg):
            self.assertFalse(desktop.brain_bound())
            private = Path(self.cfg.resolve(self.cfg.get('paths', 'private_dir')))
            private.mkdir(parents=True, exist_ok=True)
            (private / 'brain-session.cookies').write_text('', encoding='utf-8')
            self.assertTrue(desktop.brain_bound())

    def test_brain_login_spawns_interactive_terminal(self):
        with patch.object(desktop.subprocess, 'Popen') as popen:
            result = desktop.control('brain-login')
        popen.assert_called_once()
        flat = ' '.join(popen.call_args.args[0])
        self.assertIn('brain', flat)
        self.assertIn('login', flat)
        self.assertIn('message', result)

    def test_brain_login_without_terminal_raises(self):
        with patch.object(desktop, 'spawn_login_terminal', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'wq brain login'):
                desktop.control('brain-login')

    def test_brain_check_reports_success_and_failure(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), \
             patch.object(desktop, 'wq', return_value={'http_status': 200, 'allow': 'POST'}):
            self.assertIn('核验通过', desktop.control('brain-check')['message'])
        with patch.object(desktop, '_cfg', return_value=self.cfg), \
             patch.object(desktop, 'wq', side_effect=RuntimeError('未登录或会话过期')):
            message = desktop.control('brain-check')['message']
            self.assertIn('核验未通过', message)
            self.assertIn('会话过期', message)

    def test_brain_register_opens_official_page(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), \
             patch.object(desktop.webbrowser, 'open') as open_url:
            result = desktop.control('brain-register')
        open_url.assert_called_once_with(desktop.BRAIN_REGISTER_URL)
        self.assertIn('注册页', result['message'])


class LanguageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.cfg, self.c = make_env(tmp.name); self.addCleanup(self.c.close)
        self.data = {'default': 'p', 'providers': {'a': {'model': 'A'}},
                     'presets': {'p': {'routes': {'research': ['a'], 'review': ['a'], 'engineering': ['a']}}}}

    def fresh_cfg(self):
        from wq.config import Config
        return Config.load(self.cfg.path, os.path.dirname(os.path.dirname(self.cfg.path)))

    def settings(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing._unavailable', return_value=None):
            return desktop.control('settings')

    def set_language(self, value):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg):
            return desktop.control('config', f'language={value}')

    def test_settings_report_language_fields(self):
        snapshot = self.settings()
        self.assertEqual(snapshot['language_setting'], 'auto')
        self.assertIn(snapshot['language'], ('zh', 'en'))

    def test_language_switch_persists_and_renders(self):
        self.assertEqual(self.set_language('en')['language_setting'], 'en')
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch.object(desktop, 'wq', return_value={'unknown_pending': 0}), \
             patch.object(desktop, 'loaded', return_value=True):
            self.assertIn('Automatic research started', desktop.control('start')['message'])
        self.assertEqual(self.settings()['language_setting'], 'en')
        self.set_language('zh')
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch.object(desktop, 'wq', return_value={'unknown_pending': 0}), \
             patch.object(desktop, 'loaded', return_value=True):
            self.assertIn('已开始自动运行', desktop.control('start')['message'])
        self.set_language('auto')
        self.assertEqual(self.settings()['language_setting'], 'auto')

    def test_language_rejects_unknown_values(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg):
            with self.assertRaises(ValueError):
                desktop.control('config', 'language=fr')

    def test_status_message_translates_known_ledger_text(self):
        auto = {'enabled': True, 'message': '等待现有任务完成', 'next_cycle_at': None,
                'last_tick_at': None, 'total_cycles': 1, 'max_cycles_total': None}
        self.set_language('en')
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch.object(desktop, 'wq', side_effect=[{'paused': False, 'unknown_pending': 0}, auto]), \
             patch.object(desktop, 'loaded', return_value=True), \
             patch('wq.desktop.connect_readonly', return_value=self.c), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing.active_preset', return_value='p'), \
             patch('wq.routing._unavailable', return_value=None):
            result = desktop.control('status')
        self.assertIn('Waiting for existing tasks', result['message'])
        self.assertEqual(result['language'], 'en')
