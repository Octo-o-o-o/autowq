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
from wq.errors import AdapterError


def proposal(op='mean'):
    return {'title':'过去收益的可证伪探索假设','hypothesis':'研究过去收益的结构是否和后续收益有关，窗口在观测前固定。','counterexample':'交易成本或风险暴露可能完全解释表面关联，因此不能宣称盈利。','ast':{'op':'neg','arg':{'op':op,'arg':{'op':'field','name':'daily_return'},'window':20}}}


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg,self.c=make_env(self.temp.name,{'brain_api':{'enabled':True,'min_post_interval_s':0,'authorized_until':'2099-01-01T00:00:00Z'},'routing':{'authorized_until':'2099-01-01T00:00:00Z'},'autopilot':{'enabled':True,'policy_file':'config/policy.json','interval_s':60,'max_cycles_per_day':4,'max_simulations_per_week':3}})
        self.addCleanup(lambda:self.c.close())
        self.root=Path(self.temp.name);proof=self.root/'evidence.json';util.write_json(str(proof),{'fixture':True})
        settings={'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY'}
        self.p={'version':1,'scope':'exploratory_only_no_submission','valid_until':'2099-01-01T00:00:00Z','verified_at':'2026-01-01T00:00:00Z','settings':settings,'source':'fixture://source','bindings':{n:{'expression':'returns','fields':['returns'],'source':'fixture://field'} for n in research_dsl.ROLES},'evidence_files':[{'path':str(proof),'sha256':util.sha256_json({'fixture':True})}]}
        self.save_policy();autopilot.setup(self.c);brain_jobs.setup(self.c)
        self.counter=0;self.posts=0;self.reject_review=False;self.same_provider=False
        self.add_patch('wq.routing.catalog',return_value={'default':'steady','presets':{'steady':{'routes':{'research':['a'],'review':['b']}}}})
        self.add_patch('wq.routing._unavailable',return_value=None)
        self.add_patch('wq.routing.enqueue_job',side_effect=self.job)
        preflight=self.add_patch('wq.brain_client.BrainClient').return_value
        preflight.jar=[True]
        preflight.preflight.return_value=(200,{}, {})
        client=self.add_patch('wq.brain_jobs.BrainClient').return_value;client.jar=[True];client.preflight.return_value=(200,{},{});client.request.side_effect=self.api
        self.real_dispatch=runner.dispatch_task
        self.add_patch('wq.runner.dispatch_task',side_effect=self.dispatch)

    def test_plan_first_routes_one_selected_candidate_through_review_and_one_simulation(self):
        from wq import research_learning as learning
        def dispatch(conn,cfg,t):
            result=self.dispatch(conn,cfg,t)
            payload=json.loads(t['payload_json'])
            if t['kind']=='agent_call' and payload['role']=='research':
                self.assertEqual(payload.get('plan_contract_version'),1)
                plan=lambda c:{'candidate':c,'measurement':'Only observable past daily returns',
                    'prediction':'A falsifiable future ranking association','falsifier':'No association in further measurements'}
                util.write_json(str(Path(payload['job_dir'])/'result.json'),{'status':'completed','plans':[plan(proposal()),plan(proposal('std'))]})
            return result
        with patch('wq.runner.dispatch_task',side_effect=dispatch):self.full_cycle()
        self.assertEqual(self.cycle()['state'],'closed');self.assertEqual(self.posts,1);self.assertEqual(self.counter,2)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM learning_selections').fetchone()[0],1)
        report=learning.report(self.c)
        self.assertEqual(report['actual_requests'],1);self.assertEqual(report['execution_counts']['complete'],1)
        self.assertEqual(report['usable_families'],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)

    def test_frozen_experiment_switches_prompt_only_without_extra_requests(self):
        from wq import research_learning as learning
        learning.freeze_experiment(self.c,'fixture',learning.current_baseline(self.cfg),2)
        self.full_cycle()
        first=json.loads(self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?',(self.cycle()['research_task'],)).fetchone()[0])
        self.assertNotIn('plan_contract_version',first)
        store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        self.full_cycle()
        second=json.loads(self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?',(self.cycle()['research_task'],)).fetchone()[0])
        self.assertEqual(second.get('plan_contract_version'),1)
        arms=learning.report(self.c,experiment_id='fixture')['arms']
        self.assertEqual(arms['baseline']['actual_requests'],1);self.assertEqual(arms['learning']['actual_requests'],1)
        self.assertEqual(self.posts,2)

    def test_total_cap_finishes_active_cycle_and_survives_restart(self):
        self.cfg.data['autopilot']['max_cycles_total']=1
        self.full_cycle()
        self.assertEqual(self.cycle()['state'],'closed')
        self.assertEqual(self.posts,1)
        self.c.close();self.c=connect(self.cfg.db_path)
        store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        self.c.execute("UPDATE research_cycles SET created_at='2000-01-01T00:00:00Z'")
        self.tick()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0],1)
        result=autopilot.status(self.c,self.cfg)
        self.assertIsNone(result['next_cycle_at'])
        self.assertIn('累计研究轮数上限',result['message'])
        self.assertEqual(result['total_cycles'],1)
        self.assertEqual(self.counter,2)

    def test_measurement_rejection_blocks_even_with_other_checks_passed(self):
        checks={k:True for k in autopilot.REVIEW_CHECKS}
        obj={'review':{'candidate_hash':'digest','accept':True,'checks':checks,
                       'reason':'成交股数排名不足以证明换手率机制成立。'}}
        self.assertTrue(autopilot.validate_review(obj,'digest'))
        checks['measurement_valid']=False
        self.assertFalse(autopilot.validate_review(obj,'digest'))
        del checks['measurement_valid']
        with self.assertRaises(ValueError):autopilot.validate_review(obj,'digest')

    def test_quality_first_history_keeps_all_counterexamples(self):
        history=[]
        for op in ('mean','std','delta'):
            item=proposal(op);item['hypothesis']='机制解释'*200
            item['counterexample']='反例清单'*200
            history.append(item)
        context=json.loads(autopilot.history_context(history,proposal('std')))
        self.assertEqual(context['full_candidates'],history)
        older=[{'cycle':i,'candidate':proposal('mean')} for i in range(20)]
        compacted=json.loads(autopilot.history_context(older))['full_candidates']
        self.assertEqual(compacted[14]['candidate'],proposal('mean'))
        self.assertEqual(set(compacted[15]['candidate']),{'title','ast','note'})
        prompt=autopilot.review_prompt(proposal(),history)
        self.assertIn(history[2]['counterexample'],prompt)
        self.assertNotIn('reason建议120',prompt)
        self.assertIn('measurement_valid',prompt)

    def test_quality_text_limit_matches_prompt_and_keeps_hard_bound(self):
        candidate = proposal()
        candidate['hypothesis'] = '机' * research_dsl.TEXT_LIMITS['hypothesis']
        candidate['counterexample'] = '反' * research_dsl.TEXT_LIMITS['counterexample']
        research_dsl.validate_candidate(candidate, self.p['bindings'])
        candidate['hypothesis'] += '超'
        with self.assertRaisesRegex(ValueError, 'hypothesis需为有内容的有限长度文字'):
            research_dsl.validate_candidate(candidate, self.p['bindings'])
        self.assertIn('hypothesis和counterexample各不超过2000字符', autopilot.generate_prompt(self.c))


    def test_prompt_limits_follow_compiler_profile_for_research_and_review(self):
        for plan, profile in ((None, 'proposal'), ({'ast': proposal()['ast']}, 'combination')):
            contract = research_dsl.public_contract(self.p['bindings'], profile)
            research = autopilot.generate_prompt(self.c, combination=plan, bindings=self.p['bindings'])
            review = autopilot.review_prompt(proposal(), combination=plan, bindings=self.p['bindings'])
            for prompt in (research, review):
                self.assertIn(contract['limits'], prompt)
                self.assertNotIn('最多2个时间序列操作和2个组合操作', prompt)
            if plan:
                self.assertIn('允许复用父信号的角色和机制', research)
                self.assertNotIn('最多24节点', research)
                self.assertNotIn('最多24节点', review)

    def test_blocked_research_keeps_reason_and_never_enqueues_review(self):
        self.tick()
        row = self.cycle()
        self.c.execute("UPDATE tasks SET status='blocked',last_error=? WHERE task_id=?",
                       ('固定AST与提示词复杂度限制冲突', row['research_task']))
        self.tick()
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertIn('固定AST与提示词复杂度限制冲突', self.cycle()['outcome'])
        self.assertIsNone(self.cycle()['review_task'])
        self.assertEqual(self.posts, 0)

    def test_invalid_candidate_survives_rollback_for_future_history(self):
        self.tick()
        row=self.cycle()
        task_row=self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?',(row['research_task'],)).fetchone()
        payload=json.loads(task_row['payload_json'])
        candidate=proposal()
        candidate['ast']={'op':'neg','arg':{'op':'field','name':'daily_return'}}
        util.write_json(str(Path(payload['job_dir'])/'result.json'),{'status':'completed','candidate':candidate})
        self.tick()
        closed=self.cycle()
        self.assertEqual(closed['state'],'closed')
        self.assertIsNone(closed['simulation_task'])
        self.assertIsNone(closed['family_hash'])
        self.assertEqual(json.loads(closed['candidate_json']),candidate)
        self.assertIn(candidate['title'],autopilot.generate_prompt(self.c))
        self.assertEqual(self.posts,0)

    def test_research_model_can_abstain_without_platform_call(self):
        self.tick()
        row=self.cycle()
        payload=json.loads(self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?',
                                          (row['research_task'],)).fetchone()[0])
        util.write_json(str(Path(payload['job_dir'])/'result.json'),
                        {'status':'blocked','summary':'没有新的可核验机制，主动停止'})
        self.tick()
        closed=self.cycle()
        self.assertEqual(closed['state'],'closed')
        self.assertIn('主动放弃',closed['outcome'])
        self.assertIsNone(closed['simulation_task'])
        self.assertEqual(self.posts,0)

    def test_status_daily_cap_does_not_show_past_next_cycle(self):
        for _ in range(4):
            self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,created_at,updated_at) VALUES('closed','{}','x',?,?)",(util.now_iso(),util.now_iso()))
        store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        result=autopilot.status(self.c,self.cfg)
        self.assertGreater(util.parse_iso(result['next_cycle_at']),util.now())
        self.assertIn('日研究轮数上限',result['message'])

    def add_patch(self,*a,**k):
        p=patch(*a,**k);obj=p.start();self.addCleanup(p.stop);return obj
    def save_policy(self):util.write_json(str(self.root/'config/policy.json'),self.p)
    def job(self,conn,cfg,role,prompt_file,input_dir=None,title=None):
        self.counter+=1;path=self.root/f'job-{self.counter}';path.mkdir()
        (path/'prompt.txt').write_text(Path(prompt_file).read_text())
        (path/'packet').mkdir()
        (path/'packet'/'request.md').write_text(Path(prompt_file).read_text())
        payload={'job_dir':str(path),'role':role,'routing':True}
        return store.enqueue_task(conn,'agent_call',payload)[0],str(path)
    def dispatch(self,conn,cfg,t):
        if t['kind']!='agent_call':return self.real_dispatch(conn,cfg,t)
        payload=json.loads(t['payload_json']);role=payload['role']
        row=conn.execute("SELECT * FROM research_cycles WHERE state!='closed'").fetchone()
        if role=='research':obj={'status':'completed','candidate':proposal('mean' if row['cycle_id']==1 else 'std')}
        else:
            checks = {k: True for k in autopilot.REVIEW_CHECKS}
            evidence = []
            if self.reject_review:
                checks['economic_rationale'] = False
                evidence = [{'check':'economic_rationale', 'field':'hypothesis',
                             'quote':json.loads(row['candidate_json'])['hypothesis'],
                             'explanation':'测试夹具中的否决理由，不代表真实模型质量判断。'}]
            obj={'status':'completed','review':{'candidate_hash':row['candidate_hash'],'accept':not self.reject_review,
                 'checks':checks,'reason':'这是明确受限的探索，不能当作经济机制已获证明。','blocking_evidence':evidence}}
            if payload.get('fallback_objections'):
                obj['review']['resolutions'] = [{'check':key,'disposition':'uphold' if self.reject_review else 'overturn',
                    'field':'hypothesis','quote':json.loads(row['candidate_json'])['hypothesis'],
                    'explanation':'测试夹具明确回应首次裁决，不代表真实模型复核。'} for key in payload['fallback_objections']]
        util.write_json(str(Path(payload['job_dir'])/'result.json'),obj)
        name='a' if role=='research' or self.same_provider else 'b'
        conn.execute('INSERT OR IGNORE INTO task_routes(task_id,snapshot_json,phase,updated_at) VALUES(?,?,?,?)',(t['task_id'],json.dumps({'chain':[name],'preset':'steady'}),'complete',util.now_iso()))
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

    def test_country_campaign_preserves_reviewer_and_posts_only_reserved_candidate(self):
        from wq import research_campaign as campaign, catalog
        query=catalog.query_from_settings(self.p['settings'])
        snap={'schema':catalog.SNAPSHOT_SCHEMA,'query':query,'context':[query],
              'field':{'id':'returns','type':'MATRIX'},'queried_at':util.now_iso()}
        proof=self.p['evidence_files'][0];util.write_json(proof['path'],snap);proof['sha256']=util.sha256_json(snap)
        c=campaign.template(self.p);c['enabled']=True
        h=c['hypotheses'][0];h.update(state='ready',reason='',required_roles=['daily_return'],
            data_contract={'measurement':'Historical scalar proxy only','availability':'Available before configured delay',
            'missing':'No missing values replaced by zero','source':'fixture source document','evidence':dict(proof),
            'verified_at':'2026-01-01T00:00:00Z','valid_until':'2099-01-01T00:00:00Z'})
        self.p['campaign']=c;self.save_policy()
        self.full_cycle()
        self.assertEqual(self.cycle()['state'],'closed');self.assertEqual(self.counter,2);self.assertEqual(self.posts,1)
        self.assertEqual(campaign.assignment(self.c,1)['hypothesis'],'H-N1')
        self.assertEqual(campaign.report(self.c,self.p)['reserved'],1)
        self.assertIn('有界增量研究合同',(self.root/'job-1'/'prompt.txt').read_text())
        self.assertIn('有界增量研究合同',(self.root/'job-2'/'prompt.txt').read_text())

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
        for _ in range(4):self.tick()
        self.assertEqual(self.cycle()['state'],'closed');self.assertEqual(self.posts,0)

    def test_final_review_rejection_feeds_quotes_into_next_prompt(self):
        # 最终否决的否决项与候选原句进入事件表，下一轮生成提示以硬约束回流，避免同类映射换措辞重提。
        self.reject_review=True
        for _ in range(4):self.tick()
        row=self.cycle()
        self.assertIn('模型审查拒绝',row['outcome'])
        ctx=autopilot.rejection_context(self.c)
        self.assertEqual(ctx[0]['轮次'],row['cycle_id'])
        self.assertIn('economic_rationale',ctx[0]['否决项'])
        self.assertTrue(ctx[0]['被否决原句'])
        prompt=autopilot.generate_prompt(self.c)
        self.assertIn('最近审查否决记录',prompt)
        self.assertIn(json.loads(row['candidate_json'])['hypothesis'],prompt)
        self.assertIsInstance(autopilot.occupied_neighborhoods(self.c), list)

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

    def test_expired_brain_session_is_rejected_before_model_call(self):
        client = self.add_patch('wq.brain_client.BrainClient')
        client.return_value.jar = [True]
        client.return_value.preflight.side_effect = AdapterError(
            AdapterError.AUTH, 'BRAIN认证/权限未通过')
        self.tick()
        self.assertEqual(self.counter, 0)
        self.assertTrue(store.is_paused(self.c))
        self.assertIn('auth:', store.get_flag(self.c, 'pause_reason'))
        self.assertEqual(store.get_flag(self.c, 'brain_preflight_status'), 'auth')

    def test_brain_preflight_is_cached_without_repeating_options(self):
        client = self.add_patch('wq.brain_client.BrainClient')
        client.return_value.jar = [True]
        client.return_value.preflight.return_value = (200, {}, {})
        self.assertEqual(autopilot.brain_preflight(self.c, self.cfg), (True, ''))
        self.assertEqual(autopilot.brain_preflight(self.c, self.cfg), (True, ''))
        self.assertEqual(client.return_value.preflight.call_count, 1)

    def test_disabled_and_paused_no_new_calls(self):
        store.set_flag(self.c,'autopilot_enabled','0');self.tick();self.assertEqual(self.counter,0)
        store.set_flag(self.c,'autopilot_enabled','1');store.set_flag(self.c,'paused','1');self.tick();self.assertEqual(self.counter,0)
        result=autopilot.status(self.c,self.cfg)
        self.assertIsNone(result['next_cycle_at'])

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
        for _ in range(4):self.tick()
        store.set_flag(self.c,'autopilot_next_at','2000-01-01T00:00:00Z')
        self.tick();self.assertEqual(self.counter,3)
        with patch('wq.util.now',return_value=util.now()+dt.timedelta(days=1)):self.tick()
        self.assertEqual(self.counter,4)

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

    def test_manual_pause_is_never_auto_cleared_even_with_auth_wording(self):
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_origin','manual')
        store.set_flag(self.c,'pause_reason','auth: pause requested by owner')
        self.assertIsNone(autopilot.auto_resume_after_auth(self.c,self.cfg))
        self.assertTrue(store.is_paused(self.c))

    def test_login_only_requeues_authenticated_get_not_post(self):
        tid,_=store.enqueue_task(self.c,'brain_simulation',{})
        self.c.execute("UPDATE tasks SET status='blocked',last_error='BRAIN认证/权限未通过' WHERE task_id=?",(tid,))
        self.c.execute("INSERT INTO brain_runs VALUES(?,'polling','/simulations/test',NULL,NULL,?,?)",(tid,util.now_iso(),util.now_iso()))
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        self.assertEqual(autopilot.after_login(self.c),[tid]);self.assertFalse(store.is_paused(self.c))
        self.assertEqual(autopilot.task(self.c,tid)['status'],'queued')

    def test_login_requeues_preflight_block_without_post(self):
        tid,_=store.enqueue_task(self.c,'brain_simulation',{})
        self.c.execute("UPDATE tasks SET status='blocked',last_error='BRAIN认证/权限未通过；未发送POST' WHERE task_id=?",(tid,))
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        self.assertEqual(autopilot.after_login(self.c),[tid]);self.assertFalse(store.is_paused(self.c))
        self.assertEqual(autopilot.task(self.c,tid)['status'],'queued')

    def test_keychain_auth_pause_auto_recovers_only_when_preflight_succeeds(self):
        self.cfg.data['brain_api']['auto_login'] = True
        self.cfg.data['brain_api']['auto_login_email'] = 'user@example.com'
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        client = self.add_patch('wq.brain_client.BrainClient')
        client.return_value.preflight.return_value = (200, {}, {})
        recovered = autopilot.auto_resume_after_auth(self.c, self.cfg)
        self.assertEqual(recovered, [])
        self.assertFalse(store.is_paused(self.c))
        client.return_value.preflight.assert_called_once()

    def test_keychain_failure_keeps_auth_pause(self):
        self.cfg.data['brain_api']['auto_login'] = True
        self.cfg.data['brain_api']['auto_login_email'] = 'user@example.com'
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        client = self.add_patch('wq.brain_client.BrainClient')
        client.return_value.preflight.side_effect = AdapterError(AdapterError.AUTH, '仍未通过')
        self.assertIsNone(autopilot.auto_resume_after_auth(self.c, self.cfg))
        self.assertTrue(store.is_paused(self.c))
        self.assertTrue(store.get_flag(self.c,'pause_reason').startswith('auth:'))

    def test_unknown_prevents_auth_recovery_and_network_calls(self):
        self.cfg.data['brain_api'].update(auto_login=True, auto_login_email='user@example.com')
        tid, _ = store.enqueue_task(self.c, 'brain_simulation', {})
        self.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?", (tid,))
        store.set_flag(self.c, 'paused', '1')
        store.set_flag(self.c, 'pause_reason', 'auth: expired')
        with patch('wq.brain_client.BrainClient') as client:
            self.assertIsNone(autopilot.auto_resume_after_auth(self.c, self.cfg))
            client.assert_not_called()
        self.assertTrue(store.is_paused(self.c))

    def test_keychain_failure_backs_off_for_five_minutes(self):
        self.cfg.data['brain_api']['auto_login'] = True
        self.cfg.data['brain_api']['auto_login_email'] = 'user@example.com'
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        client = self.add_patch('wq.brain_client.BrainClient')
        client.return_value.preflight.side_effect = AdapterError(AdapterError.AUTH, '仍未通过')
        self.assertIsNone(autopilot.auto_resume_after_auth(self.c,self.cfg))
        self.assertIsNotNone(store.get_flag(self.c,'brain_auto_auth_not_before'))
        self.assertIsNone(autopilot.auto_resume_after_auth(self.c,self.cfg))
        self.assertEqual(client.return_value.preflight.call_count,1)

    def test_login_invalidates_cached_brain_preflight(self):
        store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','auth: expired')
        store.set_flag(self.c,'brain_preflight_at',util.now_iso())
        store.set_flag(self.c,'brain_preflight_status','auth')
        store.set_flag(self.c,'brain_preflight_message','cached auth failure')
        autopilot.after_login(self.c)
        self.assertEqual(store.get_flag(self.c,'brain_preflight_status'),'')
        self.assertEqual(store.get_flag(self.c,'brain_preflight_at'),'')
