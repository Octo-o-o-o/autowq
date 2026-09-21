"""PnL 语义：累计差分、日 PnL 不再差分、缺失日期 ≠ 0。"""
import unittest

from wq import pnl
from wq.errors import ContractError

PTS = [{"date": "2024-01-02", "value": 10.0},
       {"date": "2024-01-03", "value": 15.0},
       {"date": "2024-01-05", "value": 12.0}]


class TestPnl(unittest.TestCase):
    def test_cumulative_to_daily(self):
        daily = pnl.to_daily(PTS, "cumulative")
        self.assertEqual([v for _, v in daily], [10.0, 5.0, -3.0])

    def test_daily_not_re_diffed(self):
        daily = pnl.to_daily(PTS, "daily")
        self.assertEqual([v for _, v in daily], [10.0, 15.0, 12.0])

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ContractError):
            pnl.to_daily(PTS, "weekly")

    def test_missing_dates_flagged_not_zero(self):
        gaps = pnl.find_gaps(PTS)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["missing_days"], 1)  # 01-04 缺失
        self.assertEqual(gaps[0]["after"], "2024-01-03")

    def test_duplicate_date_rejected(self):
        with self.assertRaises(ContractError):
            pnl.to_daily(PTS + [{"date": "2024-01-03", "value": 1.0}], "daily")

    def test_corr_key_view(self):
        v = pnl.daily_corr_key(PTS, "cumulative")
        self.assertEqual(v["n_points"], 3)
        self.assertEqual(len(v["gaps"]), 1)


if __name__ == "__main__":
    unittest.main()
