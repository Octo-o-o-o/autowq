import json
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import feedback, autopilot, util
from wq.brain_submission import REQUIRED


def recordset(names, rows):
    return {'schema':{'properties':[{'name':n} for n in names]},'records':rows}


class FeedbackTests(unittest.TestCase):
    def test_correlation_uses_pnl_changes_and_exact_intervals(self):
        a=recordset(['date','pnl'],[['2020-01-01',0],['2020-01-02',1],['2020-01-03',3],['2020-01-04',6]])
        b=recordset(['date','pnl'],[['2020-01-01',10],['2020-01-02',9],['2020-01-03',7],['2020-01-04',4]])
        self.assertAlmostEqual(feedback.correlation(a,b,3)['value'],-1)
        b['records'].pop(1)
        result=feedback.correlation(a,b,2)
        self.assertIsNone(result['value']);self.assertEqual(result['observations'],1)
        a['records'].append(a['records'][-1])
        with self.assertRaises(ValueError):feedback.daily_pnl(a)

    def test_failed_alpha_can_be_retained_without_becoming_submittable(self):
        a={'id':'test','is':{'sharpe':1.02,'checks':[{'name':n,'result':'FAIL' if n=='LOW_FITNESS' else 'PASS'} for n in REQUIRED]}}
        r=feedback.diagnose(a)
        self.assertTrue(r['retain_for_complementarity']);self.assertFalse(r['submission_candidate'])
        self.assertIn('收益效率不足',r['diagnosis']);self.assertIn('test指标缺失',r['validation_gaps'])
        a['is']['checks'].append({'name':'NEW_PLATFORM_CHECK','result':'FAIL'})
        self.assertIn('NEW_PLATFORM_CHECK:FAIL',feedback.diagnose(a)['platform_blockers'])

    def test_temporal_failures_are_not_hidden_by_overall_pass(self):
        a={'id':'test','is':{'sharpe':1.7,'checks':[{'name':n,'result':'PASS'} for n in REQUIRED]},
           'train':{'sharpe':1.7,'fitness':1.1},'test':{'sharpe':1.6,'fitness':1.02}}
        y=recordset(['year','sharpe','fitness','pnl'],[['2021',1.7,.8,100],['2022',1.4,.9,50],['2023',1.7,.95,90]])
        self.assertTrue(feedback.diagnose(a,y)['submission_candidate'])
        y['records'][1][3]=-10
        self.assertFalse(feedback.diagnose(a,y)['submission_candidate'])
        # 年度fitness <1本身不伪造为官方FAIL。
        self.assertFalse(feedback.diagnose(a,y)['platform_blockers'])

    def test_model_feedback_has_no_platform_values_and_fixed_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp);autopilot.setup(c)
            context=[{'cycle':20,'diagnosis':['收益效率不足'],'retain_for_complementarity':True}]
            prompt=autopilot.generate_prompt(c,context,{'ast':{'op':'add'},'experiment':'一次固定组合'})
            self.assertIn('收益效率不足',prompt);self.assertIn('不可改权重',prompt)
            self.assertNotIn('不提供其平台成绩，避免收益反馈搜索',prompt)
            c.close()

    def test_missing_evidence_blocks_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp)
            with self.assertRaisesRegex(ValueError,'提交前须完成'):
                feedback.require_submission_evidence(c,{'id':'test'})
            c.close()

    def test_readonly_collection_resumes_without_repeating_requests(self):
        from pathlib import Path
        from wq import importer, store
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp)
            a={'id':'real1','regular':{'code':'rank(x)'},'settings':{},'is':{'sharpe':1.5,'checks':[]}}
            path=Path(cfg.private_dir)/'alpha.json';util.write_json(str(path),a)
            importer.import_result_obj(c,cfg,{'schema':'wq.imported-result/v1','source':'api','synthetic':False,
                'simulation':{'remote_id':'real1','expression':'rank(x)','config':{},'stats':{'sharpe':1.5},'checks':{}},
                'evidence':{'path':str(path),'purpose':'research_validation'}},True)
            tid,_=feedback.enqueue(c,cfg,'real1');c.commit()
            payload=json.loads(c.execute('SELECT payload_json FROM tasks WHERE task_id=?',(tid,)).fetchone()[0])
            pnl=recordset(['date','pnl'],[['2020-01-01',0],['2020-01-02',1]])
            yearly=recordset(['year','sharpe','fitness','pnl'],[['2020',1,.9,1]])
            with patch('wq.feedback.get_with_reauth',side_effect=[(200,{},a),(200,{},pnl),(200,{},yearly),(200,{}, {'is':{'checks':[]}})]) as get:
                for _ in range(4):self.assertEqual(feedback.step(c,cfg,{'task_id':tid},payload)[0],'retry_scheduled')
                self.assertEqual(feedback.step(c,cfg,{'task_id':tid},payload)[0],'succeeded')
                self.assertEqual(get.call_count,4)
            self.assertEqual(feedback.enqueue(c,cfg,'real1'),(tid,False))
            self.assertEqual(len(feedback.report(c)['candidates']),1)
            c.close()

    def test_combination_pair_reserved_once_and_counts_toward_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp);autopilot.setup(c);feedback.setup(c)
            c.execute('CREATE TABLE brain_runs(task_id TEXT,alpha_id TEXT)')
            policy={'settings':{'delay':1},'bindings':{'daily_return':{'expression':'returns','fields':['returns']},'activity_rank':{'expression':'rank(volume)','fields':['volume']}}}
            for i,role in enumerate(('daily_return','activity_rank'),1):
                candidate={'ast':{'op':'mean','arg':{'op':'field','name':role},'window':20}}
                c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,candidate_json,simulation_task,created_at,updated_at) VALUES(?,'closed',?,'hash',?,?,?,?)",(i,json.dumps(policy),json.dumps(candidate),'task'+str(i),util.now_iso(),util.now_iso()))
                c.execute('INSERT INTO brain_runs VALUES(?,?)',('task'+str(i),'alpha'+str(i)))
            with patch('wq.feedback.report',return_value={'pairs':[{'parents':['alpha1','alpha2'],'value':-.1,'worth_combination_review':True}]}):
                plan=feedback.next_combination(c);self.assertIsNotNone(plan)
                c.execute('INSERT INTO combination_plans VALUES(?,?,?,?)',(plan['pair_key'],3,json.dumps(plan),util.now_iso()))
                self.assertIsNone(feedback.next_combination(c))
                self.assertIsNone(feedback.next_combination(c,0))
            c.close()


