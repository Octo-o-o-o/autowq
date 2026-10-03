"""Cross-module regressions for multi-lane, campaign and recovery boundaries."""
import concurrent.futures
import copy
import math
import queue
import json
import threading
import unittest
from unittest.mock import patch

from wq import autopilot, db, research_campaign, research_learning as learning
from wq import research_meta as meta, runner, store, util
import test_lanes
import test_research_dual_loop
import test_research_strategy
import test_feedback


class LaneReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.f = test_lanes.LaneTests('test_two_lanes_open_with_staggered_route_heads')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.c, self.cfg = self.f.c, self.f.cfg

    def test_fill_lanes_never_crosses_total_cap(self):
        self.cfg.data['autopilot'].update(concurrent_lanes=8, max_cycles_total=3)
        autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0], 3)
        self.assertEqual(self.f.counter, 3)
        autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.f.counter, 3)

    def test_framework_failure_is_actionable_and_existing_lifecycle_still_advances(self):
        self.cfg.data['research_framework'] = {'enabled': True}
        with patch('wq.research_framework.tick', side_effect=ValueError('fixture digest mismatch')), \
                patch('wq.autopilot.tick') as tick:
            runner._coordinate(self.c, self.cfg)
        tick.assert_called_once_with(self.c, self.cfg, allow_new=False)
        self.assertIn('框架检查失败', store.get_flag(self.c, 'autopilot_message'))
        self.assertIn('fixture digest mismatch', store.get_flag(self.c, 'autopilot_message'))


    def test_worker_connection_failure_reports_completion_to_supervisor(self):
        results = queue.Queue()
        with patch('wq.db.connect', side_effect=OSError('fixture unavailable database')):
            runner._agent_worker(self.cfg, {'task_id': 'fixture-task'}, results)
        tid, code, lines = results.get_nowait()
        self.assertEqual(tid, 'fixture-task')
        self.assertEqual(code, runner.ERR)
        self.assertIn('unavailable database', lines[0])

    def test_three_rounds_resume_after_process_restart_without_duplicate_posts(self):
        # Different predeclared settings avoid intentionally rejecting duplicate expressions.
        self.cfg.data['brain_api']['max_posts_per_24h']=12
        for round_no in range(1, 4):
            self.cfg.data['autopilot']['max_cycles_total'] = round_no * 2
            self.f.p['settings']['decay'] = round_no
            util.write_json(str(self.f.root / 'config/policy.json'), self.f.p)
            for lane in range(2):
                store.set_flag(self.c, f'autopilot_next_at:{lane}', '2000-01-01T00:00:00Z')
            original_proposal=test_lanes.proposal
            def new_proposal(op='mean'):
                candidate=original_proposal(op)
                candidate['ast']['arg']['arg']['name']=['daily_return','market_cap_rank','activity_rank'][round_no-1]
                return candidate
            with patch('test_lanes.proposal',side_effect=new_proposal):
                self.f.full_cycle()
            self.assertEqual(self.c.execute("SELECT COUNT(*) FROM research_cycles WHERE state='closed'").fetchone()[0], round_no * 2, [dict(r) for r in self.c.execute('SELECT cycle_id,state,outcome FROM research_cycles')] + [dict(r) for r in self.c.execute("SELECT task_id,status,last_error,not_before FROM tasks WHERE status NOT IN ('succeeded','failed')")])
            self.assertEqual(self.f.posts, round_no * 2)
            self.assertEqual(self.f.counter, round_no * 4)
            before = self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
            self.c.close()
            self.c = self.f.c = db.connect(self.cfg.db_path)
            self.f.tick()
            self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], before)


class DualReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.f = test_research_dual_loop.DualLoopTests('test_model_reservations_failures_do_not_refund_and_cap_persists')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.c, self.cfg, self.p = self.f.c, self.f.cfg, self.f.p


    def test_exact_epoch_migration_preserves_budgets_and_rejects_stale_document(self):
        from wq import research_lifecycle as lifecycle
        self.cfg.data['autopilot']['enabled'] = True
        before = lifecycle.remaining(self.c, 'fixture-dual-epoch')
        proposal = lifecycle.prepare_transition(self.c, self.cfg, 'fixture-owner', 'Reviewed candidate')
        stale = copy.deepcopy(proposal)
        stale['to_baseline']['source'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'changed'):
            lifecycle.apply_transition(self.c, self.cfg, stale)
        self.assertIsNone(lifecycle.stop_kind(self.c, 'fixture-dual-epoch'))
        result = lifecycle.apply_transition(self.c, self.cfg, proposal)
        self.assertEqual(result['state'], 'created')
        self.assertEqual(result['remaining'], before)
        self.assertEqual(lifecycle.apply_transition(self.c, self.cfg, proposal)['state'], 'already_created')
        learning.stop_experiment(self.c, result['experiment'], 'Owner stop')
        self.cfg.data['autopilot']['interval_s'] = 181
        with self.assertRaisesRegex(ValueError, 'Owner stopped'):
            lifecycle.prepare_transition(self.c, self.cfg)

    def test_epoch_migration_waits_for_queued_cycle_and_keeps_original_epoch(self):
        from wq import research_lifecycle as lifecycle
        self.cfg.data['autopilot']['enabled'] = True
        doc = lifecycle.prepare_transition(self.c, self.cfg)
        now=util.now_iso()
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('researching','{}','fixture',?,?)",(now,now))
        with self.assertRaisesRegex(ValueError,'Drain'):
            lifecycle.apply_transition(self.c,self.cfg,doc)
        self.assertIsNone(lifecycle.stop_kind(self.c,'fixture-dual-epoch'))


    def test_only_new_verified_information_wakes_exhausted_discovery(self):
        meta.record_discovery(self.c,self.cfg,700,False,self.p)
        meta.record_discovery(self.c,self.cfg,702,False,self.p)
        old=meta.discovery_permission(self.c,self.cfg,self.p)
        self.assertFalse(old['allowed'])
        starts=store.get_flag(self.c,'dual_model_reservations:fixture-dual','0')
        # This fixture rejects unknown, missing and synthetic receipts before accepting it.
        self.f.test_not_sent_unknown_missing_or_synthetic_simulation_is_not_information()
        store.set_flag(self.c,'dual_cycle:701','fixture-dual-epoch')
        new=meta.discovery_permission(self.c,self.cfg,self.p)
        self.assertTrue(new['allowed'])
        self.assertNotEqual(old['episode'],new['episode'])
        self.assertEqual(store.get_flag(self.c,'dual_model_reservations:fixture-dual','0'),starts)
        meta.record_discovery(self.c,self.cfg,703,False,self.p)
        meta.record_discovery(self.c,self.cfg,704,False,self.p)
        repeated=meta.discovery_permission(self.c,self.cfg,self.p)
        self.assertEqual(repeated['episode'],new['episode'])
        self.assertFalse(repeated['allowed'])
        self.assertEqual(json.loads(store.get_flag(self.c,'discovery_episode:'+old['episode']))['empty_cycles'],[700,702])

    def test_upgrade_without_real_receipts_preserves_old_exhausted_episode(self):
        from wq import research_framework as framework
        sources=framework.latest(self.c,'research_evidence_source','gap_id')
        material={k:framework.file_identity(v['source']['path']) for k,v in sources.items() if v['adapter']=='local_material_v1'}
        old=util.sha256_json({'policy':self.p,'sources':sources,'material':material,'bundle':meta.BUNDLE})
        store.set_flag(self.c,'discovery_episode:'+old,json.dumps({'input_hash':old,'empty_cycles':[700,702]}))
        current=meta.discovery_permission(self.c,self.cfg,self.p)
        self.assertEqual(current['episode'],old)
        self.assertFalse(current['allowed'])

    def test_consumed_receipt_loss_or_edit_never_refills_discovery(self):
        self.f.test_not_sent_unknown_missing_or_synthetic_simulation_is_not_information()
        store.set_flag(self.c,'dual_cycle:701','fixture-dual-epoch')
        original=meta.discovery_permission(self.c,self.cfg,self.p)
        meta.record_discovery(self.c,self.cfg,710,False,self.p)
        meta.record_discovery(self.c,self.cfg,711,False,self.p)
        path=self.f.root/'alpha.json'
        raw=path.read_bytes()
        self.c.commit()
        self.c.close()
        self.c=self.f.c=db.connect(self.cfg.db_path)
        for mutation in ('delete','description','sharpe'):
            path.write_bytes(raw)
            if mutation=='delete':path.unlink()
            else:
                doc=json.loads(raw)
                if mutation=='description':doc['description']='Unrelated metadata change'
                else:doc['is']['sharpe']=7
                path.write_text(json.dumps(doc))
            blocked=meta.discovery_permission(self.c,self.cfg,self.p)
            self.assertFalse(blocked['allowed'],mutation)
            self.assertEqual(blocked['reason'],'waiting_unreadable_or_changed_consumed_receipt')
        path.write_bytes(raw)
        restored=meta.discovery_permission(self.c,self.cfg,self.p)
        self.assertEqual(restored['episode'],original['episode'])
        self.assertFalse(restored['allowed'])
        self.assertEqual(restored['empty_cycles'],[710,711])

    def test_model_cap_is_atomic_across_worker_connections(self):
        key = 'dual_model_reservations:fixture-dual'
        store.set_flag(self.c, key, '63')
        self.c.commit()
        barrier = threading.Barrier(8)
        def reserve(_):
            conn = db.connect(self.cfg.db_path)
            try:
                barrier.wait(timeout=10)
                try:
                    meta.reserve_model(conn, self.cfg)
                    return 'reserved'
                except ValueError as exc:
                    self.assertIn('64', str(exc))
                    return 'blocked'
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve, range(8)))
        self.assertEqual(outcomes.count('reserved'), 1)
        self.assertEqual(store.get_flag(self.c, key), '64')

    def test_each_new_lane_reserves_discovery_before_enqueue(self):
        self.p.pop('campaign')
        self.cfg.data['autopilot'].update(enabled=True, concurrent_lanes=8, max_cycles_per_day=10)
        util.write_json(self.cfg.resolve(self.cfg.get('autopilot', 'policy_file')), self.p)
        self.c.execute('DELETE FROM learning_experiments')
        learning.freeze_experiment(self.c, 'fixture-dual-epoch', learning.current_baseline(self.cfg), 10, 6)
        with patch('wq.autopilot.brain_preflight', return_value=(True, None)):
            autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0], 2)
        permit = meta.discovery_permission(self.c, self.cfg, self.p)
        self.assertFalse(permit['allowed'])
        self.assertEqual(len(permit['empty_cycles']), 2)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='agent_call'").fetchone()[0], 2)

    def test_registered_campaign_can_dispatch_without_polluting_ordinary_comparison(self):
        now = util.now_iso()
        cid = self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('researching',?,'fixture',?,?)", (json.dumps(self.p), now, now)).lastrowid
        allocation = research_campaign.allocate(self.c, self.p, cid)
        self.assertIsNotNone(allocation)
        tid = autopilot.make_job(self.c, self.cfg, cid, 'research', 'Fixture registered campaign')
        self.c.execute('UPDATE research_cycles SET research_task=? WHERE cycle_id=?', (tid, cid))
        meta.reserve_model(self.c, self.cfg, tid, 'fixture-provider')
        learning.check_request_budget(self.c, cid, self.cfg)
        # Exercise both frozen review arms and the actual pre-POST gate, not just enqueue.
        campaign_fixture=self.f.f.f
        campaign_fixture.result(tid,{'status':'completed','candidate':campaign_fixture.candidate},'a')
        autopilot.advance(self.c,self.cfg,campaign_fixture.cycle(),self.p)
        from wq import research_campaign_v2 as campaign
        pair=campaign.pair_for_cycle(self.c,cid)
        for arm in ('treatment','control'):
            review=campaign.review_refs(self.c,pair['pair_id'])[arm]
            meta.reserve_model(self.c,self.cfg,review,'fixture-reviewer')
            campaign_fixture.review(arm)
        requests=list(self.c.execute("SELECT task_id,payload_json FROM tasks WHERE kind='brain_simulation'"))
        self.assertEqual(len(requests),2)
        for request in requests:
            learning.validate_dispatch(self.c,self.cfg,request['task_id'],json.loads(request['payload_json']))
            research_campaign.check_budget(self.c,self.cfg,cid,request['task_id'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_assignments').fetchone()[0], 0)
        resource = self.c.execute('SELECT * FROM learning_resource_cycles WHERE cycle_id=?', (cid,)).fetchone()
        self.assertEqual(resource['work_kind'], 'campaign')
        self.c.execute("UPDATE learning_resource_cycles SET experiment_id='unrelated' WHERE cycle_id=?", (cid,))
        with self.assertRaisesRegex(ValueError, 'another epoch'):
            learning.validate_dispatch(self.c,self.cfg,requests[0]['task_id'],json.loads(requests[0]['payload_json']))
        with self.assertRaisesRegex(ValueError, 'another epoch'):
            learning.check_request_budget(self.c, cid, self.cfg)
        self.assertEqual(store.get_flag(self.c, 'dual_model_reservations:fixture-dual'), '3')
        self.c.execute("UPDATE learning_resource_cycles SET experiment_id='fixture-dual-epoch' WHERE cycle_id=?",(cid,))
        self.c.execute('DELETE FROM state_flags WHERE key=?',('dual_cycle:'+str(cid),))
        learning.stop_experiment(self.c,'fixture-dual-epoch','Owner stop')
        with self.assertRaisesRegex(ValueError,'Owner stopped'):
            learning.validate_dispatch(self.c,self.cfg,requests[0]['task_id'],json.loads(requests[0]['payload_json']))



class EvidenceReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.f=test_research_strategy.ResearchStrategyTests('test_forward_excludes_prefreeze_and_uses_frozen_weights')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.c,self.cfg=self.f.c,self.f.cfg

    def test_bad_first_reference_does_not_hide_eligible_reference_or_refit(self):
        from wq import research_metrics as metrics
        for i,name in enumerate(('a_candidate','b_old_reference','c_good_reference')):
            ast={'op':'mean','arg':{'op':'field','name':['daily_return','activity_rank','cashflow_strength'][i]},'window':20}
            self.f.trial(name,ast,quality='usable')
            values=[sum((j%(3+i))-.7 for j in range(k)) for k in range(81)]
            self.f.snapshot(name,values,start='2020-01-01' if i==1 else '2025-01-01')
        result=metrics.prepare_contributions(self.c,learning.as_of(self.c))
        self.assertEqual(result['candidate'],'a_candidate')
        self.assertEqual(result['state'],'frozen')
        self.assertEqual([r['status'] for r in result['reference_checks']],['unavailable','frozen'])
        saved=self.c.execute('SELECT document_json FROM learning_pool_contracts WHERE pool_id=?',(result['pool_id'],)).fetchone()[0]
        self.assertEqual(json.loads(saved)['reference'],['c_good_reference'])
        for _ in range(4):metrics.prepare_contributions(self.c,learning.as_of(self.c))
        self.assertEqual(self.c.execute('SELECT document_json FROM learning_pool_contracts WHERE pool_id=?',(result['pool_id'],)).fetchone()[0],saved)

    def test_combination_evidence_is_bound_to_used_fields_and_current_settings(self):
        from wq import feedback
        self.c.execute('CREATE TABLE brain_runs(task_id TEXT,alpha_id TEXT)')
        policy={'settings':self.f.settings,'bindings':self.f.bindings}
        for i,role in enumerate(('daily_return','activity_rank')):
            candidate={'ast':{'op':'mean','arg':{'op':'field','name':role},'window':20}}
            self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,candidate_json,simulation_task,created_at,updated_at) VALUES(?,'closed',?,'fixture',?,?,?,?)",(i+1,json.dumps(policy),json.dumps(candidate),'task'+str(i),util.now_iso(),util.now_iso()))
            self.c.execute('INSERT INTO brain_runs VALUES(?,?)',('task'+str(i),'alpha'+str(i)))
        with test_feedback.combination_fixture(self.c,self.f.tmp.name,[{'parents':['alpha0','alpha1'],'value':.01}]):
            self.c.execute("UPDATE simulations SET stats_json='{" + '"sharpe":1.5' + "}'")
            self.assertIsNotNone(feedback.next_combination(self.c,current_policy=policy))
            unrelated=copy.deepcopy(policy)
            unrelated['bindings']['cashflow_strength']['expression']='unrelated_change'
            self.assertIsNotNone(feedback.next_combination(self.c,current_policy=unrelated))
            changed=copy.deepcopy(policy)
            changed['bindings']['daily_return']['expression']='different_field'
            self.assertIsNone(feedback.next_combination(self.c,current_policy=changed))
            self.assertIn('CURRENT_BINDINGS_MISMATCH',feedback.combination_diagnostics(self.c,current_policy=changed)['pairs'][0]['reason_codes'])
            changed=copy.deepcopy(policy);changed['settings']['delay']=0
            self.assertIsNone(feedback.next_combination(self.c,current_policy=changed))

if __name__ == '__main__':
    unittest.main()
