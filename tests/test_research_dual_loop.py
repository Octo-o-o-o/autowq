import copy
import datetime as dt
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from wq import util,store,runner,research_framework as framework,research_meta as meta,research_evidence as evidence
from wq import research_learning as learning,research_strategy as strategy
import test_research_framework as framework_fixture


class DualLoopTests(unittest.TestCase):
    def setUp(self):
        self.f=framework_fixture.FrameworkTests('test_gap_collection_uses_task_ledger_and_does_not_claim_semantics');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c,self.cfg,self.p,self.root=self.f.c,self.f.cfg,self.f.p,self.f.root
        self.cfg.data['research_dual_loop']={'enabled':True,'root_id':'fixture-dual','valid_until':'2099-01-01T00:00:00Z','learning_root':'fixture-dual-epoch'}

        learning.freeze_experiment(self.c,'fixture-dual-epoch',learning.current_baseline(self.cfg),10,max_requests_per_arm=6)

    def no_steps(self):
        p=copy.deepcopy(self.p)
        for h in p['campaign']['hypotheses']:
            h.pop('steps',None);h['required_roles']=[next(iter(p['campaign']['execution_profiles']['base']['bindings']))]
        return p

    def test_steps_are_not_cached_without_proven_dependency_closure(self):
        self.assertIsNone(meta.signature(self.c,self.p))
        calls=[]
        for _ in range(3):meta.sync_gaps(self.c,self.cfg,self.p,lambda c,p:calls.append(1) or [])
        self.assertEqual(len(calls),3);self.assertEqual(meta.state(self.c)['mode'],'full')

    def test_state_changing_full_call_is_not_cached_and_stable_shadow_is_bounded(self):
        p=self.no_steps();meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        self.assertEqual(store.get_flag(self.c,'research_meta_cache'),'null')
        meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        for _ in range(8):meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        s=meta.state(self.c);self.assertEqual(len(s['pairs']),8)
        self.assertIn(s['status'],('active_engineering','inconclusive'))
        self.assertEqual(s['bundle_hash'],util.sha256_json(meta.BUNDLE))

    def test_cached_output_mismatch_rolls_back_before_consumption(self):
        p=self.no_steps();meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps);meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        result=meta.sync_gaps(self.c,self.cfg,p,lambda c,p:[{'different':'guardrail'}])
        self.assertEqual(result,[{'different':'guardrail'}]);self.assertEqual(meta.state(self.c)['status'],'rejected')
        self.assertEqual(meta.state(self.c)['mode'],'full')

    def test_source_changes_and_expiry_invalidate_signature(self):
        p=self.no_steps();framework.sync_gaps(self.c,p)
        gap=next(iter(framework.latest(self.c,'research_gap','gap_id').values()))
        path=self.root/'material.json';path.write_text('material')
        source={'gap_id':gap['gap_id'],'revision':1,'adapter':'local_material_v1','source':{'path':str(path),'sha256':framework.file_identity(path),'source_ref':'fixture://source'},'approved_by':'fixture','valid_until':(util.now()+dt.timedelta(seconds=2)).isoformat(),'max_attempts':3}
        framework.register_source(self.c,self.cfg,source);before=meta.signature(self.c,p)
        path.write_text('changed');self.assertNotEqual(before,meta.signature(self.c,p))
        with patch('wq.util.now',return_value=util.now()+dt.timedelta(seconds=3)):
            self.assertNotEqual(before,meta.signature(self.c,p))
        path.unlink();self.assertIsNone(meta.signature(self.c,p))

    def test_superseded_transition_cannot_poison_next_cached_return(self):
        p=self.no_steps();meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        deleted=p['campaign']['hypotheses'].pop()
        result=meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        self.assertTrue(any(g['hypothesis']==deleted['id'] and g['state']=='superseded' for g in result))
        self.assertEqual(store.get_flag(self.c,'research_meta_cache'),'null')
        result=meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        self.assertFalse(any(g['hypothesis']==deleted['id'] for g in result))

    def test_no_steps_assertion_is_not_semantic_validation(self):
        p=self.no_steps();gaps=framework.sync_gaps(self.c,p)
        self.assertTrue(all(g['state']!='validated' for g in gaps))
        self.assertTrue(all(g['fields'] for g in gaps))

    def test_metadata_collection_verify_handoff_and_incoming_material_resume(self):
        gap,source,path,_=self.f.gap_source()
        # Do not auto-register other fixture sources in this focused real state-machine exercise.
        framework.sync_gaps(self.c,self.p)
        job=framework.evidence_candidates(self.c,self.cfg)[0]
        tid,_=store.enqueue_task(self.c,'research_evidence',job,job['id']);self.c.commit()
        task=store.claim_task(self.c,'dual');store.set_task_running(self.c,tid)
        status,detail,error=runner.dispatch_task(self.c,self.cfg,task);store.finish_task(self.c,tid,status,detail,error)
        self.assertEqual(status,'succeeded');self.assertEqual(store.get_flag(self.c,'evidence_reads:fixture-dual'),'1')
        verify=evidence.receipt_candidates(self.c,self.cfg)[0];vid,_=store.enqueue_task(self.c,'research_evidence_verify',verify,verify['id']);self.c.commit()
        task=store.claim_task(self.c,'dual');store.set_task_running(self.c,vid)
        status,detail,error=runner.dispatch_task(self.c,self.cfg,task);store.finish_task(self.c,vid,status,detail,error)
        self.assertEqual(status,'succeeded',(detail,error));self.assertEqual(detail['semantic_status'],'insufficient')
        framework.sync_gaps(self.c,self.p)
        self.assertEqual(framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]['state'],'waiting_handoff')
        incoming=Path(detail['handoff_path']);incoming.write_text(json.dumps({'gap_id':gap['gap_id'],'scope':gap['scope'],'source_ref':'fixture://provider','text':'Authoritative fixture document must still be semantically reviewed.'}))
        evidence.incoming(self.c,self.cfg)
        self.assertEqual(len(evidence.receipt_candidates(self.c,self.cfg)),1)
        evidence.incoming(self.c,self.cfg)
        self.assertEqual(len(evidence.receipt_candidates(self.c,self.cfg)),1)
        self.assertEqual(framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]['state'],'collected')

    def test_source_attempt_limit_survives_new_gap_or_revision(self):
        gap,source,_,_=self.f.gap_source()
        for _ in range(3):evidence.reserve_read(self.c,self.cfg,source)
        source.update(revision=2,gap_id='different-gap')
        with self.assertRaisesRegex(ValueError,'exhausted'):evidence.reserve_read(self.c,self.cfg,source)
        self.assertEqual(store.get_flag(self.c,'evidence_reads:fixture-dual'),'3')

    def test_read_root_cap_and_authority_expiry(self):
        _,source,_,_=self.f.gap_source();store.set_flag(self.c,'evidence_reads:fixture-dual','24')
        with self.assertRaisesRegex(ValueError,'exhausted'):evidence.reserve_read(self.c,self.cfg,source)
        self.cfg.data['research_dual_loop']['valid_until']='2000-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'expired'):evidence.reserve_read(self.c,self.cfg,source)

    def test_proposal_or_own_receipt_does_not_refill_discovery_episode(self):
        meta.record_discovery(self.c,self.cfg,700,False,self.p);meta.record_discovery(self.c,self.cfg,701,False,self.p)
        self.assertFalse(meta.discovery_permission(self.c,self.cfg,self.p)['allowed'])
        from wq import research_campaign_v2 as events
        events.append_once(self.c,'fake-new-issue','research_issue_proposed',{'issue_hash':'new-id','issue':{'id':'H-NEW'}})
        self.assertFalse(meta.discovery_permission(self.c,self.cfg,self.p)['allowed'])
        # A real registered source/material change changes evidence inputs.
        self.f.gap_source();self.assertTrue(meta.discovery_permission(self.c,self.cfg,self.p)['allowed'])

    def test_experiment_cycle_limit_is_selectable_without_touching_baseline(self):
        from wq.research_lifecycle import experiment_cycle_budget, set_experiment_cycle_limit, document
        before = document(self.c, 'fixture-dual-epoch')['baseline_hash']
        now = util.now_iso()
        for cycle_id in (1, 2, 3):
            self.c.execute("INSERT INTO learning_assignments VALUES(?,'fixture-dual-epoch','baseline','hash',?)", (cycle_id, now))
        budget = experiment_cycle_budget(self.c)
        self.assertEqual(budget['used'], 3)
        self.assertEqual(budget['limit'], 10)
        raised = set_experiment_cycle_limit(self.c, 80)
        self.assertEqual(raised['limit'], 80)
        self.assertEqual(raised['remaining'], 77)
        self.assertEqual(document(self.c, 'fixture-dual-epoch')['baseline_hash'], before)
        self.assertGreaterEqual(document(self.c, 'fixture-dual-epoch')['max_requests_per_arm'], 80)
        with self.assertRaisesRegex(ValueError, '已用'):
            set_experiment_cycle_limit(self.c, 2)

    def test_daily_cycle_setting_is_not_replaced_by_remaining_budget_pace(self):
        self.f.gap_source()
        from wq import autopilot
        autopilot.setup(self.c)
        now=util.now_iso()
        for _ in range(3):
            self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('closed','{}','x',?,?)",(now,now))
        permit=meta.discovery_permission(self.c,self.cfg,self.p)
        self.assertTrue(permit['allowed'])
        self.assertNotEqual(permit['reason'],'waiting_next_pacing_window')

    def test_empty_wait_never_allows_new_quant_but_does_allow_lifecycle(self):
        self.cfg.data['research_learning']={'maintenance_enabled':False}
        self.cfg.data['research_framework']['enabled']=True
        with patch('wq.research_evidence.prepare'),patch('wq.research_framework.evidence_candidates',return_value=[]),patch('wq.research_evidence.receipt_candidates',return_value=[]),patch('wq.research_framework.reassessment_candidates',return_value=[]):
            result=framework.tick(self.c,self.cfg)
        self.assertFalse(result['allow_research']);self.assertTrue(result['allow_existing_lifecycle'])

    def test_plan_preflight_excludes_top_duplicate_and_freezes_choice(self):
        # Use this fixture's simple approved DSL binding.
        bindings=self.p['bindings'];role=next(r for r,b in bindings.items() if not b.get('group_field'))
        candidate={'title':'Fixture title','hypothesis':'Fixture mechanism measurable','counterexample':'Fixture falsifier measurable','ast':{'op':'mean','arg':{'op':'field','name':role},'window':20}}
        second=copy.deepcopy(candidate);second['ast']={'op':'neg','arg':second['ast']}
        plans=[{'candidate':c,'measurement':'Fixture observed measure','prediction':'Fixture registered prediction','falsifier':'Fixture negative counterexample'} for c in (candidate,second)]
        chosen=learning.select_plans(self.c,900,{'plans':plans},bindings,self.p['settings'],preflight=lambda c:'blocked' if c==candidate else None)
        self.assertEqual(chosen,second)
        repeated=learning.select_plans(self.c,900,{'plans':plans},bindings,self.p['settings'],preflight=lambda c:'all_now_blocked')
        self.assertEqual(repeated,second)
        changed=copy.deepcopy(plans);changed[1]['prediction']='Changed registered prediction'
        with self.assertRaisesRegex(ValueError,'frozen'):learning.select_plans(self.c,900,{'plans':changed},bindings,self.p['settings'])

    def test_model_reservations_failures_do_not_refund_and_cap_persists(self):
        for _ in range(64):meta.reserve_model(self.c,self.cfg)
        with self.assertRaisesRegex(ValueError,'64'):meta.reserve_model(self.c,self.cfg)
        self.assertEqual(store.get_flag(self.c,'dual_model_reservations:fixture-dual'),'64')

    def test_later_owner_pause_cannot_be_cleared_by_deployment(self):
        for k,v in (('paused','1'),('pause_origin','manual'),('pause_reason','menu:stop-after-cycle')):store.set_flag(self.c,k,v)
        receipt=[dict(r) for r in self.c.execute("SELECT * FROM state_flags WHERE key IN ('paused','pause_origin','pause_reason') ORDER BY key")]
        with patch('wq.util.now',return_value=util.now()+dt.timedelta(seconds=2)):store.set_flag(self.c,'paused','1')
        with self.assertRaisesRegex(ValueError,'ownership'):meta.restore_deployment_pause(self.c,self.cfg,receipt)
        self.assertTrue(store.is_paused(self.c))

    def test_exhausted_or_unrelated_lineage_never_grants_discovery(self):
        row=self.c.execute('SELECT document_json FROM learning_experiments').fetchone();doc=json.loads(row[0]);doc['max_cycles']=0
        self.c.execute('UPDATE learning_experiments SET document_json=?',(json.dumps(doc),))
        self.assertFalse(meta.discovery_permission(self.c,self.cfg,self.p)['allowed'])
        with self.assertRaisesRegex(ValueError,'exhausted'):meta.required_epoch(self.c,self.cfg)
        doc['max_cycles']=10;doc['root_experiment']='unrelated'
        self.c.execute('UPDATE learning_experiments SET document_json=?',(json.dumps(doc),))
        with self.assertRaisesRegex(ValueError,'Unrelated'):meta.required_epoch(self.c,self.cfg)

    def test_owner_stop_and_baseline_drift_block_even_pending_work(self):
        self.cfg.data['arbitrary_baseline_change']=True
        with self.assertRaisesRegex(ValueError,'baseline'):meta.required_epoch(self.c,self.cfg)
        del self.cfg.data['arbitrary_baseline_change']
        learning.stop_experiment(self.c,'fixture-dual-epoch','Fixture explicit owner stop')
        with self.assertRaisesRegex(ValueError,'Owner stopped'):meta.required_epoch(self.c,self.cfg)

    def test_frozen_episode_settlement_uses_reservation_not_changed_source(self):
        meta.record_discovery(self.c,self.cfg,700,False,self.p)
        old=store.get_flag(self.c,'discovery_reservation:700');self.f.gap_source()
        meta.record_discovery(self.c,self.cfg,700,False,self.p)
        self.assertEqual(store.get_flag(self.c,'discovery_reservation:700'),old)
        self.assertEqual(json.loads(store.get_flag(self.c,'discovery_episode:'+old))['empty_cycles'],[700])
        meta.record_discovery(self.c,self.cfg,700,True,self.p)
        self.assertEqual(json.loads(store.get_flag(self.c,'discovery_episode:'+old))['empty_cycles'],[])

    def test_not_sent_unknown_missing_or_synthetic_simulation_is_not_information(self):
        from wq import brain_jobs
        brain_jobs.setup(self.c)
        tid,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':701},'fake-information');store.finish_task(self.c,tid,'succeeded',{},None)
        evidence_path=self.root/'alpha.json';evidence_path.write_text(json.dumps({'id':'fixture-alpha','is':{'sharpe':-2}}))
        self.c.execute('INSERT INTO brain_runs(task_id,state,location,alpha_id,evidence_path,started_at,updated_at) VALUES(?,?,?,?,?,?,?)',(tid,'unknown','https://fixture/simulation','fixture-alpha',str(evidence_path),util.now_iso(),util.now_iso()))
        self.assertFalse(meta.information_receipt(self.c,701,tid))
        self.c.execute("UPDATE brain_runs SET state='complete' WHERE task_id=?",(tid,));self.assertFalse(meta.information_receipt(self.c,701,tid))
        fid=store.insert_family(self.c,None,'fixture-info',None,'fixture',True,None)
        candidate=store.insert_candidate(self.c,fid,'fixture',{},'fixture',True)
        sid=store.insert_simulation(self.c,candidate,'fixture-alpha','api',True,'failed',{'stats':{'sharpe':-2}},None,None)
        self.assertFalse(meta.information_receipt(self.c,701,tid))
        self.c.execute('UPDATE simulations SET synthetic=0 WHERE sim_id=?',(sid,))
        self.assertTrue(meta.information_receipt(self.c,701,tid));self.assertFalse(meta.information_receipt(self.c,702,tid))

    def test_no_source_inbox_material_reaches_identity_verify_then_handoff(self):
        p=self.no_steps()
        for h in p['campaign']['hypotheses']:h['required_roles']=[]
        gaps=framework.sync_gaps(self.c,p);gap=next(g for g in gaps if g['state']=='owner_required')
        self.assertEqual(gap['fields'],[])
        request=evidence.handoff(self.c,self.cfg,gap,'binding_required')
        Path(request['incoming_path']).write_text(json.dumps({'gap_id':gap['gap_id'],'scope':gap['scope'],'source_ref':'fixture://document','text':'Document identity retained without pretending a semantic contract is verified.'}))
        evidence.incoming(self.c,self.cfg);framework.sync_gaps(self.c,p)
        payload=next(x for x in evidence.receipt_candidates(self.c,self.cfg) if x['gap_id']==gap['gap_id'])
        status,result,_=evidence.verify(self.c,self.cfg,{'task_id':'fixture-verify'},payload)
        self.assertEqual(status,'succeeded');self.assertTrue(result['material_verified']);self.assertEqual(result['semantic_status'],'insufficient')
        framework.sync_gaps(self.c,p);after=framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]
        self.assertEqual(after['state'],'waiting_handoff');self.assertTrue(after['verification']['material_verified'])

    def test_trial_deadline_cannot_promote_and_full_failure_consumes_opportunity(self):
        p=self.no_steps();meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps);meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        before=meta.state(self.c);before['pairs']=[{'gain':.9}]*7
        store.set_flag(self.c,'research_meta_state',json.dumps(before))
        with patch('wq.util.now',return_value=util.now()+dt.timedelta(hours=73)):
            meta.sync_gaps(self.c,self.cfg,p,framework.sync_gaps)
        self.assertEqual(meta.state(self.c)['status'],'inconclusive');self.assertEqual(meta.state(self.c)['mode'],'full')
        before['pairs']=[];store.set_flag(self.c,'research_meta_state',json.dumps(before));self.c.commit()
        with self.assertRaisesRegex(RuntimeError,'fixture crash'):
            meta.sync_gaps(self.c,self.cfg,p,lambda c,p:(_ for _ in ()).throw(RuntimeError('fixture crash')))
        after=meta.state(self.c);self.assertEqual(after['status'],'rejected');self.assertEqual(after['opportunities'],1)
        self.assertEqual(after['financial_comparability'],'needs_review')

    def test_batch_sign_and_window_variants_do_not_create_choice_opportunity(self):
        bindings=self.p['bindings'];role=next(r for r,b in bindings.items() if not b.get('group_field'))
        c={'title':'Fixture title','hypothesis':'Fixture mechanism measurable','counterexample':'Fixture falsifier measurable','ast':{'op':'mean','arg':{'op':'field','name':role},'window':20}}
        variants=[c,copy.deepcopy(c),copy.deepcopy(c)];variants[1]['ast']={'op':'neg','arg':variants[1]['ast']};variants[2]['ast']['window']=40
        plans=[{'candidate':x,'measurement':'Fixture observed measure','prediction':'Fixture registered prediction','falsifier':'Fixture negative counterexample'} for x in variants]
        learning.select_plans(self.c,901,{'plans':plans},bindings,self.p['settings'],preflight=lambda c:None)
        doc=json.loads(self.c.execute('SELECT document_json FROM learning_selections WHERE cycle_id=901').fetchone()[0])
        self.assertEqual(doc['eligible_count'],1);self.assertFalse(doc['choice_opportunity']);self.assertEqual(len(doc['plans']),3)

    def test_batch_handoff_notification_emits_once_without_108_alerts(self):
        from wq import desktop
        gaps=framework.sync_gaps(self.c,self.no_steps())
        for gap in gaps:evidence.handoff(self.c,self.cfg,gap,'fixture_missing_material')
        self.assertEqual(len(desktop.pending_notifications(self.c)),1);self.assertEqual(desktop.pending_notifications(self.c),[])


