import copy
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import brain_jobs,runner,util
from wq.brain_client import safe_url,retry_delay
from wq.errors import AdapterError

class FakeClient:
    jar=[True]
    replies=[]
    calls=[]
    def __init__(self,*a):pass
    @classmethod
    def preflight(cls,cfg):
        if not cls.jar:
            raise AdapterError(AdapterError.AUTH,'BRAIN认证/权限未通过')
        return 200,{},{}
    def request(self,method,path,body=None):
        self.calls.append((method,path))
        response=self.replies.pop(0)
        if isinstance(response,Exception):raise response
        return response

class BrainJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg,self.c=make_env(self.temp.name,{'brain_api':{'enabled':True,'min_post_interval_s':0,'authorized_until':'2099-01-01T00:00:00Z'}})
        self.addCleanup(self.c.close)
        self.doc={'request':{'type':'REGULAR','regular':'volume','settings':{'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY'}},'config':{'region':'USA','universe':'TOP3000','delay':1,'decay':0,'truncation':0.08,'neutralization':'INDUSTRY','catalog_verified':True,'fields':['volume']},'purpose':'tutorial_validation','evidence':{'settings_verified':True,'source':'fixture://official-example'}}
        FakeClient.calls=[];FakeClient.replies=[];FakeClient.jar=[True]
        p=patch('wq.brain_jobs.BrainClient',FakeClient);p.start();self.addCleanup(p.stop)
    def tick(self,tid):
        self.c.execute("UPDATE tasks SET not_before='2000' WHERE task_id=?",(tid,))
        runner.run_once(self.c,self.cfg)
        return self.c.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0]
    def enqueue(self):return brain_jobs.enqueue(self.c,self.cfg,self.doc)[0]
    def test_long_retry_after_on_alpha_ready_and_missing_stats(self):
        tid=self.enqueue()
        alpha={'id':'abc','regular':{'code':'volume'},'settings':self.doc['request']['settings'],'is':{}}
        FakeClient.replies=[(201,{'location':'/simulations/s1'},{}),(200,{'retry-after':'1800'},{'alpha':'abc'}),(200,{'retry-after':'2400'},alpha)]
        self.tick(tid);self.tick(tid)
        n=self.c.execute('SELECT not_before FROM tasks WHERE task_id=?',(tid,)).fetchone()[0]
        self.assertGreater((util.parse_iso(n)-util.now()).total_seconds(),1790)
        self.tick(tid)
        n=self.c.execute('SELECT not_before FROM tasks WHERE task_id=?',(tid,)).fetchone()[0]
        self.assertGreater((util.parse_iso(n)-util.now()).total_seconds(),2390)

    def test_public_dispatch_spacing_and_daily_cap(self):
        tid=self.enqueue();self.cfg.data['brain_api']['min_post_interval_s']=3600
        self.c.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',(tid,'rejected',None,None,None,util.now_iso(),util.now_iso()))
        self.c.execute("UPDATE tasks SET status='blocked' WHERE task_id=?",(tid,))
        self.doc['request']['regular']='-volume';other=self.enqueue()
        self.assertEqual(self.tick(other),'queued');self.assertEqual(FakeClient.calls,[])
        self.cfg.data['brain_api']['max_posts_per_24h']=1
        self.cfg.data['brain_api']['min_post_interval_s']=0
        self.assertEqual(self.tick(other),'queued');self.assertEqual(FakeClient.calls,[])
        n=self.c.execute('SELECT not_before FROM tasks WHERE task_id=?',(other,)).fetchone()[0]
        self.assertGreater((util.parse_iso(n)-util.now()).total_seconds(),86300)

    def test_slow_simulation_reports_progress_without_reposting(self):
        tid=self.enqueue()
        FakeClient.replies=[(201,{'location':'/simulations/s1'},{}),(200,{'retry-after':'5'},{'progress':0.1})]
        self.tick(tid)
        self.c.execute('UPDATE brain_runs SET started_at=? WHERE task_id=?',
                       ((util.now()-dt.timedelta(hours=2)).isoformat(),tid))
        self.assertEqual(self.tick(tid),'queued')
        row=self.c.execute('SELECT not_before,last_error FROM tasks WHERE task_id=?',(tid,)).fetchone()
        self.assertIn('10%',row['last_error'])
        self.assertGreater((util.parse_iso(row['not_before'])-util.now()).total_seconds(),295)
        self.assertEqual([c[0] for c in FakeClient.calls],['POST','GET'])

    def test_day_old_simulation_retains_receipt_and_blocks_new_post(self):
        tid=self.enqueue()
        FakeClient.replies=[(201,{'location':'/simulations/s1'},{}),(200,{}, {'progress':0.1})]
        self.tick(tid)
        self.c.execute('UPDATE brain_runs SET started_at=? WHERE task_id=?',
                       ((util.now()-dt.timedelta(hours=25)).isoformat(),tid))
        self.assertEqual(self.tick(tid),'blocked')
        self.assertEqual(self.c.execute('SELECT state FROM brain_runs WHERE task_id=?',(tid,)).fetchone()[0],'polling')
        self.doc['request']['regular']='-volume'
        other=self.enqueue()
        self.assertEqual(self.tick(other),'queued')
        self.assertEqual([c[0] for c in FakeClient.calls],['POST','GET'])

    def test_post_poll_import_and_dedup(self):
        tid=self.enqueue()
        alpha={'id':'abc123','regular':{'code':'volume'},'settings':self.doc['request']['settings'],'is':{'sharpe':0.2,'checks':[{'name':'LOW_FITNESS','result':'FAIL'}]}}
        FakeClient.replies=[(201,{'location':'/simulations/s1'},{}),(200,{}, {'alpha':'abc123'}),(200,{},alpha)]
        self.assertEqual(self.tick(tid),'queued');self.assertEqual(self.tick(tid),'queued');self.assertEqual(self.tick(tid),'succeeded')
        self.assertEqual([x[0] for x in FakeClient.calls],['POST','GET','GET'])
        row=self.c.execute('SELECT synthetic,status FROM simulations').fetchone()
        self.assertEqual(tuple(row),(0,'failed'))
        self.assertFalse(brain_jobs.enqueue(self.c,self.cfg,self.doc)[1])
    def test_post_timeout_unknown_no_replay(self):
        tid=self.enqueue();FakeClient.replies=[AdapterError(AdapterError.UNKNOWN_REMOTE,'timeout')]
        self.assertEqual(self.tick(tid),'unknown');self.tick(tid)
        self.assertEqual(len(FakeClient.calls),1)
    def test_crash_after_post_intent_no_second_post(self):
        tid=self.enqueue();brain_jobs.setup(self.c)
        self.c.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',(tid,'post_started',None,None,None,util.now_iso(),util.now_iso()))
        self.assertEqual(self.tick(tid),'unknown');self.assertEqual(FakeClient.calls,[])
    def test_get_rate_limit_keeps_receipt(self):
        tid=self.enqueue();FakeClient.replies=[(201,{'location':'/simulations/s1'},{}),AdapterError(AdapterError.RATE_LIMIT,'rate',120)]
        self.tick(tid);before=util.now();self.assertEqual(self.tick(tid),'queued')
        row=self.c.execute('SELECT not_before FROM tasks WHERE task_id=?',(tid,)).fetchone()
        self.assertGreaterEqual((util.parse_iso(row[0])-before).total_seconds(),120)
        self.assertEqual([c[0] for c in FakeClient.calls],['POST','GET'])
    def test_mismatched_alpha_never_imported(self):
        tid=self.enqueue();FakeClient.replies=[(201,{'location':'/simulations/s1'},{}),(200,{}, {'alpha':'abc'}),(200,{}, {'id':'wrong'})]
        self.tick(tid);self.tick(tid);self.assertEqual(self.tick(tid),'unknown')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM simulations').fetchone()[0],0)
    def test_missing_session_no_post(self):
        tid=self.enqueue();FakeClient.jar=[];self.assertEqual(self.tick(tid),'blocked');self.assertEqual(FakeClient.calls,[])
    def test_expired_window_no_post(self):
        self.cfg.data['brain_api']['authorized_until']='2000-01-01T00:00:00Z'
        tid=self.enqueue();self.assertEqual(self.tick(tid),'blocked');self.assertEqual(FakeClient.calls,[])
    def test_research_requires_acceptance_and_settings_match(self):
        self.doc['purpose']='research_validation'
        with self.assertRaises(ValueError):self.enqueue()
        self.doc['purpose']='tutorial_validation';self.doc['config']['delay']=0
        with self.assertRaises(ValueError):self.enqueue()
    def test_post_auth_pauses_before_next_research(self):
        from wq import store
        tid=self.enqueue();FakeClient.replies=[AdapterError(AdapterError.AUTH,'BRAIN认证/权限未通过')]
        self.assertEqual(self.tick(tid),'blocked')
        self.assertTrue(store.is_paused(self.c))
        self.assertEqual(len(FakeClient.calls),1)

    def test_post_rate_limit_applies_to_next_request(self):
        from wq import store
        tid=self.enqueue();FakeClient.replies=[AdapterError(AdapterError.RATE_LIMIT,'BRAIN限流',7200)]
        before=util.now();self.assertEqual(self.tick(tid),'blocked')
        self.assertGreaterEqual((util.parse_iso(store.get_flag(self.c,'brain_not_before'))-before).total_seconds(),7200)
        self.doc['request']['regular']='-volume';next_id=self.enqueue()
        self.assertEqual(self.tick(next_id),'queued')
        self.assertEqual(len(FakeClient.calls),1)

    def test_url_origin_and_server_delay(self):
        for u in ['https://evil.example/simulations/x','http://api.worldquantbrain.com/x','https://api.worldquantbrain.com@evil.example/x']:
            with self.assertRaises(ValueError):safe_url(u)
        self.assertEqual(retry_delay('120'),120)
