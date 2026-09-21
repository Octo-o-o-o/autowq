"""去重：精确（含空白/大小写归一）、参数微调归族、每族上限。"""
import tempfile
import unittest

from helpers import make_env
from wq import dedup, importer, store
from wq.errors import WqExit

CFG = {"region": "USA", "delay": 1}


class TestDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg, self.conn = make_env(self.tmp)

    def test_whitespace_and_case_same_hash(self):
        a = dedup.config_hash("acct", "rank( x )  * 2", CFG)
        b = dedup.config_hash("acct", "RANK(x) * 2", CFG)
        self.assertEqual(a, b)

    def test_param_change_same_family(self):
        self.assertEqual(dedup.family_key("rank(x) * 2"),
                         dedup.family_key("rank(x)*0.5"))
        self.assertNotEqual(dedup.family_key("rank(x)"), dedup.family_key("ts_mean(x, 3)"))

    def test_exact_dup_import(self):
        doc = {"expression": "rank(x) * 2", "config": CFG}
        cid1, out1 = importer.ensure_candidate(self.conn, self.cfg, doc["expression"], CFG, True)
        cid2, out2 = importer.ensure_candidate(self.conn, self.cfg, "RANK( x ) * 2", CFG, True)
        self.assertEqual((out1, out2), ("new", "exact_dup"))
        self.assertEqual(cid1, cid2)

    def test_param_variant_same_family_capped(self):
        for i in range(4):
            importer.ensure_candidate(self.conn, self.cfg, f"rank(x) * {i+1}", CFG, True)
        fams = self.conn.execute("SELECT COUNT(*) c FROM families").fetchone()["c"]
        self.assertEqual(fams, 1)  # 参数微调全部归一族
        with self.assertRaises(WqExit):  # 第 5 个超上限
            importer.ensure_candidate(self.conn, self.cfg, "rank(x) * 9", CFG, True)

    def test_enqueue_dedup_key_prevents_resend(self):
        chash = dedup.config_hash("testacct", "rank(x)", CFG)
        t1, c1 = store.enqueue_task(self.conn, "simulation", {"expression": "rank(x)"}, chash)
        t2, c2 = store.enqueue_task(self.conn, "simulation", {"expression": "rank(x)"}, chash)
        self.assertEqual((t1 == t2, c1, c2), (True, True, False))


if __name__ == "__main__":
    unittest.main()
