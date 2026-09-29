import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import make_env
from wq import brain_submission as sub, importer, runner, store, util
from wq.errors import AdapterError


class Client:
    jar = [True]
    replies = []
    calls = []
    preflight_error = None
    def __init__(self, *args): pass
    @classmethod
    def preflight(cls, cfg):
        if cls.preflight_error:
            raise cls.preflight_error
        return 200, {}, {}
    def request(self, method, path, body=None):
        self.calls.append((method, path))
        r = self.replies.pop(0)
        if isinstance(r, Exception): raise r
        return r


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg, self.c = make_env(self.tmp.name, {'brain_submission': {'enabled': True, 'authorized_until': '2099-01-01T00:00:00Z'}})
        self.addCleanup(self.c.close)
        self.alpha = {'id': 'abc123', 'type': 'REGULAR', 'status': 'UNSUBMITTED', 'stage': 'IS',
                      'regular': {'code': 'rank(cashflow_op/assets)'},
                      'settings': {'region': 'USA', 'universe': 'TOP3000', 'delay': 1, 'decay': 0, 'neutralization': 'INDUSTRY', 'truncation': .08},
                      'is': {'sharpe': 1.5, 'checks': [{'name': k, 'result': 'PASS'} for k in sorted(sub.REQUIRED)]}}
        path = Path(self.cfg.private_dir)/'alpha.json';util.write_json(str(path), self.alpha)
        doc = {'schema': 'wq.imported-result/v1', 'synthetic': False, 'source': 'api',
               'simulation': {'remote_id': 'abc123', 'expression': self.alpha['regular']['code'],
                              'config': self.alpha['settings'], 'stats': {'sharpe': 1.5}, 'checks': {'passed': True}},
               'evidence': {'path': str(path), 'purpose': 'research_validation'}}
        importer.import_result_obj(self.c, self.cfg, doc, True)
        self.review = {'schema': 'wq.submission-review/v1', 'alpha_id': 'abc123',
                       'request_hash': sub.identity(self.alpha), 'decision': 'approved_for_submission',
                       'reviewer': 'test fixture reviewer', 'reviewed_at': util.now_iso()}
        for k in ('originality', 'robustness', 'data_timing'):
            self.review[k] = {'accepted': True, 'evidence': 'fixture evidence for offline test only'}
        Client.jar=[True];Client.calls=[];Client.replies=[];Client.preflight_error=None
        p=patch('wq.brain_submission.BrainClient', Client);p.start();self.addCleanup(p.stop)

    def enqueue(self):
        return sub.enqueue(self.c, self.cfg, 'abc123', self.review)[0]

    def tick(self, tid):
        self.c.execute("UPDATE tasks SET not_before='2000' WHERE task_id=?", (tid,))
        runner.run_once(self.c, self.cfg)
        return self.c.execute('SELECT status FROM tasks WHERE task_id=?', (tid,)).fetchone()[0]

    def preflight(self):
        return [(200, {}, self.alpha), (200, {}, {'is': {'checks': self.alpha['is']['checks']}})]

    def submitted(self):
        a=copy.deepcopy(self.alpha);a.update(status='ACTIVE', stage='OS', dateSubmitted=util.now_iso());return a

    def test_review_expired_disabled_or_missing_evidence(self):
        saved=copy.deepcopy(self.review)
        self.review['reviewed_at']='2000-01-01T00:00:00Z'
        with self.assertRaises(ValueError): self.enqueue()
        self.review=copy.deepcopy(saved);self.review['robustness']['accepted']=False
        with self.assertRaises(ValueError): self.enqueue()
        self.review=saved;self.cfg.data['brain_submission']['enabled']=False
        with self.assertRaises(ValueError): self.enqueue()
        self.assertEqual(Client.calls,[])

    def test_generic_reconcile_cannot_claim_acceptance(self):
        from wq import reconcile
        from wq.errors import WqExit
        tid=self.enqueue();store.mark_task_unknown(self.c,tid,{})
        with self.assertRaises(WqExit):reconcile.resolve(self.c,tid,'accepted','unverified claim')

    def test_lost_local_receipt_after_post_is_unknown(self):
        tid=self.enqueue();Client.replies=self.preflight()+[(201,{}, {})]
        real_write=util.write_json
        def write(path,obj):
            if path.endswith('receipt.json'): raise OSError('disk error')
            return real_write(path,obj)
        with patch('wq.brain_submission.util.write_json',side_effect=write):
            self.assertEqual(self.tick(tid),'unknown')
        self.tick(tid);self.assertEqual([m for m,p in Client.calls].count('POST'),1)

    def test_preflight_can_be_renewed_but_post_rejection_cannot(self):
        tid=self.enqueue();store.finish_task(self.c,tid,'blocked',{},'check incomplete')
        self.assertEqual(self.enqueue(),tid)
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],'queued')
        sub.set_state(self.c,tid,'rejected');store.finish_task(self.c,tid,'blocked',{},'POST rejected')
        self.assertEqual(self.enqueue(),tid)
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],'blocked')

    def test_complete_is_not_http_acceptance(self):
        tid=self.enqueue();Client.replies=self.preflight()+[(201, {}, {}), (200, {}, {}), (200, {}, self.submitted())]
        self.assertEqual(self.tick(tid),'queued')
        self.assertEqual(self.c.execute('SELECT count(*) FROM submissions').fetchone()[0],0)
        self.assertEqual(self.tick(tid),'queued')
        self.assertEqual(self.tick(tid),'succeeded')
        self.assertEqual([m for m,p in Client.calls].count('POST'),1)
        self.assertEqual(self.c.execute('SELECT status FROM submissions').fetchone()[0],'accepted')
        self.assertTrue(store.get_flag(self.c, 'brain_identity_refresh_at'))
        self.assertFalse(sub.enqueue(self.c,self.cfg,'abc123',self.review)[1])

    def test_pending_missing_unknown_or_fail_never_post(self):
        for result in ('PENDING','FAIL','WARNING',None):
            with self.subTest(result=result):
                tid=self.enqueue();checks=copy.deepcopy(self.alpha['is']['checks']);checks[0]['result']=result
                Client.replies=[(200,{},self.alpha),(200,{}, {'is':{'checks':checks}})]
                self.assertEqual(self.tick(tid),'queued' if result=='PENDING' else 'blocked')
                self.assertFalse(any(m=='POST' for m,p in Client.calls))
                self.c.execute('DELETE FROM brain_submissions');self.c.execute('DELETE FROM tasks');self.c.commit()
        self.assertTrue(sub.problems([{'name':'LOW_SHARPE','result':'PASS'}]))
        self.assertTrue(sub.problems('PASS'))

    def test_timeout_and_crash_never_repost(self):
        tid=self.enqueue();Client.replies=self.preflight()+[AdapterError(AdapterError.UNKNOWN_REMOTE,'timeout')]
        self.assertEqual(self.tick(tid),'unknown')
        self.tick(tid);self.assertEqual([m for m,p in Client.calls].count('POST'),1)
        self.c.execute("UPDATE tasks SET status='running',lease_until='2000' WHERE task_id=?",(tid,))
        store.recover_stale(self.c)
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],'unknown')
        Client.replies=[(200,{},self.submitted())]
        self.assertIn('submission_id',sub.reconcile(self.c,self.cfg,'abc123'))
        self.assertEqual([m for m,p in Client.calls].count('POST'),1)

    def test_reconcile_unsubmitted_keeps_unknown(self):
        tid=self.enqueue();sub.set_state(self.c,tid,'post_started');store.mark_task_unknown(self.c,tid,{})
        Client.replies=[(200,{},self.alpha)]
        self.assertFalse(sub.reconcile(self.c,self.cfg,'abc123')['accepted'])
        self.assertEqual(self.c.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],'unknown')

    def test_failed_snapshot_and_teaching_rejected(self):
        path=Path(self.cfg.private_dir)/'alpha.json'
        a=copy.deepcopy(self.alpha);a['is']['checks'][0]['result']='FAIL';util.write_json(str(path),a)
        with self.assertRaisesRegex(ValueError,'FAIL'):self.enqueue()
        self.c.execute('UPDATE simulations SET evidence_json=?',(json.dumps({'path':str(path),'purpose':'tutorial_validation'}),))
        with self.assertRaisesRegex(ValueError,'教学'):self.enqueue()

    def test_changed_alpha_expired_review_and_disabled_no_post(self):
        self.review['request_hash']='wrong'
        with self.assertRaises(ValueError):self.enqueue()
        self.review['request_hash']=sub.identity(self.alpha)
        tid=self.enqueue();a=copy.deepcopy(self.alpha);a['settings']['delay']=0
        Client.replies=[(200,{},a)];self.assertEqual(self.tick(tid),'unknown')
        self.assertFalse(any(m=='POST' for m,p in Client.calls))

    def test_auth_pause_no_retry(self):
        tid=self.enqueue();Client.replies=[AdapterError(AdapterError.AUTH,'BRAIN认证/权限未通过')]
        Client.preflight_error=AdapterError(AdapterError.AUTH,'BRAIN认证/权限未通过')
        self.assertEqual(self.tick(tid),'blocked');self.assertTrue(store.is_paused(self.c))

    def test_post_rate_limit_blocked_and_global_cooldown(self):
        tid=self.enqueue();Client.replies=self.preflight()+[AdapterError(AdapterError.RATE_LIMIT,'rate',900)]
        self.assertEqual(self.tick(tid),'blocked')
        self.assertGreater(util.parse_iso(store.get_flag(self.c,'brain_not_before')),util.now())
        self.assertEqual(self.c.execute('SELECT state FROM brain_submissions').fetchone()[0],'rejected')

    def test_server_wait_on_check_never_post(self):
        tid=self.enqueue();Client.replies=[(200,{},self.alpha),(200,{'retry-after':'1800'}, {})]
        self.assertEqual(self.tick(tid),'queued')
        nb=self.c.execute('SELECT not_before FROM tasks WHERE task_id=?',(tid,)).fetchone()[0]
        self.assertGreater((util.parse_iso(nb)-util.now()).total_seconds(),1790)
        self.assertFalse(any(m=='POST' for m,p in Client.calls))

    def test_existing_accepted_alpha_does_not_repost(self):
        tid=self.enqueue();Client.replies=[(200,{},self.submitted())]
        self.assertEqual(self.tick(tid),'succeeded');self.assertEqual(len(Client.calls),1)

    def test_missing_cookie_runs_read_only_preflight_before_gets(self):
        tid=self.enqueue();Client.jar=[]
        checks=copy.deepcopy(self.alpha['is']['checks']);checks[0]['result']='PENDING'
        Client.replies=[(200,{},self.alpha),(200,{}, {'is': {'checks': checks}})]
        self.assertEqual(self.tick(tid),'queued')
        self.assertEqual(Client.calls, [('GET','/alphas/abc123'), ('GET','/alphas/abc123/check')])
        self.assertFalse(any(method == 'POST' for method, _ in Client.calls))

    def test_standby_waits_out_the_cap_then_queues(self):
        from wq import autopilot, brain_jobs, desktop, feedback
        autopilot.setup(self.c); brain_jobs.setup(self.c); feedback.setup(self.c); sub.setup(self.c)
        self.cfg.data['brain_submission']['max_posts_per_24h'] = 1
        store.set_flag(self.c, 'brain_last_submit_at', util.now_iso())
        pnl = Path(self.cfg.private_dir)/'pnl.json'
        util.write_json(str(pnl), {'schema': {'properties': [{'name': 'date'}, {'name': 'pnl'}]},
                                   'records': [['2020-01-01', 1.0], ['2020-01-02', 2.0]]})
        report = {'submission_candidate': True, 'diagnosis': ['平台快照通过'], 'validation_gaps': [], 'pnl_path': str(pnl)}
        self.c.execute('INSERT INTO research_feedback VALUES(?,?,?,?)',
                       ('abc123', sub.identity(self.alpha), json.dumps(report), util.now_iso()))
        sim_task, _ = store.enqueue_task(self.c, 'brain_simulation', {}, 'sim')
        self.c.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?", (sim_task,))
        self.c.execute("INSERT INTO brain_runs(task_id,state,alpha_id,started_at,updated_at) VALUES(?,?,?,?,?)",
                       (sim_task, 'complete', 'abc123', util.now_iso(), util.now_iso()))
        self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,simulation_task,outcome,created_at,updated_at) VALUES('closed','{}','h',?,'平台快照通过',?,?)",
                       (sim_task, util.now_iso(), util.now_iso()))
        cid = self.c.execute('SELECT MAX(cycle_id) FROM research_cycles').fetchone()[0]
        self.assertEqual(sub.offer_submission(self.c, self.cfg, cid, 'abc123', report), 'standby')
        entry = desktop.history(self.c, self.cfg)['entries'][0]
        self.assertEqual(entry['badge'], 'standby')
        self.assertIn('备选提交', entry['title'])
        store.set_flag(self.c, 'brain_last_submit_at', '2000-01-01T00:00:00Z')
        self.assertEqual(sub.release_standby(self.c, self.cfg), 'abc123')
        self.assertEqual(self.c.execute("SELECT state FROM submission_standby").fetchone()[0], 'queued')
        self.assertEqual(desktop.standby(self.c)['count'], 1)
        self.assertEqual(entry['badge'], 'standby')  # 入队后、平台接收前仍是备选
        fresh = desktop.history(self.c, self.cfg)['entries'][0]
        self.assertEqual(fresh['badge'], 'standby')
        tid = self.c.execute('SELECT task_id FROM brain_submissions').fetchone()[0]
        Client.replies = self.preflight()+[(201, {}, {}), (200, {}, {}), (200, {}, self.submitted())]
        self.assertEqual(self.tick(tid), 'queued')
        self.assertEqual(self.tick(tid), 'queued')
        self.assertEqual(self.tick(tid), 'succeeded')
        self.assertEqual(self.c.execute("SELECT state FROM submission_standby").fetchone()[0], 'submitted')
        self.assertEqual(desktop.standby(self.c)['count'], 0)
        self.assertEqual(desktop.history(self.c, self.cfg)['entries'][0]['badge'], 'submitted')
        self.c.execute("UPDATE submission_standby SET state='queued'")
        self.assertEqual(sub.retire_submitted_standby(self.c), ['abc123'])
        self.assertEqual(desktop.standby(self.c)['count'], 0)

    def test_rolling_cap_prevents_post(self):
        tid=self.enqueue();store.set_flag(self.c,'brain_last_submit_at',util.now_iso())
        Client.replies=self.preflight();self.assertEqual(self.tick(tid),'queued')
        self.assertFalse(any(m=='POST' for m,p in Client.calls))

    def test_recovery_polling_resumes_get(self):
        tid=self.enqueue();sub.set_state(self.c,tid,'polling')
        self.c.execute("UPDATE tasks SET status='running',lease_until='2000' WHERE task_id=?",(tid,));self.c.commit()
        store.recover_stale(self.c);Client.replies=[(200,{}, {})]
        self.assertEqual(self.tick(tid),'queued');self.assertEqual(Client.calls,[('GET','/alphas/abc123/submit')])

    def test_ambiguous_platform_status_not_success(self):
        self.assertFalse(sub.accepted({'status':'ACTIVE'}))
        self.assertFalse(sub.accepted({'status':'UNSUBMITTED','stage':'IS','dateSubmitted':util.now_iso()}))

if __name__ == '__main__': unittest.main()