class DualRoutingTests(unittest.TestCase):
    def setUp(self):
        import test_routing as fixture
        self.f=fixture.RoutingTests('test_research_blocked_does_not_retry');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.cfg.data['research_dual_loop']={'enabled':True,'root_id':'routing-fixture','valid_until':'2099-01-01T00:00:00Z'}

    def test_65th_reservation_blocks_before_running_provider_marker(self):
        store.set_flag(self.f.conn,'dual_model_reservations:routing-fixture','64')
        tid,_=self.f.enqueue();self.f.tick()
        self.assertEqual(self.f.status(tid),'blocked')
        self.assertEqual(self.f.conn.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],0)
        row=self.f.conn.execute('SELECT phase FROM task_routes WHERE task_id=?',(tid,)).fetchone()
        self.assertNotEqual(row[0],'running')

    def test_invalid_artifact_with_parsed_plans_never_regenerates(self):
        self.f.script.write_text("import json\nfrom pathlib import Path\nPath('result.json').write_text(json.dumps({'status':'completed','plans':[{'fixture':'retained'}]}))\n")
        tid,path=self.f.enqueue();self.f.tick();self.f.tick()
        self.assertEqual(self.f.status(tid),'blocked')
        self.assertEqual(self.f.conn.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],1)
        self.assertTrue(store.get_flag(self.f.conn,'plans_seen:'+tid))
        self.assertEqual(json.loads((Path(path)/'parsed-plans.json').read_text())['plans'],[{'fixture':'retained'}])

    def test_pause_restore_transaction_blocks_interleaved_owner_write(self):
        import sqlite3
        c,cfg=self.f.conn,self.f.cfg;learning.setup(c)
        for k,v in (('paused','1'),('pause_origin','manual'),('pause_reason','menu:stop-after-cycle')):store.set_flag(c,k,v)
        c.commit();receipt=[dict(r) for r in c.execute("SELECT * FROM state_flags WHERE key IN ('paused','pause_origin','pause_reason') ORDER BY key")]
        other=sqlite3.connect(cfg.db_path,timeout=.01)
        try:
            c.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):other.execute("UPDATE state_flags SET value='manual-new' WHERE key='pause_origin'")
            # This routing fixture has no financial experiment: explicitly turn off dual-loop for this isolated lock test.
            cfg.data['research_dual_loop']['enabled']=False
            meta.restore_deployment_pause(c,cfg,receipt);c.commit()
            other.execute("UPDATE state_flags SET value='1' WHERE key='paused'");other.commit()
            self.assertTrue(store.is_paused(c))
        finally:other.close()