class CombinationRankingTests(unittest.TestCase):
    def test_combination_prefers_strong_parents(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp);autopilot.setup(c);feedback.setup(c);autopilot.brain_jobs.setup(c)
            from wq import store, research_dsl
            bindings={n:{'expression':f'f{i}','fields':[f'f{i}'],'source':'s'} for i,n in enumerate(research_dsl.ROLES)}
            policy=json.dumps({'settings':{'decay':0},'bindings':bindings})
            def add(cid,aid,sharpe,role):
                tid=store.enqueue_task(c,'brain_simulation',{},'k'+aid)[0]
                c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,simulation_task,candidate_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (cid,'closed',policy,'h',tid,json.dumps({'ast':{'op':'mean','arg':{'op':'field','name':role},'window':20}}),util.now_iso(),util.now_iso()))
                c.execute("INSERT INTO brain_runs(task_id,state,alpha_id,started_at,updated_at) VALUES(?,?,?,?,?)",(tid,'complete',aid,util.now_iso(),util.now_iso()))
                c.execute("INSERT INTO families(family_id,family_key,origin,synthetic,created_at) VALUES(?,?,?,?,?)",('fam'+aid,'fk'+aid,'test',0,util.now_iso()))
                c.execute("INSERT INTO candidates(candidate_id,family_id,expression,config_json,config_hash,synthetic,created_at) VALUES(?,?,?,?,?,?,?)",('cand'+aid,'fam'+aid,'x','{}','ch'+aid,0,util.now_iso()))
                c.execute("INSERT INTO simulations(sim_id,candidate_id,remote_id,source,synthetic,status,stats_json,imported_at) VALUES(?,?,?,?,?,?,?,?)",('sim'+aid,'cand'+aid,aid,'api',0,'failed',json.dumps({'sharpe':sharpe}),util.now_iso()))
            roles=list(research_dsl.ROLES)
            for i,(aid,sharpe) in enumerate([('strongA',1.3),('strongB',1.1),('weakA',0.2),('weakB',0.1)]): add(i+1,aid,sharpe,roles[i])
            pairs=[{'parents':['strongA','strongB'],'value':0.25,'worth_combination_review':True},
                   {'parents':['weakA','weakB'],'value':0.01,'worth_combination_review':True}]
            with patch('wq.feedback.report',return_value={'pairs':pairs}):
                plan=feedback.next_combination(c,max_plans=4)
            self.assertEqual(plan['parents'],['strongA','strongB'])
            # 已提交的父信号被排除，退到下一对
            from wq import brain_submission; brain_submission.setup(c)
            tsub=store.enqueue_task(c,'brain_submission',{},'ksub')[0]
            c.execute("INSERT INTO brain_submissions(task_id,alpha_id,sim_id,state,started_at,updated_at) VALUES(?,?,?,?,?,?)",(tsub,'strongA','simstrongA','accepted',util.now_iso(),util.now_iso()))
            with patch('wq.feedback.report',return_value={'pairs':pairs}):
                plan=feedback.next_combination(c,max_plans=4)
            self.assertEqual(plan['parents'],['weakA','weakB'])
            # 祖先含已提交信号的组合轮 alpha 也不能再作父信号
            add(5,'blendA',1.5,roles[4])
            c.execute("INSERT INTO combination_plans VALUES(?,?,?,?)",('strongA:strongB',5,'{}',util.now_iso()))
            pairs.append({'parents':['blendA','weakA'],'value':0.02,'worth_combination_review':True})
            with patch('wq.feedback.report',return_value={'pairs':pairs}):
                plan=feedback.next_combination(c,max_plans=6)
            self.assertEqual(plan['parents'],['weakA','weakB'])
            c.close()
