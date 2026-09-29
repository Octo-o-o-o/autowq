import json
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_cfg, make_env
from wq import autopilot, brain_jobs, desktop, feedback, store, util
import test_autopilot


class RunNextTests(unittest.TestCase):
    def setUp(self):
        self.f = test_autopilot.AutopilotTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_one_request_finishes_exactly_one_cycle_without_enabling_auto(self):
        f = self.f
        store.set_flag(f.c, 'autopilot_enabled', '0')
        store.set_flag(f.c, 'autopilot_next_at', '2099-01-01T00:00:00Z')
        autopilot.request_run_next(f.c, f.cfg)
        self.assertFalse(autopilot.enabled(f.c, f.cfg))
        self.assertTrue(autopilot.run_next_requested(f.c))
        f.full_cycle()
        self.assertEqual(f.cycle()['state'], 'closed')
        self.assertFalse(autopilot.run_next_requested(f.c))
        store.set_flag(f.c, 'autopilot_next_at', '2000-01-01T00:00:00Z')
        for _ in range(4): f.tick()
        self.assertEqual(f.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0], 1)
        self.assertEqual(f.posts, 1)

    def test_single_request_cannot_bypass_platform_cooldown_or_authorization(self):
        f = self.f
        autopilot.request_run_next(f.c, f.cfg)
        store.set_flag(f.c, 'brain_not_before', '2099-01-01T00:00:00Z')
        f.tick()
        self.assertEqual(f.counter, 0)
        self.assertEqual(f.posts, 0)
        store.set_flag(f.c, 'brain_not_before', '')
        f.cfg.data['routing']['authorized_until'] = '2000-01-01T00:00:00Z'
        f.tick()
        self.assertEqual(f.counter, 0)
        self.assertTrue(autopilot.run_next_requested(f.c))

    def test_active_cycle_queues_immediate_follow_up(self):
        f = self.f
        autopilot.request_run_next(f.c, f.cfg)
        self.assertTrue(autopilot.enabled(f.c, f.cfg))
        f.tick()
        self.assertNotEqual(f.cycle()['state'], 'closed')
        autopilot.request_run_next(f.c, f.cfg)
        self.assertTrue(autopilot.enabled(f.c, f.cfg))
        self.assertTrue(autopilot.run_next_requested(f.c))
        autopilot.finish(f.c, f.cfg, f.cycle(), '测试结束')
        nxt = util.parse_iso(store.get_flag(f.c, 'autopilot_next_at'))
        self.assertLess(abs((nxt - util.now()).total_seconds()), 5)
        self.assertFalse(autopilot.run_next_requested(f.c))

    def test_unknown_rejected_without_unpausing(self):
        f = self.f
        tid, _ = store.enqueue_task(f.c, 'simulation', {})
        f.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?", (tid,))
        store.set_flag(f.c,'paused','1')
        with self.assertRaisesRegex(ValueError, 'UNKNOWN'):
            autopilot.request_run_next(f.c, f.cfg)
        self.assertTrue(store.is_paused(f.c))
        self.assertFalse(autopilot.run_next_requested(f.c))


class DesktopDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg, self.c = make_env(self.tmp.name); self.addCleanup(self.c.close)
        autopilot.setup(self.c); brain_jobs.setup(self.c); feedback.setup(self.c)

    def cycle(self, state='closed', outcome=None, cid=None, focus=None, plan=None):
        """插入一轮研究；返回 cycle_id。plan=(父轮列表) 时登记组合实验。"""
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,outcome,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (state, '{}', 'x', outcome, '2026-09-25T20:00:00Z', '2026-09-25T21:00:00Z'))
        new_id = cid or self.c.execute('SELECT MAX(cycle_id) FROM research_cycles').fetchone()[0]
        if focus:
            store.set_flag(self.c, f'autopilot_focus_{new_id}', focus)
        if plan is not None:
            self.c.execute('INSERT INTO combination_plans VALUES(?,?,?,?)',
                (f'pair-{new_id}', new_id, json.dumps({'parent_cycles': plan}), util.now_iso()))
        return new_id

    def test_beijing_conversion_crosses_date_and_rejects_naive_time(self):
        self.assertEqual(desktop.beijing('2026-09-25T20:02:03.001Z'), '2026年09月26日 04:02:03')
        self.assertEqual(desktop.beijing('2026-09-26T04:02:03+08:00'), '2026年09月26日 04:02:03')
        self.assertEqual(desktop.beijing('2026-09-25T20:02:03Z', fmt='%m-%d %H:%M'), '09-26 04:02')
        self.assertIn('缺少时区', desktop.beijing('2026-09-26T04:02:03'))
        self.assertNotIn('T20:', desktop.localize_message('等待 2026-09-25T20:02:03Z'))

    def test_history_directive_marks_each_cycle(self):
        self.cycle(focus='analyst4')
        self.cycle(plan=[3, 5])
        self.cycle()
        entries = desktop.history(self.c, self.cfg)['entries']
        self.assertIn('单角色基线 · analyst4', entries[2]['title'])
        self.assertIn('组合实验 · 父轮 3+5', entries[1]['title'])
        self.assertIn('自由探索', entries[0]['title'])

    def test_history_badges_only_notable_outcomes(self):
        self.cycle(state='researching')
        self.cycle(outcome='任务未完成：failed')
        self.cycle(outcome='筛选通过，留待进一步验证（不提交）')
        entries = desktop.history(self.c, self.cfg)['entries']
        self.assertEqual((entries[2]['badge'], entries[2]['title'].split(' · ')[-1]), ('progress', '研究中'))
        self.assertEqual(entries[1]['badge'], 'failed')
        self.assertIn('失败', entries[1]['title'])
        self.assertIsNone(entries[0]['badge'])          # 正常完结不标状态
        self.assertIn('尚未完成', '\n'.join(entries[2]['detail']))

    def test_history_highlights_cycle_with_accepted_submission(self):
        fid = store.insert_family(self.c, None, 'family', 'hypothesis', 'test', False, None)
        cidc = store.insert_candidate(self.c, fid, 'rank(x)', {}, 'hash', False)
        sid = store.insert_simulation(self.c, cidc, 'alpha-a', 'api', False, 'passed', {'stats': {}}, None, None)
        store.add_submission(self.c, sid, 'alpha-a', 'accepted', {})
        self.cycle()
        self.c.execute("INSERT INTO tasks(task_id,kind,payload_json,status,not_before,created_at,updated_at) VALUES('sim-task','brain_simulation','{}','succeeded',?,?,?)",(util.now_iso(),util.now_iso(),util.now_iso()))
        self.c.execute("INSERT INTO brain_runs(task_id,state,alpha_id,started_at,updated_at) VALUES('sim-task','complete','alpha-a',?,?)",(util.now_iso(),util.now_iso()))
        self.c.execute("UPDATE research_cycles SET simulation_task='sim-task' WHERE cycle_id=(SELECT MAX(cycle_id) FROM research_cycles)")
        entry = desktop.history(self.c, self.cfg)['entries'][0]
        self.assertEqual(entry['badge'], 'submitted')
        self.assertIn('已提交', entry['title'])
        sid_rej = store.insert_simulation(self.c, cidc, 'alpha-b', 'api', False, 'passed', {'stats': {}}, None, None)
        store.add_submission(self.c, sid_rej, 'alpha-b', 'rejected', {})
        self.cycle()
        self.c.execute("INSERT INTO tasks(task_id,kind,payload_json,status,not_before,created_at,updated_at) VALUES('sim-task2','brain_simulation','{}','succeeded',?,?,?)",(util.now_iso(),util.now_iso(),util.now_iso()))
        self.c.execute("INSERT INTO brain_runs(task_id,state,alpha_id,started_at,updated_at) VALUES('sim-task2','complete','alpha-b',?,?)",(util.now_iso(),util.now_iso()))
        self.c.execute("UPDATE research_cycles SET simulation_task='sim-task2' WHERE cycle_id=(SELECT MAX(cycle_id) FROM research_cycles)")
        entry = desktop.history(self.c, self.cfg)['entries'][0]
        self.assertIsNone(entry['badge'])

    def test_old_blocked_history_shows_task_reason_without_rewriting_ledger(self):
        cid = self.cycle(outcome='任务未完成：blocked')
        tid, _ = store.enqueue_task(self.c, 'agent_call', {})
        self.c.execute("UPDATE tasks SET status='blocked',last_error=? WHERE task_id=?",
                       ('固定组合与复杂度说明冲突', tid))
        self.c.execute('UPDATE research_cycles SET research_task=? WHERE cycle_id=?', (tid, cid))
        entry = desktop.history(self.c, self.cfg)['entries'][0]
        self.assertEqual(entry['badge'], 'failed')
        self.assertIn('固定组合与复杂度说明冲突', entry['lines'][-1])
        self.assertIn('固定组合与复杂度说明冲突', entry['detail'][-1])
        self.assertEqual(self.c.execute('SELECT outcome FROM research_cycles WHERE cycle_id=?', (cid,)).fetchone()[0],
                         '任务未完成：blocked')

    def test_history_all_cycles_and_compact_lines(self):
        for i in range(46):
            self.cycle(state='researching' if i == 45 else 'closed')
        result = desktop.history(self.c, self.cfg)
        self.assertEqual(result['count'], 46)
        self.assertIn('研究中', result['entries'][0]['title'])
        self.assertIn('第 1 轮', result['entries'][-1]['title'])
        lines = '\n'.join(result['entries'][-1]['lines'])
        self.assertLessEqual(len(result['entries'][-1]['lines']), 4)   # 时间/模型/成本/结果

    def test_submitted_time_frozen_models_settings_and_direct_unknown(self):
        fid = store.insert_family(self.c,None,'family','hypothesis','test',False,None)
        cid = store.insert_candidate(self.c,fid,'rank(x)',{'extra':{'brain_settings':{'delay':1,'decay':8,'universe':'TOP3000'}}},'hash',False)
        sid = store.insert_simulation(self.c,cid,'alpha-one','api',False,'passed',{'stats':{'sharpe':1.5}},None,None)
        store.add_submission(self.c,sid,'alpha-one','accepted',{'dateSubmitted':'2026-09-25T20:01:00Z','platform_status':'ACTIVE'})
        direct = desktop.submitted(self.c)['entries'][0]
        self.assertEqual(direct['badge'],'submitted')
        self.assertNotIn('第 ', direct['title'])
        self.assertIn('未关联自动轮次','\n'.join(direct['lines']))
        tid,_ = store.enqueue_task(self.c,'agent_call',{})
        snap = {'chain':['grok'],'providers':{'grok':{'model':'historic-model'}}}
        self.c.execute('INSERT INTO task_routes(task_id,snapshot_json,updated_at) VALUES(?,?,?)',(tid,json.dumps(snap),util.now_iso()))
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,research_task,simulation_task,created_at,updated_at) VALUES('closed','{}','x',?,'sim-task',?,?)",(tid,util.now_iso(),util.now_iso()))
        self.c.execute("INSERT INTO tasks(task_id,kind,payload_json,status,not_before,created_at,updated_at) VALUES('sim-task','brain_simulation','{}','succeeded',?,?,?)",(util.now_iso(),util.now_iso(),util.now_iso()))
        self.c.execute("INSERT INTO brain_runs(task_id,state,alpha_id,started_at,updated_at) VALUES('sim-task','complete','alpha-one',?,?)",(util.now_iso(),util.now_iso()))
        result=desktop.submitted(self.c)['entries'][0]
        text='\n'.join(result['lines'])
        cycle_id = self.c.execute('SELECT MAX(cycle_id) FROM research_cycles').fetchone()[0]
        self.assertIn('historic-model',text)
        self.assertIn(f'第 {cycle_id} 轮 · alpha-one · 2026年09月26日 04:01:00', result['title'])
        self.assertIn(f'轮次：第 {cycle_id} 轮', text)
        english = desktop.submitted(self.c, lang='en')['entries'][0]
        self.assertIn(f'Cycle {cycle_id} · alpha-one', english['title'])
        self.assertIn('decay：8',text)
        self.assertNotIn('extra：',text)

    def test_standby_list_matches_submitted_shape(self):
        from wq import brain_submission
        brain_submission.setup(self.c)
        fid = store.insert_family(self.c, None, 'family', 'hypothesis', 'test', False, None)
        cid = store.insert_candidate(self.c, fid, 'rank(cash)', {'extra': {'brain_settings': {'delay': 1}}}, 'hash', False)
        store.insert_simulation(self.c, cid, 'standby-one', 'api', False, 'passed', {'stats': {'sharpe': 1.5, 'fitness': 1.2}}, None, None)
        cycle_id = self.cycle()
        self.c.execute("INSERT INTO submission_standby VALUES(?,?,?,?,?)",
                       ('standby-one', cycle_id, 'waiting', util.now_iso(), util.now_iso()))
        listed = desktop.standby(self.c)
        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['entries'][0]['badge'], 'standby')
        self.assertIn(f'第 {cycle_id} 轮 · standby-one', listed['entries'][0]['title'])
        lines = '\n'.join(listed['entries'][0]['lines'])
        self.assertIn('rank(cash)', lines)
        self.assertIn(f'轮次：第 {cycle_id} 轮', lines)

    def test_history_cost_unknown_is_not_zero(self):
        tid,_=store.enqueue_task(self.c,'agent_call',{'purpose':'missing-usage'})
        self.c.execute("UPDATE tasks SET status='succeeded',attempts=1 WHERE task_id=?",(tid,))
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,research_task,created_at,updated_at) VALUES('closed','{}','x',?,?,?)",(tid,util.now_iso(),util.now_iso()))
        entry=desktop.history(self.c,self.cfg)['entries'][0]
        self.assertIn('成本未知','\n'.join(entry['lines']))
        self.assertNotIn('小计 $0','\n'.join(entry['lines']))

    def test_pending_notifications_baseline_consume_and_dedupe(self):
        self.assertEqual(desktop.pending_notifications(self.c), [])      # 首次只建基线
        self.assertEqual(desktop.pending_notifications(self.c), [])      # 无新事件不误报
        for key in ('desktop_notified_submissions', 'desktop_notified_failures'):
            store.set_flag(self.c, key, '2020-01-01T00:00:00.000+00:00')
        fid = store.insert_family(self.c, None, 'family', 'hypothesis', 'test', False, None)
        cidc = store.insert_candidate(self.c, fid, 'rank(x)', {}, 'hash', False)
        sid = store.insert_simulation(self.c, cidc, 'alpha-n1', 'api', False, 'passed', {'stats': {}}, None, None)
        store.add_submission(self.c, sid, 'alpha-n1', 'accepted', {})
        tid, _ = store.enqueue_task(self.c, 'agent_call', {})
        self.c.execute("UPDATE tasks SET status='failed',last_error='boom' WHERE task_id=?", (tid,))
        items = desktop.pending_notifications(self.c)
        self.assertEqual([i['kind'] for i in items], ['submitted', 'failed'])
        self.assertIn('alpha-n1', items[0]['body'])
        self.assertIn('boom', items[1]['body'])
        self.assertEqual(desktop.pending_notifications(self.c), [])      # 已消费不重复

    def test_notifications_enabled_default_on_and_config_off(self):
        self.assertTrue(desktop.notifications_enabled(self.cfg))
        cfg2 = make_cfg(self.tmp.name + '/nested', {'desktop': {'notifications': False}})
        self.assertFalse(desktop.notifications_enabled(cfg2))

    def test_next_model_prediction_skips_unavailable_provider_and_separates_reviewer(self):
        data={'default':'p','providers':{'a':{'model':'A'},'b':{'model':'B'},'c':{'model':'C'}},
              'presets':{'p':{'routes':{'research':['a','b'],'review':['b','c']}}}}
        with patch('wq.routing.catalog',return_value=data), patch('wq.routing._unavailable',side_effect=lambda c,cfg,n:'disabled' if n=='a' else None):
            result=desktop.next_models(self.c,self.cfg)
        self.assertIn('b · B',result[0]['title'])
        self.assertIn('c · C',result[1]['title'])