class DualTailBudgetTests(unittest.TestCase):
    def setUp(self):
        import test_autopilot_variants as fixture
        self.f=fixture.VariantTests('test_sign_flip_only_when_base_is_strongly_negative_and_once');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.cfg.data['autopilot']['max_cycles_total']=1
        self.f.sharpe=-1.2;self.f.p['setting_variants']=[];self.f.save_policy();util.write_json(self.f.cfg.path,self.f.cfg.data)
        learning.freeze_experiment(self.f.c,'tail-epoch',learning.current_baseline(self.f.cfg),2,max_requests_per_arm=1)

    def test_last_request_skips_only_optional_variants_then_archives_true_negative(self):
        for _ in range(40):self.f.tick()
        row=self.f.cycle();self.assertEqual(row['state'],'closed');self.assertEqual(self.f.posts,1)
        self.assertEqual(self.f.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],1)
        self.assertTrue(all(v['task_id'].startswith('skipped:') for v in self.f.sims()))
        self.assertIn('未运行',row['outcome'])
        self.assertTrue(meta.information_receipt(self.f.c,row['cycle_id'],row['simulation_task']))
        task=self.f.c.execute('SELECT * FROM tasks WHERE task_id=?',(row['simulation_task'],)).fetchone()
        evidence_path=self.f.c.execute('SELECT evidence_path FROM brain_runs WHERE task_id=?',(task['task_id'],)).fetchone()[0]
        alpha=util.read_json(evidence_path);alpha['is']['sharpe']=None;util.write_json(evidence_path,alpha)
        self.assertFalse(meta.information_receipt(self.f.c,row['cycle_id'],task['task_id']))

    def test_other_contract_error_is_not_swallowed_as_budget_skip(self):
        from wq import autopilot,brain_jobs
        for _ in range(20):
            if self.f.c.execute("SELECT 1 FROM brain_runs WHERE state='complete'").fetchone():break
            self.f.tick()
        row=self.f.cycle();self.assertEqual(row['state'],'simulating')
        with patch('wq.brain_jobs.enqueue',side_effect=ValueError('Fixture contract failure')):
            with self.assertRaisesRegex(ValueError,'contract failure'):autopilot._advance_standard(self.f.c,self.f.cfg,row,self.f.p)
        self.assertEqual(self.f.sims(),[])


