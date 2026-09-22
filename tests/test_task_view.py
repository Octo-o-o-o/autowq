import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import autopilot, store, task_view, util


class GroupedTasksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg, self.c = make_env(self.temp.name)
        self.addCleanup(self.c.close)
        autopilot.setup(self.c)

    def task(self, purpose):
        return store.enqueue_task(self.c, 'agent_call', {'purpose': purpose})[0]

    def cycle(self, research, review=None):
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,research_task,review_task,outcome,created_at,updated_at) VALUES('closed','{}','x',?,?,'模型审查拒绝，不回测',?,?)", (research, review, util.now_iso(), util.now_iso()))

    def call(self, cid, purpose):
        self.c.execute("INSERT INTO agent_calls(call_id,agent,purpose,status,week,started_at) VALUES(?,'grok',?,'succeeded','test',?)", (cid,purpose,util.now_iso()))

    def test_retries_deduplicated_unknown_not_zero(self):
        tid = self.task('job')
        self.call('first', 'job'); self.call('retry', 'job')
        for event in ('provider_result','finish'):
            store.add_attempt(self.c,tid,event,'succeeded',{'call_id':'retry'})
        with patch('wq.usage.for_call', side_effect=lambda c: {'cost_usd':0.25,'total_tokens':100} if c['call_id']=='first' else None):
            text=task_view.group_cost(self.c,store.list_tasks(self.c))
        self.assertIn('$0.2500',text)
        self.assertIn('1 次调用成本未知，总额未知',text)
        self.assertIn('已知 100 token',text)

    def test_status_filter_keeps_full_round_cost_and_no_double_tasks(self):
        a=self.task('a'); b=self.task('b'); other=self.task('other')
        self.cycle(a,b)
        self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(a,))
        self.call('ca','a'); self.call('cb','b')
        with patch('wq.usage.for_call',return_value={'cost_usd':0.5,'total_tokens':10}):
            text=task_view.render_tasks(self.c,self.cfg,store.list_tasks(self.c,'queued'))
        self.assertIn('第 1 轮',text)
        self.assertIn('本组显示 1/2 项',text)
        self.assertIn('$1.0000',text)
        self.assertIn('最终质量：未回测；模型审查拒绝',text)
        self.assertIn('其他任务',text)
        self.assertEqual(text.count('编号：'+b),1)
        self.assertNotIn('编号：'+a,text)
        self.assertEqual(text.count('编号：'+other),1)

    def test_unstarted_and_crashed_unlinked_start(self):
        tid=self.task('fresh')
        self.assertIn('尚未调用模型',task_view.group_cost(self.c,store.list_tasks(self.c)))
        store.add_attempt(self.c,tid,'provider_start','running',{})
        text=task_view.group_cost(self.c,store.list_tasks(self.c))
        self.assertIn('总额未知',text)
        self.assertNotIn('$0',text)

    def test_grouping_uses_ids_not_titles_and_latest_first(self):
        a=self.task('a');b=self.task('b');self.cycle(a);self.cycle(b)
        groups=task_view.task_groups(self.c,store.list_tasks(self.c))
        self.assertEqual([c['cycle_id'] for c,rows in groups],[2,1])
        self.assertEqual(groups[0][1][0]['task_id'],b)
