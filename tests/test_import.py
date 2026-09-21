"""manual-import：synthetic 分流、--real 闸门、质量失败、报告口径。"""
import copy
import tempfile
import unittest

from helpers import RESULT_DOC, make_env
from wq import importer, report, store
from wq.errors import ContractError, WqExit


class TestImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg, self.conn = make_env(self.tmp)

    def test_synthetic_import_no_flag(self):
        res = importer.import_result_obj(self.conn, self.cfg, copy.deepcopy(RESULT_DOC), False)
        self.assertEqual(res["outcome"], "imported")
        self.assertTrue(res["synthetic"])

    def test_real_requires_flag(self):
        doc = copy.deepcopy(RESULT_DOC)
        doc["synthetic"], doc["source"] = False, "manual"
        with self.assertRaises(WqExit):
            importer.import_result_obj(self.conn, self.cfg, doc, allow_real=False)
        res = importer.import_result_obj(self.conn, self.cfg, doc, allow_real=True)
        self.assertEqual(res["outcome"], "imported")

    def test_contract_error(self):
        doc = copy.deepcopy(RESULT_DOC)
        del doc["simulation"]["expression"]
        with self.assertRaises(ContractError):
            importer.import_result_obj(self.conn, self.cfg, doc, True)

    def test_quality_fail_not_success(self):
        doc = copy.deepcopy(RESULT_DOC)
        doc["quality"] = {"status": "fail", "notes": "参数孤峰"}
        res = importer.import_result_obj(self.conn, self.cfg, doc, False)
        self.assertEqual(res["sim_status"], "quality_failed")

    def test_checks_fail(self):
        doc = copy.deepcopy(RESULT_DOC)
        doc["simulation"]["checks"] = {"passed": False}
        res = importer.import_result_obj(self.conn, self.cfg, doc, False)
        self.assertEqual(res["sim_status"], "failed")

    def test_synthetic_excluded_from_real_report(self):
        importer.import_result_obj(self.conn, self.cfg, copy.deepcopy(RESULT_DOC), False)
        rep = report.build_report(self.conn, self.cfg)
        self.assertIn("假设族 0", rep)                      # synthetic 不计入
        rep_all = report.build_report(self.conn, self.cfg, include_synthetic=True)
        self.assertIn("假设族 1", rep_all)

    def test_multi_variant_card_same_family(self):
        # 回归：卡内多变体表达式指纹不同，也必须归卡声明的 family_id
        card = {
            "schema": "wq.research-card/v1", "hypothesis_id": "HX", "family_id": "fam-x",
            "mechanism": "m",
            "variants": [
                {"variant_id": "a", "config": {"delay": 1}},
                {"variant_id": "b", "config": {"delay": 2}},
            ],
            "provenance": {"synthetic": False, "origin": "test"},
        }
        for v in card["variants"]:
            importer.ensure_candidate(self.conn, self.cfg,
                                      f"ABSTRACT:HX:{v['variant_id']}",
                                      v["config"], False, card=card)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM candidates WHERE family_id='fam-x'").fetchone()["c"]
        self.assertEqual(n, 2)
        fams = self.conn.execute(
            "SELECT COUNT(*) c FROM families WHERE family_id='fam-x'").fetchone()["c"]
        self.assertEqual(fams, 1)

    def test_first_result_for_preexisting_candidate_is_imported(self):
        doc = copy.deepcopy(RESULT_DOC)
        sim = doc['simulation']
        cid, _ = importer.ensure_candidate(self.conn, self.cfg, sim['expression'], sim['config'], True)
        result = importer.import_result_obj(self.conn, self.cfg, doc, False)
        self.assertEqual(result['outcome'], 'imported')
        self.assertEqual(result['candidate_id'], cid)
        repeated = importer.import_result_obj(self.conn, self.cfg, doc, False)
        self.assertEqual(repeated['sim_id'], result['sim_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM simulations').fetchone()[0], 1)

    def test_synthetic_candidate_cannot_absorb_real_result(self):
        doc = copy.deepcopy(RESULT_DOC)
        importer.import_result_obj(self.conn, self.cfg, doc, False)
        doc.update(synthetic=False, source='manual')
        with self.assertRaises(WqExit):
            importer.import_result_obj(self.conn, self.cfg, doc, True)

    def test_remote_id_conflict_does_not_create_candidate(self):
        doc = copy.deepcopy(RESULT_DOC)
        importer.import_result_obj(self.conn, self.cfg, doc, False)
        doc['simulation']['expression']='rank(y)'
        with self.assertRaises(WqExit):
            importer.import_result_obj(self.conn, self.cfg, doc, False)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],1)

    def test_pnl_not_income(self):
        doc = copy.deepcopy(RESULT_DOC)
        doc["synthetic"], doc["source"] = False, "manual"
        doc["simulation"]["pnl"] = {"kind": "daily",
                                    "points": [{"date": "2024-01-02", "value": 999.0}]}
        importer.import_result_obj(self.conn, self.cfg, doc, allow_real=True)
        rep = report.build_report(self.conn, self.cfg)
        self.assertIn("可依赖收入按 0", rep)   # 有 PnL 记录但无 payments → 收入 0


if __name__ == "__main__":
    unittest.main()
