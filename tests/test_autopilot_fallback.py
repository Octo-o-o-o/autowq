import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_autopilot
from wq import autopilot, desktop, store, task_view, util


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.f = test_autopilot.AutopilotTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def rejected(self):
        f = self.f; f.reject_review = True
        f.tick(); f.tick()
        row = f.cycle()
        payload = json.loads(autopilot.task(f.c,row['review_task'])['payload_json'])
        return row, Path(payload['job_dir'])/'result.json'

    def test_rejection_overturned_only_with_bound_responses_and_original_kept(self):
        f = self.f; row, path = self.rejected(); original = path.read_bytes()
        f.reject_review = False
        f.tick(); retry = f.cycle()['review_task']
        self.assertNotEqual(retry,row['review_task'])
        self.assertEqual(path.read_bytes(),original)
        f.tick()
        self.assertEqual((f.posts,f.counter),(1,3))
        self.assertEqual(f.c.execute('SELECT COUNT(*) FROM research_fallbacks').fetchone()[0],1)
        groups = task_view.task_groups(f.c,list(store.list_tasks(f.c)))
        members = next(m for c,m in groups if c and c['cycle_id']==row['cycle_id'])
        self.assertTrue({row['research_task'],row['review_task'],retry}.issubset({t['task_id'] for t in members}))
        entry = desktop.history(f.c,f.cfg)['entries'][0]
        self.assertIn('补充尝试 1/1','\n'.join(entry['lines']))
        self.assertIn('首次审查记录','\n'.join(entry['detail']))
        self.assertIn('overturn','\n'.join(entry['detail']))

    def test_repeated_ticks_and_restart_do_not_create_third_review(self):
        f = self.f; self.rejected()
        f.tick(); f.tick()
        self.assertEqual(f.cycle()['outcome'],'模型审查拒绝，不回测')
        for _ in range(3): autopilot.setup(f.c); f.tick()
        self.assertEqual((f.counter,f.posts),(3,0))

    def test_second_accept_without_response_remains_blocked(self):
        f = self.f; self.rejected(); f.reject_review = False; f.tick()
        payload = json.loads(autopilot.task(f.c,f.cycle()['review_task'])['payload_json'])
        path = Path(payload['job_dir'])/'result.json'
        obj = util.read_json(str(path)); del obj['review']['resolutions']; util.write_json(str(path),obj)
        f.tick()
        self.assertIn('待复核',f.cycle()['outcome'])
        self.assertEqual((f.counter,f.posts),(3,0))

    def test_fallback_preserves_original_review_constraints(self):
        f=self.f; row,path=self.rejected()
        original=path.parent/'packet'/'request.md'
        marker='CUSTOM FROZEN REVIEW CONSTRAINT: retain coverage controls'
        original.write_text(original.read_text()+'\n'+marker)
        f.tick()
        payload=json.loads(autopilot.task(f.c,f.cycle()['review_task'])['payload_json'])
        second=Path(payload['job_dir'])/'packet'/'request.md'
        self.assertIn(marker,second.read_text())
        self.assertIn('resolutions',second.read_text())

    def test_unavailable_channel_does_not_expand_preset_or_use_researcher(self):
        f = self.f; self.rejected()
        with patch('wq.routing._unavailable',return_value='预算耗尽'): f.tick()
        self.assertEqual((f.counter,f.posts),(2,0))
        self.assertEqual(f.cycle()['state'],'closed')

    def test_prefers_another_channel_only_inside_frozen_review_route(self):
        f = self.f; row, _ = self.rejected()
        snap={'chain':['b','c','a'],'preset':'steady','providers':{'a':{},'b':{},'c':{}}}
        f.c.execute('UPDATE task_routes SET snapshot_json=? WHERE task_id=?',(json.dumps(snap),row['review_task']))
        f.tick()
        retry=f.cycle()['review_task']
        snap=json.loads(f.c.execute('SELECT snapshot_json FROM task_routes WHERE task_id=?',(retry,)).fetchone()[0])
        self.assertEqual(snap['chain'],['c']); self.assertEqual(snap['retries'],0)

    def test_text_repair_uses_shared_allowance_and_preserves_ast(self):
        f=self.f; f.tick(); row=f.cycle()
        payload=json.loads(autopilot.task(f.c,row['research_task'])['payload_json'])
        path=Path(payload['job_dir'])/'result.json'; obj=util.read_json(str(path))
        obj['candidate']['hypothesis']='短'; util.write_json(str(path),obj)
        f.tick()
        self.assertNotEqual(f.cycle()['research_task'],row['research_task'])
        f.reject_review=True
        f.tick(); f.tick()
        self.assertEqual(f.cycle()['state'],'closed')
        self.assertEqual((f.counter,f.posts),(3,0))

    def test_terminal_execution_failure_can_use_one_extra_call(self):
        f=self.f; f.tick(); row=f.cycle()
        tid=row['research_task']
        f.c.execute("UPDATE tasks SET status='failed',last_error='CLI nonzero exit' WHERE task_id=?",(tid,))
        store.add_attempt(f.c,tid,'provider_result','failed',{'call_status':'failed'})
        f.tick()
        self.assertNotEqual(f.cycle()['research_task'],tid)
        self.assertEqual(f.counter,2)

    def test_unknown_blocked_pause_and_policy_change_never_fallback(self):
        f=self.f; row,_=self.rejected(); tid=row['review_task']
        for status in ('unknown','blocked','aborted','running','claimed','queued'):
            f.c.execute('UPDATE tasks SET status=? WHERE task_id=?',(status,tid))
            with self.subTest(status=status):
                self.assertFalse(autopilot.fallback_once(f.c,f.cfg,row,f.p,'测试'))
        f.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
        store.set_flag(f.c,'paused','1')
        self.assertFalse(autopilot.fallback_once(f.c,f.cfg,row,f.p,'测试'))
        store.set_flag(f.c,'paused','0')
        changed=dict(f.p,version=99)
        self.assertFalse(autopilot.fallback_once(f.c,f.cfg,row,changed,'测试'))
        self.assertEqual(f.counter,2)
