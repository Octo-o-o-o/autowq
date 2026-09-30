import copy
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch

from helpers import make_env
from test_autopilot import proposal
from wq import research_learning as l, research_dsl, autopilot, feedback, store, util


def bindings():
    return {name:{'expression':'private_'+name,'fields':['private_'+name], 'source':'fixture'} for name in research_dsl.ROLES}


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg,self.c=make_env(self.tmp.name);self.addCleanup(self.c.close)
        autopilot.setup(self.c);feedback.setup(self.c);l.setup(self.c)
        self.bindings=bindings();self.settings={'region':'USA','universe':'TOP3000','delay':1,'decay':0}

    def test_display_and_submission_settings_do_not_invalidate_research_baseline(self):
        with patch('wq.autopilot.policy',return_value={'fixture':True}):
            original=l.current_baseline(self.cfg)
            for key in ('ui','notifications','brain_submission'):
                self.cfg.data[key]={'changed':'fixture'}
                self.assertEqual(original,l.current_baseline(self.cfg))
            for key in ('models','brain_api','autopilot','debug_authorization','research_learning'):
                old=copy.deepcopy(self.cfg.data.get(key))
                self.cfg.data[key]={'changed':'fixture'}
                self.assertNotEqual(original,l.current_baseline(self.cfg))
                if old is None:self.cfg.data.pop(key)
                else:self.cfg.data[key]=old

    def trial(self,key,ast=None,parent=None,settings=None):
        ast=ast or proposal()['ast'];settings=settings or self.settings
        ids=l.identities(ast,self.bindings,settings)
        doc={**ids,'ast':ast,'settings':settings,'roles':research_dsl.roles_used(ast),'parent_id':parent,'edit':[]}
        if parent:
            p=json.loads(self.c.execute('SELECT document_json FROM learning_trials WHERE trial_id=?',(parent,)).fetchone()[0])
            doc['scope_id']=p['scope_id'];doc['edit']=l.ast_diff({'ast':p['ast'],'settings':p['settings']},{'ast':ast,'settings':settings})
        l.record_trial(self.c,key,doc);return doc

    def outcome(self,key,quality='weak'):
        l.append_outcome(self.c,key,{'execution':'complete','quality':quality,'evidence_complete':True})

    def test_structure_collision_is_not_execution_collision_and_old_family_preserved(self):
        a,b,c=[{'op':'field','name':n} for n in list(self.bindings)[:3]]
        x={'op':'mul','left':{'op':'add','left':a,'right':b},'right':c}
        y={'op':'add','left':a,'right':{'op':'mul','left':b,'right':c}}
        ix=l.identities(x,self.bindings,self.settings);iy=l.identities(y,self.bindings,self.settings)
        self.assertEqual(ix['family_id'],iy['family_id'])
        self.assertNotEqual(ix['structure_id'],iy['structure_id']);self.assertNotEqual(ix['execution_id'],iy['execution_id'])
        self.assertNotEqual(ix['execution_id'],l.identities(x,self.bindings,{**self.settings,'decay':2})['execution_id'])

    def test_edit_is_actual_and_trial_immutable(self):
        p=self.trial('parent');child=self.trial('child',parent='parent',settings={**self.settings,'decay':2})
        self.assertEqual(child['edit'],[{'path':'$.settings.decay','before':0,'after':2}])
        child['edit']=[]
        with self.assertRaises(ValueError):l.record_trial(self.c,'child2',child)
        p['settings']={'decay':8}
        with self.assertRaises(ValueError):l.record_trial(self.c,'parent',p)

    def test_unknown_is_not_failed_or_usable(self):
        self.trial('x')
        with self.assertRaises(ValueError):l.append_outcome(self.c,'x',{'execution':'unknown','quality':'weak'})
        with self.assertRaises(ValueError):l.append_outcome(self.c,'x',{'execution':'complete','quality':'usable','evidence_complete':False})
        l.append_outcome(self.c,'x',{'execution':'unknown','quality':'unassessed'})
        self.assertEqual(l.as_of(self.c)[0]['outcome']['execution'],'unknown')

    def test_asof_never_backdates_import_or_uses_future_correction(self):
        with patch('wq.util.now_iso',return_value='2026-01-02T00:00:00.000+00:00'):
            self.trial('x');self.outcome('x')
        with patch('wq.util.now_iso',return_value='2026-01-03T00:00:00.000+00:00'):
            self.outcome('x','usable')
        self.assertEqual(l.as_of(self.c,'2026-01-01T00:00:00Z'),[])
        self.assertEqual(l.as_of(self.c,'2026-01-02T08:00:00+08:00')[0]['outcome']['quality'],'weak')
        self.assertEqual(l.as_of(self.c,'2026-01-04T00:00:00Z')[0]['outcome']['quality'],'usable')
        self.outcome('x','weak')
        self.assertEqual(l.as_of(self.c)[0]['outcome']['quality'],'weak')
        count=self.c.execute('SELECT COUNT(*) FROM learning_outcomes').fetchone()[0]
        self.outcome('x','weak');self.assertEqual(count,self.c.execute('SELECT COUNT(*) FROM learning_outcomes').fetchone()[0])

    def test_shadow_rule_keeps_counterexamples_and_expires_without_refreshing_same_evidence(self):
        self.trial('p');self.outcome('p')
        self.trial('c',parent='p',settings={**self.settings,'decay':2});self.outcome('c','usable')
        created=l.derive_rules(self.c);self.assertEqual(len(created),1)
        rule=l.rules(self.c)[0];self.assertEqual(rule['mode'],'shadow');self.assertIsNone(rule['confidence_probability'])
        self.assertEqual(rule['independent_families'],1)
        self.assertEqual(l.derive_rules(self.c),[])
        self.outcome('c','weak');l.derive_rules(self.c)
        rule=l.rules(self.c)[0];self.assertEqual(len(rule['support']),0);self.assertEqual(len(rule['contradictions']),1)
        self.assertTrue(l.rules(self.c,'2099-01-01T00:00:00Z')[0]['expired'])
        self.assertEqual(l.derive_rules(self.c),[])

    def test_model_packet_never_contains_private_bindings_raw_results_or_text(self):
        self.trial('x');self.outcome('x')
        packet=json.dumps(l.model_context(self.c,self.bindings,self.settings))
        self.assertNotIn('private_',packet);self.assertNotIn('expression',packet);self.assertNotIn('sharpe',packet)
        self.assertIn('weak',packet)
        changed={'different':self.bindings['daily_return']}
        self.assertEqual(l.model_context(self.c,changed,self.settings)['facts'],[])

    def plan(self,candidate):
        return {'candidate':candidate,'measurement':'Observable past returns only','prediction':'Future relative ranking can falsify it','falsifier':'No stable association in later evidence'}

    def test_bounded_plan_selection_eliminates_exact_duplicate_without_platform_calls(self):
        self.trial('existing')
        p={'plans':[self.plan(proposal()),self.plan(proposal('std'))]}
        candidate=l.select_plans(self.c,1,p,self.bindings,self.settings)
        self.assertEqual(candidate['ast'],proposal('std')['ast'])
        saved=json.loads(self.c.execute('SELECT document_json FROM learning_selections').fetchone()[0])
        self.assertEqual(saved['selected_index'],1);self.assertFalse(saved['rules_applied'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(candidate,l.select_plans(self.c,1,p,self.bindings,self.settings))
        with self.assertRaises(ValueError):l.select_plans(self.c,2,{'plans':[self.plan(proposal())]*4},self.bindings,self.settings)
        with self.assertRaises(ValueError):l.select_plans(self.c,2,{'plans':p['plans'],'candidate':proposal()},self.bindings,self.settings)

    def test_feedback_versions_preserve_content_revision_and_recompute_origin(self):
        l.capture_feedback(self.c,'A',{'collection_status':'complete'},origin='platform_collection',data_through='2026-01-01',content_hash='old')
        l.capture_feedback(self.c,'A',{'collection_status':'complete'},origin='local_recompute',data_through='2026-01-01',content_hash='new')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_observations').fetchone()[0],2)
        self.assertEqual(l.report(self.c)['forward_retention'],[])

    def test_correlation_identity_changes_when_same_dates_are_revised(self):
        def pnl(values):return {'schema':{'properties':[{'name':'date'},{'name':'pnl'}]},'records':[[f'2026-01-0{i+1}',v] for i,v in enumerate(values)]}
        x,y=pnl([0,1,3,6]),pnl([0,2,1,6])
        old=feedback.correlation(x,y,2)
        y['records'][1][1]=4
        new=feedback.correlation(x,y,2)
        self.assertNotEqual(old['contract']['right_content_hash'],new['contract']['right_content_hash'])
        self.assertEqual(old['contract']['aligned_intervals_hash'],new['contract']['aligned_intervals_hash'])

    def test_frozen_experiment_is_prospective_bounded_and_baseline_bound(self):
        baseline={k:'fixture' for k in ('source','policy','models','budget','usable_definition')}
        l.freeze_experiment(self.c,'exp',baseline,2)
        def new_cycle(cid):
            self.c.execute("UPDATE research_cycles SET state='closed'")
            self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(?,'researching','{}','x',?,?)",(cid,util.now_iso(),util.now_iso()))
        new_cycle(1)
        self.assertEqual(l.assign(self.c,1,'exp',baseline),'baseline')
        new_cycle(2)
        with self.assertRaises(ValueError):l.assign(self.c,2,'exp',{**baseline,'source':'changed'})
        self.assertEqual(l.assign(self.c,2,'exp',baseline),'learning')
        new_cycle(3)
        with self.assertRaises(ValueError):l.assign(self.c,3,'exp',baseline)
        self.assertEqual(l.report(self.c,experiment_id='exp')['arms']['learning']['allocated_cycles'],1)

    def test_sync_import_is_idempotent_preserves_transaction_and_no_network(self):
        candidate=proposal();policy={'bindings':self.bindings,'settings':self.settings}
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,candidate_json,created_at,updated_at) VALUES('closed',?,'x',?,?,?)",(json.dumps(policy),json.dumps(candidate),util.now_iso(),util.now_iso()))
        self.c.execute('BEGIN IMMEDIATE')
        with patch('wq.brain_client.BrainClient.request',side_effect=AssertionError('network')):
            l.sync(self.c);l.sync(self.c)
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_trials').fetchone()[0],1)
        self.c.rollback();self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_trials').fetchone()[0],0)

    def test_declared_intermediate_types_and_units_are_checked(self):
        b=copy.deepcopy(self.bindings)
        b['daily_return']['expression_type']='vector'
        with self.assertRaises(ValueError):research_dsl.compile_ast(proposal()['ast'],b)
        b=copy.deepcopy(self.bindings)
        b['daily_return']['measurement']={'unit':'return'}
        b['market_cap_rank']['measurement']={'unit':'currency'}
        ast={'op':'sub','left':{'op':'mean','arg':{'op':'field','name':'daily_return'},'window':20},'right':{'op':'field','name':'market_cap_rank'}}
        with self.assertRaisesRegex(ValueError,'量纲'):research_dsl.compile_ast(ast,b)
        ast['left']={'op':'rank','arg':ast['left']};ast['right']={'op':'rank','arg':ast['right']}
        research_dsl.compile_ast(ast,b)

    def test_variants_bind_to_actual_base_and_recognize_only_exact_reverse(self):
        from wq import brain_jobs
        brain_jobs.setup(self.c)
        cand=proposal();policy={'bindings':self.bindings,'settings':self.settings}
        expr=research_dsl.compile_ast(cand['ast'],self.bindings)[0]
        base,_=store.enqueue_task(self.c,'brain_simulation',{'request':{'regular':expr,'settings':self.settings}})
        flip,_=store.enqueue_task(self.c,'brain_simulation',{'request':{'regular':'reverse('+expr+')','settings':self.settings}})
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,candidate_json,simulation_task,created_at,updated_at) VALUES('closed',?,'x',?,?,?,?)",(json.dumps(policy),json.dumps(cand),base,util.now_iso(),util.now_iso()))
        self.c.execute('INSERT INTO cycle_simulations VALUES(?,?,?,?,?)',(1,'sign_flip',flip,'fixture',util.now_iso()))
        l.sync(self.c)
        row=self.c.execute('SELECT document_json FROM learning_trials WHERE trial_id=?',('task:'+flip,)).fetchone()
        child=json.loads(row[0]);self.assertEqual(child['parent_id'],'task:'+base)
        self.assertEqual(child['ast'],{'op':'neg','arg':cand['ast']})
        self.assertTrue(child['edit'])
        l.sync(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_trials').fetchone()[0],3)

    def test_frozen_pool_requires_new_market_dates_not_local_recompute(self):
        doc=self.trial('x')
        self.c.execute("UPDATE learning_trials SET task_id='fixture' WHERE trial_id='x'")
        outcome={'execution':'complete','quality':'usable','evidence_complete':True,'origin':'platform_collection',
                 'data_through':'2026-01-01','content_hash':'old'}
        l.append_outcome(self.c,'x',outcome);l.freeze_pool(self.c,'pool',['x'])
        self.assertEqual(l.pool_report(self.c)[0]['new_data_evaluable'],0)
        l.append_outcome(self.c,'x',{**outcome,'origin':'local_recompute','data_through':'2026-02-01','content_hash':'new'})
        self.assertEqual(l.pool_report(self.c)[0]['new_data_evaluable'],0)
        l.append_outcome(self.c,'x',{**outcome,'data_through':'2026-02-01','content_hash':'new'})
        self.assertEqual(l.pool_report(self.c)[0]['quality_retention_rate'],1)
        l.append_outcome(self.c,'x',{**outcome,'quality':'weak','data_through':'2026-03-01','content_hash':'newer'})
        self.assertEqual(l.pool_report(self.c)[0]['quality_retention_rate'],0)

    def test_request_cap_counts_queued_failed_and_unknown_before_dispatch(self):
        baseline={k:'fixture' for k in ('source','policy','models','budget','usable_definition')}
        l.freeze_experiment(self.c,'exp',baseline,2,max_requests_per_arm=1)
        self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(1,'researching','{}','x',?,?)",(util.now_iso(),util.now_iso()))
        l.assign(self.c,1,'exp',baseline)
        l.check_request_budget(self.c,1)
        tid,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':1})
        for state in ('queued','failed','unknown'):
            self.c.execute('UPDATE tasks SET status=? WHERE task_id=?',(state,tid))
            with self.assertRaisesRegex(ValueError,'budget exhausted'):l.check_request_budget(self.c,1)

    def test_readonly_refresh_is_versioned_idempotent_and_preserves_old_files(self):
        from pathlib import Path
        from wq import importer
        from test_feedback import recordset
        alpha={'id':'real','regular':{'code':'rank(x)'},'settings':{},'is':{'sharpe':1.5,'checks':[]}}
        source=Path(self.cfg.private_dir)/'source.json';util.write_json(str(source),alpha)
        importer.import_result_obj(self.c,self.cfg,{'schema':'wq.imported-result/v1','source':'api','synthetic':False,
            'simulation':{'remote_id':'real','expression':'rank(x)','config':{},'stats':{'sharpe':1.5},'checks':{}},
            'evidence':{'path':str(source),'purpose':'research_validation'}},True)
        old=Path(self.cfg.private_dir)/'research-feedback'/'real'/'pnl.json';util.write_json(str(old),{'old':'preserve'})
        tid,created=feedback.enqueue_refresh(self.c,self.cfg,'real');self.assertTrue(created)
        self.assertEqual(feedback.enqueue_refresh(self.c,self.cfg,'real'),(tid,False))
        payload=json.loads(self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?',(tid,)).fetchone()[0])
        pnl=recordset(['date','pnl'],[['2026-01-01',0],['2026-01-02',1]])
        year=recordset(['year','sharpe','fitness','pnl'],[['2025',1,.9,1]])
        with patch('wq.feedback.get_with_reauth',side_effect=[(200,{},alpha),(200,{},pnl),(200,{},year),(200,{}, {'is':{'checks':[]}})]) as get:
            for _ in range(4):feedback.step(self.c,self.cfg,{'task_id':tid},payload)
            self.assertEqual(feedback.step(self.c,self.cfg,{'task_id':tid},payload)[0],'succeeded')
            self.assertTrue(all(call.args[2].startswith('/alphas/') for call in get.call_args_list))
        self.assertEqual(util.read_json(str(old)),{'old':'preserve'})
        report=json.loads(self.c.execute("SELECT report_json FROM research_feedback WHERE alpha_id='real'").fetchone()[0])
        self.assertIn('/observations/',report['pnl_path'])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],0)
        self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
        self.assertEqual(feedback.enqueue_refresh(self.c,self.cfg,'real'),(tid,False))
        l.sync(self.c)
        self.assertEqual(l.report(self.c)['execution_counts']['unknown'],1)

    def test_explicit_stop_blocks_new_allocations_and_dispatch(self):
        baseline={k:'fixture' for k in ('source','policy','models','budget','usable_definition')}
        l.freeze_experiment(self.c,'exp',baseline,2)
        self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(1,'researching','{}','x',?,?)",(util.now_iso(),util.now_iso()))
        l.assign(self.c,1,'exp',baseline)
        l.stop_experiment(self.c,'exp','Evidence gap requires stopping')
        self.assertIsNone(l.active_experiment(self.c))
        with self.assertRaisesRegex(ValueError,'stopped'):l.check_request_budget(self.c,1)
        with self.assertRaisesRegex(ValueError,'stopped'):l.validate_dispatch(self.c,self.cfg,'task',{'research_cycle_id':1})

    def test_dispatch_rechecks_frozen_baseline_and_reservation(self):
        baseline={k:'fixture' for k in ('source','policy','models','budget','usable_definition')}
        l.freeze_experiment(self.c,'exp',baseline,2,max_requests_per_arm=1)
        self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(1,'researching','{}','x',?,?)",(util.now_iso(),util.now_iso()))
        l.assign(self.c,1,'exp',baseline)
        first,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':1})
        second,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':1})
        with patch('wq.research_learning.current_baseline',return_value=baseline):
            l.validate_dispatch(self.c,self.cfg,first,{'research_cycle_id':1})
            with self.assertRaisesRegex(ValueError,'reservation'):l.validate_dispatch(self.c,self.cfg,second,{'research_cycle_id':1})
        with patch('wq.research_learning.current_baseline',return_value={**baseline,'source':'changed'}):
            with self.assertRaisesRegex(ValueError,'baseline changed'):l.validate_dispatch(self.c,self.cfg,first,{'research_cycle_id':1})

    def test_retrieval_excludes_changed_bindings_or_market_settings(self):
        self.trial('x');self.outcome('x')
        altered=copy.deepcopy(self.bindings);altered['daily_return']['expression']='different_private_field'
        self.assertEqual(l.model_context(self.c,altered,self.settings)['facts'],[])
        self.assertEqual(l.model_context(self.c,self.bindings,{**self.settings,'universe':'TOP1000'})['facts'],[])
        self.assertEqual(l.model_context(self.c,self.bindings,{**self.settings,'decay':8})['facts'],[])

    def test_plan_selection_prefers_new_concept_over_new_alias_in_used_cluster(self):
        self.bindings['daily_return']['cluster']='price'
        self.bindings['activity_rank']['cluster']='price'
        self.bindings['cashflow_strength']['cluster']='fundamental'
        self.trial('previous')
        alias=proposal('std');alias['ast']['arg']['arg']['name']='activity_rank'
        different=proposal('std');different['ast']['arg']['arg']['name']='cashflow_strength'
        selected=l.select_plans(self.c,4,{'plans':[self.plan(alias),self.plan(different)]},self.bindings,self.settings)
        self.assertEqual(selected,different)
