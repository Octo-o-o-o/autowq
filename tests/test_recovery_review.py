import unittest
import test_brain_jobs as brain_tests
from test_brain_jobs import FakeClient
import test_routing as routing_tests
from wq import brain_jobs, reconcile, store, runner, autopilot, contracts, evidence_gate, util
from wq.errors import WqExit
from wq.brain_client import retry_delay


class RecoveryReviewTests(unittest.TestCase):
    setUp = brain_tests.BrainJobTests.setUp
    tick = brain_tests.BrainJobTests.tick
    enqueue = brain_tests.BrainJobTests.enqueue

    def unknown(self):
        tid = self.enqueue()
        self.c.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',
                       (tid,'post_started',None,None,None,util.now_iso(),util.now_iso()))
        store.mark_task_unknown(self.c, tid, {})
        return tid

    def test_lost_releases_remote_slot_without_replay(self):
        tid=self.unknown()
        self.assertEqual(reconcile.resolve(self.c,tid,'lost','Checked official history'),'failed')
        self.doc['request']['regular']='-volume'
        next_id=self.enqueue()
        FakeClient.replies=[(201,{'location':'/simulations/new'}, {})]
        self.assertEqual(self.tick(next_id),'queued')
        self.assertEqual(len(FakeClient.calls),1)

    def test_accepted_needs_receipt_then_get_verifies_and_imports(self):
        tid=self.unknown()
        with self.assertRaises(WqExit):reconcile.resolve(self.c,tid,'accepted','Checked')
        self.assertEqual(reconcile.resolve(self.c,tid,'accepted','Verified in UI','abc123'),'queued')
        alpha={'id':'abc123','regular':{'code':'volume'},'settings':self.doc['request']['settings'],
               'is':{'sharpe':0.2,'checks':[]}}
        FakeClient.replies=[(200,{},alpha)]
        self.assertEqual(self.tick(tid),'succeeded')
        self.assertEqual([c[0] for c in FakeClient.calls],['GET'])

    def test_wrong_reconciled_alpha_is_still_unknown(self):
        tid=self.unknown()
        reconcile.resolve(self.c,tid,'accepted','Verified in UI','abc123')
        FakeClient.replies=[(200,{}, {'id':'abc123','regular':{'code':'wrong'}})]
        self.assertEqual(self.tick(tid),'unknown')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM simulations').fetchone()[0],0)

    def test_expired_session_pauses_and_login_resumes_get(self):
        tid=self.enqueue();FakeClient.replies=[(201,{'location':'/simulations/s1'}, {})]
        self.tick(tid);FakeClient.jar=[]
        self.assertEqual(self.tick(tid),'blocked')
        self.assertTrue(store.is_paused(self.c))
        self.assertEqual(autopilot.after_login(self.c),[tid])
        self.assertFalse(store.is_paused(self.c))
        self.assertEqual(len(FakeClient.calls),1)

    def test_interrupted_post_becomes_unknown_before_attempt_cap(self):
        tid=self.unknown()
        self.c.execute("UPDATE tasks SET status='running',lease_until='2000',attempts=max_attempts WHERE task_id=?",(tid,))
        store.recover_stale(self.c)
        self.assertEqual(self.tick(tid),'unknown')
        self.assertEqual(FakeClient.calls,[])

    def test_interrupted_get_requeues_and_never_posts(self):
        tid=self.enqueue()
        self.c.execute('INSERT INTO brain_runs VALUES(?,?,?,?,?,?,?)',
                       (tid,'polling','https://api.worldquantbrain.com/simulations/s1',None,None,util.now_iso(),util.now_iso()))
        self.c.execute("UPDATE tasks SET status='running',lease_until='2000' WHERE task_id=?",(tid,))
        FakeClient.replies=[(200,{}, {})]
        self.assertEqual(self.tick(tid),'queued')
        self.assertEqual([c[0] for c in FakeClient.calls],['GET'])


class FallbackReviewTests(unittest.TestCase):
    setUp=routing_tests.RoutingTests.setUp
    write_config=routing_tests.RoutingTests.write_config
    enqueue=routing_tests.RoutingTests.enqueue
    tick=routing_tests.RoutingTests.tick
    status=routing_tests.RoutingTests.status

    def test_non_capacity_failure_stops_after_three_retries(self):
        self.script.write_text('import sys\nprint("invalid configuration")\nsys.exit(1)\n')
        tid,_=self.enqueue()
        for _ in range(4):self.tick()
        self.assertEqual(self.status(tid),'failed')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_calls WHERE agent='b'").fetchone()[0],0)


class InputReviewTests(unittest.TestCase):
    def test_nonfinite_retry_header_uses_default(self):
        for x in ('NaN','Infinity','-Infinity'):
            self.assertEqual(retry_delay(x),60)

    def test_malformed_declarations_are_rejected(self):
        _,code=evidence_gate.evaluate([{'id':'x','blocking':True}],[],util.now())
        self.assertEqual(code,2)

    def test_nonfinite_or_boolean_pnl_rejected(self):
        for value in (float('nan'),float('inf'),True):
            errors=[]
            contracts.validate_pnl({'kind':'daily','points':[{'date':'2026-01-01','value':value}]},errors)
            self.assertTrue(errors)
