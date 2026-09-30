import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_campaign_v2 as campaign_fixture
import test_research_strategy as strategy_fixture
from helpers import make_env
from wq import autopilot,store,util,runner,research_learning as learning,research_metrics as metrics
from wq import research_framework as framework,research_lifecycle as lifecycle,research_agenda as agenda
from wq import research_campaign_v2 as campaign,research_knowledge as knowledge,research_observation as observation,research_contracts

REAL_CAPABILITY=observation.capability


class FrameworkTests(unittest.TestCase):
    def setUp(self):
        self.f=campaign_fixture.CampaignV2Tests('test_both_original_reviews_before_exact_atomic_pair')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cfg,self.c,self.p,self.root=self.f.cfg,self.f.c,self.f.p,self.f.root
        self.cfg.data['research_framework']={'enabled':True}
        self.cfg.data['autopilot']['enabled']=False
        framework.setup(self.c)

    def dispatch(self):
        self.c.commit();task=store.claim_task(self.c,'framework-test');self.assertIsNotNone(task)
        store.set_task_running(self.c,task['task_id'])
        status,detail,error=runner.dispatch_task(self.c,self.cfg,task)
        store.finish_task(self.c,task['task_id'],status,detail,error)
        self.c.commit()
        return status,detail

    def gap_source(self,missing=False,max_attempts=3):
        gaps=framework.sync_gaps(self.c,self.p)
        gap=next(g for g in gaps if g['hypothesis']=='H-N2' and g['predicate']=='historical_availability')
        material=self.root/'provider-document.txt';data=b'Fixture provider description: publication timestamps.'
        if not missing:material.write_bytes(data)
        source={'gap_id':gap['gap_id'],'revision':1,'adapter':'local_material_v1',
                'source':{'path':str(material),'sha256':hashlib.sha256(data).hexdigest(),'source_ref':'fixture://provider'},
                'approved_by':'fixture-owner','valid_until':'2099-01-01T00:00:00Z','max_attempts':max_attempts}
        framework.register_source(self.c,self.cfg,source)
        return gap,source,material,data

    def test_unregistered_scope_gaps_describe_target_without_authorizing_it(self):
        p=copy.deepcopy(self.p);p.pop('campaign')
        p['campaign']=campaign.template(p,self.cfg.get('account_alias'))
        gaps=framework.sync_gaps(self.c,p)
        targets={'H-D0':{'delay':0},'H-U1':{'universe':'TOP1000'},'H-U2':{'universe':'TOPSP500'}}
        for hid,scope in targets.items():
            items=[g for g in gaps if g['hypothesis']==hid];self.assertTrue(items)
            for gap in items:
                for key,value in scope.items():
                    self.assertEqual(gap['scope'][key],value);self.assertEqual(gap['query'][key],value)
                self.assertTrue(gap['owner_required']);self.assertIsNone(gap['binding_hash'])
        self.assertFalse(p['campaign']['enabled'])
        self.assertEqual(list(p['campaign']['execution_profiles']),['base'])

    def test_gap_collection_uses_task_ledger_and_does_not_claim_semantics(self):
        gap,source,material,data=self.gap_source()
        with patch('wq.brain_client.BrainClient.request',side_effect=AssertionError('No network')):
            selected=framework.tick(self.c,self.cfg);self.assertEqual(selected['state'],'queued')
            self.assertEqual(self.dispatch()[0],'succeeded')
            first=framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]
            self.assertEqual(first['state'],'collected');self.assertTrue(first['owner_required'])
            self.assertEqual(Path(first['receipt']['path']).read_bytes(),data)
            for _ in range(4):framework.tick(self.c,self.cfg)
            self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='research_evidence'").fetchone()[0],1)
            self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind IN ('brain_simulation','agent_call')").fetchone()[0],0)

    def test_missing_evidence_backoff_content_arrival_and_source_expiry(self):
        gap,source,material,data=self.gap_source(missing=True)
        framework.tick(self.c,self.cfg);self.assertEqual(self.dispatch()[0],'failed')
        self.assertEqual(framework.tick(self.c,self.cfg)['state'],'waiting_for_changed_evidence')
        material.write_bytes(data)
        # A changed local input is a wakeup, but prior failure backoff still bounds attempts.
        now=util.now()+dt.timedelta(hours=2)
        with patch('wq.util.now',return_value=now):
            self.assertEqual(framework.tick(self.c,self.cfg)['state'],'queued')
            self.assertEqual(self.dispatch()[0],'succeeded')
        source.update(revision=2,valid_until=(util.now()+dt.timedelta(seconds=1)).isoformat())
        framework.register_source(self.c,self.cfg,source)
        with patch('wq.util.now',return_value=util.now()+dt.timedelta(seconds=2)):
            framework.sync_gaps(self.c,self.p)
            self.assertEqual(framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]['state'],'expired')

    def test_source_hash_mismatch_never_marks_gap_verified(self):
        gap,source,material,_=self.gap_source();material.write_text('Different document')
        framework.tick(self.c,self.cfg);self.assertEqual(self.dispatch()[0],'failed')
        self.assertEqual(framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]['state'],'rejected')
        for _ in range(4):framework.tick(self.c,self.cfg)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='research_evidence'").fetchone()[0],1)

    def test_closed_pair_late_evidence_is_reevaluated_once_and_reaches_next_context(self):
        pair=self.f.prepared()
        self.c.execute("UPDATE tasks SET status='succeeded'");self.c.execute("UPDATE research_cycles SET state='closed'")
        complete=False
        def observed(c,p,r):
            value=self.f.measured(p,r,value=.1,version='later' if complete else 'initial')
            if not complete:value['evidence_complete']=False
            return value
        with patch('wq.research_campaign_v2.collect_observation',side_effect=observed):
            self.assertEqual(framework.tick(self.c,self.cfg)['state'],'queued');self.dispatch()
            self.assertEqual(campaign.evaluations(self.c,pair['pair_id'])[-1]['assessment'],'inconclusive')
            complete=True
            self.assertEqual(framework.tick(self.c,self.cfg)['state'],'queued');self.dispatch()
            latest=campaign.evaluations(self.c,pair['pair_id'])[-1]
            self.assertEqual(latest['assessment'],'falsified');self.assertEqual(latest['next_action'],'stop')
            self.c.commit()
            from wq import db
            self.c.close();self.c=db.connect(self.cfg.db_path);self.f.c=self.c
            for _ in range(3):framework.tick(self.c,self.cfg)
            self.assertEqual(len(campaign.evaluations(self.c,pair['pair_id'])),2)
        facts=learning.model_context(self.c,self.p['bindings'],self.p['settings'])['paired_findings']
        self.assertEqual(facts[0]['kind'],'counterexample')
        self.assertEqual(knowledge.context(self.c,self.p['bindings'],{**self.p['settings'],'delay':0}),[])
        self.assertEqual(len(campaign.records(self.c,'campaign_action_decided')),1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],2)

    def test_late_evidence_after_campaign_stop_never_creates_action(self):
        pair=self.f.prepared();self.c.execute("UPDATE tasks SET status='succeeded'");self.c.execute("UPDATE research_cycles SET state='closed'")
        autopilot.event(self.c,None,'campaign_stop',json.dumps({'campaign_id':self.p['campaign']['id'],'reason':'Fixture owner stop'}))
        with patch('wq.research_campaign_v2.collect_observation',side_effect=lambda c,p,r:self.f.measured(p,r)):
            framework.tick(self.c,self.cfg);self.dispatch()
        self.assertEqual(campaign.evaluations(self.c,pair['pair_id'])[-1]['next_action'],'none')
        self.assertEqual(campaign.records(self.c,'campaign_proposal_created'),[])

    def test_global_unknown_and_pause_prevent_new_framework_tasks(self):
        self.gap_source();tid,_=store.enqueue_task(self.c,'brain_simulation',{'fixture':True})
        self.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?",(tid,))
        self.assertEqual(framework.tick(self.c,self.cfg)['state'],'waiting_inflight')
        self.c.execute("UPDATE tasks SET status='failed' WHERE task_id=?",(tid,));store.set_flag(self.c,'paused','1')
        self.assertEqual(framework.tick(self.c,self.cfg)['state'],'paused')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='research_evidence'").fetchone()[0],0)

    def test_additional_issue_registration_preserves_caps_and_historical_owner(self):
        issue={'id':'H-X15','revision':1,'mechanism':'A distinct measured mechanism predicts returns.',
               'measurement':'A registered historical scalar observation.','falsifier':'The fixed paired contrast fails in all registered segments.',
               'profile':'base','required_assertions':list(research_contracts.COMMON)+['fixture_measurement'],
               'template':'paired_intervention_v1'}
        proposal=agenda.propose(self.c,self.p,issue)
        self.assertEqual(len(self.p['campaign']['hypotheses']),14)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
        h=copy.deepcopy(self.p['campaign']['hypotheses'][0]);h.update(id=issue['id'],claim=issue['mechanism'],falsifier=issue['falsifier'],required_assertions=issue['required_assertions'],state='blocked')
        h['steps'][0]['data_contract']['contract_id']=issue['id']
        h['steps'][0]['data_contract']['required_assertions']=issue['required_assertions']
        approval={'issue_id':issue['id'],'issue_revision':1,'issue_hash':proposal['issue_hash'],'approved_by':'fixture-owner',
                  'reason':'Register a bounded new research question within existing total allocation',
                  'source_policy_hash':util.sha256_json(self.p),'valid_until':'2099-01-01T00:00:00Z','hypothesis':h}
        autopilot.event(self.c,99,'campaign_assignment',json.dumps({'campaign_id':self.p['campaign']['id'],'version':1,'hypothesis':'H-N2','phase':'representative'}))
        tid,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':99},'owned-queued')
        before=autopilot.task(self.c,tid)['payload_json']
        result=agenda.register(self.c,self.cfg,approval)
        updated=autopilot.policy(self.cfg)
        self.assertEqual(len(updated['campaign']['hypotheses']),15);self.assertEqual(updated['campaign']['pilot_cap'],56)
        self.assertEqual(result['migration']['remaining'],55)
        self.assertEqual(autopilot.task(self.c,tid)['payload_json'],before)
        self.assertEqual(agenda.register(self.c,self.cfg,approval),result)
        self.assertEqual(result['state'],'registered')

    def test_registration_recovers_after_file_write_before_database_commit(self):
        real_write=util.write_json
        def interrupted(path,doc):
            real_write(path,doc)
            raise OSError('Fixture interruption after atomic policy write')
        with patch('wq.util.write_json',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'Fixture interruption'):
                self.test_additional_issue_registration_preserves_caps_and_historical_owner()
        intent=campaign.records(self.c,'research_issue_registration_intent')[-1][1]
        self.assertEqual(campaign.records(self.c,'research_issue_registered'),[])
        from wq import db
        self.c.close();self.c=db.connect(self.cfg.db_path);self.f.c=self.c
        result=agenda.register(self.c,self.cfg,intent['approval'])
        self.assertEqual(result['state'],'registered')
        self.assertEqual(len(autopilot.policy(self.cfg)['campaign']['hypotheses']),15)
        self.assertEqual(agenda.register(self.c,self.cfg,intent['approval']),result)
        self.assertEqual(len(campaign.records(self.c,'research_issue_registered')),1)

    def test_registration_recovery_preserves_unrelated_policy_edit(self):
        with patch('wq.util.write_json',side_effect=OSError('Fixture interruption before write')):
            with self.assertRaises(OSError):self.test_additional_issue_registration_preserves_caps_and_historical_owner()
        intent=campaign.records(self.c,'research_issue_registration_intent')[-1][1]
        self.p['valid_until']='2098-01-01T00:00:00Z';self.f.save()
        before=autopilot.policy(self.cfg)
        with self.assertRaisesRegex(ValueError,'unrelated policy'):
            agenda.register(self.c,self.cfg,intent['approval'])
        self.assertEqual(autopilot.policy(self.cfg),before)

    def test_runner_restart_collects_once_and_pause_preserves_queued_task(self):
        gap,_,_,_=self.gap_source()
        framework.tick(self.c,self.cfg);self.c.commit()
        store.set_flag(self.c,'paused','1');self.c.commit()
        with patch('wq.brain_client.BrainClient.request',side_effect=AssertionError('No network')):
            self.assertEqual(runner._run_once(self.c,self.cfg,300)[0],runner.PAUSED)
            self.assertEqual(self.c.execute("SELECT status FROM tasks WHERE kind='research_evidence'").fetchone()[0],'queued')
            store.set_flag(self.c,'paused','0');self.c.commit()
            from wq import db
            self.c.close();self.c=db.connect(self.cfg.db_path);self.f.c=self.c
            self.assertEqual(runner._run_once(self.c,self.cfg,300)[0],0)
            self.c.close();self.c=db.connect(self.cfg.db_path);self.f.c=self.c
            for _ in range(3):self.assertEqual(runner._run_once(self.c,self.cfg,300)[0],0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='research_evidence'").fetchone()[0],1)
        resource=framework.report(self.c)['resources']
        self.assertEqual(len(resource),1);self.assertEqual(resource[0]['model_calls'],0)
        self.assertEqual(resource[0]['api_read_attempts'],0);self.assertIsNone(resource[0]['runtime_cost_usd'])
        self.assertGreater(resource[0]['runtime_seconds'],0)
        self.assertEqual(framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]['next_trigger'],'semantic_contract_verification')

    def test_queued_evidence_expiry_is_checked_at_dispatch(self):
        _,source,_,_=self.gap_source();framework.tick(self.c,self.cfg)
        with patch('wq.util.now',return_value=util.parse_iso(source['valid_until'])):
            self.assertEqual(self.dispatch()[0],'blocked')
        self.assertEqual(campaign.records(self.c,'research_work_resources')[-1][1]['api_read_attempts'],0)

    def test_maintenance_cannot_starve_behind_continuous_auxiliary_work(self):
        now=util.now_iso()
        options=[{'id':k,'kind':k,'first_seen':now} for k in ('evidence','maintenance','reassess','research')]
        selected=[]
        for i in range(12):
            choice=framework.choose(self.c,options);selected.append(choice['kind'])
            campaign.append_once(self.c,'fairness:'+str(i),'research_work_selected',
                                 {**choice,'options':options})
        self.assertIn('maintenance',selected[:7])
        for start in range(10):self.assertIn('research',selected[start:start+3])

    def test_registered_collector_through_transport_feedback_evaluation_and_replay(self):
        from wq import feedback,brain_jobs
        from wq.brain_submission import REQUIRED
        from test_feedback import recordset
        material=self.root/'collector-source.md'
        text='Fixture provider: daily USD gross cumulative PnL, capital bookSize, UTC intervals and dataset revision.'
        material.write_text(text);proof=[{'material_id':'provider','quote':text}]
        mappings={k:{'source':'contract','value':v,'evidence_refs':proof} for k,v in {'frequency':'daily','value_unit':'USD','cost_basis':'gross','timezone':'UTC'}.items()}
        mappings.update(capital_basis={'source':'alpha','path':['settings','bookSize'],'evidence_refs':proof},data_revision={'source':'pnl','path':['revision'],'evidence_refs':proof})
        contract={'schema':'wq.observation-contract/v1','id':'fixture','adapter':observation.ADAPTER,
                  'scope':{k:self.p['settings'][k] for k in ('region','universe','delay')},
                  'verification_method':'owner_attestation','verified_by':'fixture-owner',
                  'verified_at':'2026-01-01T00:00:00Z','valid_until':'2099-01-01T00:00:00Z','mappings':mappings,
                  'materials':[{'id':'provider','path':str(material),'sha256':hashlib.sha256(material.read_bytes()).hexdigest(),
                                'source':'fixture://provider','source_type':'provider_documentation','product_version':'fixture-v1'}]}
        path=self.root/'collector.json';path.write_text(json.dumps(contract))
        self.p['campaign']['execution_profiles']['base']['observation_collectors']={'fixture':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}
        self.p['campaign']['hypotheses'][0]['steps'][0]['evaluation']['collector_id']='fixture'
        self.f.save()
        with patch('wq.research_observation.capability',wraps=REAL_CAPABILITY):
            pair=self.f.prepared();refs={d['arm']:d for _,d in campaign.records(self.c,'campaign_request_ref')}
            for index,(arm,ref) in enumerate(refs.items()):
                tid=ref['task_id'];task=autopilot.task(self.c,tid);payload=json.loads(task['payload_json']);aid='verifiedfixture'+str(index)
                stats={'sharpe':1.5,'fitness':1.1}
                alpha={'id':aid,'regular':{'code':payload['request']['regular']},'settings':{**payload['request']['settings'],'bookSize':100},
                       'is':{**stats,'checks':[{'name':n,'result':'FAIL' if n=='LOW_SHARPE' else 'PASS'} for n in REQUIRED]},'train':stats,'test':stats}
                with patch('wq.brain_jobs.BrainClient') as client:
                    client.return_value.preflight.return_value=(200,{},{})
                    client.return_value.request.side_effect=[(201,{'location':'/simulations/fixture'+str(index)},{}),(200,{}, {'alpha':aid}),(200,{},alpha)]
                    for _ in range(3):status,_,_=brain_jobs.step(self.c,self.cfg,task,payload);self.c.commit()
                    self.assertEqual(status,'succeeded')
                self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
                ft,_=feedback.enqueue(self.c,self.cfg,aid);fp=json.loads(autopilot.task(self.c,ft)['payload_json'])
                pnl=recordset(['date','pnl'],[[date,float(i*(2 if arm=='treatment' else 1))] for i,date in enumerate(pair['evaluation']['date_grid'])]);pnl['revision']='fixture-dataset-r1'
                yearly=recordset(['year','sharpe','fitness','pnl'],[[str(y),1.5,1.1,100] for y in range(2015,2025)])
                with patch('wq.feedback.get_with_reauth',side_effect=[(200,{},alpha),(200,{},pnl),(200,{},yearly)]):
                    for _ in range(4):status,_,_=feedback.step(self.c,self.cfg,{'task_id':ft},fp)
                    self.assertEqual(status,'succeeded')
                self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(ft,))
            autopilot.advance(self.c,self.cfg,self.f.cycle(),self.p)
            result=campaign.evaluations(self.c,pair['pair_id'])[-1]
            self.assertEqual(result['assessment'],'eligible_for_expansion')
            self.assertEqual(result['next_action'],'create_review_proposal')
            for _,record in campaign.records(self.c,'campaign_observation_bound'):
                obs=record['observation'];self.assertEqual(obs['capital_basis'],100)
                original=observation.read_snapshot(obs['identity_snapshot'])
                Path(original['observation']['report']['pnl_path']).unlink()
            path.unlink();material.unlink()
            replay=campaign.replay_evaluation(self.c,pair['pair_id'],result['observation_hash'])
            self.assertEqual(replay['measurement']['assessment'],'continue');self.assertFalse(replay['dispatch'])
            decision=knowledge.resolve_proposal(self.c,pair['pair_id'],'accepted','Owner accepts planning, with no new execution budget','fixture-owner',{'reference':'fixture://owner','additional_requests':0})
            self.assertEqual(decision['execution_authority'],0)
            self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'").fetchone()[0],2)

    def remote_source(self,adapter):
        if adapter=='brain_field_metadata_v1':
            self.p['campaign']['hypotheses'][1]['steps']=copy.deepcopy(self.p['campaign']['hypotheses'][0]['steps'])
            self.f.save()
        gap,source,_,_=self.gap_source()
        source.update(revision=2,adapter=adapter,source={} if adapter=='brain_operators_v1' else {'field_id':gap['fields'][0],'query':gap['query']})
        framework.register_source(self.c,self.cfg,source)
        self.cfg.data['brain_api']={'enabled':True,'authorized_until':'2099-01-01T00:00:00Z'}
        return gap,source

    def test_registered_remote_readers_keep_scope_and_collect_without_semantic_authority(self):
        for adapter in ('brain_field_metadata_v1','brain_operators_v1'):
            with self.subTest(adapter=adapter):
                gap,source=self.remote_source(adapter)
                response=({'id':source['source']['field_id'],'data':[gap['query']]} if adapter=='brain_field_metadata_v1' else [{'name':'rank','scope':['REGULAR']}])
                with patch('wq.brain_client.BrainClient.request',return_value=(200,{},response)) as request:
                    framework.tick(self.c,self.cfg);status,detail=self.dispatch()
                self.assertEqual(status,'succeeded');self.assertEqual(detail['semantic_authority'],0)
                self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.args[0],'GET')
                resource=campaign.records(self.c,'research_work_resources')[-1][1]
                self.assertEqual(resource['api_read_attempts'],1);self.assertIsNone(resource['api_cost_usd'])
                # Remove the source only within this isolated test between the two adapters.
                self.c.execute("DELETE FROM campaign_record_keys")
                self.c.execute("DELETE FROM research_events WHERE kind IN ('research_evidence_source','research_gap')")

    def test_read_timeout_backs_off_without_unknown_post_or_global_pause(self):
        from wq.errors import AdapterError
        gap,_=self.remote_source('brain_operators_v1')
        framework.tick(self.c,self.cfg)
        with patch('wq.brain_client.BrainClient.request',side_effect=AdapterError(AdapterError.NETWORK,'Fixture read timeout')):
            self.assertEqual(self.dispatch()[0],'failed')
        self.assertFalse(store.is_paused(self.c))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM tasks WHERE status='unknown'").fetchone()[0],0)
        self.assertEqual(framework.latest(self.c,'research_gap','gap_id')[gap['gap_id']]['state'],'waiting_retry')
        self.assertEqual(framework.tick(self.c,self.cfg)['state'],'waiting_for_changed_evidence')

    def test_read_auth_error_still_requires_authentication_recovery(self):
        from wq.errors import AdapterError
        self.remote_source('brain_operators_v1');framework.tick(self.c,self.cfg)
        with patch('wq.brain_client.BrainClient.request',side_effect=AdapterError(AdapterError.AUTH,'Fixture expired authentication')):
            self.assertEqual(self.dispatch()[0],'blocked')
        self.assertTrue(store.is_paused(self.c))
        self.assertEqual(campaign.records(self.c,'research_work_resources')[-1][1]['outcome'],'adapter_error:auth')

    def test_work_selection_reserves_exploration_and_is_readable(self):
        now=util.now_iso()
        for i in range(2):
            campaign.append_once(self.c,'aux:'+str(i),'research_work_selected',{'kind':'evidence','id':str(i)})
        options=[{'id':'evidence','kind':'evidence','first_seen':now},{'id':'research','kind':'research','first_seen':now}]
        self.assertEqual(framework.choose(self.c,options)['kind'],'research')
        self.c.execute('PRAGMA query_only=ON')
        self.assertEqual(framework.report(self.c)['queue_source'],'tasks')


class AllocationAndMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.f=strategy_fixture.ResearchStrategyTests('test_maintenance_is_disabled_without_optin_and_does_not_dispatch')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cfg,self.c=self.f.cfg,self.f.c
        self.baseline={k:'fixture' for k in ('source','policy','models','budget','usable_definition')}

    def cycle(self,cid):
        now=util.now_iso()
        self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(?,'researching','{}','fixture',?,?)",(cid,now,now))

    def test_mixed_cycles_keep_two_ordinary_arms_and_all_resource_cost(self):
        learning.freeze_experiment(self.c,'mixed',self.baseline,4,2)
        for cid in range(1,9):
            self.cycle(cid)
            if cid%2==0:autopilot.event(self.c,cid,'campaign_v2_assignment',json.dumps({'campaign_id':'fixture','hypothesis':'H-N1'}))
            arm=learning.assign(self.c,cid,'mixed',self.baseline)
            self.assertEqual(arm,('baseline' if cid in (1,5) else 'learning') if cid%2 else None)
            self.c.execute("UPDATE research_cycles SET state='closed' WHERE cycle_id=?",(cid,))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_assignments').fetchone()[0],4)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_resource_cycles').fetchone()[0],8)
        with patch('wq.research_learning.model_cost',side_effect=lambda c,ids:{'known_usd':len(ids),'linked_calls':len(ids),'unknown_cost_calls':0}):
            report=metrics.comparison(self.c,'mixed')
        self.assertEqual(report['global_resource_cost']['known_usd'],8)
        self.assertEqual(report['campaign_resource_cost']['known_usd'],4)
        self.assertFalse(any('campaign_measurements' in r for r in report['reasons']))

    def test_ordinary_allocation_balances_each_scope_independently(self):
        learning.freeze_experiment(self.c,'strata',self.baseline,6,3)
        observed={0:[],1:[]}
        for cid,scope in enumerate((0,1,1,0,0,1),1):
            self.cycle(cid)
            self.c.execute('UPDATE research_cycles SET policy_json=? WHERE cycle_id=?',
                           (json.dumps({'settings':{'delay':scope},'bindings':{}}),cid))
            observed[scope].append(learning.assign(self.c,cid,'strata',self.baseline))
            self.c.execute("UPDATE research_cycles SET state='closed' WHERE cycle_id=?",(cid,))
        self.assertEqual(observed,{0:['baseline','learning','baseline'],1:['baseline','learning','baseline']})

    def test_legacy_campaign_assignment_is_never_relabelled_as_clean(self):
        learning.freeze_experiment(self.c,'legacy',self.baseline,4,2)
        self.cycle(1);learning.assign(self.c,1,'legacy',self.baseline)
        autopilot.event(self.c,1,'campaign_v2_assignment',json.dumps({'campaign_id':'fixture','hypothesis':'H-N1'}))
        self.c.execute("UPDATE research_cycles SET state='closed' WHERE cycle_id=1")
        before=[tuple(r) for r in self.c.execute('SELECT * FROM learning_assignments')]
        self.cycle(2)
        with self.assertRaisesRegex(ValueError,'Legacy mixed'):learning.assign(self.c,2,'legacy',self.baseline)
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT * FROM learning_assignments')],before)
        result=metrics.comparison(self.c,'legacy')
        self.assertTrue(any('campaign_measurements' in r for r in result['reasons']))

    def test_contribution_retries_when_reference_and_intervals_arrive_without_refitting(self):
        from wq import research_maintenance as maintenance
        self.cfg.data['research_learning']={'maintenance_enabled':True}
        self.f.trial('first',quality='usable');self.f.snapshot('first',[0,1,3,2])
        actual_as_of=learning.as_of
        def trials(*args,**kwargs):
            values=actual_as_of(*args,**kwargs)
            for t in values:
                oid=t['outcome']['observation_id'];obs=json.loads(self.c.execute('SELECT document_json FROM learning_observations WHERE observation_id=?',(oid,)).fetchone()[0])
                t['outcome'].update(content_hash=obs['content_hash'],data_through=obs['data_through'])
            return values
        with patch('wq.research_learning.sync',return_value={}),patch('wq.research_learning.as_of',side_effect=trials),patch('wq.autopilot.policy',return_value={'settings':self.f.settings,'bindings':self.f.bindings}):
            one=maintenance.tick(self.c,self.cfg,True)
            self.assertEqual(one['contribution_preparation']['state'],'waiting_reference')
            self.f.trial('later',strategy_fixture.proposal('std')['ast'],quality='usable');self.f.snapshot('later',[0,2,1,4])
            two=maintenance.tick(self.c,self.cfg,True)
            self.assertEqual(two['contribution_preparation']['candidate'],'first')
            self.assertEqual(two['contribution_preparation']['state'],'waiting_calibration')
            values=[sum(j%5-1 for j in range(i)) for i in range(80)]
            other=[sum(j%3-.4 for j in range(i)) for i in range(80)]
            self.f.snapshot('first',values);self.f.snapshot('later',other)
            three=maintenance.tick(self.c,self.cfg,True)
            self.assertEqual(three['contribution_preparation']['state'],'frozen')
            pid=three['contribution_preparation']['pool_id']
            frozen=self.c.execute('SELECT document_json FROM learning_pool_contracts WHERE pool_id=?',(pid,)).fetchone()[0]
            self.f.snapshot('first',values+[200,201])
            for _ in range(3):maintenance.tick(self.c,self.cfg,True)
            self.assertEqual(self.c.execute('SELECT document_json FROM learning_pool_contracts WHERE pool_id=?',(pid,)).fetchone()[0],frozen)

    def test_approved_epoch_inherits_remaining_and_owner_stop_is_permanent(self):
        learning.freeze_experiment(self.c,'root',self.baseline,6,2)
        for cid in (1,2):
            self.cycle(cid);learning.assign(self.c,cid,'root',self.baseline)
            self.c.execute("UPDATE research_cycles SET state='closed' WHERE cycle_id=?",(cid,))
        tid,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':1},'old-reservation')
        self.c.execute("UPDATE tasks SET status='failed' WHERE task_id=?",(tid,))
        learning.stop_experiment(self.c,'root','Frozen baseline superseded by approved candidate',stop_type='baseline_superseded')
        target={**self.baseline,'source':'new-source'}
        self.cfg.data.update(brain_api={'enabled':True,'authorized_until':'2099-01-01T00:00:00Z'},routing={'authorized_until':'2099-01-01T00:00:00Z'})
        approval={'transition_id':'fixture-next','from_experiment':'root','from_baseline_hash':util.sha256_json(self.baseline),
                  'to_baseline':target,'valid_until':'2099-01-01T00:00:00Z','reason':'Owner reviewed this exact candidate without additional budget','approved_by':'fixture-owner'}
        with patch('wq.research_learning.current_baseline',return_value=target),patch('wq.autopilot.policy',return_value={'valid_until':'2099-01-01T00:00:00Z'}),patch('wq.autopilot.enabled',return_value=True):
            self.assertEqual(lifecycle.advance(self.c,self.cfg)['state'],'owner_required')
            lifecycle.approve_transition(self.c,self.cfg,approval)
            result=lifecycle.advance(self.c,self.cfg)
            self.assertEqual(result['state'],'created');self.assertEqual(result['remaining']['cycles'],4)
            self.assertEqual(result['remaining']['requests_by_arm']['baseline'],1)
            self.assertEqual(lifecycle.advance(self.c,self.cfg)['state'],'unchanged')
            self.cycle(3);learning.assign(self.c,3,result['experiment'],target)
            tid,_=store.enqueue_task(self.c,'brain_simulation',{'research_cycle_id':3},'new-reservation')
            with self.assertRaisesRegex(ValueError,'budget'):learning.check_request_budget(self.c,3)
            learning.stop_experiment(self.c,'root','Owner explicitly stops the original lineage')
            self.c.execute("UPDATE research_cycles SET state='closed' WHERE cycle_id=3")
            self.cycle(4)
            with self.assertRaisesRegex(ValueError,'Owner'):learning.assign(self.c,4,result['experiment'],target)
            with self.assertRaisesRegex(ValueError,'Owner'):learning.check_request_budget(self.c,3)
            learning.stop_experiment(self.c,result['experiment'],'Owner explicitly stops this successor')
            self.assertEqual(lifecycle.advance(self.c,self.cfg)['state'],'owner_stop')
            with self.assertRaises(ValueError):lifecycle.approve_transition(self.c,self.cfg,{**approval,'from_experiment':result['experiment'],'from_baseline_hash':util.sha256_json(target)})