class DualImportContractTests(unittest.TestCase):
    def test_real_import_statuses_and_missing_measurement(self):
        from helpers import make_env
        from wq import importer,brain_jobs
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            cfg,c=make_env(root);self.addCleanup(c.close);brain_jobs.setup(c)
            for i,(passed,quality,status) in enumerate(((True,None,'passed'),(False,None,'failed'),(True,{'status':'fail'},'quality_failed'),(None,None,'unchecked'))):
                with self.subTest(status=status):
                    tid,_=store.enqueue_task(c,'brain_simulation',{'research_cycle_id':i+1},'import-'+str(i));store.finish_task(c,tid,'succeeded',{},None)
                    path=Path(root)/('alpha'+str(i)+'.json');alpha={'id':'import'+str(i),'is':{'sharpe':-1.4}}
                    util.write_json(str(path),alpha)
                    c.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',(tid,'complete','https://fixture/sim/'+str(i),alpha['id'],str(path),util.now_iso(),util.now_iso()))
                    obj={'schema':'wq.imported-result/v1','synthetic':False,'source':'api','simulation':{'remote_id':alpha['id'],'expression':'field'+str(i),'config':{'region':'USA','delay':1,'catalog_verified':False},'stats':alpha['is'],'checks':{'passed':passed}},'quality':quality}
                    result=importer.import_result_obj(c,cfg,obj,True);self.assertEqual(result['sim_status'],status)
                    self.assertTrue(meta.information_receipt(c,i+1,tid));self.assertFalse(meta.information_receipt(c,i+2,tid))
                    c.execute("UPDATE tasks SET status='failed' WHERE task_id=?",(tid,));self.assertFalse(meta.information_receipt(c,i+1,tid))


