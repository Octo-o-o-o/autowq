import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wq import catalog, util, submission_ranking as ranking, brain_submission as submission, store, autopilot, feedback
from helpers import make_env


class VectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.start=(util.now()-dt.timedelta(minutes=1)).isoformat()
        self.settings={'region':'USA','delay':1,'universe':'TOP3000'}
        query=catalog.query_from_settings(self.settings)
        self.field={'schema':catalog.SNAPSHOT_SCHEMA,'field':{'id':'event_values','type':'VECTOR'},'query':query,'context':[query],'queried_at':util.now_iso()}
        self.operator={'schema':'wq.operator-snapshot/v1','source':'https://api.worldquantbrain.com/operators','queried_at':util.now_iso(),
                       'operators':[{'name':'vec_avg','scope':['REGULAR']}]}
        self.fproof=self.proof('field',self.field);op=self.proof('operators',self.operator)
        self.reduction={'reducer':'vec_avg','meaning':'Arithmetic mean of available event values','availability':'Observed in the configured delayed information set',
            'missing':'Empty vectors preserve platform missing values','verified_at':self.start,'valid_until':(util.now()+dt.timedelta(days=1)).isoformat(),'operator_evidence':op}
        self.binding={'expression':'vec_avg(event_values)','fields':['event_values'],'source':'fixture','vector_reduction':self.reduction,'output_type':'MATRIX'}
        self.p={'settings':self.settings,'evidence_files':[self.fproof,op]}

    def proof(self,name,doc):
        path=str(Path(self.tmp.name)/(name+'.json'));util.write_json(path,doc)
        return {'path':path,'sha256':util.sha256_json(doc)}

    def test_only_one_leaf_reduction_with_current_operator_and_scope(self):
        catalog.validate_binding_scope(self.p,self.binding)
        for expression in ('event_values','rank(vec_avg(event_values))','vec_avg(event_values*event_values)','vec_sum(event_values)','vec_avg(event_values)+1'):
            b={**self.binding,'expression':expression}
            with self.assertRaises(ValueError):catalog.validate_binding_scope(self.p,b)
        bare={**self.binding,'expression':'event_values'};bare.pop('vector_reduction')
        with self.assertRaisesRegex(ValueError,'naked VECTOR'):catalog.validate_binding_scope(self.p,bare)
        for changes in ({'delay':0},{'universe':'TOP1000'}):
            with self.assertRaisesRegex(ValueError,'scope'):catalog.validate_binding_scope({**self.p,'settings':{**self.settings,**changes}},self.binding)
        self.reduction['valid_until']=self.start
        with self.assertRaisesRegex(ValueError,'current'):catalog.validate_binding_scope(self.p,self.binding)

    def test_reducer_counts_against_compiler_depth_without_changing_matrix_output(self):
        from wq import research_dsl
        ast={'op':'field','name':'events'}
        for _ in range(6): ast={'op':'rank','arg':ast}
        ast['op']='zscore'
        matrix={**self.binding,'expression':'event_values'};matrix.pop('vector_reduction')
        self.assertTrue(research_dsl.compile_ast(ast,{'events':matrix})[0].startswith('zscore('))
        with self.assertRaisesRegex(ValueError,'AST node/depth'):research_dsl.compile_ast(ast,{'events':self.binding})


    def test_operator_tamper_or_scope_missing_is_not_accepted(self):
        self.operator['operators'][0]['scope']=[]
        util.write_json(self.reduction['operator_evidence']['path'],self.operator)
        with self.assertRaisesRegex(ValueError,'changed'):catalog.validate_binding_scope(self.p,self.binding)
        proof=self.proof('operators',self.operator);self.p['evidence_files'][1]=proof;self.reduction['operator_evidence']=proof
        with self.assertRaisesRegex(ValueError,'unavailable'):catalog.validate_binding_scope(self.p,self.binding)

    def test_registration_atomically_rejects_invalid_semantics(self):
        path=str(Path(self.tmp.name)/'policy.json');util.write_json(path,{**self.p,'bindings':{}})
        before=Path(path).read_bytes();bad={**self.reduction,'missing':''}
        with self.assertRaises(ValueError):catalog.add_role(path,'events','vec_avg(event_values)',['event_values'],'过去事件平均值，不代表未来收益',[self.fproof['path']],vector_reduction=bad)
        self.assertEqual(before,Path(path).read_bytes())
        catalog.add_role(path,'events','vec_avg(event_values)',['event_values'],'过去事件平均值，不代表未来收益',[self.fproof['path']],vector_reduction=self.reduction)
        self.assertEqual(util.read_json(path)['bindings']['events']['output_type'],'MATRIX')


