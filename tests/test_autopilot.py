import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import autopilot, brain_jobs, research_dsl, runner, store, util
from wq.db import connect


def proposal(op='mean'):
    return {'title':'过去收益的可证伪探索假设','hypothesis':'研究过去收益的结构是否和后续收益有关，窗口在观测前固定。','counterexample':'交易成本或风险暴露可能完全解释表面关联，因此不能宣称盈利。','ast':{'op':'neg','arg':{'op':op,'arg':{'op':'field','name':'daily_return'},'window':20}}}


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg,self.c=make_env(self.temp.name,{'brain_api':{'enabled':True,'authorized_until':'2099-01-01T00:00:00Z'},'routing':{'authorized_until':'2099-01-01T00:00:00Z'},'autopilot':{'enabled':True,'policy_file':'config/policy.json','interval_s':60,'max_cycles_per_day':4,'max_simulations_per_week':3}})
        self.addCleanup(lambda:self.c.close())
        self.root=Path(self.temp.name);proof=self.root/'evidence.json';util.write_json(str(proof),{'fixture':True})
        settings={'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY'}
        self.p={'version':1,'scope':'exploratory_only_no_submission','valid_until':'2099-01-01T00:00:00Z','verified_at':'2026-01-01T00:00:00Z','settings':settings,'source':'fixture://source','bindings':{n:{'expression':'returns','fields':['returns'],'source':'fixture://field'} for n in research_dsl.ROLES},'evidence_files':[{'path':str(proof),'sha256':util.sha256_json({'fixture':True})}]}
        self.save_policy();autopilot.setup(self.c);brain_jobs.setup(self.c)
        self.counter=0;self.posts=0;self.reject_review=False;self.same_provider=False
        self.add_patch('wq.routing.catalog',return_value={'default':'steady','presets':{'steady':{'routes':{'research':['a'],'review':['b']}}}})
        self.add_patch('wq.routing._unavailable',return_value=None)
        self.add_patch('wq.routing.enqueue_job',side_effect=self.job)
        self.add_patch('wq.brain_client.BrainClient').return_value.jar=[True]
        client=self.add_patch('wq.brain_jobs.BrainClient').return_value;client.jar=[True];client.request.side_effect=self.api
        self.real_dispatch=runner.dispatch_task
        self.add_patch('wq.runner.dispatch_task',side_effect=self.dispatch)

    def add_patch(self,*a,**k):
        p=patch(*a,**k);obj=p.start();self.addCleanup(p.stop);return obj
    def save_policy(self):util.write_json(str(self.root/'config/policy.json'),self.p)
    def job(self,conn,cfg,role,prompt_file,input_dir=None,title=None):
        self.counter+=1;path=self.root/f'job-{self.counter}';path.mkdir()
        (path/'prompt.txt').write_text(Path(prompt_file).read_text())
        payload={'job_dir':str(path),'role':role,'routing':True}
        return store.enqueue_task(conn,'agent_call',payload)[0],str(path)
    def dispatch(self,conn,cfg,t):
        if t['kind']!='agent_call':return self.real_dispatch(conn,cfg,t)
        payload=json.loads(t['payload_json']);role=payload['role']
        row=conn.execute("SELECT * FROM research_cycles WHERE state!='closed'").fetchone()
        if role=='research':obj={'status':'completed','candidate':proposal('mean' if row['cycle_id']==1 else 'std')}
        else:obj={'status':'completed','review':{'candidate_hash':row['candidate_hash'],'accept':not self.reject_review,'checks':{k:True for k in ['past_only','economic_rationale','falsifiable','not_parameter_search','within_scope','simple']},'reason':'这是明确受限的探索，不能当作经济机制已获证明。'}}
        util.write_json(str(Path(payload['job_dir'])/'result.json'),obj)
        name='a' if role=='research' or self.same_provider else 'b'
        conn.execute('INSERT INTO task_routes(task_id,snapshot_json,phase,updated_at) VALUES(?,?,?,?)',(t['task_id'],json.dumps({'chain':[name],'preset':'steady'}),'complete',util.now_iso()))
        return 'succeeded',{},None
    def api(self,method,path,body=None):
        if method=='POST':
            self.posts+=1;self.last_request=body;return 201,{'location':'/simulations/test'},{}
        if path.endswith('/test'):return 200,{}, {'alpha':f'alpha{self.posts}'}
        return 200,{}, {'id':f'alpha{self.posts}','regular':{'code':self.last_request['regular']},'settings':self.last_request['settings'],'is':{'sharpe':0.1,'checks':[{'name':'LOW_SHARPE','result':'FAIL'}]}}
    def tick(self):
        self.c.execute("UPDATE tasks SET not_before='2000' WHERE status='queued'")
        return runner.run_once(self.c,self.cfg)
    def cycle(self):return dict(self.c.execute('SELECT * FROM research_cycles ORDER BY cycle_id DESC LIMIT 1').fetchone())
    def full_cycle(self):
        for _ in range(6):self.tick()

    def test_two_cycles_resume_from_disk_and_real_state_transitions(self):
        self.tick();self.c.close();self.c=connect(self.cfg.db_path)
        for _ in range(5):self.tick()
        self.assertEqual(self.cycle()['state'],'closed');self.assertEqual(self.posts,1)
        self.assertIn('未通过',self.cycle()['outcome'])
        self.tick();self.assertEqual(self.counter,2) # cooldown: no extra models
        store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        self.full_cycle();self.assertEqual(self.posts,2)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM simulations WHERE synthetic=0').fetchone()[0],2)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)

    def test_double_tick_does_not_duplicate_proposal(self):
        autopilot.tick(self.c,self.cfg);autopilot.tick(self.c,self.cfg)
        self.assertEqual(self.counter,1)

    def test_reviewer_rejects_without_platform_call(self):
        self.reject_review=True
        for _ in range(3):self.tick()
        self.assertEqual(self.cycle()['state'],'closed');self.assertEqual(self.posts,0)

    def test_same_provider_cannot_approve_itself(self):
        self.same_provider=True
        for _ in range(3):self.tick()
        self.assertIn('不同渠道',self.cycle()['outcome']);self.assertEqual(self.posts,0)

    def test_unknown_freezes_no_fresh_cycle(self):
        autopilot.tick(self.c,self.cfg);r=self.cycle()
        self.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?",(r['research_task'],))
        for _ in range(3):autopilot.tick(self.c,self.cfg)
        self.assertEqual(self.counter,1);self.assertNotEqual(self.cycle()['state'],'closed')

    def test_expired_authorization_no_generation(self):
        self.p['valid_until']='2000-01-01T00:00:00Z';self.save_policy()
        self.tick();self.assertEqual(self.counter,0)

    def test_disabled_and_paused_no_new_calls(self):
        store.set_flag(self.c,'autopilot_enabled','0');self.tick();self.assertEqual(self.counter,0)
        store.set_flag(self.c,'autopilot_enabled','1');store.set_flag(self.c,'paused','1');self.tick();self.assertEqual(self.counter,0)

    def test_week_cap_auto_resumes_next_week(self):
        self.cfg.data['autopilot']['max_simulations_per_week']=1
        self.full_cycle();store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        self.tick();self.assertEqual(self.counter,2)
        later=util.now()+dt.timedelta(days=8)
        with patch('wq.util.now',return_value=later):self.tick()
        self.assertEqual(self.counter,3)

    def test_no_new_generation_during_global_platform_cooldown(self):
        store.set_flag(self.c,'brain_not_before',(util.now()+dt.timedelta(hours=2)).isoformat())
        self.tick();self.assertEqual(self.counter,0)

    def test_daily_limit_resumes_without_codex_next_day(self):
        self.cfg.data['autopilot']['max_cycles_per_day']=1
        self.reject_review=True
        for _ in range(3):self.tick()
        store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        self.tick();self.assertEqual(self.counter,2)
        with patch('wq.util.now',return_value=util.now()+dt.timedelta(days=1)):self.tick()
        self.assertEqual(self.counter,3)

    def test_unavailable_providers_wait_then_auto_resume(self):
        with patch('wq.routing._unavailable',return_value='quota'):
            self.tick();self.assertEqual(self.counter,0)
        self.tick();self.assertEqual(self.counter,1)

    def test_changed_evidence_no_new_call(self):
        (self.root/'evidence.json').write_text('{}');self.tick();self.assertEqual(self.counter,0)

    def test_policy_change_after_enqueue_blocks_post(self):
        self.tick();self.tick()
        # Admit without claiming simulation, then revoke local policy.
        autopilot.tick(self.c,self.cfg)
        self.p['source']='changed';self.save_policy()
        self.tick();self.assertEqual(self.posts,0)

    def test_crash_between_enqueue_and_cycle_commit_rolls_back(self):
        original=self.job
        def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('simulated crash')
        with patch('wq.routing.enqueue_job',side_effect=fail):
            with self.assertRaises(RuntimeError):autopilot.tick(self.c,self.cfg)
        self.assertFalse(self.c.in_transaction)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
        autopilot.tick(self.c,self.cfg)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)

    def test_parameter_and_sign_changes_same_family(self):
        a=proposal()['ast'];b=copy.deepcopy(a);b['arg']['window']=60
        self.assertEqual(research_dsl.compile_ast(a,self.p['bindings'])[2],research_dsl.compile_ast(b,self.p['bindings'])[2])
        self.assertEqual(research_dsl.compile_ast(a,self.p['bindings'])[2],research_dsl.compile_ast(a['arg'],self.p['bindings'])[2])

    def test_unsafe_ast_cannot_compile(self):
        for ast in [{'op':'field','name':'secret'},{'op':'raw','code':'submit()'},{'op':'mean','arg':{'op':'field','name':'daily_return'},'window':-1},{'op':'divide','left':{},'right':{}}]:
            with self.subTest(ast=ast),self.assertRaises(ValueError):research_dsl.compile_ast(ast,self.p['bindings'])

    def test_no_results_in_model_prompt(self):
        self.full_cycle()
        text=autopilot.generate_prompt(self.c)
        self.assertNotIn('alpha1',text);self.assertNotIn('sharpe',text.lower())
        self.assertNotIn('cashflow_op',text);self.assertNotIn('brain-session',text)

    def test_review_checks_must_be_explicit_and_bound(self):
        with self.assertRaises(ValueError):autopilot.validate_review({'review':{'candidate_hash':'wrong','accept':True}},'right')

    def test_login_does_not_clear_user_pause(self):
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','用户暂停')
        self.assertEqual(autopilot.after_login(self.c),[]);self.assertTrue(store.is_paused(self.c))

    def test_login_only_requeues_authenticated_get_not_post(self):
        tid,_=store.enqueue_task(self.c,'brain_simulation',{})
        self.c.execute("UPDATE tasks SET status='blocked',last_error='BRAIN认证/权限未通过' WHERE task_id=?",(tid,))
        self.c.execute("INSERT INTO brain_runs VALUES(?,'polling','/simulations/test',NULL,NULL,?,?)",(tid,util.now_iso(),util.now_iso()))
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        self.assertEqual(autopilot.after_login(self.c),[tid]);self.assertFalse(store.is_paused(self.c))
        self.assertEqual(autopilot.task(self.c,tid)['status'],'queued')