class DualFrozenReuseTests(DualRoutingTests):
    # Do not duplicate the parent tests during discovery.
    test_65th_reservation_blocks_before_running_provider_marker=None
    test_invalid_artifact_with_parsed_plans_never_regenerates=None
    test_pause_restore_transaction_blocks_interleaved_owner_write=None

    def test_original_formal_result_reuses_without_spawn_but_mutation_blocks(self):
        from wq import routing
        self.f.script.write_text("import json\nfrom pathlib import Path\nPath('result.json').write_text(json.dumps({'status':'completed','summary':'Fixture valid envelope','findings':[],'plans':[{'prediction':'fixture unchanged'}]}))\n")
        tid,path=self.f.enqueue();self.f.tick();task=dict(self.f.conn.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone());payload=json.loads(task['payload_json'])
        self.assertEqual(routing.dispatch_routed(self.f.conn,self.f.cfg,task,payload)[0],'succeeded')
        formal=Path(path)/'result.json';doc=util.read_json(str(formal));doc['plans'][0]['prediction']='fixture mutation';util.write_json(str(formal),doc)
        self.assertEqual(routing.dispatch_routed(self.f.conn,self.f.cfg,task,payload)[0],'blocked')
        doc['status']='invalid';util.write_json(str(formal),doc)
        self.assertEqual(routing.dispatch_routed(self.f.conn,self.f.cfg,task,payload)[0],'blocked')
        self.assertEqual(self.f.conn.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],1)

class DualAllocationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f=DualLoopTests('test_steps_are_not_cached_without_proven_dependency_closure');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c,self.cfg,self.p=self.f.c,self.f.cfg,self.f.p

    def cycle(self,cid,policy):
        self.c.execute("UPDATE research_cycles SET state='closed'")
        self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(?,'researching',?,?,?,?)",(cid,json.dumps(policy),util.sha256_json(policy),util.now_iso(),util.now_iso()))
        return learning.assign(self.c,cid,'fixture-dual-epoch',learning.current_baseline(self.cfg))

    def test_next_arm_uses_current_stratum_not_global_epoch_counts(self):
        row=self.c.execute('SELECT document_json FROM learning_experiments').fetchone();doc=json.loads(row[0]);doc['max_requests_per_arm']=1
        self.c.execute('UPDATE learning_experiments SET document_json=?',(json.dumps(doc),))
        other=copy.deepcopy(self.p);other['settings']['neutralization']='SECTOR'
        self.assertEqual(self.cycle(9001,other),'baseline')
        store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':9001},'reserved-other-stratum')
        # This untouched stratum selects baseline too, whose cumulative request is exhausted.
        with self.assertRaisesRegex(ValueError,'arm request budget'):meta.required_epoch(self.c,self.cfg)

    def test_last_assigned_cycle_advances_at_zero_remaining_but_new_work_stops(self):
        row=self.c.execute('SELECT document_json FROM learning_experiments').fetchone();doc=json.loads(row[0]);doc['max_cycles']=2
        self.c.execute('UPDATE learning_experiments SET document_json=?',(json.dumps(doc),))
        self.cycle(9001,self.p);self.cycle(9002,self.p)
        self.assertEqual(meta.required_epoch(self.c,self.cfg,9002),'fixture-dual-epoch')
        with self.assertRaisesRegex(ValueError,'cycles exhausted'):meta.required_epoch(self.c,self.cfg)