class RankingTests(unittest.TestCase):
    def test_unknown_separator_prevents_comparator_cycle_and_domains_do_not_cross(self):
        rows=[{'alpha_id':x} for x in ('A','U','B','C','D','E')]
        data={'A':('s',(1,1)), 'U':None, 'B':('s',(3,3)), 'C':('t',(1,1)), 'D':('t',(2,2)), 'E':('s',(5,5))}
        with patch('wq.submission_ranking.evidence',side_effect=lambda aid,report:data[aid]):
            for _ in range(3):self.assertEqual([x['alpha_id'] for x in ranking.order(rows,lambda aid:(aid,{}))],['A','U','B','D','C','E'])
        self.assertEqual([x['alpha_id'] for x in ranking.pareto_segment([(rows[0],'s',(1,2)),(rows[2],'s',(2,1))])],['A','B'])

    def test_real_evidence_domain_requires_same_pool_snapshot_dates_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            pnl={'schema':{'properties':[{'name':'date'},{'name':'pnl'}]},'records':[[(dt.date(2020,1,1)+dt.timedelta(days=i)).isoformat(),i*i] for i in range(254)]}
            path=str(Path(tmp)/'pnl.json');util.write_json(path,pnl)
            alpha={'settings':{'region':'USA','universe':'TOP3000','delay':1,'neutralization':'INDUSTRY','decay':0,'truncation':0.08},'is':{'fitness':1.5}}
            report={'collection_status':'complete','submission_candidate':True,'segment_rules':{'min_sharpe':1.25},'pnl_path':path,'temporal':[{'segment':'test','sharpe':1.6,'fitness':1.1}],
                'submitted_correlation':{'max':0.2,'missing':[],'pool_contract':{'members_hash':'x','official':False},'against':[{'alpha_id':'P','contract':{'algorithm':'delta','minimum':252,'right_content_hash':'v1','aligned_intervals_hash':'dates'}}]}}
            original=ranking.evidence(alpha,report);self.assertIsNotNone(original)
            r=copy.deepcopy(report);r['submitted_correlation']['against'][0]['contract']['right_content_hash']='v2'
            self.assertNotEqual(original[0],ranking.evidence(alpha,r)[0])
            r['submitted_correlation']['missing']=['P2'];self.assertIsNone(ranking.evidence(alpha,r))
            r=copy.deepcopy(report);r['temporal'][0]['fitness']=None;self.assertIsNone(ranking.evidence(alpha,r))
            alpha['settings']['delay']=0;self.assertNotEqual(original[0],ranking.evidence(alpha,report)[0])

    def test_offer_collects_all_then_atomic_release_preserves_claimed_and_first_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,conn=make_env(tmp,{'brain_submission':{'enabled':True,'authorized_until':'2099-01-01T00:00:00Z','standby_order':'evidence','max_posts_per_24h':2}})
            self.addCleanup(conn.close);autopilot.setup(conn);feedback.setup(conn)
            with patch('wq.brain_submission.enqueue') as enqueue:
                for aid in ('A','B'):
                    self.assertEqual(submission.offer_submission(conn,cfg,1,aid,{'submission_candidate':True}),'standby')
                first=conn.execute("SELECT created_at FROM submission_standby WHERE alpha_id='A'").fetchone()[0]
                submission.offer_submission(conn,cfg,1,'A',{'submission_candidate':True})
                self.assertEqual(first,conn.execute("SELECT created_at FROM submission_standby WHERE alpha_id='A'").fetchone()[0]);enqueue.assert_not_called()
            task,_=store.enqueue_task(conn,'brain_submission',{},'claimed');conn.execute("UPDATE tasks SET status='claimed' WHERE task_id=?",(task,))
            self.assertIsNone(submission.release_standby(conn,cfg))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM submission_standby WHERE state='waiting'").fetchone()[0],2)

    def test_release_rolls_back_partial_enqueue_and_repeated_release_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,conn=make_env(tmp,{'brain_submission':{'enabled':True,'authorized_until':'2099-01-01T00:00:00Z','standby_order':'evidence','max_posts_per_24h':2}})
            self.addCleanup(conn.close);autopilot.setup(conn);feedback.setup(conn)
            submission.offer_submission(conn,cfg,1,'A',{'submission_candidate':True})
            conn.execute('INSERT INTO research_feedback VALUES(?,?,?,?)',('A','fixture',json.dumps({'submission_candidate':True}),util.now_iso()));conn.commit()
            def enqueue(*args):return store.enqueue_task(conn,'brain_submission',{},'submit-A')
            def fail(*args):enqueue();raise ValueError('fixture interruption after enqueue')
            with patch('wq.brain_submission.source',return_value=({}, {'id':'A'})),patch('wq.feedback.require_submission_evidence'):
                with patch('wq.brain_submission.enqueue',side_effect=fail):
                    with self.assertRaises(ValueError):submission.release_standby(conn,cfg)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT state FROM submission_standby').fetchone()[0],'waiting')
                with patch('wq.brain_submission.enqueue',side_effect=enqueue):
                    self.assertEqual(submission.release_standby(conn,cfg),'A')
                    self.assertIsNone(submission.release_standby(conn,cfg))
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)


class OperatorSnapshotTests(unittest.TestCase):
    def test_official_scope_is_preserved_and_missing_scope_is_not_invented(self):
        from wq import catalog
        class Client:
            def __init__(self,data):self.data=data
            def request(self,method,path):
                assert (method,path)==('GET','/operators')
                return 200,{},self.data
        doc=catalog.operator_snapshot(Client([{'name':'vec_avg','scope':['REGULAR']}]))
        self.assertEqual(doc['operators'][0]['scope'],['REGULAR'])
        with self.assertRaises(ValueError):catalog.operator_snapshot(Client([{'name':'vec_avg'}]))
