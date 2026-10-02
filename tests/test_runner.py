"""run-once 分派：manual/api adapter、认证/限流/配额、超时远端已接受、暂停清理。"""
import os
import signal
import subprocess
import tempfile
import time
import unittest

from helpers import make_env
from wq import reconcile, runner, store
from wq.errors import AdapterError
from wq.adapters.base import SimAdapter


class FakeAdapter(SimAdapter):
    """测试用假 adapter（非交付 adapter），按脚本抛错/回执。"""
    name = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def _next(self):
        self.calls += 1
        item = self.script.pop(0) if self.script else ("ok", {"remote_id": "r-9"})
        kind, val = item
        if kind == "raise":
            raise val
        return val

    def simulate(self, payload):
        return self._next()

    def submit(self, payload):
        return self._next()


def run_with_adapter(conn, cfg, adapter):
    orig = runner.build_adapter
    runner.build_adapter = lambda c: adapter
    try:
        return runner.run_once(conn, cfg)
    finally:
        runner.build_adapter = orig


class TestRunOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg, self.conn = make_env(self.tmp)

    def test_idle(self):
        code, lines = runner.run_once(self.conn, self.cfg)
        self.assertEqual((code, "idle" in lines[0]), (0, True))

    def test_framework_gate_still_ticks_and_reports_reason(self):
        """研究框架挂起新研究时：autopilot.tick 仍须运行（last_tick 更新）且写出阻塞原因。"""
        from wq import autopilot, research_framework
        autopilot.setup(self.conn)  # 最小库缺 autopilot 表；cfg 无 autopilot 段时 tick 会提前返回
        cfg = self.cfg
        cfg.data.setdefault('autopilot', {'enabled': False})
        orig_enabled = research_framework.enabled
        orig_tick = research_framework.tick
        research_framework.enabled = lambda c: True
        research_framework.tick = lambda conn, c: {
            'state': 'Dual-loop baseline not approved',
            'allow_research': False, 'allow_new_quant_cycle': False}
        try:
            runner._coordinate(self.conn, cfg)
        finally:
            research_framework.enabled = orig_enabled
            research_framework.tick = orig_tick
        self.assertTrue(store.get_flag(self.conn, 'autopilot_last_tick'))
        self.assertEqual(store.get_flag(self.conn, 'autopilot_message'),
                         '自动研究待命：双环路冻结基线未审批')

    def test_simulation_manual_blocked_policy(self):
        store.enqueue_task(self.conn, "simulation",
                           {"expression": "rank(x)", "config": {"delay": 1}}, "h")
        code, _ = runner.run_once(self.conn, self.cfg)
        self.assertEqual(code, 3)  # manual 模式拒绝真实模拟
        t = store.list_tasks(self.conn)[0]
        self.assertEqual(t["status"], "blocked")

    def test_simulation_api_not_implemented(self):
        cfg, conn = make_env(tempfile.mkdtemp(),
                             {"adapters": {"brain": {"mode": "api"}}})
        store.enqueue_task(conn, "simulation",
                           {"expression": "rank(x)", "config": {"delay": 1}}, "h")
        code, _ = runner.run_once(conn, cfg)
        self.assertEqual(code, 4)  # NOT_IMPLEMENTED
        self.assertEqual(store.list_tasks(conn)[0]["status"], "blocked")

    def test_auth_pauses(self):
        store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h")
        fake = FakeAdapter([("raise", AdapterError(AdapterError.AUTH, "401"))])
        code, _ = run_with_adapter(self.conn, self.cfg, fake)
        self.assertEqual(code, 3)
        self.assertTrue(store.is_paused(self.conn))
        self.assertEqual(store.list_tasks(self.conn)[0]["status"], "blocked")

    def test_rate_limit_requeue_then_fail_when_over_max(self):
        store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h")
        fake = FakeAdapter([("raise", AdapterError(AdapterError.RATE_LIMIT,
                                                 "429", retry_after=0.05))])
        code, lines = run_with_adapter(self.conn, self.cfg, fake)
        self.assertEqual(code, 0)
        t = store.list_tasks(self.conn)[0]
        self.assertEqual(t["status"], "queued")
        self.assertGreater(t["not_before"], t["created_at"])  # Retry-After 生效

        store.finish_task(self.conn, t["task_id"], "failed")  # 重置环境
        tid2, _ = store.enqueue_task(self.conn, "simulation", {"expression": "y"}, "h2")
        fake2 = FakeAdapter([("raise", AdapterError(AdapterError.RATE_LIMIT,
                                                  "429", retry_after=99999))])
        code, _ = run_with_adapter(self.conn, self.cfg, fake2)
        self.assertEqual(code, 3)
        row = self.conn.execute("SELECT status FROM tasks WHERE task_id=?",
                                (tid2,)).fetchone()
        self.assertEqual(row["status"], "failed")  # 等待超上限 → 直接失败，不无限退避

    def test_quota_blocked(self):
        store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h")
        fake = FakeAdapter([("raise", AdapterError(AdapterError.QUOTA, "quota exhausted"))])
        code, _ = run_with_adapter(self.conn, self.cfg, fake)
        self.assertEqual(code, 3)
        self.assertEqual(store.list_tasks(self.conn)[0]["status"], "blocked")

    def test_timeout_remote_accepted_then_reconcile(self):
        # 远端已接受但本地超时 → UNKNOWN → 人工对账 accepted；dedup 阻止盲目重发
        tid, _ = store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h")
        fake = FakeAdapter([("raise", AdapterError(AdapterError.UNKNOWN_REMOTE,
                                                  "timeout after send"))])
        code, _ = run_with_adapter(self.conn, self.cfg, fake)
        self.assertEqual(code, 0)
        self.assertEqual(store.list_tasks(self.conn)[0]["status"], "unknown")

        t2, created = store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h")
        self.assertFalse(created)  # 不盲目重发

        new = reconcile.resolve(self.conn, tid, "accepted", "UI 确认已接受 sim-123")
        self.assertEqual(new, "succeeded")
        self.assertEqual(store.list_tasks(self.conn)[0]["status"], "succeeded")

    def test_pause_blocks_claim_and_kills_inflight(self):
        proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            store.start_agent_call(self.conn, "grok", "t", None, proc.pid, "p", "l")
            store.set_flag(self.conn, "paused", "1")
            code, _ = runner.run_once(self.conn, self.cfg)
            self.assertEqual(code, 6)  # PAUSED
        finally:
            if store.pid_alive(proc.pid):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    def test_auth_pause_attempts_keychain_recovery_before_returning_paused(self):
        from unittest.mock import patch
        store.set_flag(self.conn, "paused", "1")
        store.set_flag(self.conn, "pause_reason", "auth: expired")
        def recover(conn, cfg):
            store.set_flag(conn, "paused", "0")
            return []
        with patch("wq.autopilot.auto_resume_after_auth", side_effect=recover):
            code, lines = runner.run_once(self.conn, self.cfg)
        self.assertEqual(code, 0)
        self.assertTrue(any("automatically recovered" in line for line in lines))

    def test_recovery_that_leaves_pause_set_cannot_tick(self):
        from unittest.mock import patch
        store.set_flag(self.conn, "paused", "1")
        store.set_flag(self.conn, "pause_reason", "auth: expired")
        with patch("wq.autopilot.auto_resume_after_auth", return_value=[]), patch("wq.autopilot.tick") as tick:
            code, _ = runner.run_once(self.conn, self.cfg)
        self.assertEqual(code, 6)
        tick.assert_not_called()

    def test_pause_cmd_aborts_inflight(self):
        from wq.cli import cmd_pause
        import argparse
        proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                                start_new_session=True)
        def cleanup_process():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        self.addCleanup(cleanup_process)
        store.start_agent_call(self.conn, "grok", "t", None, proc.pid, "p", "l")
        self.conn.commit()
        args = argparse.Namespace(config=None, db=self.cfg.db_path, reason="test")
        code = cmd_pause(args)
        self.assertEqual(code, 0)
        deadline = time.time() + 5
        while store.pid_alive(proc.pid) and time.time() < deadline:
            time.sleep(0.05)                          # pid_alive 会顺带回收 zombie
        self.assertFalse(store.pid_alive(proc.pid))  # 在途调用被整组终止
        row = self.conn.execute("SELECT status FROM agent_calls").fetchone()
        self.assertEqual(row["status"], "aborted")

    def test_resume_blocked_by_unknown(self):
        from wq.cli import cmd_resume
        import argparse
        store.enqueue_task(self.conn, "simulation", {"expression": "x"}, "h")
        tid = store.list_tasks(self.conn)[0]["task_id"]
        store.mark_task_unknown(self.conn, tid, {})
        store.set_flag(self.conn, "paused", "1")
        self.conn.commit()
        args = argparse.Namespace(config=None, db=self.cfg.db_path, force=False)
        self.assertEqual(cmd_resume(args), 3)              # 有 UNKNOWN → 先对账
        self.assertTrue(store.is_paused(self.conn))
        args.force = True
        self.assertEqual(cmd_resume(args), 0)
        self.assertFalse(store.is_paused(self.conn))

    def test_agent_task_blocked_when_no_budget(self):
        store.enqueue_task(self.conn, "agent_call",
                           {"agent": "grok", "prompt_file": "p.md", "purpose": "cycle"})
        code, lines = runner.run_once(self.conn, self.cfg)
        self.assertEqual(code, 3)
        self.assertIn("BLOCKED", lines[-1])


if __name__ == "__main__":
    unittest.main()
