"""debug_authorization 首周授权窗口 + workdir/路径校验回归测试。

全部用 stub 二进制走真实 subprocess 路径；不调用任何真实模型，不配置真实预算。
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

from helpers import make_env
from wq import report, runner, store, util
from wq.wrappers.agent import AgentSpec, run_agent, spec_for

STUB = os.path.join(os.path.dirname(__file__), "fixtures", "stub_agent.py")


def _window(start_s=-3600, end_s=3600, agents=("grok",), **kw):
    now = util.now()
    w = {"enabled": True,
         "starts_at": (now + dt.timedelta(seconds=start_s)).isoformat(),
         "expires_at": (now + dt.timedelta(seconds=end_s)).isoformat(),
         "agents": list(agents), "unlimited": True,
         "evidence": "用户明确要求启动 Grok/Devin 自动调用，首周不限额（会话授权原文）"}
    w.update(kw)
    return {"debug_authorization": w}


# 正常路径全通：模型启用 + 预算启用且 remaining 已确认（数值仅为测试占位，非真实余额）
GATES = {"models": {"grok": {"enabled": True}},
         "budgets": {"grok": {"enabled": True, "remaining": 5, "unit": "pct"}}}


def stub_spec(tmp, mode="ok", artifact="out/result.json", timeout=10, name="grok",
              workdir=None):
    argv = [sys.executable, STUB, "--mode", mode]
    if artifact:
        argv += ["--artifact", os.path.join(workdir or tmp, artifact)]
    return AgentSpec(name=name, argv=argv, workdir=workdir or tmp,
                     timeout_s=timeout, artifacts=[artifact] if artifact else [])


def _prompt(tmp, name="p.md"):
    p = os.path.join(tmp, name)
    with open(p, "w") as f:
        f.write("stub prompt\n")
    return name


class TestWindowStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, over, agent="grok"):
        cfg, _ = make_env(self.tmp, over)
        return cfg.debug_window(agent)

    def test_valid_window_ok(self):
        st, info = self._state(_window())
        self.assertEqual(st, "ok")
        self.assertTrue(info)  # info = 到期时间

    def test_expired_window_invalid(self):
        st, info = self._state(_window(start_s=-7200, end_s=-3600))
        self.assertEqual((st, "过期" in info), ("invalid", True))

    def test_future_window_invalid(self):
        st, info = self._state(_window(start_s=3600, end_s=7200))
        self.assertEqual((st, "未开始" in info), ("invalid", True))

    def test_no_timezone_invalid(self):
        naive = dt.datetime.now().replace(microsecond=0)
        w = _window(starts_at=naive.isoformat(),
                    expires_at=(naive + dt.timedelta(hours=2)).isoformat())
        st, info = self._state(w)
        self.assertEqual((st, "时区" in info), ("invalid", True))

    def test_over_7_days_invalid(self):
        st, info = self._state(_window(end_s=8 * 86400))
        self.assertEqual((st, "7 天" in info), ("invalid", True))

    def test_bad_date_invalid(self):
        st, _ = self._state(_window(starts_at="not-a-date"))
        self.assertEqual(st, "invalid")

    def test_explicit_renewals_cover_boundaries_and_stop_at_final_deadline(self):
        from wq.config import Config
        start = util.now() - dt.timedelta(days=6)
        middle = start + dt.timedelta(days=7)
        end = middle + dt.timedelta(days=7)
        w = _window(starts_at=start.isoformat(), expires_at=end.isoformat())
        w['debug_authorization']['windows'] = [
            {'starts_at':start.isoformat(), 'expires_at':middle.isoformat(), 'evidence':'Original user authorization'},
            {'starts_at':middle.isoformat(), 'expires_at':end.isoformat(), 'evidence':'Explicit user renewal to fixed deadline'}]
        cfg = Config(w, self.tmp, None)
        for now in (start, middle-dt.timedelta(microseconds=1), middle, end-dt.timedelta(microseconds=1)):
            self.assertEqual(cfg.debug_window('grok', now)[0], 'ok')
        self.assertEqual(cfg.debug_window('grok', end)[0], 'invalid')
        self.assertEqual(cfg.debug_window('devin', middle)[0], 'off')
        self.assertEqual(cfg.debug_window('grok', start-dt.timedelta(seconds=1))[0], 'invalid')

    def test_renewal_rejects_gaps_overlap_missing_evidence_and_unbounded_segments(self):
        import copy
        from wq.config import Config
        start = util.now()-dt.timedelta(days=1)
        middle = start+dt.timedelta(days=7)
        end = middle+dt.timedelta(days=2)
        w = _window(starts_at=start.isoformat(), expires_at=end.isoformat())
        w['debug_authorization']['windows'] = [
            {'starts_at':start.isoformat(),'expires_at':middle.isoformat(),'evidence':'Original user authorization'},
            {'starts_at':middle.isoformat(),'expires_at':end.isoformat(),'evidence':'Explicit fixed renewal'}]
        changes = [([],), (None,), ('evidence',''),
                   ('starts_at',(middle+dt.timedelta(seconds=1)).isoformat()),
                   ('starts_at',(middle-dt.timedelta(seconds=1)).isoformat()),
                   ('expires_at',(middle+dt.timedelta(days=8)).isoformat()),
                   ('expires_at',(end-dt.timedelta(seconds=1)).isoformat())]
        for change in changes:
            broken = copy.deepcopy(w)
            if len(change)==1: broken['debug_authorization']['windows']=change[0]
            else: broken['debug_authorization']['windows'][1][change[0]]=change[1]
            self.assertEqual(Config(broken,self.tmp,None).debug_window('grok')[0],'invalid',change)

    def test_missing_evidence_invalid(self):
        st, info = self._state(_window(evidence=""))
        self.assertEqual((st, "evidence" in info), ("invalid", True))

    def test_malformed_agents_invalid(self):
        w = _window()
        w["debug_authorization"]["agents"] = "grok"  # 字符串而非数组 → fail closed
        st, _ = self._state(w)
        self.assertEqual(st, "invalid")

    def test_unlisted_agent_off(self):
        st, _ = self._state(_window(agents=("devin",)), agent="grok")
        self.assertEqual(st, "off")

    def test_disabled_or_unlimited_false_off(self):
        st, _ = self._state(_window(enabled=False))
        self.assertEqual(st, "off")
        st, _ = self._state(_window(unlimited=False))
        self.assertEqual(st, "off")  # 不豁免 → 回退原预算逻辑

    def test_unconfigured_off(self):
        cfg, _ = make_env(self.tmp)
        self.assertEqual(cfg.debug_window("grok"), ("off", ""))


class TestWindowGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _prompt(self.tmp)

    def _run(self, over, **kw):
        cfg, conn = make_env(self.tmp, over)
        return run_agent(conn, cfg, kw.pop("spec", stub_spec(self.tmp)),
                         "p.md", "t", allow=kw.pop("allow", True), **kw)

    def test_window_waives_remaining_unknown(self):
        # remaining=None 在窗口内豁免；budgets.enabled 仍须为真
        over = _window() | {"models": {"grok": {"enabled": True}},
                            "budgets": {"grok": {"enabled": True, "remaining": None}}}
        self.assertEqual(self._run(over).status, "succeeded")

    def test_window_waives_weekly_quota(self):
        over = GATES | _window()
        over["limits"] = {"grok_calls_per_week": 1}
        cfg, conn = make_env(self.tmp, over)
        s1 = stub_spec(self.tmp, artifact="out/r1.json")
        s2 = stub_spec(self.tmp, artifact="out/r2.json")
        self.assertEqual(run_agent(conn, cfg, s1, "p.md", "t", allow=True).status, "succeeded")
        self.assertEqual(run_agent(conn, cfg, s2, "p.md", "t", allow=True).status, "succeeded")

    def test_expired_window_blocks_despite_budget(self):
        over = _window(start_s=-7200, end_s=-3600) | {
            "models": {"grok": {"enabled": True}},
            "budgets": {"grok": {"enabled": True, "remaining": 5}}}
        self.assertEqual(self._run(over).status, "blocked_authorization")

    def test_model_enabled_still_required(self):
        over = _window() | {"budgets": {"grok": {"enabled": True}}}
        self.assertEqual(self._run(over).status, "blocked_policy")

    def test_budget_enabled_still_required(self):
        over = _window() | {"models": {"grok": {"enabled": True}},
                            "budgets": {"grok": {"enabled": False}}}
        self.assertEqual(self._run(over).status, "blocked_budget")

    def test_allow_still_required(self):
        self.assertEqual(self._run(GATES | _window(), allow=False).status, "blocked_policy")

    def test_pause_still_blocks(self):
        cfg, conn = make_env(self.tmp, GATES | _window())
        store.set_flag(conn, "paused", "1")
        conn.commit()
        out = run_agent(conn, cfg, stub_spec(self.tmp), "p.md", "t", allow=True)
        self.assertEqual(out.status, "blocked_policy")

    def test_unlisted_agent_keeps_budget_gate(self):
        over = _window(agents=("devin",)) | {
            "models": {"grok": {"enabled": True}},
            "budgets": {"grok": {"enabled": True, "remaining": None}}}
        self.assertEqual(self._run(over).status, "blocked_budget")  # 窗口不覆盖 → 原逻辑

    def test_window_does_not_open_simulation(self):
        # 即使 agents 列表里写了 simulation，模拟权限（manual adapter/周上限）不受影响
        cfg, conn = make_env(self.tmp, _window(agents=("grok", "devin", "simulation")))
        store.enqueue_task(conn, "simulation",
                           {"expression": "rank(x)", "config": {"delay": 1}}, "h")
        code, _ = runner.run_once(conn, cfg)
        self.assertEqual(code, 3)
        self.assertEqual(store.list_tasks(conn)[0]["status"], "blocked")


class TestPathValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _prompt(self.tmp)
        self.cfg, self.conn = make_env(self.tmp, GATES)

    def _run(self, spec, prompt="p.md"):
        return run_agent(self.conn, self.cfg, spec, prompt, "t", allow=True)

    def test_artifact_escape_blocked(self):
        spec = stub_spec(self.tmp, artifact="../escape.json")
        self.assertEqual(self._run(spec).status, "blocked_path")

    def test_artifact_absolute_blocked(self):
        spec = stub_spec(self.tmp)
        spec.artifacts = [os.path.join(self.tmp, "out", "r.json")]
        self.assertEqual(self._run(spec).status, "blocked_path")

    def test_artifact_symlink_dir_escape_blocked(self):
        outside = tempfile.mkdtemp()
        os.symlink(outside, os.path.join(self.tmp, "linkout"))
        spec = stub_spec(self.tmp, artifact="linkout/r.json")
        self.assertEqual(self._run(spec).status, "blocked_path")

    def test_prompt_outside_workdir_blocked(self):
        other = tempfile.mkdtemp()
        p = _prompt(other)
        self.assertEqual(self._run(stub_spec(self.tmp), os.path.join(other, p)).status,
                         "blocked_path")

    def test_prompt_missing_blocked(self):
        self.assertEqual(self._run(stub_spec(self.tmp), "nope.md").status, "blocked_path")

    def test_prompt_symlink_blocked(self):
        real = _prompt(self.tmp, "real.md")
        os.symlink(real, os.path.join(self.tmp, "link.md"))
        self.assertEqual(self._run(stub_spec(self.tmp), "link.md").status, "blocked_path")

    def test_stale_artifact_blocked(self):
        os.makedirs(os.path.join(self.tmp, "out"))
        with open(os.path.join(self.tmp, "out", "result.json"), "w") as f:
            json.dump({"old": True}, f)
        out = self._run(stub_spec(self.tmp))
        self.assertEqual(out.status, "blocked_stale_artifact")

    def test_second_run_same_artifact_stale(self):
        spec = stub_spec(self.tmp)
        self.assertEqual(self._run(spec).status, "succeeded")
        self.assertEqual(self._run(spec).status, "blocked_stale_artifact")

    def test_no_declared_artifact_not_success(self):
        spec = stub_spec(self.tmp, artifact=None)  # exit 0 但无声明产物
        out = self._run(spec)
        self.assertEqual(out.status, "artifact_invalid")

    def test_symlink_artifact_not_accepted(self):
        # 预期产物路径已是 symlink（指向 workdir 内文件，通过逃逸检查）→ 陈旧/链接拒绝
        with open(os.path.join(self.tmp, "real.json"), "w") as f:
            json.dump({"x": 1}, f)
        os.makedirs(os.path.join(self.tmp, "out"))
        os.symlink(os.path.join(self.tmp, "real.json"),
                   os.path.join(self.tmp, "out", "result.json"))
        self.assertEqual(self._run(stub_spec(self.tmp)).status, "blocked_stale_artifact")

    def test_symlink_artifact_escaping_blocked(self):
        # symlink 指向 workdir 外 → realpath 逃逸，在静态校验即拒
        os.makedirs(os.path.join(self.tmp, "out"))
        os.symlink("/etc/hostname", os.path.join(self.tmp, "out", "result.json"))
        self.assertEqual(self._run(stub_spec(self.tmp)).status, "blocked_path")


class TestWorkdirAndArgv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_spec_for_workdir_config(self):
        wd = os.path.join(self.tmp, "approved_copy")
        os.makedirs(wd)
        cfg, _ = make_env(self.tmp, {"models": {"grok": {"workdir": wd}}})
        spec = spec_for(cfg, "grok", "p.md", ["o/r.json"])
        self.assertEqual(spec.workdir, wd)
        self.assertIn("{cwd}", spec.argv)  # {cwd} 运行时替换为 workdir

    def test_spec_for_default_workdir_root(self):
        cfg, _ = make_env(self.tmp)
        self.assertEqual(spec_for(cfg, "grok", "p.md", []).workdir, cfg.root)

    def test_extra_args_appended(self):
        cfg, _ = make_env(self.tmp, {"models": {"grok": {
            "enabled": True, "extra_args": ["--output-format", "json", "--tag", "dbg"]}}})
        spec = spec_for(cfg, "grok", "p.md", [])
        self.assertEqual(spec.argv[-2:], ["--tag", "dbg"])

    def test_extra_args_must_be_str_list(self):
        cfg, _ = make_env(self.tmp, {"models": {"grok": {"extra_args": "--shell-ish"}}})
        with self.assertRaises(ValueError):
            spec_for(cfg, "grok", "p.md", [])

    def test_generic_agent_via_argv_template(self):
        # 不写死新模型：models.<x>.argv 模板 + {bin}/{cwd}/{prompt} 占位
        cfg, conn = make_env(self.tmp, {
            "models": {"m1": {"bin": sys.executable, "enabled": True, "timeout_s": 10,
                              "argv": ["{bin}", STUB, "--mode", "ok",
                                       "--artifact", "{cwd}/o.json", "{prompt}"],
                              "extra_args": ["--ignored-by-stub"]}},
            "budgets": {"m1": {"enabled": True, "remaining": 1}}})
        _prompt(self.tmp)
        spec = spec_for(cfg, "m1", "p.md", ["o.json"])
        self.assertEqual(spec.argv[0], sys.executable)
        out = run_agent(conn, cfg, spec, "p.md", "t", allow=True)
        self.assertEqual(out.status, "succeeded")
        self.assertIn("o.json", out.artifacts)

    def test_unknown_agent_rejected(self):
        cfg, _ = make_env(self.tmp)
        with self.assertRaises(ValueError):
            spec_for(cfg, "nosuch", "p.md", [])

    def test_invoke_workdir_end_to_end(self):
        # workdir 指向独立输入副本：prompt 在副本内，产物也落在副本内
        wd = os.path.join(self.tmp, "copy")
        os.makedirs(wd)
        _prompt(wd)
        cfg, conn = make_env(self.tmp, GATES | {"models": {"grok": {
            "enabled": True, "workdir": wd}}})
        spec = spec_for(cfg, "grok", "p.md", ["out/result.json"])
        spec.argv = [sys.executable, STUB, "--mode", "ok",
                     "--artifact", "{cwd}/out/result.json"]
        out = run_agent(conn, cfg, spec, "p.md", "t", allow=True)
        self.assertEqual(out.status, "succeeded")
        self.assertTrue(os.path.isfile(os.path.join(wd, "out", "result.json")))


class TestReporting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_report_shows_window_not_infinite_balance(self):
        cfg, conn = make_env(self.tmp, GATES | _window())
        txt = report.build_report(conn, cfg)
        self.assertIn("不限本地周上限", txt)
        self.assertIn("未知", txt)          # 供应商余额仍 unknown
        self.assertNotIn("无限余额", txt.replace("非无限", ""))

    def test_report_shows_invalid_window(self):
        cfg, conn = make_env(self.tmp, _window(start_s=-7200, end_s=-3600))
        self.assertIn("debug_authorization 无效", report.build_report(conn, cfg))


if __name__ == "__main__":
    unittest.main()
