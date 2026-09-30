import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from helpers import make_env
from wq import autopilot, brain_jobs, research_campaign as old, research_campaign_v2 as v2
from wq import research_contracts, research_dsl, research_learning, store, util


class CampaignV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg,self.c=make_env(self.tmp.name,{'brain_api':{'enabled':True,'authorized_until':'2099-01-01T00:00:00Z','min_post_interval_s':0,'max_posts_per_24h':80},
            'routing':{'authorized_until':'2099-01-01T00:00:00Z'},'limits':{'sims_per_week':240},
            'autopilot':{'policy_file':'config/policy.json','max_simulations_per_week':240}})
        self.addCleanup(self.c.close);autopilot.setup(self.c);brain_jobs.setup(self.c)
        from wq import feedback
        feedback.setup(self.c)
        self.root=Path(self.tmp.name);proof=self.root/'catalog.json';util.write_json(str(proof),{'fixture':True})
        self.p={'version':1,'scope':'exploratory_only_no_submission','valid_until':'2099-01-01T00:00:00Z','verified_at':'2026-01-01T00:00:00Z',
            'settings':{'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY'},
            'source':'fixture://source','bindings':{r:{'expression':'fixture_'+r,'fields':['fixture_'+r],'source':'fixture://field'} for r in research_dsl.ROLES},
            'evidence_files':[{'path':str(proof),'sha256':util.sha256_json({'fixture':True})}]}
        from wq import catalog
        self.p['evidence_files']=[]
        query=catalog.query_from_settings(self.p['settings'])
        for role,binding in self.p['bindings'].items():
            doc={'schema':catalog.SNAPSHOT_SCHEMA,'query':query,'context':[query],
                 'field':{'id':binding['fields'][0],'type':'MATRIX'},'queried_at':util.now_iso()}
            path=self.root/(role+'-metadata.json');util.write_json(str(path),doc)
            self.p['evidence_files'].append({'path':str(path),'sha256':util.sha256_json(doc)})
        self.p['campaign']=v2.template(self.p,'testacct');self.p['campaign']['enabled']=True
        self.candidate={'title':'Registered historical interaction','hypothesis':'Historical interaction predicts subsequent returns.',
            'counterexample':'Comparable no-condition measurement has no lower effect.',
            'ast':{'op':'mul','left':{'op':'mean','arg':{'op':'field','name':'daily_return'},'window':20},
                   'right':{'op':'rank','arg':{'op':'field','name':'activity_rank'}}}}
        roles=['activity_rank','daily_return'];contract=research_contracts.draft('H-N1')
        contract.update(status='verified',scope=research_contracts.scope(self.p,'testacct'),binding_hash=research_contracts.binding_hash(self.p,roles))
        text='Fixture only: documented historical timing, missing data, and exact registered comparison semantics.'
        path=self.root/'semantics.txt';path.write_text(text)
        contract['materials']=[{'material_id':'fixture','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'source':'fixture://provider','source_type':'provider_documentation','product_version':'fixture-v1','content_type':'text/plain',
             'access_status':'content_verified','fetched_at':'2026-01-01T00:00:00Z'}]
        contract['assertions']=[{'predicate':x,'subject_ref':contract['binding_hash'],'value':'Fixture only semantic claim','status':'verified',
             'verification_method':'owner_attestation','verified_by':'fixture-owner','verified_at':'2026-01-01T00:00:00Z',
             'valid_until':'2099-01-01T00:00:00Z','evidence_refs':[{'material_id':'fixture','locator':'line 1','quote':text}]} for x in research_contracts.requirements('H-N1')]
        dates=[(dt.date(2020,1,1)+dt.timedelta(days=i)).isoformat() for i in range(181)]
        step={'id':'primary','profile':'base','condition':'always','data_contract':contract,
            'roles_by_arm':{'treatment':roles,'control':['daily_return']},
            'recipe':{'kind':'remove_binary_condition','path':[],'before':copy.deepcopy(self.candidate['ast']),'keep':'left'},
            'evaluation':{'reuse_policy':'new_executions_only','decision_unit':'pair','statistic_unit':'mean_daily_return','metric':'paired_mean_daily_return','predicted_direction':1,'effect_floor':0,'tolerance':0.00001,
                'capital_basis':100,'frequency':'daily','timezone':'UTC','value_unit':'USD','cost_basis':'gross',
                'date_grid':dates,'segment_ends':[60,120,180],'missing_policy':'inconclusive','revision_policy':'append_no_redispatch','inference':'descriptive_pilot'}}
        isolation=next(a for a in contract['assertions'] if a['predicate']=='measurement_isolation')
        isolation['value']={k:'Fixture only documented and owner verified semantic contract.' for k in (
            'measured_difference','common_sample_rule','zero_semantics','missing_semantics','warmup_semantics','verification_method')}
        isolation['value'].update(common_sample_basis='contract_identical_effective_set',
            allowed_changes=['weights'],recipe_hash=util.sha256_json(v2.isolation_subject(step)),
            intervention_kind='remove_binary_condition',measured_quantity='weight_intensity_effect',
            operator_semantics={'before':'Fixture multiplicative weighting on same effective set','after':'Fixture weights removed while valid sample remains same'})
        # Offline state-machine fixture only; production capability remains unavailable.
        self.patch('wq.research_observation.capability',return_value={'ready':True,'reason_codes':[]})
        self.p['campaign']['hypotheses'][0].update(state='ready',reason='',steps=[step],required_roles=roles)
        self.save()
        self.sequence=0
        self.patch('wq.routing.catalog',return_value={'default':'steady','presets':{'steady':{'routes':{'research':['a'],'review':['b']}}}})
        self.patch('wq.routing._unavailable',return_value=None)
        self.patch('wq.routing.enqueue_job',side_effect=self.job)

    def patch(self,*args,**kwargs):
        p=patch(*args,**kwargs);value=p.start();self.addCleanup(p.stop);return value

    def save(self):
        self.p['campaign']['baseline']=old.baseline(self.p)
        util.write_json(self.cfg.resolve('config/policy.json'),self.p)

    def job(self,conn,cfg,role,prompt_file,input_dir=None,title=None):
        self.sequence+=1;path=self.root/f'job-{self.sequence}';path.mkdir()
        (path/'packet').mkdir();(path/'packet/request.md').write_text(Path(prompt_file).read_text())
        tid,_=store.enqueue_task(conn,'agent_call',{'job_dir':str(path),'role':role,'routing':True})
        return tid,str(path)

    def result(self,tid,obj,channel):
        task=autopilot.task(self.c,tid);path=Path(json.loads(task['payload_json'])['job_dir'])/'result.json';util.write_json(str(path),obj)
        self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
        self.c.execute('INSERT OR REPLACE INTO task_routes(task_id,snapshot_json,phase,updated_at) VALUES(?,?,?,?)',
            (tid,json.dumps({'chain':[channel],'preset':'steady','authorized_until':'2099-01-01T00:00:00Z'}),'complete',util.now_iso()))

    def cycle(self):return dict(self.c.execute('SELECT * FROM research_cycles ORDER BY cycle_id DESC LIMIT 1').fetchone())

    def begin(self):
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('researching',?,?,?,?)",
            (json.dumps(self.p),util.sha256_json(self.p),util.now_iso(),util.now_iso()))
        cid=self.c.execute('SELECT last_insert_rowid()').fetchone()[0];old.allocate(self.c,self.p,cid)
        prompt=self.root/'research.md';prompt.write_text('Fixture research request')
        tid,_=self.job(self.c,self.cfg,'research',str(prompt))
        self.c.execute('UPDATE research_cycles SET research_task=? WHERE cycle_id=?',(tid,cid))
        self.result(tid,{'status':'completed','candidate':self.candidate},'a')
        autopilot.advance(self.c,self.cfg,self.cycle(),self.p)
        return cid

    def review(self,arm,channel='b',accept=True):
        pair=v2.pair_for_cycle(self.c,self.cycle()['cycle_id']);tid=v2.review_refs(self.c,pair['pair_id'])[arm]
        candidate=pair['candidates'][arm]
        checks={k:True for k in autopilot.REVIEW_CHECKS};evidence=[]
        if not accept:
            checks['economic_rationale']=False;evidence=[{'check':'economic_rationale','field':'hypothesis','quote':candidate['hypothesis'],
                    'explanation':'Fixture rejection preserves original economic rationale gate.'}]
        self.result(tid,{'status':'completed','review':{'candidate_hash':util.sha256_json(candidate),'accept':accept,'checks':checks,
            'reason':'Fixture review checks the registered restricted measurement only.','blocking_evidence':evidence}},channel)
        autopilot.advance(self.c,self.cfg,self.cycle(),self.p)

    def prepared(self):
        self.begin();self.review('treatment');self.review('control')
        return v2.pair_for_cycle(self.c,self.cycle()['cycle_id'])

    def test_both_original_reviews_before_exact_atomic_pair(self):
        self.begin()
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],0)
        self.review('treatment')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],0)
        self.review('control')
        self.assertEqual(self.cycle()['state'],'simulating')
        tasks=self.c.execute("SELECT payload_json FROM tasks WHERE kind='brain_simulation'").fetchall()
        self.assertEqual(len(tasks),2)
        docs=[json.loads(t[0]) for t in tasks]
        self.assertEqual({d['campaign_pair']['arm'] for d in docs},{'treatment','control'})
        self.assertNotEqual(docs[0]['request']['regular'],docs[1]['request']['regular'])
        self.assertEqual(docs[0]['request']['settings'],docs[1]['request']['settings'])
        self.assertEqual(old.report(self.c,self.p)['reserved'],2)
        self.assertEqual(len(autopilot.due_conditional(self.cfg,self.cycle(),{'stats_json':'{}'},[])),0)

    def test_only_one_remaining_slot_rolls_back_entire_pair(self):
        cid=self.begin();self.review('treatment')
        for i in range(3):store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':cid},'prior'+str(i))
        with self.assertRaisesRegex(ValueError,'cap'):self.review('control')
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),3)
        self.assertEqual(v2.records(self.c,'campaign_request_ref'),[])

    def test_same_channel_or_old_candidate_hash_does_not_admit(self):
        self.begin();self.review('treatment')
        with patch('wq.autopilot.fallback_once',return_value=False):self.review('control',channel='a')
        self.assertEqual(self.cycle()['state'],'closed')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],0)

    def test_all_blocked_is_readable_and_can_yield_ordinary_but_unknown_halts(self):
        self.p['campaign']['hypotheses'][0]['state']='blocked';self.save()
        self.c.commit();self.c.execute('PRAGMA query_only=ON')
        self.assertIsNone(v2.select(self.c,self.p))
        report=old.report(self.c,self.p);self.assertEqual(len(report['opportunities']),14)
        self.c.execute('PRAGMA query_only=OFF')
        tid,_=store.enqueue_task(self.c,'brain_simulation',{},'unknown')
        self.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?",(tid,))
        with self.assertRaisesRegex(ValueError,'UNKNOWN'):v2.select(self.c,self.p)

    def test_control_ast_recorded_and_campaign_cannot_supply_learning_rules(self):
        pair=self.prepared();research_learning.sync(self.c)
        refs={d['arm']:d for _,d in v2.records(self.c,'campaign_request_ref')}
        control=json.loads(self.c.execute('SELECT document_json FROM learning_trials WHERE task_id=?',(refs['control']['task_id'],)).fetchone()[0])
        self.assertEqual(control['ast'],pair['candidates']['control']['ast'])
        self.assertEqual(control['source'],'campaign')
        self.assertEqual(research_learning.derive_rules(self.c),[])

    def test_changed_profile_or_expired_contract_blocks_dispatch_not_report(self):
        self.prepared();tid=self.cycle()['simulation_task']
        old.check_budget(self.c,self.cfg,self.cycle()['cycle_id'],tid)
        self.p['campaign']['hypotheses'][0]['steps'][0]['data_contract']['assertions'][0]['valid_until']='2000-01-01T00:00:00Z'
        util.write_json(self.cfg.resolve('config/policy.json'),self.p)
        with self.assertRaises(ValueError):old.check_budget(self.c,self.cfg,self.cycle()['cycle_id'],tid)
        self.assertEqual(len(old.report(self.c,self.p)['opportunities']),14)

    def test_missing_platform_receipts_stay_inconclusive_and_evaluation_idempotent(self):
        pair=self.prepared();first=v2.evaluate_pair(self.c,self.p,pair);second=v2.evaluate_pair(self.c,self.p,pair)
        self.assertEqual(first,second);self.assertEqual(first['assessment'],'inconclusive')
        self.assertEqual(len(v2.records(self.c,'campaign_evaluated')),1)
        self.assertEqual(v2.records(self.c,'campaign_proposal_created'),[])

    def test_v1_reservations_and_explicit_stop_survive_schema_change(self):
        for i in range(4):
            autopilot.event(self.c,100+i,'campaign_assignment',json.dumps({'campaign_id':self.p['campaign']['id'],'version':1,'hypothesis':'H-N1','phase':'representative'}))
            store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':100+i},'v1-'+str(i))
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),4)
        self.assertIsNone(v2.select(self.c,self.p))
        autopilot.event(self.c,None,'campaign_stop',json.dumps({'campaign_id':self.p['campaign']['id']}))
        with self.assertRaisesRegex(ValueError,'stopped'):v2.active(self.c,self.p)

    def test_transport_feedback_and_retained_snapshots_do_not_invent_units(self):
        from wq import feedback, research_observation
        from test_feedback import recordset
        pair=self.prepared();refs={d['arm']:d for _,d in v2.records(self.c,'campaign_request_ref')}
        for index,(arm,ref) in enumerate(refs.items()):
            tid=ref['task_id'];task=autopilot.task(self.c,tid);payload=json.loads(task['payload_json'])
            aid='fixturealpha'+str(index)
            alpha={'id':aid,'regular':{'code':payload['request']['regular']},'settings':payload['request']['settings'],
                   'is':{'sharpe':.2,'fitness':.1,'checks':[{'name':'LOW_SHARPE','result':'FAIL'}]}}
            with patch('wq.brain_jobs.BrainClient') as client:
                client.return_value.preflight.return_value=(200,{},{})
                client.return_value.request.side_effect=[(201,{'location':'/simulations/fixture'+str(index)},{}),(200,{}, {'alpha':aid}),(200,{},alpha)]
                for _ in range(3):
                    status,_,_=brain_jobs.step(self.c,self.cfg,task,payload);self.c.commit()
                self.assertEqual(status,'succeeded')
                self.assertEqual([c.args[0] for c in client.return_value.request.call_args_list],['POST','GET','GET'])
            self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
            ft,_=feedback.enqueue(self.c,self.cfg,aid);fp=json.loads(autopilot.task(self.c,ft)['payload_json'])
            pnl=recordset(['date','pnl'],[[date,float(i*(index+1))] for i,date in enumerate(pair['evaluation']['date_grid'])])
            yearly=recordset(['year','sharpe','fitness','pnl'],[[str(y),.2,.1,1] for y in range(2015,2025)])
            with patch('wq.feedback.get_with_reauth',side_effect=[(200,{},alpha),(200,{},pnl),(200,{},yearly)]) as get:
                for _ in range(4):status,_,_=feedback.step(self.c,self.cfg,{'task_id':ft},fp)
                self.assertEqual(status,'succeeded');self.assertEqual(get.call_count,3)
            self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(ft,))
        with patch('wq.brain_submission.offer_submission') as submit:
            autopilot.advance(self.c,self.cfg,self.cycle(),self.p)
            submit.assert_not_called()
        self.assertEqual(self.cycle()['state'],'closed')
        result=v2.evaluations(self.c,pair['pair_id'])[-1]
        self.assertEqual(result['assessment'],'inconclusive');self.assertEqual(result['next_action'],'none')
        observations=[d['observation'] for _,d in v2.records(self.c,'campaign_observation_bound')]
        self.assertTrue(all(o['identity_verified'] for o in observations))
        self.assertTrue(all(o['collection_error']=='OBSERVATION_CAPABILITY_UNSUPPORTED' for o in observations))
        for obs in observations:
            frozen=research_observation.read_snapshot(obs['identity_snapshot'])
            Path(frozen['observation']['report']['pnl_path']).write_text('{"overwritten":true}')
            Path(frozen['brain_run']['evidence_path']).unlink()
            self.assertEqual(research_observation.replay(obs)['values'],obs['values'])
        replay=v2.replay_evaluation(self.c,pair['pair_id'],result['observation_hash'])
        self.assertEqual(replay['measurement']['assessment'],'inconclusive')
        self.assertFalse(replay['dispatch'])
        snapshot=Path(observations[0]['raw_snapshot']['path']);snapshot.write_text('{}')
        with self.assertRaisesRegex(ValueError,'integrity'):v2.replay_evaluation(self.c,pair['pair_id'],result['observation_hash'])

    def test_production_collector_capability_blocks_before_paid_tasks(self):
        from wq import research_observation
        # Restore the real function hidden by this class's offline capability fixture.
        import importlib
        importlib.reload(research_observation)
        before=self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        self.assertIsNone(v2.select(self.c,self.p))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],before)
        reasons=v2.eligibility(self.p,self.p['campaign']['hypotheses'][0],self.p['campaign']['hypotheses'][0]['steps'][0])['reasons']
        self.assertIn('OBSERVATION_CAPABILITY_UNSUPPORTED',[r['code'] for r in reasons])

    def test_migration_keeps_old_queued_owner_and_stop_and_is_idempotent(self):
        old_doc=old.template({k:v for k,v in self.p.items() if k!='campaign'})
        old_doc['id']=self.p['campaign']['id'];old_doc['version']=1
        autopilot.event(self.c,None,'campaign_frozen',json.dumps(old_doc))
        autopilot.event(self.c,99,'campaign_assignment',json.dumps({'campaign_id':old_doc['id'],'version':1,'hypothesis':'H-N1','phase':'representative'}))
        tid,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':99},'oldqueued')
        self.p['campaign']['version']=2;self.save()
        first=v2.migrate(self.c,self.p,'Bounded schema revision for registered pairs')
        self.assertEqual(first['remaining'],55)
        self.assertEqual(v2.migrate(self.c,self.p,'Repeated request preserves original migration'),first)
        self.assertEqual(json.loads(autopilot.task(self.c,tid)['payload_json'])['research_cycle_id'],99)
        autopilot.event(self.c,None,'campaign_stop',json.dumps({'campaign_id':old_doc['id'],'reason':'Explicit owner stop'}))
        with self.assertRaisesRegex(ValueError,'stopped'):v2.active(self.c,self.p)
        self.assertEqual(len(old.reservations(self.c,old_doc['id'])),1)

    def confirmation(self):
        step=copy.deepcopy(self.p['campaign']['hypotheses'][0]['steps'][0])
        step.update(id='confirmation',condition='if_continue',transform={'op':'mean','window':5})
        isolation=next(a for a in step['data_contract']['assertions'] if a['predicate']=='measurement_isolation')['value']
        isolation.update(recipe_hash=util.sha256_json(v2.isolation_subject(step)),intervention_kind='registered_followup',
                         measured_quantity='same_intervention_under_transform')
        self.p['campaign']['hypotheses'][0]['steps'].append(step);self.save()

    def measured(self,pair,ref,value=2,version='fixture-1'):
        spec=pair['evaluation'];dates=spec['date_grid']
        return {'identity_verified':True,'task_id':ref['task_id'],'alpha_id':'alpha'+ref['task_id'],
            'outcome_version':version,'synthetic':False,'origin':'platform_collection',
            'execution':'complete','evidence_complete':True,'prior_observed':False,
            **{k:spec[k] for k in ('frequency','timezone','value_unit','capital_basis','cost_basis')},
            'intervals':[list(x) for x in zip(dates[:-1],dates[1:])],
            'values':[value if ref['arm']=='treatment' else 1.0]*180,'revision_evidence':'fixture-source-revision'}

    def test_complete_stop_cannot_revive_after_outcome_revision(self):
        self.confirmation();pair=self.prepared()
        with patch('wq.research_campaign_v2.collect_observation',side_effect=lambda c,p,r:self.measured(p,r,value=0)):
            first=v2.evaluate_pair(self.c,self.p,pair)
        self.assertEqual(first['next_action'],'stop')
        with patch('wq.research_campaign_v2.collect_observation',side_effect=lambda c,p,r:self.measured(p,r,version='fixture-2')):
            later=v2.evaluate_pair(self.c,self.p,pair)
        self.assertEqual(later['assessment'],'continue');self.assertEqual(later['next_action'],'none')
        self.assertIsNone(v2.select(self.c,self.p,mixed=False))
        self.assertEqual(v2.records(self.c,'campaign_proposal_created'),[])
        self.assertEqual(len(v2.records(self.c,'campaign_action_decided')),1)

    def test_concurrent_pair_admission_is_idempotent_and_does_not_rewrite_approval(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from wq.db import connect
        self.begin();self.review('treatment')
        # Finish the second review but let competing callers perform admission.
        with patch('wq.research_campaign_v2.admit_pair'):self.review('control')
        row=self.cycle();pair=v2.pair_for_cycle(self.c,row['cycle_id']);self.c.commit()
        barrier=Barrier(2)
        def admit(_):
            conn=connect(self.cfg.db_path)
            try:
                barrier.wait(timeout=5)
                v2.admit_pair(conn,self.cfg,row,self.p,pair);conn.commit()
                return len(old.reservations(conn,self.p['campaign']['id']))
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(admit,(0,1)))
        self.assertEqual(results,[2,2])
        self.assertEqual(len(v2.records(self.c,'campaign_request_ref')),2)
        for task in self.c.execute("SELECT payload_json FROM tasks WHERE kind='brain_simulation'"):
            from wq import research_gate
            payload=json.loads(task[0]);self.assertEqual(research_gate.validate(self.cfg,payload),payload['evidence']['research_review_sha256'])

    def test_global_55_rejects_two_new_tasks_atomically(self):
        self.begin();self.review('treatment')
        for index,hid in enumerate(old.IDS):
            cid=100+index
            autopilot.event(self.c,cid,'campaign_assignment',json.dumps({'campaign_id':self.p['campaign']['id'],'version':1,'hypothesis':hid,'phase':'representative'}))
            for j in range(3 if index==0 else 4):store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':cid},f'prior-{index}-{j}')
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),55)
        with self.assertRaisesRegex(ValueError,'cap'):self.review('control')
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),55)
        self.assertEqual(v2.records(self.c,'campaign_request_ref'),[])

    def test_confirmation_inherits_author_without_new_generation_and_separates_resource_assignment(self):
        from wq import research_learning
        self.confirmation();pair=self.prepared();author=self.cycle()['research_task']
        with patch('wq.research_campaign_v2.collect_observation',side_effect=lambda c,p,r:self.measured(p,r)):
            self.assertEqual(v2.evaluate_pair(self.c,self.p,pair)['next_action'],'continue_registered_step')
        self.c.execute("UPDATE tasks SET status='succeeded'")
        self.c.execute("UPDATE research_cycles SET state='closed'")
        # One ordinary cycle between campaign steps is the registered mixed scheduler.
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('closed',?,?,?,?)",
                       (json.dumps(self.p),util.sha256_json(self.p),util.now_iso(),util.now_iso()))
        self.cfg.data['autopilot']['enabled']=True;util.write_json(self.cfg.path,self.cfg.data)
        research_learning.freeze_experiment(self.c,'fixture-followup-budget',research_learning.current_baseline(self.cfg),2,2)
        before=self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='agent_call'").fetchone()[0]
        with patch('wq.autopilot.brain_preflight',return_value=(True,None)):
            autopilot.tick(self.c,self.cfg)
        self.assertEqual(self.cycle()['research_task'],author)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='agent_call'").fetchone()[0],before)
        cid=self.cycle()['cycle_id'];self.assertEqual(old.assignment(self.c,cid)['step'],'confirmation')
        self.assertIsNone(self.c.execute('SELECT 1 FROM learning_assignments WHERE cycle_id=?',(cid,)).fetchone())
        self.assertEqual(self.c.execute('SELECT work_kind FROM learning_resource_cycles WHERE cycle_id=?',(cid,)).fetchone()[0],'campaign')
        autopilot.advance(self.c,self.cfg,self.cycle(),self.p)
        self.review('treatment');self.review('control')
        self.assertEqual(self.cycle()['state'],'simulating')
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),4)
        second=v2.pair_for_cycle(self.c,cid)
        self.assertEqual(second['parent_ref'],pair['parent_ref'])
        self.assertEqual(second['candidates']['control']['ast']['arg'],pair['candidates']['control']['ast'])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM research_events WHERE kind='research_author_inherited'").fetchone()[0],1)

    def test_followup_registration_cannot_be_forged_or_third_pair_created(self):
        self.confirmation();pair=self.prepared()
        with patch('wq.research_campaign_v2.collect_observation',side_effect=lambda c,p,r:self.measured(p,r)):
            v2.evaluate_pair(self.c,self.p,pair)
        self.p['campaign']['hypotheses'][0]['steps'][1]['transform']['window']=20
        self.save()
        with self.assertRaisesRegex(ValueError,'Frozen campaign version changed'):v2.select(self.c,self.p,mixed=False)
        self.p['campaign']['hypotheses'][0]['steps'].append(copy.deepcopy(self.p['campaign']['hypotheses'][0]['steps'][1]))
        with self.assertRaisesRegex(ValueError,'two registered pairs'):v2.validate(self.p)

    def test_all_blocked_fallback_keeps_original_ordinary_plan_contract(self):
        self.p['campaign']['hypotheses'][0]['state']='blocked';self.save()
        self.cfg.data['autopilot']['enabled']=True;util.write_json(self.cfg.path,self.cfg.data)
        with patch('wq.autopilot.brain_preflight',return_value=(True,None)):
            autopilot.tick(self.c,self.cfg)
        row=self.cycle();self.assertIsNone(old.assignment(self.c,row['cycle_id']))
        payload=json.loads(autopilot.task(self.c,row['research_task'])['payload_json'])
        self.assertEqual(payload.get('plan_contract_version'),1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],0)

    def test_fallback_budget_is_shared_by_both_arms_and_survives_restart(self):
        from wq.db import connect
        self.begin();self.review('treatment',accept=False)
        pair=v2.pair_for_cycle(self.c,self.cycle()['cycle_id']);tid=v2.review_refs(self.c,pair['pair_id'])['treatment']
        candidate=pair['candidates']['treatment']
        self.result(tid,{'status':'completed','review':{'candidate_hash':util.sha256_json(candidate),'accept':True,
            'checks':{k:True for k in autopilot.REVIEW_CHECKS},'reason':'Fixture independent resolution of registered hypothesis.',
            'blocking_evidence':[],'resolutions':[{'check':'economic_rationale','disposition':'overturn','field':'hypothesis',
                'quote':candidate['hypothesis'],'explanation':'Fixture verifies the fixed measured claim under its own stated contract.'}]}},'b')
        autopilot.advance(self.c,self.cfg,self.cycle(),self.p)
        self.c.commit();self.c.close();self.c=connect(self.cfg.db_path);self.addCleanup(self.c.close)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_fallbacks').fetchone()[0],1)
        self.review('control',accept=False)
        self.assertEqual(self.cycle()['state'],'closed')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_fallbacks').fetchone()[0],1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],0)

    def reuse_task(self,pair,arm):
        settings=self.p['settings'];candidate=pair['candidates'][arm]
        expression,fields,_=research_dsl.validate_candidate(candidate,self.p['bindings'])
        doc={'purpose':'tutorial_validation','request':{'type':'REGULAR','regular':expression,'settings':settings},
            'config':{**settings,'fields':fields,'catalog_verified':True,'extra':{'brain_settings':settings,'purpose':'research_validation'}},
            'evidence':{'settings_verified':True,'source':'fixture://previous-authorized-execution'}}
        tid,created=brain_jobs.enqueue(self.c,self.cfg,doc);self.assertTrue(created)
        return tid,json.loads(autopilot.task(self.c,tid)['payload_json'])

    def test_one_remaining_slot_can_reuse_one_task_without_moving_its_owner(self):
        self.p['campaign']['hypotheses'][0]['steps'][0]['evaluation']['reuse_policy']='descriptive_reuse';self.save()
        cid=self.begin();self.review('treatment');pair=v2.pair_for_cycle(self.c,cid)
        tid,original=self.reuse_task(pair,'control')
        for j in range(3):store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':cid},'occupied'+str(j))
        self.review('control')
        refs={d['arm']:d for _,d in v2.records(self.c,'campaign_request_ref')}
        self.assertFalse(refs['control']['created']);self.assertTrue(refs['treatment']['created'])
        self.assertEqual(refs['control']['task_id'],tid)
        self.assertEqual(json.loads(autopilot.task(self.c,tid)['payload_json']),original)
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),4)

    def test_two_reused_tasks_do_not_consume_or_reset_occupied_budget(self):
        self.p['campaign']['hypotheses'][0]['steps'][0]['evaluation']['reuse_policy']='descriptive_reuse';self.save()
        cid=self.begin();self.review('treatment');pair=v2.pair_for_cycle(self.c,cid)
        self.reuse_task(pair,'control');self.reuse_task(pair,'treatment')
        for j in range(4):store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':cid},'occupied'+str(j))
        self.review('control')
        self.assertTrue(all(not d['created'] for _,d in v2.records(self.c,'campaign_request_ref')))
        self.assertEqual(len(old.reservations(self.c,self.p['campaign']['id'])),4)
