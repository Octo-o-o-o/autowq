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