class DualStartAndIntentTests(unittest.TestCase):
    def setUp(self):
        import test_routing as fixture
        self.f=fixture.RoutingTests('test_research_blocked_does_not_retry');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c,self.cfg,self.root=self.f.conn,self.f.cfg,self.f.root
        self.cfg.data['research_dual_loop']={'enabled':True,'root_id':'start-fixture','valid_until':'2099-01-01T00:00:00Z','learning_root':'start-epoch'}
        from wq import autopilot
        from wq import research_dsl
        proof=self.root/'catalog-proof.json';util.write_json(str(proof),{'fixture':True})
        self.p={'version':1,'scope':'exploratory_only_no_submission','valid_until':'2099-01-01T00:00:00Z','verified_at':'2026-01-01T00:00:00Z',
            'settings':{'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY'},'source':'fixture://source',
            'bindings':{r:{'expression':'returns','fields':['returns'],'source':'fixture://field'} for r in research_dsl.ROLES},
            'evidence_files':[{'path':str(proof),'sha256':util.sha256_json({'fixture':True})}]}
        self.cfg.data['autopilot']={'policy_file':'config/policy.json','enabled':False}
        self.cfg.data['brain_api']={'enabled':True,'authorized_until':'2099-01-01T00:00:00Z'}
        self.cfg.data['routing']['authorized_until']='2099-01-01T00:00:00Z'
        util.write_json(str(self.root/'config/policy.json'),self.p)
        self.f.data['providers']['a']['argv'][-1]='completed'
        self.f.data['providers']['b']['argv'].append('fixture-second-channel');self.f.write_config()
        autopilot.setup(self.c)
        learning.freeze_experiment(self.c,'start-epoch',learning.current_baseline(self.cfg),10,max_requests_per_arm=6)

    def cycle_job(self,role='research'):
        from wq import autopilot
        policy=self.p
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('researching',?,?,?,?)",(json.dumps(policy),util.sha256_json(policy),util.now_iso(),util.now_iso()))
        cid=self.c.execute('SELECT last_insert_rowid()').fetchone()[0]
        tid=autopilot.make_job(self.c,self.cfg,cid,'research','Fixture measurable Quant research')
        self.c.execute('UPDATE research_cycles SET research_task=? WHERE cycle_id=?',(tid,cid))
        if role=='review':
            store.finish_task(self.c,tid,'succeeded',{},None)
            tid=autopilot.make_job(self.c,self.cfg,cid,'review','Fixture immutable Quant review')
            self.c.execute("UPDATE research_cycles SET review_task=?,state='reviewing' WHERE cycle_id=?",(tid,cid))
        return cid,tid

    def assert_no_start(self,tid):
        self.f.tick()
        self.assertEqual(self.f.status(tid),'blocked')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],0)
        self.assertEqual(store.get_flag(self.c,'dual_model_reservations:start-fixture','0'),'0')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM attempts WHERE task_id=? AND event='provider_start'",(tid,)).fetchone()[0],0)

    def test_queued_research_owner_stop_blocks_at_actual_runner_start(self):
        _,tid=self.cycle_job();learning.stop_experiment(self.c,'start-epoch','Fixture explicit owner stop')
        self.assert_no_start(tid)

    def test_queued_review_owner_stop_blocks_at_actual_runner_start(self):
        _,tid=self.cycle_job('review');learning.stop_experiment(self.c,'start-epoch','Fixture explicit owner stop')
        self.assert_no_start(tid)

    def test_queued_model_baseline_drift_blocks_without_reservation(self):
        _,tid=self.cycle_job();self.cfg.data['research_learning']={'enabled':False}
        self.assert_no_start(tid)

    def test_queued_model_missing_assignment_blocks_without_reservation(self):
        cid,tid=self.cycle_job();self.c.execute('DELETE FROM learning_assignments WHERE cycle_id=?',(cid,))
        self.assert_no_start(tid)

    def test_wrong_cycle_stage_pointer_blocks_without_reservation(self):
        cid,tid=self.cycle_job();self.c.execute("UPDATE research_cycles SET research_task='other-task' WHERE cycle_id=?",(cid,))
        self.assert_no_start(tid)

    def test_legitimate_assigned_research_still_starts_one_stub(self):
        _,tid=self.cycle_job();self.f.tick()
        self.assertEqual(self.f.status(tid),'succeeded')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],1)
        self.assertEqual(store.get_flag(self.c,'dual_model_reservations:start-fixture'),'1')

    def test_legitimate_assigned_review_still_starts_one_stub(self):
        _,tid=self.cycle_job('review');self.f.tick()
        self.assertEqual(self.f.status(tid),'succeeded')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM agent_calls').fetchone()[0],1)
        self.assertEqual(store.get_flag(self.c,'dual_model_reservations:start-fixture'),'1')

    def test_old_epoch_cycle_cannot_start_under_a_new_epoch(self):
        _,tid=self.cycle_job()
        learning.stop_experiment(self.c,'start-epoch','Fixture explicit baseline replacement',stop_type='baseline_superseded')
        learning.freeze_experiment(self.c,'new-epoch',learning.current_baseline(self.cfg),10,max_requests_per_arm=6)
        row=self.c.execute("SELECT document_json FROM learning_experiments WHERE experiment_id='new-epoch'").fetchone();doc=json.loads(row[0]);doc['root_experiment']='start-epoch'
        self.c.execute("UPDATE learning_experiments SET document_json=? WHERE experiment_id='new-epoch'",(json.dumps(doc),))
        self.assert_no_start(tid)

    def test_pending_research_intent_recovers_after_temporary_preflight_failure(self):
        from wq import research_campaign_v2 as events
        self.cfg.data['autopilot']['enabled']=True
        self.cfg.data['research_framework']={'enabled':True}
        self.cfg.data['research_learning']={'maintenance_enabled':False}
        self.c.execute('DELETE FROM learning_experiments')
        learning.freeze_experiment(self.c,'start-epoch',learning.current_baseline(self.cfg),10,max_requests_per_arm=6)
        with patch('wq.research_evidence.prepare'),patch('wq.research_evidence.incoming'),patch('wq.research_framework.evidence_candidates',side_effect=lambda *args:[]),patch('wq.research_evidence.receipt_candidates',side_effect=lambda *args:[]),patch('wq.research_framework.reassessment_candidates',side_effect=lambda *args:[]):
            first=framework.tick(self.c,self.cfg);self.assertTrue(first['allow_research'])
            with patch('wq.brain_client.BrainClient') as client:
                client.return_value.jar=[True];client.return_value.preflight.return_value=(503,{}, {})
                runner.run_once(self.c,self.cfg)
                self.assertEqual(client.return_value.preflight.call_count,1)
                self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0],0)
                count=len(events.records(self.c,'research_work_selected'))
                for _ in range(2):framework.tick(self.c,self.cfg)
                self.assertEqual(len(events.records(self.c,'research_work_selected')),count)
                client.return_value.preflight.return_value=(200,{}, {})
                with patch('wq.util.now',return_value=util.now()+dt.timedelta(minutes=6)):
                    runner.run_once(self.c,self.cfg)
                self.assertEqual(client.return_value.preflight.call_count,2)
                self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0],1)
                self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_assignments').fetchone()[0],1)
