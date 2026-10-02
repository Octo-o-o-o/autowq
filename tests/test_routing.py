import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from helpers import make_env
from wq import routing, runner, store, util
from wq.wrappers.agent import AgentSpec, run_agent


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg, self.conn = make_env(self.tmp.name, {
            'routing': {'profiles_file': 'config/profiles.json', 'work_root': str(self.root/'jobs')},
            'models': {n: {'enabled': True} for n in ['a','b']},
            'budgets': {n: {'enabled': True, 'remaining': 100, 'unit': 'calls'} for n in ['a','b']}})
        self.addCleanup(self.conn.close)
        self.script = self.root/'stub.py'
        self.script.write_text('''import json,sys\nfrom pathlib import Path\nmode=sys.argv[1]\nif mode=='fail':\n Path('partial.txt').write_text('evidence')\n print('quota exhausted')\n sys.exit(9)\nPath('result.json').write_text(json.dumps({'status':mode,'summary':'stub','findings':[]}))\n''')
        self.data = {'default':'first', 'providers': {
            'a': {'argv': [sys.executable, str(self.script), 'fail']},
            'b': {'argv': [sys.executable, str(self.script), 'completed']}},
            'presets': {n: {'retries':3,'retry_delays_s':[0,0,0],
                           'routes': {r: chain for r in routing.ROLES}}
                        for n,chain in [('first',['a','b']),('second',['b','a'])]}}
        self.write_config()
        self.prompt=self.root/'request.md'; self.prompt.write_text('Read provided inputs only.')

    def write_config(self):
        (self.root/'config/profiles.json').write_text(json.dumps(self.data))

    def enqueue(self):
        return routing.enqueue_job(self.conn,self.cfg,'engineering',self.prompt)

    def status(self,tid):
        return self.conn.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0]

    def tick(self):
        return runner.run_once(self.conn,self.cfg,lease_s=3600)

    def test_three_retries_then_fallback_and_preserve_evidence(self):
        tid,path=self.enqueue()
        for _ in range(4):
            self.assertEqual(self.tick()[0],0)
            self.assertEqual(self.status(tid),'queued')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0],4)
        self.assertEqual(len(list(Path(path).glob('00-a-attempt-*/partial.txt'))),4)
        self.tick()
        self.assertEqual(self.status(tid),'succeeded')
        self.assertEqual(json.loads((Path(path)/'result.json').read_text())['status'],'completed')

    def test_single_attempt_does_not_expand_into_retries_or_provider_fallback(self):
        tid, path = self.enqueue()
        row = self.conn.execute('SELECT payload_json FROM tasks WHERE task_id=?',(tid,)).fetchone()
        payload = json.loads(row[0]); payload['single_attempt'] = True
        self.conn.execute('UPDATE tasks SET payload_json=? WHERE task_id=?',(json.dumps(payload),tid))
        self.tick()
        self.assertEqual(self.status(tid),'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT max_attempts FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],1)
        self.assertEqual(len(list(Path(path).glob('*-attempt-*'))),1)
        self.tick()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],1)

    def test_invalid_envelope_records_actual_validation_failure(self):
        self.script.write_text("from pathlib import Path\nPath('result.json').write_text('{\"status\":\"completed\"}')\n")
        tid, _ = self.enqueue()
        for _ in range(4): self.tick()
        self.assertEqual(self.status(tid),'failed')
        row=self.conn.execute("SELECT detail_json FROM attempts WHERE task_id=? AND event='artifact_validation' ORDER BY attempt_id DESC LIMIT 1",(tid,)).fetchone()
        self.assertEqual(json.loads(row[0])['call_status'],'artifact_invalid')

    def test_profile_switch_does_not_change_existing_retry_chain(self):
        tid,_=self.enqueue();self.tick()
        routing.choose_preset(self.conn,self.cfg,'second')
        # 从磁盘重新加载会话和配置后的下一轮仍走旧快照。
        self.data['providers']['a']['argv'][-1]='completed'; self.write_config()
        self.tick()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0],2)
        for _ in range(3):self.tick()
        new,_=self.enqueue();self.tick()
        snap=json.loads(self.conn.execute('SELECT snapshot_json FROM task_routes WHERE task_id=?',(new,)).fetchone()[0])
        self.assertEqual(snap['preset'],'second')
        self.assertEqual(self.status(new),'succeeded')

    def test_research_blocked_does_not_retry(self):
        self.data['providers']['a']['argv'][-1]='blocked';self.write_config()
        tid,_=self.enqueue();self.tick();self.tick()
        self.assertEqual(self.status(tid),'blocked')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0],1)

    def test_disable_channel_skips_without_call(self):
        store.set_flag(self.conn,'provider_disabled:a','1')
        tid,_=self.enqueue();self.tick()
        self.assertEqual(self.status(tid),'succeeded')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0],0)

    def test_input_changed_blocks_retry(self):
        tid,path=self.enqueue();self.tick()
        (Path(path)/'packet/request.md').write_text('changed')
        self.tick();self.assertEqual(self.status(tid),'blocked')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0],1)

    def test_pause_does_not_consume_retry(self):
        tid,_=self.enqueue();self.tick()
        store.set_flag(self.conn,'paused','1')
        self.assertEqual(self.tick()[0],6)
        self.assertEqual(self.conn.execute('SELECT retry_index FROM task_routes WHERE task_id=?',(tid,)).fetchone()[0],1)

    def test_all_providers_exhausted_is_terminal(self):
        self.data['providers']['b']['argv'][-1]='fail';self.write_config()
        tid,_=self.enqueue()
        for _ in range(8):self.tick()
        self.assertEqual(self.status(tid),'failed')
        self.tick()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0],8)

    def test_interrupted_dispatch_is_unknown_not_replayed(self):
        tid,_=self.enqueue();self.tick()
        self.conn.execute("UPDATE task_routes SET phase='running' WHERE task_id=?",(tid,))
        self.tick();self.assertEqual(self.status(tid),'unknown')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0],1)

    def test_budget_counts_failed_real_calls(self):
        self.cfg.data['budgets']['a']['remaining']=1
        tid,_=self.enqueue();self.tick();self.tick()
        self.assertEqual(self.status(tid),'succeeded')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0],1)

    def test_symlink_inputs_rejected(self):
        inputs=self.root/'input';inputs.mkdir();(inputs/'bad').symlink_to(self.prompt)
        with self.assertRaises(ValueError):routing.enqueue_job(self.conn,self.cfg,'research',self.prompt,inputs)

    def test_pause_aborted_call_is_not_overwritten(self):
        import subprocess
        original_wait = subprocess.Popen.wait
        def pause_wait(proc, timeout=None):
            result = original_wait(proc, timeout=timeout)
            store.set_flag(self.conn,'paused','1')
            return result
        prompt=self.root/'p';prompt.write_text('x')
        spec=AgentSpec('a',[sys.executable,'-c','pass'],str(self.root),artifacts=['missing.json'])
        with patch('subprocess.Popen.wait',pause_wait):
            out=run_agent(self.conn,self.cfg,spec,str(prompt),'pause-test',allow=True)
        self.assertEqual(out.status,'aborted')
        self.assertEqual(self.conn.execute('SELECT status FROM agent_calls WHERE call_id=?',(out.call_id,)).fetchone()[0],'aborted')

    def test_retry_after_is_not_shortened(self):
        self.script.write_text("import sys\nprint('Retry-After: 120')\nsys.exit(1)\n")
        tid,_=self.enqueue();before=util.now();self.tick()
        row=self.conn.execute('SELECT not_before FROM tasks WHERE task_id=?',(tid,)).fetchone()
        self.assertGreaterEqual((util.parse_iso(row[0])-before).total_seconds(),120)

    def test_expired_window_skips_provider_without_spawning(self):
        now=util.now()
        self.cfg.data['debug_authorization']={'enabled':True,'unlimited':True,'agents':['a'],
            'starts_at':(now-dt.timedelta(days=2)).isoformat(),
            'expires_at':(now-dt.timedelta(days=1)).isoformat(),'evidence':'test'}
        tid,_=self.enqueue();self.tick()
        self.assertEqual(self.status(tid),'succeeded')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0],0)

    def test_invalid_json_retries_in_new_directory(self):
        self.script.write_text("from pathlib import Path\nPath('result.json').write_text('{broken')\n")
        tid,path=self.enqueue();self.tick();self.tick()
        self.assertEqual(self.status(tid),'queued')
        self.assertEqual(len(list(Path(path).glob('00-a-attempt-*/result.json'))),2)

    def test_terminal_marker_required(self):
        from wq.wrappers.agent import _verify_terminal
        f=self.root/'log';f.write_text('{"type":"result","subtype":"success","is_error":false}')
        self.assertTrue(_verify_terminal(str(f),'cursor')[0])
        f.write_text('{"type":"result","subtype":"success","is_error":true}')
        self.assertFalse(_verify_terminal(str(f),'cursor')[0])
        f.write_text('{"stopReason":"max_turns"}')
        self.assertFalse(_verify_terminal(str(f),'grok')[0])
        # ZCode 无头 --json 终态：{"type":"result",...,"projection":{"status":"completed"}}
        f.write_text('{"type":"result","sessionId":"sess_1","response":"ok","projection":{"status":"completed","turnCount":2}}')
        self.assertTrue(_verify_terminal(str(f),'zcode')[0])
        f.write_text('{"type":"result","projection":{"status":"failed"}}')
        self.assertFalse(_verify_terminal(str(f),'zcode')[0])
        f.write_text('{"type":"assistant","text":"partial"}')
        self.assertFalse(_verify_terminal(str(f),'zcode')[0])
        f.write_text('ZCode Built-in skipped (not-due)\n'
                     '{"sessionId":"sess_1","response":"done","projection":{"status":"idle","turnCount":1}}')
        self.assertTrue(_verify_terminal(str(f),'zcode')[0])
        f.write_text('{"sessionId":"sess_1","response":"  ","projection":{"status":"idle","turnCount":1}}')
        self.assertFalse(_verify_terminal(str(f),'zcode')[0])

    def test_capacity_patterns(self):
        for tail in ('[1310] Weekly/Monthly Limit Exhausted',
                     'API Error: quota exceeded for this key',
                     'rate limit reached', '余额不足', '429 too many requests'):
            self.assertTrue(routing._CAPACITY_RE.search(tail), tail)
        for tail in ('max_turns reached', 'exit 1', 'JSON decode error',
                     'turn limit exhausted', 'context token limit exhausted'):
            self.assertFalse(routing._CAPACITY_RE.search(tail), tail)

    def test_limit_exhausted_falls_back_and_sets_not_before(self):
        # ZCode 实测形态：重试耗尽后须判为容量故障 → 切下一渠道并设 provider_not_before。
        capacity = self.root/'capacity.py'
        capacity.write_text("import sys\nprint('[1310] Weekly/Monthly Limit Exhausted')\nsys.exit(1)\n")
        self.data['providers']['a'] = {'argv': [sys.executable, str(capacity)]}
        self.write_config()
        tid,_=self.enqueue()
        for _ in range(4): self.tick()
        self.assertEqual(self.status(tid),'queued')
        self.tick()
        self.assertEqual(self.status(tid),'succeeded')
        self.assertIsNotNone(store.get_flag(self.conn,'provider_quota_until:a'))  # 容量故障转为额度暂停，到点自动恢复
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0],4)

    def test_generic_failure_after_retries_is_terminal_without_fallback(self):
        self.script.write_text("import sys\nprint('unexpected boom')\nsys.exit(1)\n")
        tid,_=self.enqueue()
        for _ in range(4): self.tick()
        self.assertEqual(self.status(tid),'failed')
        self.assertIsNone(store.get_flag(self.conn,'provider_not_before:a'))

    def test_expired_route_window_blocks_all_fallbacks(self):
        self.cfg.data['routing']['authorized_until']=(util.now()-dt.timedelta(seconds=1)).isoformat()
        tid,_=self.enqueue();self.tick()
        self.assertEqual(self.status(tid),'blocked')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0],0)

    def test_frozen_window_cannot_be_extended_by_config_edit(self):
        deadline=util.now()+dt.timedelta(minutes=1)
        self.cfg.data['routing']['authorized_until']=deadline.isoformat()
        tid,_=self.enqueue();self.tick()
        self.cfg.data['routing']['authorized_until']=(deadline+dt.timedelta(days=1)).isoformat()
        with patch('wq.routing.util.now',return_value=deadline+dt.timedelta(seconds=1)):
            self.tick()
        self.assertEqual(self.status(tid),'blocked')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0],1)


