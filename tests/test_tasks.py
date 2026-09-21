"""任务队列：双触发只领一个、崩溃/租约恢复、远端类 UNKNOWN、账本分离。"""
import json
import tempfile
import unittest

from helpers import make_env
from wq import store
from wq.db import connect


class TestTasks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg, self.conn = make_env(self.tmp)

    def test_claim_respects_microsecond_due_time(self):
        import datetime as dt
        from unittest.mock import patch
        tid, _ = store.enqueue_task(self.conn, "reconcile", {})
        due = dt.datetime(2026, 9, 21, 0, 0, 0, 123400, tzinfo=dt.timezone.utc)
        self.conn.execute("UPDATE tasks SET not_before=? WHERE task_id=?", (due.isoformat(), tid))
        with patch("wq.store.util.now", return_value=due-dt.timedelta(microseconds=1)):
            self.assertIsNone(store.claim_task(self.conn, "early"))
        with patch("wq.store.util.now", return_value=due+dt.timedelta(microseconds=1)):
            self.assertEqual(store.claim_task(self.conn, "due")["task_id"], tid)

    def test_double_trigger_single_claim(self):
        tid, _ = store.enqueue_task(self.conn, "reconcile", {})
        conn2 = connect(self.cfg.db_path)   # 第二个连接模拟并发触发
        t1 = store.claim_task(self.conn, "owner-A")
        t2 = store.claim_task(conn2, "owner-B")
        self.assertIsNotNone(t1)
        self.assertIsNone(t2)
        self.assertEqual(t1["task_id"], tid)

    def test_stale_local_task_requeued(self):
        tid, _ = store.enqueue_task(self.conn, "reconcile", {})
        store.claim_task(self.conn, "dead-owner", lease_s=0)
        import time; time.sleep(1.05)
        rec = store.recover_stale(self.conn)
        self.assertEqual(rec[0]["action"], "requeued")
        t = store.claim_task(self.conn, "new-owner")
        self.assertEqual(t["task_id"], tid)  # 可再次被领取

    def test_stale_remote_task_goes_unknown(self):
        tid, _ = store.enqueue_task(self.conn, "simulation", {"expression": "x"},
                                    dedup_key="h1")
        store.claim_task(self.conn, "dead-owner", lease_s=0)
        import time; time.sleep(1.05)
        rec = store.recover_stale(self.conn)
        self.assertEqual(rec[0]["action"], "unknown")
        # 对账前不得重发：同 dedup_key 入队被拒
        t2, created = store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h1")
        self.assertFalse(created)
        self.assertEqual(t2, tid)

    def test_attempts_recorded(self):
        tid, _ = store.enqueue_task(self.conn, "reconcile", {})
        store.claim_task(self.conn, "o")
        store.finish_task(self.conn, tid, "succeeded")
        n = self.conn.execute("SELECT COUNT(*) c FROM attempts WHERE task_id=?",
                              (tid,)).fetchone()["c"]
        self.assertGreaterEqual(n, 3)  # enqueue + claim + finish 全部留痕

    def test_submission_payment_separation(self):
        conn = self.conn
        cid = store.insert_family(conn, None, "k", "h", "test", False, None)
        cand = store.insert_candidate(conn, cid, "e", {}, "h", False)
        sim = store.insert_simulation(conn, cand, "r1", "manual", False, "passed",
                                      {"expression": "e", "config": {}}, None, None)
        s1 = store.add_submission(conn, sim, "sub1", "accepted", None)
        store.add_submission(conn, sim, "sub2", "final_valid", None)
        store.add_payment(conn, s1, "received", 12.5, "USD", "2026-09-20", "bank ref #1")
        subs = conn.execute("SELECT status, COUNT(*) c FROM submissions GROUP BY status").fetchall()
        self.assertEqual({r["status"]: r["c"] for r in subs},
                         {"accepted": 1, "final_valid": 1})  # 接受≠最终有效
        pay = conn.execute("SELECT amount FROM payments").fetchone()
        self.assertEqual(pay["amount"], 12.5)                # 仅 received 入账


if __name__ == "__main__":
    unittest.main()
