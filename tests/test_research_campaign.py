import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from helpers import make_env
from wq import autopilot, brain_jobs, catalog, research_campaign as c, research_dsl, store, util


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg,self.db=make_env(self.tmp.name,{'brain_api':{'enabled':True,'authorized_until':'2099-01-01T00:00:00Z','min_post_interval_s':0,'max_posts_per_24h':80},'limits':{'sims_per_week':240},'autopilot':{'max_simulations_per_week':240,'policy_file':'config/policy.json'}})
        self.addCleanup(self.db.close);autopilot.setup(self.db);brain_jobs.setup(self.db)
        self.settings={'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY'}
        snap={'schema':catalog.SNAPSHOT_SCHEMA,'query':catalog.query_from_settings(self.settings),'context':[self.settings],
              'field':{'id':'private_field','type':'MATRIX'},'queried_at':util.now_iso()}
        path=str(Path(self.tmp.name)/'evidence.json');util.write_json(path,snap)
        proof={'path':path,'sha256':util.sha256_json(snap)}
        b={'expression':'private_field','fields':['private_field'],'description':'Observable historical proxy only','source':'fixture'}
        self.p={'version':1,'scope':'exploratory_only_no_submission','valid_until':'2099-01-01T00:00:00Z','settings':self.settings,
                'bindings':{n:dict(b) for n in research_dsl.ROLES},'evidence_files':[proof]}
        hs=[{'id':hid,'state':'blocked','reason':'missing verified data','required_roles':['daily_return'],'settings':self.settings,
             'claim':'Historical event mechanism can predict later ranking','falsifier':'No association in later observations','control':'Use the same historical input without conditioning',
             'request_cap':4,'max_cycles':4} for hid in c.IDS]
        hs[0].update(state='ready',reason='',data_contract={'measurement':'Historical scalar proxy only','availability':'Available before the configured delay',
                     'missing':'Missing observations are not filled as zero','source':'fixture documentation','evidence':proof,
                     'verified_at':'2026-01-01T00:00:00Z','valid_until':'2099-01-01T00:00:00Z'})
        self.p['campaign']={'schema':c.SCHEMA,'enabled':True,'id':'fixture','version':1,'valid_until':'2099-01-01T00:00:00Z',
                            'baseline':c.baseline(self.p),'pilot_cap':56,'total_cap':240,'hypotheses':hs}
        self.save()

    def save(self):util.write_json(self.cfg.resolve('config/policy.json'),self.p)

    def cycle(self,n):
        self.db.execute("UPDATE research_cycles SET state='closed'")
        self.db.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(?,'researching',?,?,?,?)",(n,json.dumps(self.p),util.sha256_json(self.p),util.now_iso(),util.now_iso()))
        return c.allocate(self.db,self.p,n)

    def reserve(self,n,key):
        c.check_budget(self.db,self.cfg,n)
        return store.enqueue_task(self.db,'brain_simulation',{'research_cycle_id':n},key)[0]

    def test_readonly_report_preserves_expired_contract_and_all_opportunities(self):
        self.p['campaign']['hypotheses'][0]['data_contract']['valid_until']='2000-01-01T00:00:00Z'
        self.db.commit()
        from wq import desktop
        before=self.db.total_changes
        self.db.execute('PRAGMA query_only=ON')
        result=c.report(self.db,self.p)
        self.assertEqual(len(result['opportunities']),14)
        self.assertIn('expired',result['stop_reason'])
        with patch('wq.autopilot.policy',return_value=self.p):
            menu=desktop.research(self.db,self.cfg)
        self.assertEqual(len(menu['entries']),20)
        self.assertIn('持续研究框架',[entry['title'] for entry in menu['entries']])
        self.assertEqual(before,self.db.total_changes)

    def test_all_opportunities_preserved_missing_scope_and_semantics_rejected(self):
        self.assertEqual(len(c.report(self.db,self.p)['opportunities']),14)
        self.assertEqual(c.select(self.db,self.p)['hypothesis'],'H-N1')
        for mutation in ('scope','semantics','raw_field','unsupported'):
            p=copy.deepcopy(self.p);h=p['campaign']['hypotheses'][0]
            if mutation=='scope':h['settings']={**h['settings'],'delay':0}
            elif mutation=='semantics':h['data_contract']['missing']=''
            elif mutation=='raw_field':h['claim']='Use private_field historical observation'
            else:p['campaign']['hypotheses'][10].update(state='ready')
            with self.assertRaises(ValueError):c.validate(p)

    def test_round_robin_representatives_then_controls_and_bounded_no_post_cycles(self):
        self.p['campaign']['hypotheses'][1].update(copy.deepcopy(self.p['campaign']['hypotheses'][0]));self.p['campaign']['hypotheses'][1]['id']='H-N2';self.save()
        assigned=[self.cycle(i) for i in range(1,9)]
        self.assertEqual([a['hypothesis'] for a in assigned[:4]],['H-N1','H-N2','H-N1','H-N2'])
        self.assertEqual([a['phase'] for a in assigned[:4]],['representative','representative','control','control'])
        with self.assertRaisesRegex(ValueError,'waiting_evidence_or_review'):c.select(self.db,self.p)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_56_reservations_allow_first_56_dispatch_not_57_and_unknown_never_refunds(self):
        # The dispatch contract is identical for each hypothesis; isolate budget from the H-V2 capability gate.
        self.cycle(1)
        for i,hid in enumerate(c.IDS,1):
            if i>1:autopilot.event(self.db,i,'campaign_assignment',json.dumps({'campaign_id':'fixture','version':1,'hypothesis':hid,'phase':'representative'}))
            for j in range(4):store.enqueue_task(self.db,'brain_simulation',{'research_cycle_id':i},f'{i}-{j}')
        tasks=self.db.execute("SELECT task_id FROM tasks WHERE kind='brain_simulation' ORDER BY rowid").fetchall()
        for status in ('queued','failed','unknown'):
            self.db.execute('UPDATE tasks SET status=?',(status,))
            with self.assertRaisesRegex(ValueError,'reservation cap'):c.check_budget(self.db,self.cfg,1)
            for task in tasks[:4]:c.check_budget(self.db,self.cfg,1,task[0])
            # Isolate the 56-slot accounting invariant from independent capability gates.
            authorized=copy.deepcopy(self.p)
            for h in authorized['campaign']['hypotheses']:h['state']='ready'
            with patch('wq.autopilot.policy',return_value=authorized), patch('wq.research_campaign.active',return_value=authorized['campaign']):
                for index,task in enumerate(tasks):c.check_budget(self.db,self.cfg,index//4+1,task[0])
            self.assertEqual(c.report(self.db,self.p)['reserved'],56)
        self.db.commit()
        from wq.db import connect
        other=connect(self.cfg.db_path)
        try:
            with self.assertRaisesRegex(ValueError,'reservation cap'):c.check_budget(other,self.cfg,1)
        finally:other.close()
        self.p['campaign']['version']=2;self.save()
        with self.assertRaisesRegex(ValueError,'pilot reservations exhausted'):c.select(self.db,self.p)

    def test_bucket_caps_do_not_move_and_cycle_roles_are_enforced(self):
        self.cycle(1)
        for i in range(4):self.reserve(1,str(i))
        with self.assertRaisesRegex(ValueError,'cap'):self.reserve(1,'excess')
        with self.assertRaisesRegex(ValueError,'no executable'):c.select(self.db,self.p)
        with self.assertRaisesRegex(ValueError,'roles'):c.validate_candidate(self.db,1,self.p,{'ast':{'op':'field','name':'price_level'}})
        self.assertEqual(c.report(self.db,self.p)['not_dispatched'],4)

    def test_drift_disable_and_freeze_changes_block_only_new_dispatch(self):
        self.cycle(1);tid=self.reserve(1,'one')
        c.check_budget(self.db,self.cfg,1,tid)
        self.p['campaign']['enabled']=False;self.save()
        with self.assertRaisesRegex(ValueError,'disabled'):c.check_budget(self.db,self.cfg,1,tid)
        self.db.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',(tid,'post_started',None,None,None,util.now_iso(),util.now_iso()))
        task=dict(self.db.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone())
        with patch('wq.brain_jobs.BrainClient') as client:
            result=brain_jobs.step(self.db,self.cfg,task,{'research_cycle_id':1})
            self.assertEqual(result[0],'unknown');client.assert_not_called()
        report=c.report(self.db,self.p)
        self.assertEqual(report['dispatch_uncertain'],1);self.assertEqual(report['post_denominator_range'],[0,1])
        self.p['campaign']['enabled']=True;self.p['settings']['decay']=8;self.save()
        with self.assertRaises(ValueError):c.check_budget(self.db,self.cfg,1,tid)

    def test_cross_cycle_reuse_keeps_original_ownership_and_no_new_reservation(self):
        # Exercise the actual enqueue boundary with a valid tutorial request.
        self.cycle(1)
        doc={'research_cycle_id':1,'purpose':'tutorial_validation','request':{'type':'REGULAR','regular':'rank(private_field)','settings':self.settings},
             'config':{**self.settings,'fields':['private_field'],'catalog_verified':True},'evidence':{'settings_verified':True,'source':'fixture'}}
        first,created=brain_jobs.enqueue(self.db,self.cfg,doc);self.assertTrue(created)
        self.db.execute("UPDATE tasks SET status='failed' WHERE task_id=?",(first,))
        self.cycle(2);doc['research_cycle_id']=2
        second,created=brain_jobs.enqueue(self.db,self.cfg,doc)
        self.assertEqual(first,second);self.assertFalse(created)
        self.assertEqual(json.loads(self.db.execute('SELECT payload_json FROM tasks WHERE task_id=?',(first,)).fetchone()[0])['research_cycle_id'],1)
        self.assertEqual(c.report(self.db,self.p)['reserved'],1)

    def tutorial_doc(self,cid=1,expression='rank(private_field)'):
        return {'research_cycle_id':cid,'purpose':'tutorial_validation','request':{'type':'REGULAR','regular':expression,'settings':self.settings},
                'config':{**self.settings,'fields':['private_field'],'catalog_verified':True},'evidence':{'settings_verified':True,'source':'fixture'}}

    def test_concurrent_enqueue_serializes_last_bucket_reservation(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from wq.db import connect
        self.cycle(1)
        for i in range(3):self.reserve(1,'prior-'+str(i))
        self.db.commit();barrier=Barrier(2)
        def enqueue(i):
            conn=connect(self.cfg.db_path)
            try:
                barrier.wait(timeout=5)
                try:
                    result=brain_jobs.enqueue(conn,self.cfg,self.tutorial_doc(expression=f'ts_mean(private_field, {i+5})'))
                    conn.commit();return result[1]
                except ValueError as exc:
                    conn.rollback();return str(exc)
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(enqueue,(0,1)))
        self.assertEqual(results.count(True),1)
        self.assertTrue(any(isinstance(x,str) and 'cap reached' in x for x in results))
        self.assertEqual(c.report(self.db,self.p)['reserved'],4)

    def test_remote_acceptance_before_local_response_crash_cannot_repost(self):
        from wq.db import connect
        self.cycle(1);doc=self.tutorial_doc();tid,_=brain_jobs.enqueue(self.db,self.cfg,doc)
        task=dict(self.db.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone());accepted=[]
        def remote(method,path,body=None):
            accepted.append((method,path));raise RuntimeError('fixture crash after remote acceptance')
        with patch('wq.brain_jobs.BrainClient') as cls:
            cls.return_value.preflight.return_value=(200,{},{})
            cls.return_value.request.side_effect=remote
            with self.assertRaises(RuntimeError):brain_jobs.step(self.db,self.cfg,task,doc)
            other=connect(self.cfg.db_path)
            try:self.assertEqual(brain_jobs.step(other,self.cfg,task,doc)[0],'unknown')
            finally:other.close()
            self.assertEqual(accepted,[('POST','/simulations')])
        self.assertEqual(c.report(self.db,self.p)['dispatch_uncertain'],1)

    def test_disabled_campaign_still_reconciles_existing_receipt_via_get(self):
        self.cycle(1);doc=self.tutorial_doc();tid,_=brain_jobs.enqueue(self.db,self.cfg,doc)
        task=dict(self.db.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone())
        with patch('wq.brain_jobs.BrainClient') as cls:
            client=cls.return_value;client.jar=[True];client.preflight.return_value=(200,{},{})
            client.request.side_effect=[(201,{'location':'/simulations/fixture'},{}),(200,{'retry-after':'60'},{})]
            brain_jobs.step(self.db,self.cfg,task,doc)
            self.p['campaign']['enabled']=False;self.save()
            brain_jobs.step(self.db,self.cfg,task,doc)
            self.assertEqual([x.args[0] for x in client.request.call_args_list],['POST','GET'])