class QuotaPauseTests(RoutingTests):
    """额度暂停：适配器报告 limit=quota 时不耗重试，暂停渠道；无可用渠道时排队到恢复时刻，到点自动继续。"""

    def quota_stub(self, wait='600'):
        path = self.root/'quota.py'
        suffix = f' retry_after={wait}' if wait else ''
        path.write_text(f"import sys\nprint('Provider adapter failed: API HTTP 429 limit=quota{suffix}', file=sys.stderr)\nsys.exit(2)\n")
        return [sys.executable, str(path)]

    def test_quota_pauses_without_retries_and_falls_back(self):
        self.data['providers']['a'] = {'argv': self.quota_stub()}
        self.write_config()
        tid, _ = self.enqueue()
        self.tick(); self.tick()
        self.assertEqual(self.status(tid), 'succeeded')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agent_calls WHERE agent='a'").fetchone()[0], 1)
        until = store.get_flag(self.conn, 'provider_quota_until:a')
        self.assertGreater(util.parse_iso(until), util.now() + dt.timedelta(seconds=500))
        self.assertIn('额度暂停至', routing._unavailable(self.conn, self.cfg, 'a'))

    def test_all_paused_waits_then_resumes_automatically(self):
        self.data['providers']['a'] = {'argv': self.quota_stub()}
        self.data['presets']['first']['routes'] = {r: ['a'] for r in routing.ROLES}
        self.write_config()
        tid, _ = self.enqueue()
        self.tick()          # 调用 a → 额度用尽，暂停
        self.tick()          # 链上无可用渠道 → 排队等恢复，不是 blocked
        row = self.conn.execute('SELECT status,not_before,attempts,last_error FROM tasks WHERE task_id=?', (tid,)).fetchone()
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['not_before'], store.get_flag(self.conn, 'provider_quota_until:a'))
        self.assertEqual(row['attempts'], 0)   # 额度拒绝与等待都不消耗任务尝试次数
        self.assertIn('恢复后自动继续', row['last_error'])
        for _ in range(3): self.tick()        # 未到恢复时刻，不会再调用
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0], 1)
        # 额度恢复：供应商恢复服务，时间走到暂停结束之后
        # 快照冻结了渠道定义，这里改写同一脚本模拟供应商恢复服务
        (self.root/'quota.py').write_text("import json\nfrom pathlib import Path\nPath('result.json').write_text(json.dumps({'status':'completed','summary':'ok','findings':[]}))\n")
        later = util.parse_iso(row['not_before']) + dt.timedelta(seconds=1)
        with patch('wq.util.now', return_value=later), patch('wq.store.util.now', return_value=later):
            self.tick()
        self.assertEqual(self.status(tid), 'succeeded')

    def test_reset_rule_used_when_vendor_gives_no_wait(self):
        self.data['providers']['a'] = {'argv': self.quota_stub(wait=None),
                                       'quota_reset': {'tz': 'America/Los_Angeles', 'at': '00:00'}}
        self.write_config()
        tid, _ = self.enqueue(); self.tick()
        until = util.parse_iso(store.get_flag(self.conn, 'provider_quota_until:a'))
        from zoneinfo import ZoneInfo
        local = until.astimezone(ZoneInfo('America/Los_Angeles'))
        self.assertEqual((local.hour, local.minute), (0, 0))
        self.assertLessEqual(until - util.now(), dt.timedelta(days=1, minutes=1))

    def test_manual_resume_clears_pause(self):
        self.data['providers']['a'] = {'argv': self.quota_stub()}
        self.write_config()
        self.enqueue(); self.tick()
        self.assertEqual([p['provider'] for p in routing.quota_pauses(self.conn, self.cfg)], ['a'])
        routing.clear_quota_pause(self.conn, 'a')
        self.assertEqual(routing.quota_pauses(self.conn, self.cfg), [])
        self.assertIsNone(routing._unavailable(self.conn, self.cfg, 'a'))

    def test_auth_failure_stops_without_retry(self):
        path = self.root/'auth.py'
        path.write_text("import sys\nprint('Provider adapter failed: API HTTP 401 limit=auth', file=sys.stderr)\nsys.exit(2)\n")
        self.data['providers']['a'] = {'argv': [sys.executable, str(path)]}
        self.write_config()
        tid, _ = self.enqueue(); self.tick()
        self.assertEqual(self.status(tid), 'blocked')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM agent_calls').fetchone()[0], 1)

    def test_next_reset_daily_and_monthly(self):
        now = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(routing.next_reset({'tz': 'UTC', 'at': '00:00'}, now), dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(routing.next_reset({'tz': 'Asia/Shanghai', 'at': '00:00'}, now),
                         dt.datetime(2026, 9, 29, 16, tzinfo=dt.timezone.utc))
        self.assertEqual(routing.next_reset({'tz': 'UTC', 'at': '00:00', 'period': 'monthly'}, now),
                         dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
        self.assertIsNone(routing.next_reset({'tz': 'Nowhere/Zone'}, now))


class ChannelIdentityTests(unittest.TestCase):
    def test_aliases_of_one_service_are_one_channel(self):
        api = lambda url, model: {'transport': {'kind': 'api', 'protocol': 'openai', 'base_url': url, 'model': model}}
        data = {'providers': {
            'free-research': api('https://openrouter.ai/api/v1', 'x:free'),
            'free-review': api('https://OpenRouter.ai/api/v1/', 'y:free'),
            'glm': api('https://open.bigmodel.cn/api/paas/v4', 'glm-4.7-flash'),
            'local-a': api('http://127.0.0.1:11434/v1', 'm'), 'local-b': api('http://127.0.0.1:8080/v1', 'm'),
            'claude': {'transport': {'kind': 'claude', 'model': 'a'}}, 'claude-opus': {'transport': {'kind': 'claude', 'model': 'b'}},
            'grok': {'argv': ['/x/launchers/grok', '{prompt}']}, 'devin': {'argv': ['/x/launchers/devin', '{prompt}']}}}
        self.assertTrue(routing.same_channel(data, 'free-research', 'free-review'))
        self.assertTrue(routing.same_channel(data, 'claude', 'claude-opus'))
        self.assertFalse(routing.same_channel(data, 'free-research', 'glm'))
        self.assertFalse(routing.same_channel(data, 'local-a', 'local-b'))
        self.assertFalse(routing.same_channel(data, 'grok', 'devin'))

    def test_review_snapshot_excludes_aliases_of_research_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp)
            api = lambda url: {'transport': {'kind': 'api', 'protocol': 'openai', 'base_url': url, 'model': 'm'}}
            data = {'default': 'p', 'presets': {'p': {'routes': {'research': ['or1'], 'review': ['or2', 'glm']}}},
                    'providers': {'or1': api('https://openrouter.ai/api/v1'), 'or2': api('https://openrouter.ai/api/v1'),
                                  'glm': api('https://open.bigmodel.cn/api/paas/v4')}}
            with patch('wq.routing.catalog', return_value=data), patch('wq.routing.active_preset', return_value='p'):
                tid, _ = store.enqueue_task(c, 'agent_call', {'role': 'review'}, dedup_key='j')
                row = routing._snapshot(c, cfg, tid, {'role': 'review', 'excluded_providers': ['or1']})
                self.assertEqual(json.loads(row['snapshot_json'])['chain'], ['glm'])
            c.close()
