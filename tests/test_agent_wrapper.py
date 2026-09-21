"""wrapper 契约：闸门、锁、活调用去重、超时整组清理、产物校验、崩溃恢复、故障上限。

全部用 stub 二进制（tests/fixtures/stub_agent.py）走真实 subprocess 路径，
不调用任何真实模型。
"""
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import make_env
from wq import store, util
from wq.wrappers.agent import AgentSpec, run_agent

STUB = os.path.join(os.path.dirname(__file__), "fixtures", "stub_agent.py")


def stub_spec(tmp, mode="ok", artifact="out/result.json", timeout=10):
    argv = [sys.executable, STUB, "--mode", mode]
    if artifact:
        argv += ["--artifact", os.path.join(tmp, artifact)]
    return AgentSpec(name="grok", argv=argv, workdir=tmp,
                     timeout_s=timeout, artifacts=[artifact] if artifact else [])


GATES = {"models": {"grok": {"enabled": True}},
         "budgets": {"grok": {"enabled": True, "remaining": 5, "unit": "pct"}}}


def _write_prompts(tmp):
    # wrapper 校验 prompt 是 workdir 内真实存在的普通文件
    for name in ("p.md", "p"):
        with open(os.path.join(tmp, name), "w") as f:
            f.write("stub prompt\n")


class TestGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_prompts(self.tmp)

    def test_disabled_model_blocked(self):
        cfg, conn = make_env(self.tmp)
        out = run_agent(conn, cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
        self.assertEqual(out.status, "blocked_policy")

    def test_budget_disabled_blocked(self):
        cfg, conn = make_env(self.tmp, {"models": {"grok": {"enabled": True}}})
        out = run_agent(conn, cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
        self.assertEqual(out.status, "blocked_budget")

    def test_budget_unknown_blocked(self):
        cfg, conn = make_env(self.tmp, {
            "models": {"grok": {"enabled": True}},
            "budgets": {"grok": {"enabled": True, "remaining": None}}})
        out = run_agent(conn, cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
        self.assertEqual(out.status, "blocked_budget")

    def test_no_allow_blocked(self):
        cfg, conn = make_env(self.tmp, GATES)
        out = run_agent(conn, cfg, stub_spec(self.tmp), "p.md", "t", allow=False)
        self.assertEqual(out.status, "blocked_policy")

    def test_weekly_quota(self):
        cfg, conn = make_env(self.tmp, GATES)
        cfg.data["limits"]["grok_calls_per_week"] = 1
        spec = stub_spec(self.tmp)
        self.assertEqual(run_agent(conn, cfg, spec, "p", "t", allow=True).status, "succeeded")
        out = run_agent(conn, cfg, spec, "p", "t", allow=True)
        self.assertEqual(out.status, "blocked_quota")


class TestExecution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_prompts(self.tmp)
        self.cfg, self.conn = make_env(self.tmp, GATES)

    def test_success_verifies_artifact(self):
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
        self.assertEqual(out.status, "succeeded")
        self.assertIn("out/result.json", out.artifacts)

    def test_exit0_but_bad_artifact_fails(self):
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp, mode="badjson"),
                        "p.md", "t", allow=True)
        self.assertEqual(out.status, "artifact_invalid")

    def test_missing_artifact_fails(self):
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp, mode="noartifact"),
                        "p.md", "t", allow=True)
        self.assertEqual(out.status, "artifact_invalid")

    def test_nonzero_exit_failed(self):
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp, mode="crash"),
                        "p.md", "t", allow=True)
        self.assertEqual(out.status, "failed")
        self.assertEqual(out.exit_code, 3)

    def test_timeout_kills_group(self):
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp, mode="sleep", timeout=1),
                        "p.md", "t", allow=True)
        self.assertEqual(out.status, "timeout")
        # 进程组已被清理：无残留 sleep 进程
        pid = self.conn.execute("SELECT pid FROM agent_calls WHERE call_id=?",
                                (out.call_id,)).fetchone()["pid"]
        self.assertFalse(store.pid_alive(pid))

    def test_live_dup_blocked(self):
        proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
        try:
            store.start_agent_call(self.conn, "grok", "t", None, proc.pid, "p", "l")
            out = run_agent(self.conn, self.cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
            self.assertEqual(out.status, "blocked_live_dup")
        finally:
            proc.kill(); proc.wait()

    def test_dead_pid_recovers_then_runs(self):
        proc = subprocess.Popen(["sleep", "0"], stdin=subprocess.DEVNULL)
        proc.wait()
        store.start_agent_call(self.conn, "grok", "t", None, proc.pid, "p", "l")
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp), "p.md", "t2", allow=True)
        self.assertEqual(out.status, "succeeded")
        crashed = self.conn.execute(
            "SELECT COUNT(*) c FROM agent_calls WHERE status='crashed'").fetchone()["c"]
        self.assertEqual(crashed, 1)

    def test_incident_attempt_cap_cross_session(self):
        for _ in range(2):  # 默认上限 2，跨"会话"（这里同库即累计）
            out = run_agent(self.conn, self.cfg, stub_spec(self.tmp, mode="crash"),
                            "p.md", "repair", incident_id="inc-1", allow=True)
            self.assertEqual(out.status, "failed")
        out = run_agent(self.conn, self.cfg, stub_spec(self.tmp),
                        "p.md", "repair", incident_id="inc-1", allow=True)
        self.assertEqual(out.status, "blocked_attempt_cap")

    def test_stdin_closed(self):
        # stub 若读 stdin 会立即 EOF；能正常退出即证明 stdin 非交互
        spec = stub_spec(self.tmp)
        out = run_agent(self.conn, self.cfg, spec, "p.md", "t", allow=True)
        self.assertEqual(out.status, "succeeded")

    def test_lock_held_blocks_second(self):
        import fcntl
        util.ensure_dir(self.cfg.run_dir)
        fd = os.open(os.path.join(self.cfg.run_dir, "agent-grok.lock"),
                     os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            out = run_agent(self.conn, self.cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
            self.assertEqual(out.status, "blocked_locked")
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