class CollectorTests(unittest.TestCase):
    def test_registered_mapping_reads_actual_receipt_and_replay_uses_retained_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);material=root/'provider.md';text='Fixture only: daily USD gross PnL on book capital in UTC; dataset revision is in the receipt.'
            material.write_text(text)
            spec={'collector_id':'fixture'};profile={'settings':{'region':'USA','universe':'TOP3000','delay':1}}
            proof=[{'material_id':'provider','quote':text}]
            mappings={k:{'source':'contract','value':v,'evidence_refs':proof} for k,v in {'frequency':'daily','value_unit':'USD','cost_basis':'gross','timezone':'UTC'}.items()}
            mappings.update(capital_basis={'source':'alpha','path':['settings','bookSize'],'evidence_refs':proof},data_revision={'source':'pnl','path':['revision'],'evidence_refs':proof})
            doc={'schema':'wq.observation-contract/v1','id':'fixture','adapter':observation.ADAPTER,
                 'scope':profile['settings'],'verification_method':'owner_attestation','verified_by':'fixture-owner',
                 'verified_at':'2026-01-01T00:00:00Z','valid_until':'2099-01-01T00:00:00Z','mappings':mappings,
                 'materials':[{'id':'provider','path':str(material),'sha256':hashlib.sha256(material.read_bytes()).hexdigest(),
                               'source':'fixture://provider','source_type':'provider_documentation','product_version':'fixture-v1'}]}
            path=root/'contract.json';path.write_text(json.dumps(doc));raw=path.read_bytes()
            profile['observation_collectors']={'fixture':{'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}}
            self.assertTrue(REAL_CAPABILITY(spec,profile)['ready'])
            self.assertFalse(REAL_CAPABILITY({'collector_id':'unknown'},profile)['ready'])
            alpha={'settings':{'bookSize':100}};pnl={'revision':'supplier-r1'}
            conventions=observation.mapped_conventions(doc,alpha,pnl)
            self.assertEqual(conventions['capital_basis'],100);self.assertEqual(conventions['revision_evidence'],'supplier-r1')
            with self.assertRaisesRegex(ValueError,'FIELD_MISSING'):observation.mapped_conventions(doc,alpha,{})
            saved=observation.retain_bytes(root/'cas',raw);path.unlink();material.unlink()
            self.assertFalse(REAL_CAPABILITY(spec,profile)['ready'])
            self.assertEqual(observation.mapped_conventions(observation.read_snapshot(saved),alpha,pnl),conventions)
