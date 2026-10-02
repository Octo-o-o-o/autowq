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

    def test_quit_app_leaves_the_runner_and_the_queue_alone(self):
        with patch.object(desktop, 'wq') as call, patch.object(desktop, 'command') as command:
            result = desktop.control('quit-app')
            self.assertTrue('研究继续' in result['message'] or 'research continues' in result['message'])
            call.assert_not_called()
            command.assert_not_called()

    def test_start_reuses_loaded_runner_without_kickstart(self):
        with patch.object(desktop,'loaded',return_value=True), patch.object(desktop,'wq',return_value={'unknown_pending':0}) as call, patch.object(desktop,'command') as command:
            desktop.control('start')
            self.assertEqual([x.args for x in call.call_args_list], [('status',),('autopilot','start','--json'),('resume',)])
            command.assert_not_called()


class UpdateCheckTests(unittest.TestCase):
    def test_identity_line_uses_name_then_level(self):
        view = desktop._identity_view({'bound': True, 'nickname': 'Alex', 'email': 'a@b.c', 'level': 'GOLD', 'geniusLevel': 2}, 'zh')
        self.assertEqual(view['title'], 'Alex · GOLD · Genius 2')
        self.assertIn('a@b.c', view['detail'])
        scored = desktop._identity_view({'bound': True, 'nickname': 'Alex', 'email': 'a@b.c', 'level': 'SILVER', 'score': 9674}, 'zh')
        self.assertEqual(scored['title'], 'Alex · SILVER · 分数 9674')
        self.assertIn('分数 9674', scored['detail'])
        english = desktop._identity_view({'bound': True, 'nickname': 'Alex', 'level': 'SILVER', 'score': '9674.0'}, 'en')
        self.assertEqual(english['title'], 'Alex · SILVER · Score 9674.0')
        missing = desktop._identity_view({'bound': False}, 'en')
        self.assertEqual(missing['title'], 'Not signed in')

    def test_leaderboard_score_prefers_challenge_and_drops_fraction(self):
        data = {'results': [
            {'id': 'other', 'leaderboard': {'score': 10}},
            {'id': 'challenge', 'scoring': 'CHALLENGE', 'leaderboard': {'score': 9674.0}},
        ]}
        self.assertEqual(desktop._leaderboard_score(data), '9674')
        self.assertEqual(desktop._leaderboard_score({'results': [{'id': 'cup', 'leaderboard': {'score': 3.5}}]}), '3.5')
        self.assertIsNone(desktop._leaderboard_score({'results': []}))

    def test_identity_refreshes_after_local_eight_or_submission_not_on_a_warm_cache(self):
        import datetime as dt
        beijing = dt.timezone(dt.timedelta(hours=8))
        morning = dt.datetime(2026, 9, 29, 7, 30, tzinfo=beijing)
        later = dt.datetime(2026, 9, 29, 10, 0, tzinfo=beijing)
        after_yesterday = {'queried_at': '2026-09-28T23:30:00+00:00'}  # 北京 07:30，已过昨天 8 点
        before_today = {'queried_at': '2026-09-28T23:00:00+00:00'}    # 北京 07:00
        self.assertFalse(desktop.identity_due(after_yesterday, morning))
        self.assertTrue(desktop.identity_due(before_today, later))
        self.assertFalse(desktop.identity_due({'queried_at': '2026-09-29T01:30:00+00:00'}, later))
        self.assertTrue(desktop.identity_due({'queried_at': '2026-09-29T01:30:00+00:00'}, later, '2026-09-29T02:00:00+00:00'))
        self.assertTrue(desktop.identity_due(None, later))

    def test_version_tuple_orders_releases(self):
        self.assertLess(desktop._version_tuple('0.2.5'), desktop._version_tuple('0.2.6'))
        self.assertFalse(desktop._version_tuple('v0.2.6') < desktop._version_tuple('0.2.6'))

    def test_update_check_does_not_use_the_python_urllib_agent(self):
        from wq import __version__
        body = json.dumps({'version': __version__}).encode()

        class Response:
            def read(self):
                return body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        seen = {}

        def opener(req, timeout=15):
            seen['ua'] = req.get_header('User-agent')
            return Response()

        with patch('urllib.request.urlopen', opener):
            result = desktop.check_update('en')
        self.assertTrue(seen['ua'].startswith('autowq/'))
        self.assertNotIn('Python-urllib', seen['ua'])
        self.assertFalse(result['update'])
        self.assertIn('latest', result['message'].lower())

    def test_warm_identity_cache_does_not_call_brain(self):
        from wq import util
        with tempfile.TemporaryDirectory() as tmp:
            cfg, _conn = make_env(tmp)
            private = Path(cfg.private_dir) / 'brain-account'
            private.mkdir(parents=True)
            (Path(cfg.private_dir) / 'brain-session.cookies').write_text('session')
            cache = private / 'menu-identity.json'
            cache.write_text(json.dumps({'bound': True, 'queried_at': util.now_iso(),
                                         'nickname': 'Alex', 'level': 'SILVER', 'score': '9674'}))
            with patch.object(desktop, '_fetch_identity', side_effect=AssertionError('should stay cached')):
                view, refreshed = desktop.menu_identity(cfg, 'zh')
            self.assertFalse(refreshed)
            self.assertEqual(view['title'], 'Alex · SILVER · 分数 9674')
            with patch.object(desktop, '_fetch_identity', return_value={
                    'bound': True, 'queried_at': util.now_iso(), 'nickname': 'Alex', 'level': 'SILVER', 'score': '9700'}):
                again, refreshed = desktop.menu_identity(cfg, 'zh', refresh=True)
            self.assertTrue(refreshed)
            self.assertEqual(again['title'], 'Alex · SILVER · 分数 9700')


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
        self.assertEqual(result['presets'][0]['research'], 'a · A')
        self.assertEqual(result['presets'][0]['review'], 'b · B')
        self.assertEqual(result['presets'][1]['research'], 'b · B')
        self.assertEqual(result['presets'][1]['review'], 'a · A')
        self.assertEqual(result['interval_s'], 3600)
        self.assertTrue(all(p['reason'] == '' for p in result['providers']))
        self.assertTrue(result['notifications'])                       # 默认开启

    def test_config_lane_pin_writes_validates_and_clears(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing._unavailable', return_value=None):
            result = desktop.control('config', 'lane_pin=2:b:a')
            self.assertIn('泳道2', result['message'])
            settings = desktop.control('settings')
            self.assertEqual(settings['lane_pins'], {1: {'research': 'b', 'review': 'a'}})
            self.assertEqual({(o['research'], o['review']) for o in settings['lane_pair_options']},
                             {('a', 'b'), ('b', 'a')})
            for bad in ('lane_pin=9:a:b', 'lane_pin=2:a:a', 'lane_pin=2:a:c', 'lane_pin=x:a:b'):
                with self.assertRaises(ValueError):
                    desktop.control('config', bad)
            desktop.control('config', 'lane_pin=2:off')
            self.assertEqual(desktop.control('settings')['lane_pins'], {})

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
            self.assertFalse(desktop.control('settings')['automatic_submission'])
            self.assertFalse(desktop.control('settings')['submission_enabled'])
            desktop.control('config', 'submission=on')
            self.assertTrue(desktop.control('settings')['submission_enabled'])
            self.assertFalse(desktop.control('settings')['automatic_submission'])
            desktop.control('config', 'submission=off')
            self.assertFalse(desktop.control('settings')['submission_enabled'])
            desktop.control('config', 'model_spend_cap_usd=20')
            snap = desktop.control('settings')
            self.assertEqual(snap['spend_cap_usd'], 20)
            self.assertEqual(snap['spend_known_usd'], 0)
            desktop.control('config', 'model_spend_cap_usd=none')
            self.assertIsNone(desktop.control('settings')['spend_cap_usd'])
            with self.assertRaises(ValueError):
                desktop.control('config', 'notifications=maybe')
        data = json.loads(Path(self.cfg.path).read_text())
        self.assertIs(data['desktop']['notifications'], True)

    def test_preset_switch_refuses_while_a_route_head_is_off(self):
        store.set_flag(self.c, 'provider_disabled:a', '1')
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            with self.assertRaisesRegex(ValueError, '还不能切换到 q'):
                desktop.control('preset', 'q')
            with self.assertRaisesRegex(ValueError, 'a · A'):
                desktop.control('preset-once', 'q:5')
        self.assertNotEqual(store.get_flag(self.c, 'active_preset'), 'q')
        self.assertFalse(store.get_flag(self.c, 'preset_once'))
        store.set_flag(self.c, 'provider_disabled:a', '0')
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            result = desktop.control('preset-once', 'q:2')
        self.assertIn('接下来 2 个新建轮次', result['message'])
        self.assertEqual(store.get_flag(self.c, 'preset_once'), 'q')

    def test_preset_switch_writes_flag(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            result = desktop.control('preset', 'q')
        self.assertIn('q', result['message'])
        self.assertEqual(store.get_flag(self.c, 'active_preset'), 'q')
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            with self.assertRaises(ValueError):
                desktop.control('preset', 'missing')

    def test_preset_once_parses_cycles_suffix_and_snapshots_remaining(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing._unavailable', return_value=None):
            result = desktop.control('preset-once', 'q:3')
            self.assertIn('接下来 3 个新建轮次', result['message'])
            snapshot = desktop.control('settings')
            desktop.control('preset-once', 'q')   # 无后缀按 1 轮，覆盖旧登记
            after = desktop.control('settings')
        self.assertEqual((store.get_flag(self.c, 'preset_once'), store.get_flag(self.c, 'preset_once_cycles')), ('q', '1'))
        self.assertEqual((snapshot['preset_once'], snapshot['preset_once_cycles']), ('q', 3))
        self.assertEqual(after['preset_once_cycles'], 1)

    def test_preset_cancel_reports_and_clears(self):
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=self.data):
            self.assertIn('没有待生效', desktop.control('preset-cancel')['message'])
            desktop.control('preset-once', 'q:5')
            result = desktop.control('preset-cancel')
        self.assertIn('已取消临时预设 q', result['message'])
        self.assertEqual((store.get_flag(self.c, 'preset_once') or None, store.get_flag(self.c, 'preset_once_cycles') or None), (None, None))

    def test_provider_toggle_round_trip(self):
        spare = {'default': 'p', 'providers': {'a': {'model': 'A'}, 'b': {'model': 'B'}, 'c': {'model': 'C'}},
                 'presets': {'p': {'routes': {'research': ['b', 'c'], 'review': ['b'], 'engineering': ['b']}}}}
        store.set_flag(self.c, 'active_preset', 'p')
        with patch.object(desktop, '_cfg', return_value=self.cfg), patch('wq.routing.catalog', return_value=spare):
            desktop.control('provider', 'c')
            self.assertEqual(store.get_flag(self.c, 'provider_disabled:c'), '1')
            desktop.control('provider', 'c')
            self.assertEqual(store.get_flag(self.c, 'provider_disabled:c'), '0')
            with self.assertRaisesRegex(ValueError, '还不能关闭'):
                desktop.control('provider', 'b')
            self.assertNotEqual(store.get_flag(self.c, 'provider_disabled:b'), '1')
            store.set_flag(self.c, 'provider_disabled:a', '1')
            with self.assertRaisesRegex(ValueError, '还不能把'):
                desktop.control('provider-role', 'research:a')
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
        self.assertFalse(result['cycle_open'])
        self.assertTrue(result['enabled'])

    def test_menu_pause_reason_is_english_when_the_ui_is_english(self):
        auto = {'enabled': True, 'message': '全部任务已暂停：菜单栏退出', 'next_cycle_at': None,
                'last_tick_at': None, 'total_cycles': 1, 'max_cycles_total': None}
        self.set_language('en')
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch.object(desktop, 'wq', side_effect=[{'paused': True, 'pause_reason': '菜单栏退出', 'unknown_pending': 0}, auto]), \
             patch.object(desktop, 'loaded', return_value=True), \
             patch('wq.desktop.connect_readonly', return_value=self.c), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing.active_preset', return_value='p'), \
             patch('wq.routing._unavailable', return_value=None):
            result = desktop.control('status')
        self.assertNotRegex(result['message'], r'[\u4e00-\u9fff]')
        self.assertIn('menu bar', result['message'].lower())

    def test_launch_research_defaults_on_and_can_be_turned_off(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch('wq.routing.catalog', return_value=self.data), \
             patch('wq.routing._unavailable', return_value=None):
            self.assertTrue(desktop.control('settings')['launch_research'])
            desktop.control('config', 'launch_research=off')
            self.assertFalse(desktop.control('settings')['launch_research'])
            desktop.control('config', 'launch_research=on')
            self.assertTrue(desktop.control('settings')['launch_research'])

    def test_cancel_open_cycle_stops_local_work_without_pausing(self):
        from wq import autopilot, util
        autopilot.setup(self.c)
        tid, _ = store.enqueue_task(self.c, 'agent_call', {'autopilot_cycle': 1})
        now = util.now_iso()
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,research_task,created_at,updated_at) VALUES('researching','{}','x',?,?,?)",
                       (tid, now, now))
        result = autopilot.cancel_open_cycle(self.c, self.cfg)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.c.execute("SELECT state, outcome FROM research_cycles").fetchone()[0], 'closed')
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?', (tid,)).fetchone()[0], 'aborted')
        self.assertFalse(store.is_paused(self.c))
        self.assertEqual(autopilot.cancel_open_cycle(self.c, self.cfg)['reason'], 'idle')

    def test_launch_leaves_a_running_task_alone(self):
        def fake(*args):
            if args == ('status',):
                return {'paused': False, 'pause_reason': '', 'unknown_pending': 0,
                        'live_agent_calls': [{'call_id': 'running'}]}
            return {'latest_cycle': {'state': 'researching'}, 'enabled': True}
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch.object(desktop, 'loaded', return_value=True), \
             patch.object(desktop, 'wq', side_effect=fake) as call:
            result = desktop.control('launch')
        self.assertFalse(result['started'])
        self.assertEqual([c.args for c in call.call_args_list], [('status',)])

    def test_launch_keeps_an_explicit_stop(self):
        with patch.object(desktop, '_cfg', side_effect=self.fresh_cfg), \
             patch.object(desktop, 'loaded', return_value=True), \
             patch.object(desktop, 'wq', return_value={'paused': True, 'pause_reason': 'menu:stop-now', 'unknown_pending': 0}) as call:
            result = desktop.control('launch')
        self.assertFalse(result['started'])
        self.assertEqual(call.call_args.args, ('status',))

    def test_stop_after_cycle_waits_for_the_open_cycle(self):
        from wq import autopilot, util
        autopilot.setup(self.c)
        now = util.now_iso()
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('researching','{}','x',?,?)",
                       (now, now))
        result = autopilot.stop_after_cycle(self.c)
        self.assertTrue(result['deferred'])
        self.assertFalse(store.is_paused(self.c))
        self.assertEqual(store.get_flag(self.c, 'autopilot_stop_after_cycle'), '1')
        self.c.execute("UPDATE research_cycles SET state='closed'")
        self.assertTrue(autopilot.apply_deferred_stop(self.c))
        self.assertTrue(store.is_paused(self.c))
        self.assertEqual(store.get_flag(self.c, 'pause_reason'), 'menu:stop-after-cycle')

    def test_stop_now_closes_local_work_and_leaves_a_sent_simulation(self):
        from wq import autopilot, brain_jobs, util
        autopilot.setup(self.c)
        local, _ = store.enqueue_task(self.c, 'agent_call', {})
        remote, _ = store.enqueue_task(self.c, 'brain_simulation', {})
        self.c.execute("UPDATE tasks SET status='running' WHERE task_id=?", (remote,))
        now = util.now_iso()
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,research_task,simulation_task,created_at,updated_at) VALUES('simulating','{}','x',?,?,?,?)",
                       (local, remote, now, now))
        brain_jobs.setup(self.c)
        self.c.execute("INSERT INTO brain_runs(task_id,state,started_at,updated_at) VALUES(?,'polling',?,?)", (remote, now, now))
        result = autopilot.stop_now(self.c, self.cfg)
        self.assertEqual(result['remote_left'], 1)
        self.assertEqual(self.c.execute('SELECT state FROM research_cycles').fetchone()[0], 'closed')
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?', (local,)).fetchone()[0], 'aborted')
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?', (remote,)).fetchone()[0], 'running')
        self.assertTrue(store.is_paused(self.c))
        self.assertEqual(store.get_flag(self.c, 'pause_reason'), 'menu:stop-now')

    def test_cancel_leaves_an_in_flight_platform_simulation_alone(self):
        from wq import autopilot, brain_jobs, util
        autopilot.setup(self.c)
        tid, _ = store.enqueue_task(self.c, 'brain_simulation', {})
        self.c.execute("UPDATE tasks SET status='running' WHERE task_id=?", (tid,))
        now = util.now_iso()
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,simulation_task,created_at,updated_at) VALUES('simulating','{}','x',?,?,?)",
                       (tid, now, now))
        brain_jobs.setup(self.c)
        self.c.execute("INSERT INTO brain_runs(task_id,state,started_at,updated_at) VALUES(?,'polling',?,?)", (tid, now, now))
        result = autopilot.cancel_open_cycle(self.c, self.cfg)
        self.assertFalse(result['cancelled'])
        self.assertEqual(result['reason'], 'remote')
        self.assertEqual(self.c.execute('SELECT state FROM research_cycles').fetchone()[0], 'simulating')
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?', (tid,)).fetchone()[0], 'running')
