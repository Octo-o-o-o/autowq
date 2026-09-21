"""契约校验：非法字段/配置拒绝；无字段目录时不得写可执行表达式。"""
import copy
import tempfile
import unittest

from helpers import RESULT_DOC, make_env
from wq import contracts


class TestContracts(unittest.TestCase):
    def _card(self):
        return {
            "schema": "wq.research-card/v1",
            "hypothesis_id": "H1", "family_id": "fam1", "mechanism": "m",
            "variants": [{"variant_id": "v1", "config": {"delay": 1}}],
            "provenance": {"synthetic": True, "origin": "fixture"},
        }

    def test_valid_card(self):
        self.assertEqual(contracts.validate_research_card(self._card()), [])

    def test_expression_requires_catalog(self):
        c = self._card()
        c["variants"][0]["expression"] = "rank(x)"
        errs = contracts.validate_research_card(c)
        self.assertTrue(any("catalog_verified" in e for e in errs), errs)

    def test_expression_ok_when_catalog_verified(self):
        c = self._card()
        c["variants"][0]["expression"] = "rank(x)"
        c["variants"][0]["config"]["catalog_verified"] = True
        self.assertEqual(contracts.validate_research_card(c), [])

    def test_bad_config_keys_rejected(self):
        c = self._card()
        c["variants"][0]["config"]["bogus_key"] = 1
        errs = contracts.validate_research_card(c)
        self.assertTrue(any("bogus_key" in e for e in errs), errs)

    def test_bad_config_types(self):
        errs = []
        contracts.validate_sim_config({"delay": -1, "truncation": 2.0}, errs)
        self.assertEqual(len(errs), 2, errs)

    def test_over_4_variants_rejected(self):
        c = self._card()
        c["variants"] = [{"variant_id": f"v{i}", "config": {}} for i in range(5)]
        errs = contracts.validate_research_card(c)
        self.assertTrue(any("4" in e for e in errs), errs)

    def test_result_requires_synthetic_flag(self):
        d = copy.deepcopy(RESULT_DOC)
        del d["synthetic"]
        self.assertTrue(contracts.validate_imported_result(d))

    def test_fixture_claimed_real_rejected(self):
        d = copy.deepcopy(RESULT_DOC)
        d["synthetic"], d["source"] = False, "fixture"
        self.assertTrue(contracts.validate_imported_result(d))

    def test_bad_pnl_kind_rejected(self):
        d = copy.deepcopy(RESULT_DOC)
        d["simulation"]["pnl"] = {"kind": "weekly", "points": []}
        self.assertTrue(contracts.validate_imported_result(d))

    def test_bad_pnl_point(self):
        d = copy.deepcopy(RESULT_DOC)
        d["simulation"]["pnl"] = {"kind": "daily",
                                  "points": [{"date": "not-a-date", "value": 1}]}
        self.assertTrue(contracts.validate_imported_result(d))


if __name__ == "__main__":
    unittest.main()
