import json
import tempfile
import unittest
from pathlib import Path

from wq import usage
from wq.wrappers.agent import spec_for
from helpers import make_env


class UsageParsing(unittest.TestCase):
    def test_grok_json_usage_and_cli_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grok.log"
            path.write_text(
                "warning\n"
                + json.dumps({
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 5,
                        "reasoning_tokens": 2,
                        "total_tokens": 35,
                    },
                    "modelUsage": {"grok-4.6-build": {}},
                    "total_cost_usd": 0.012345,
                })
            )
            found = usage.grok_log(str(path))
            self.assertEqual(found["total_tokens"], 35)
            self.assertEqual(found["cache_read_tokens"], 20)
            self.assertEqual(found["cost_usd"], 0.012345)

    def test_devin_final_metrics_have_unknown_dollar_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.json"
            path.write_text(json.dumps({
                "agent": {"model_name": "SWE-2 Max"},
                "final_metrics": {
                    "total_prompt_tokens": 100,
                    "total_completion_tokens": 25,
                    "total_cached_tokens": 80,
                },
            }))
            found = usage.devin_transcript(str(path))
            self.assertEqual(found["total_tokens"], 125)
            self.assertIsNone(found["cost_usd"])
            self.assertIn("未知", usage.display(found))

    def test_missing_usage_is_not_zero(self):
        self.assertIn("未知", usage.display(None))
        self.assertIn("暂未", usage.display(None, running=True))
        self.assertIn("$0", usage.display(usage.zero()))

    def test_devin_export_next_to_wrapper_log_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "logs" / "devin-20260921T000000.log"
            export = Path(tmp) / ".usage" / "devin-20260921T000000.json"
            export.parent.mkdir()
            export.write_text(json.dumps({
                "agent": {"model_name": "SWE-2 Max"},
                "final_metrics": {
                    "total_prompt_tokens": 40,
                    "total_completion_tokens": 10,
                    "total_cached_tokens": 30,
                },
            }))
            found = usage.for_call({
                "agent": "devin",
                "log_path": str(log),
                "prompt_file": str(Path(tmp) / "jobs" / "p.md"),
                "started_at": "2026-09-21T00:00:00+00:00",
            })
            self.assertEqual(found["total_tokens"], 50)

    def test_devin_export_in_configured_workdir_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "private" / "logs" / "devin-20260921T000001.log"
            export = Path(tmp) / "devin-work" / ".usage" / "devin-20260921T000001.json"
            export.parent.mkdir(parents=True)
            export.write_text(json.dumps({
                "agent": {"model_name": "SWE-2 Max"},
                "final_metrics": {
                    "total_prompt_tokens": 70,
                    "total_completion_tokens": 30,
                    "total_cached_tokens": 10,
                },
            }))
            found = usage.for_call({
                "agent": "devin", "log_path": str(log),
                "prompt_file": str(Path(tmp) / "jobs" / "p.md"),
                "started_at": "2026-09-21T00:00:00+00:00",
            }, [str(export.parent.parent)])
            self.assertEqual(found["total_tokens"], 100)

    def test_devin_spec_requests_metrics_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, conn = make_env(tmp, {"models": {"devin": {"enabled": True}}})
            spec = spec_for(cfg, "devin", "p.md", [])
            self.assertIn("--export", spec.argv)
            self.assertIn("{usage_export}", spec.argv)
            conn.close()


if __name__ == "__main__":
    unittest.main()
