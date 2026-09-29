import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from helpers import make_env
from test_autopilot import proposal
from wq import autopilot, feedback, research_learning as learning, research_strategy as strategy
from wq import research_metrics as metrics, research_maintenance as maintenance, util, store


class ResearchStrategyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg,self.c=make_env(self.tmp.name);self.addCleanup(self.c.close)
        autopilot.setup(self.c);feedback.setup(self.c);learning.setup(self.c);metrics.setup(self.c)
        self.bindings={r:{'expression':'private_'+r,'fields':['private_'+r],'source':'fixture'}
                       for r in ('daily_return','activity_rank','cashflow_strength','market_cap_rank')}
        self.settings={'region':'USA','universe':'TOP3000','delay':1,'decay':0}

    def trial(self, key, ast=None, quality='weak', parent=None):
        ast=ast or proposal()['ast'];ids=learning.identities(ast,self.bindings,self.settings)
        doc={**ids,'ast':ast,'settings':self.settings,'roles':strategy.dsl.roles_used(ast),'parent_id':parent,'edit':[]}
        if parent:
            old=json.loads(self.c.execute('SELECT document_json FROM learning_trials WHERE trial_id=?',(parent,)).fetchone()[0])
            doc['edit']=learning.ast_diff({'ast':old['ast'],'settings':self.settings},{'ast':ast,'settings':self.settings})
        learning.record_trial(self.c,key,doc,task_id='test-'+key)
        learning.append_outcome(self.c,key,{'execution':'complete','quality':quality,'evidence_complete':True})
        return doc

    def test_ordered_mechanism_ignores_only_window_rank_and_sign(self):
        ast=proposal()['ast'];other=copy.deepcopy(ast);other['arg']['window']=120
        self.assertEqual(strategy.mechanism(ast),strategy.mechanism(other))
        wrapped={'op':'neg','arg':ast}
        self.assertEqual(strategy.mechanism(ast),strategy.mechanism(wrapped))
        self.assertNotEqual(strategy.mechanism(ast),strategy.mechanism(proposal('std')['ast']))

    def test_edits_are_bounded_single_changes_and_compile(self):
        ast=proposal()['ast'];edits=strategy.finite_edits(ast,self.bindings,2)
        self.assertEqual(len(edits),2)
        for e in edits:
            self.assertEqual(len(learning.ast_diff(ast,e['ast'])),1)
            strategy.dsl.compile_ast(e['ast'],self.bindings)
        with self.assertRaises(ValueError):strategy.finite_edits(ast,self.bindings,100)

    def test_metadata_missing_and_wrong_scope_do_not_claim_coverage(self):
        self.bindings['daily_return']['measurement']={'coverage':.99,'evidence_hash':'x','as_of':'2026-01-01','scope':{'region':'CHN'}}
        result=strategy.measurement_contract(proposal()['ast'],self.bindings,self.settings)
        self.assertIsNone(result['roles'][0]['coverage']);self.assertFalse(result['neutral_imputation_enabled'])

    def test_retrieval_prioritizes_structure_and_preserves_negative_slot(self):
        ast=proposal()['ast'];self.trial('matching',ast,quality='usable')
        self.trial('negative',proposal('std')['ast'])
        result=strategy.retrieval(self.c,self.bindings,self.settings,ast,2)
        self.assertEqual({x['trial_id'] for x in result},{'matching','negative'})
        self.assertEqual(strategy.retrieval(self.c,self.bindings,{**self.settings,'delay':0}),[])

    def test_one_family_many_children_cannot_activate_rule(self):
        ast=proposal()['ast'];self.trial('parent',ast)
        child=copy.deepcopy(ast);child['arg']['window']=120
        for i in range(5):self.trial('child'+str(i),child,'usable','parent')
        learning.derive_rules(self.c)
        advice=strategy.rule_advice(self.c,child,self.bindings,self.settings)
        self.assertEqual(advice['score'],0)
        self.assertLessEqual(advice.get('support_families',0),1)

    def test_cash_does_not_mix_receivable_credits_or_currencies(self):
        store.add_payment(self.c,None,'received',20,'USD','2026-01-01','receipt')
        store.add_payment(self.c,None,'confirmed_payable',100,'USD','2026-01-01','statement')
        store.add_expense(self.c,'cash',3,'USD','2026-01-01','receipt')
        store.add_expense(self.c,'credit',50,'call','2026-01-01','quota')
        result=metrics.economics(self.c)
        self.assertEqual(result['ledger_by_unit']['USD']['recorded_net_cash'],17)
        self.assertIsNone(result['human_seconds'])
        self.assertIsNone(result['allocation_to_alpha'])

    def test_maintenance_is_disabled_without_optin_and_does_not_dispatch(self):
        self.assertEqual(maintenance.tick(self.c,self.cfg),{'status':'disabled'})
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_maintenance_respects_pause(self):
        self.cfg.data['research_learning']={'maintenance_enabled':True}
        store.set_flag(self.c,'paused','1')
        # use public pause method because pause representation belongs to store
        self.assertEqual(maintenance.tick(self.c,self.cfg,True)['status'],'paused')

    def snapshot(self, key, values, start='2025-01-01', origin='platform_collection'):
        import datetime as dt
        first=dt.date.fromisoformat(start)
        rows=[[str(first+dt.timedelta(days=i)),v] for i,v in enumerate(values)]
        pnl={'schema':{'properties':[{'name':'date'},{'name':'pnl'}]},'records':rows}
        path=Path(self.tmp.name)/(key+'-'+str(len(values))+'.json');path.write_text(json.dumps(pnl))
        oid=learning.capture_feedback(self.c,key,{'pnl_path':str(path)},origin=origin,
                                      data_through=rows[-1][0],content_hash=util.sha256_json(pnl))
        learning.append_outcome(self.c,key,{'execution':'complete','quality':'usable','evidence_complete':True,
                                           'observation_id':oid,'origin':origin})
        return path

    def test_forward_excludes_prefreeze_and_uses_frozen_weights(self):
        import datetime as dt
        self.trial('ref',quality='usable');self.trial('candidate',proposal('std')['ast'],quality='usable')
        a=[sum((j%5)-1 for j in range(i)) for i in range(101)]
        b=[sum((j%3)-.4 for j in range(i)) for i in range(101)]
        self.snapshot('ref',a[:61]);self.snapshot('candidate',b[:61])
        self.c.execute("UPDATE learning_trials SET available_at='2025-03-01T00:00:00.000+00:00'")
        self.c.execute("UPDATE learning_outcomes SET available_at='2025-03-01T00:00:00.000+00:00'")
        with patch('wq.util.now',return_value=dt.datetime(2025,3,2,tzinfo=dt.timezone.utc)):
            c=metrics.freeze_contract(self.c,'pool',['ref'],'candidate',minimum=20,forward_minimum=20)
        self.assertEqual(c['status'],'frozen')
        self.assertEqual(metrics.forward_report(self.c)[0]['observations'],0)
        self.snapshot('ref',a);self.snapshot('candidate',b)
        result=metrics.forward_report(self.c)[0]
        self.assertEqual(result['status'],'evaluated');self.assertEqual(result['observations'],40)
        saved=json.loads(self.c.execute('SELECT document_json FROM learning_pool_contracts').fetchone()[0])
        self.assertEqual(saved['reference_fit'],c['reference_fit'])
        self.assertIsNone(result['cash_return'])

    def test_revised_snapshot_fails_closed(self):
        self.trial('x',quality='usable');p=self.snapshot('x',[0,1,3,2])
        p.write_text('{}')
        with self.assertRaisesRegex(ValueError,'revised'):metrics.series_for(self.c,learning.as_of(self.c)[0])

    def test_missingness_audit_checks_point_in_time_and_intersection(self):
        from wq.research_measurement import audit_panel
        scope={'region':'USA','universe':'TOP3000','delay':1}
        rows=[{'date':'2026-01-02T00:00:00Z','asset':'a','available_at':'2026-01-01T00:00:00Z','left':1,'right':None},
              {'date':'2026-01-03T00:00:00Z','asset':'a','available_at':'2026-01-02T00:00:00Z','left':1,'right':2}]
        result=audit_panel({'scope':scope,'source':'fixture','rows':rows})
        self.assertEqual(result['coverage']['intersection'],.5)
        self.assertEqual(result['coverage']['union'],1)
        self.assertEqual(result['observed_change_fraction']['left'],0)
        self.assertFalse(result['neutral_missing_validated'])
        rows[0]['available_at']='2026-02-01T00:00:00Z'
        with self.assertRaises(ValueError):audit_panel({'scope':scope,'source':'fixture','rows':rows})

    def test_family_collision_allows_topology_but_not_window_spam(self):
        a,b,c=[{'op':'field','name':n} for n in list(self.bindings)[:3]]
        x={'op':'mul','left':{'op':'add','left':a,'right':b},'right':c}
        y={'op':'add','left':a,'right':{'op':'mul','left':b,'right':c}}
        family=learning.identities(x,self.bindings,self.settings)['family_id']
        now=util.now_iso()
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,candidate_json,family_hash,created_at,updated_at) VALUES('closed','{}','x',?,?,?,?)",
                       (json.dumps({'ast':x}),family,now,now))
        self.assertTrue(strategy.family_decision(self.c,y,family,99,{},False)['blocked'])
        self.assertFalse(strategy.family_decision(self.c,y,family,99,{},True)['blocked'])
        self.assertTrue(strategy.family_decision(self.c,x,family,99,{},True)['blocked'])
        self.assertTrue(strategy.family_decision(self.c,y,family,99,{'known_family_hashes':[family]},True)['blocked'])

    def test_shadow_choice_is_saved_without_changing_baseline(self):
        p={'plans':[{'candidate':proposal(op),'measurement':'past observable measurement','prediction':'test on unseen market dates','falsifier':'fails the fixed forward comparison'} for op in ('mean','std')]}
        with patch('wq.research_strategy.rule_advice',side_effect=[{'score':0,'parent_id':None},{'score':1,'parent_id':None}]):
            chosen=learning.select_plans(self.c,1,p,self.bindings,self.settings,use_rules=False)
        saved=json.loads(self.c.execute('SELECT document_json FROM learning_selections WHERE cycle_id=1').fetchone()[0])
        self.assertEqual(saved['selected_index'],0);self.assertEqual(saved['shadow_selected_index'],1)
        self.assertFalse(saved['rules_applied'])

    def test_exploration_slot_cannot_be_eliminated_by_rules(self):
        p={'plans':[{'candidate':proposal(op),'measurement':'past observable measurement','prediction':'test on unseen market dates','falsifier':'fails the fixed forward comparison'} for op in ('mean','std')]}
        with patch('wq.research_strategy.rule_advice',side_effect=[{'score':0,'parent_id':None},{'score':1,'parent_id':None}]):
            learning.select_plans(self.c,4,p,self.bindings,self.settings,use_rules=True)
        saved=json.loads(self.c.execute('SELECT document_json FROM learning_selections').fetchone()[0])
        self.assertEqual(saved['selected_index'],0);self.assertFalse(saved['rules_applied'])

    def test_scoped_catalog_exposes_truncation_and_incomplete_no_match(self):
        from wq import catalog
        from test_catalog import QUERY, fields, FakeClient
        doc=catalog.fetch_catalog(FakeClient(fields(10)),QUERY,sleep=lambda _:None)
        result=catalog.search_scoped(doc,QUERY,limit=2)
        self.assertTrue(result['truncated']);self.assertEqual(result['returned'],2)
        doc['complete']=False
        self.assertFalse(catalog.search_scoped(doc,QUERY,text='absent')['no_match_is_exhaustive'])
        with self.assertRaises(ValueError):catalog.search_scoped(doc,{**QUERY,'delay':0})

    def test_rejected_legacy_ast_remains_in_coverage_but_not_executable(self):
        now=util.now_iso();candidate=proposal();candidate['ast']={'op':'field','name':'daily_return'}
        policy={'bindings':self.bindings,'settings':self.settings}
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,candidate_json,created_at,updated_at) VALUES('closed',?,'x',?,?,?)",
                       (json.dumps(policy),json.dumps(candidate),now,now))
        learning.sync(self.c);learning.sync(self.c)
        report=metrics.coverage(self.c)
        self.assertTrue(report['cycle_coverage_complete']);self.assertEqual(report['rejected_ast_cycles'],1)
        self.assertIsNone(learning.as_of(self.c)[0]['document']['ast'])

    def test_gate_shadow_keeps_missing_inputs_unknown_and_never_applies(self):
        self.c.execute('INSERT INTO research_feedback VALUES(?,?,?,?)',('fixture','x',json.dumps({'temporal':[],'platform_blockers':['x']}),util.now_iso()))
        result=metrics.gate_audit(self.c,{'min_sharpe':.5,'min_fitness':.3,'min_years':3,'max_negative_years':1})
        self.assertIsNone(result['rows'][0]['shadow_temporal_pass']);self.assertFalse(result['applied'])

    def test_pool_rejects_incomparable_settings(self):
        self.trial('a',quality='usable')
        self.settings={**self.settings,'region':'CHN'}
        self.trial('b',quality='usable')
        with self.assertRaisesRegex(ValueError,'settings must match'):
            metrics.freeze_contract(self.c,'bad',['a'],'b')

    def test_maintenance_freezes_one_bounded_experiment_without_network(self):
        self.cfg.data['research_learning']={'maintenance_enabled':True,'auto_experiments':True,'experiment_cycles':4,'requests_per_arm':3}
        self.cfg.data['brain_api']={'authorized_until':'2099-01-01T00:00:00Z'}
        with patch('wq.autopilot.policy',return_value={'settings':self.settings,'bindings':self.bindings}), \
             patch('wq.autopilot.enabled',return_value=True), \
             patch('wq.research_learning.current_baseline',return_value={k:'test' for k in ('source','policy','models','budget','usable_definition')}), \
             patch('wq.brain_client.BrainClient.request',side_effect=AssertionError('network')):
            first=maintenance.tick(self.c,self.cfg,True)
            self.assertIn('new_experiment',first)
            self.assertNotIn('new_experiment',maintenance.tick(self.c,self.cfg,True))
            self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_experiments').fetchone()[0],1)
            self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
            learning.stop_experiment(self.c,first['new_experiment'],'User stops this comparison')
            self.assertEqual(maintenance.tick(self.c,self.cfg,True)['learning_state']['mode'],'baseline')

    def test_degenerate_pool_is_unavailable_not_a_maintenance_failure(self):
        self.trial('a',quality='usable');self.trial('b',proposal('std')['ast'],quality='usable')
        values=[sum((j%3)-.4 for j in range(i)) for i in range(61)]
        self.snapshot('a',values);self.snapshot('b',[-x for x in values])
        result=metrics.freeze_contract(self.c,'cancelled',['a'],'b',minimum=20)
        self.assertEqual(result['status'],'unavailable')
        self.assertIn('calibration',result['errors'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_pool_contracts').fetchone()[0],1)

    def test_refresh_rotates_even_when_market_cutoff_never_advances(self):
        from wq import brain_jobs
        brain_jobs.setup(self.c)
        self.cfg.data['research_learning']={'maintenance_enabled':True,'refresh_enabled':True,'refresh_per_day':1}
        self.cfg.data['brain_api']={'enabled':True,'authorized_until':'2099-01-01T00:00:00Z'}
        for key in ('a','b'):
            self.trial(key,quality='usable');self.snapshot(key,[0,1,3,2])
            tid,_=store.enqueue_task(self.c,'brain_simulation',{'test':key})
            self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
            self.c.execute('UPDATE learning_trials SET task_id=? WHERE trial_id=?',(tid,key))
            self.c.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',(tid,'complete',None,key,None,util.now_iso(),util.now_iso()))
        self.c.execute("UPDATE learning_observations SET available_at=CASE alpha_id WHEN 'a' THEN '2025-01-01' ELSE '2025-01-02' END")
        original=learning.as_of(self.c)
        for t in original:
            t['outcome'].update(data_through='2025-01-04',content_hash='fixture')
        calls=[]
        def refresh(conn,cfg,aid):calls.append(aid);return 'read-only-test',False
        with patch('wq.research_learning.sync',return_value={}), \
             patch('wq.research_learning.as_of',return_value=original), \
             patch('wq.autopilot.policy',return_value={'settings':self.settings,'bindings':self.bindings}), \
             patch('wq.research_learning.freeze_pool'),patch('wq.research_metrics.freeze_contract'), \
             patch('wq.feedback.enqueue_refresh',side_effect=refresh):
            maintenance.tick(self.c,self.cfg,True)
            self.assertEqual(calls[0],'a')
            calls.clear()
            self.c.execute("UPDATE learning_observations SET available_at='2025-02-01' WHERE alpha_id='a'")
            maintenance.tick(self.c,self.cfg,True)
            self.assertEqual(calls[0],'b')
