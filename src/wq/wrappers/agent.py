"""受限单次模型调用 wrapper。

硬要求（逐条对应建设任务）：
- 单实例锁：fcntl.flock 非阻塞，拿不到即拒（locked）。
- 明确工作目录：Popen(cwd=workdir)，可用 models.<agent>.workdir 指向已批准的
  独立输入副本；凭证目录 private_dir 在工作区之外（注意：cwd/0700 是卫生措施，
  子进程同 uid，不构成沙箱隔离）。
- stdin 关闭：stdin=DEVNULL，杜绝交互等待。
- 进程组超时/清理：start_new_session=True，超时先 SIGTERM 再 SIGKILL 整组。
- 退出码 + 真实产物校验：exit 0 不算完；至少声明一个 artifact，且每个产物必须
  在 workdir 内、非 symlink、运行前不存在（拒绝复用旧文件）、JSON 可解析。
- prompt/artifact 路径校验：prompt 须落在 workdir 内；artifact 为相对路径，
  realpath 不得逃逸 workdir。
- 活调用去重：DB 中同 agent 仍有 running 且 pid 存活 → 拒绝；pid 死 → 先标 crashed。
- 故障总尝试上限：同一 incident_id 的完成调用数跨会话累计，超上限拒绝。
- 预算闸门：models.<agent>.enabled 与 budgets.<agent>.enabled 都为真才放行；
  remaining=None 视为未知 → 阻断。debug_authorization 窗口有效且覆盖该 agent 时
  仅豁免 remaining 数值与本地周上限（供应商余额仍未知）；窗口已启用但无效 →
  blocked_authorization。暂停/allow/锁/活调用/超时/故障上限不受窗口影响。
  本次交付默认全部关闭，仅 stub 测契约。
- 日志卫生：stdout/stderr 进 private_dir/logs（0700）；入库 detail 先过 _scrub。
- 禁止 Agent 循环派单：wrapper 只由 runner/CLI 调用，子进程不获得本项目写权限之外的
  任何排队入口；prompt 契约也要求模型输出仅 JSON 数据。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field

from .. import store, util

_SCRUB_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+"),
    re.compile(r"(?i)((?:token|api[_-]?key|cookie|password|secret)\s*[=:]\s*)\S+"),
]


def _scrub(text: str) -> str:
    for p in _SCRUB_PATTERNS:
        text = p.sub(r"\1***", text)
    return text[:2000]


@dataclass
class AgentSpec:
    name: str
    argv: list[str]                  # 完整命令行模板；{cwd} 会被替换
    workdir: str
    timeout_s: int = 900
    artifacts: list[str] = field(default_factory=list)  # 相对 workdir 的预期产物
    artifacts_json: bool = True      # 产物须为可解析 JSON
    terminal_protocol: str | None = None
    pipe_logs: bool = False


@dataclass
class CallOutcome:
    status: str          # succeeded|failed|timeout|artifact_invalid|blocked_*|crashed
    exit_code: int | None
    detail: str
    call_id: str | None = None
    artifacts: dict = field(default_factory=dict)


def _acquire_lock(run_dir: str, name: str):
    util.ensure_dir(run_dir)
    path = os.path.join(run_dir, f"agent-{name}.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == 'win32':
            import msvcrt
            if os.path.getsize(path) == 0:
                os.write(fd, b'1')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        os.close(fd)
        return None
    return fd  # 调用方持有至结束；进程死亡内核自动释放


def _check_paths(spec: AgentSpec, prompt_file: str) -> tuple[bool, str]:
    """调用前静态校验：workdir 为真实目录；prompt 落在 workdir 内、是存在的
    普通文件且非 symlink；artifact 为不逃逸 workdir 的相对路径。"""
    wd = spec.workdir
    if not wd or not os.path.isabs(wd):
        return False, f"workdir 须为绝对路径: {wd!r}"
    if os.path.islink(wd) or not os.path.isdir(wd):
        return False, f"workdir 不存在或是 symlink: {wd}"
    wd_real = os.path.realpath(wd)

    pf = os.path.expanduser(prompt_file)
    if not os.path.isabs(pf):
        pf = os.path.join(wd_real, pf)
    if os.path.commonpath([wd_real, os.path.realpath(pf)]) != wd_real:
        return False, f"prompt 逃逸 workdir: {prompt_file}"
    if os.path.islink(pf) or not os.path.isfile(pf):
        return False, f"prompt 缺失、非普通文件或是 symlink: {prompt_file}"

    for rel in spec.artifacts:
        if os.path.isabs(rel):
            return False, f"artifact 须为相对路径: {rel}"
        ap = os.path.join(wd_real, rel)
        if os.path.commonpath([wd_real, os.path.realpath(ap)]) != wd_real:
            return False, f"artifact 逃逸 workdir: {rel}"
    return True, ""


def _verify_artifacts(spec: AgentSpec) -> tuple[bool, dict, str]:
    """完成判定：exit 0 之外还须至少一个声明产物，且每个是 workdir 内
    新建的普通文件（非 symlink），声明为 JSON 的可解析。"""
    if not spec.artifacts:
        return False, {}, "未声明任何产物；exit 0 不足以认定真实调用成功"
    wd_real = os.path.realpath(spec.workdir)
    found = {}
    for rel in spec.artifacts:
        p = os.path.join(spec.workdir, rel)
        if os.path.islink(p) or not os.path.isfile(p):
            return False, found, f"artifact 缺失、非普通文件或是 symlink: {rel}"
        if os.path.commonpath([wd_real, os.path.realpath(p)]) != wd_real:
            return False, found, f"artifact 逃逸 workdir: {rel}"
        if spec.artifacts_json:
            try:
                with open(p, encoding="utf-8") as f:
                    found[rel] = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                return False, found, f"artifact 非有效 JSON: {rel}: {e}"
        else:
            found[rel] = {"size": os.path.getsize(p)}
    return True, found, ""


def run_agent(conn, cfg, spec: AgentSpec, prompt_file: str, purpose: str,
              incident_id: str | None = None, allow: bool = False) -> CallOutcome:
    """执行一次 gated 调用。所有阻断路径也落 agent_calls 账。"""
    def blocked(status: str, detail: str) -> CallOutcome:
        cid = store.record_agent_call_terminal(conn, spec.name, purpose, incident_id, status, detail)
        conn.commit()
        return CallOutcome(status, None, detail, call_id=cid)

    model_cfg = cfg.model(spec.name)
    if not model_cfg.get("enabled"):
        return blocked("blocked_policy", f"models.{spec.name}.enabled=false；未获授权不发起调用")
    if store.is_paused(conn):
        return blocked("blocked_policy", "已暂停（wq pause）；恢复需 wq resume")

    # 首周 debug 授权窗口：有效且覆盖才豁免 remaining/周上限；窗口已启用但无效 → 阻断。
    win_state, win_info = cfg.debug_window(spec.name)
    if win_state == "invalid":
        return blocked("blocked_authorization", f"debug_authorization 无效: {win_info}")
    unlimited = win_state == "ok"

    budget = cfg.budget(spec.name)
    if not budget.get("enabled"):
        return blocked("blocked_budget", f"budgets.{spec.name} 未启用（预算未知不发调用）")
    if not unlimited:
        if budget.get("remaining") is None:
            return blocked("blocked_budget", f"budgets.{spec.name}.remaining 未知 → 阻断")
        remaining = float(budget["remaining"])
        if budget.get("unit") == "calls":
            used = conn.execute(
                "SELECT COUNT(*) FROM agent_calls WHERE agent=? AND pid IS NOT NULL AND started_at>=?",
                (spec.name, budget.get("as_of", ""))).fetchone()[0]
            remaining -= used
        if remaining <= 0:
            return blocked("blocked_budget", f"budgets.{spec.name} 已耗尽")
    if not allow:
        return blocked("blocked_policy", "缺 --allow 确认；wrapper 不被隐式触发")

    ok, why = _check_paths(spec, prompt_file)
    if not ok:
        return blocked("blocked_path", why)

    if not unlimited:
        limit_keys = {"grok": "grok_calls_per_week", "devin": "devin_tickets_per_week"}
        limit = int(cfg.get("limits", limit_keys.get(spec.name, f"{spec.name}_calls_per_week"),
                            default=0) or 0)
        used = store.agent_calls_this_week(conn, spec.name)
        if limit and used >= limit:
            return blocked("blocked_quota",
                           f"{spec.name} 本周已用 {used}/{limit}（含失败重试）")

    if incident_id:
        store.ensure_incident(conn, incident_id, purpose)
        inc = store.get_incident(conn, incident_id)
        cap = int(cfg.get("limits", "max_repair_attempts_per_incident", default=2))
        if inc["attempts"] >= cap:
            return blocked("blocked_attempt_cap",
                           f"incident {incident_id} 累计尝试 {inc['attempts']}/{cap}，"
                           "已达上限；保存证据并暂停该工单")
        if inc["status"] != "open":
            return blocked("blocked_policy", f"incident {incident_id} 状态 {inc['status']}")

    # 活调用去重：同 agent 仍有存活 running → 拒；pid 已死 → 先标 crashed。
    for r in store.live_agent_calls(conn, spec.name):
        if r["pid"] is not None and store.pid_alive(r["pid"]):
            return blocked("blocked_live_dup",
                           f"{spec.name} 已有活调用 {r['call_id']} pid={r['pid']}，不重复启动")

    lock_fd = _acquire_lock(cfg.run_dir, spec.name)
    if lock_fd is None:
        return blocked("blocked_locked", f"{spec.name} 锁被占用，另一实例进行中")

    try:
        store.recover_agent_calls(conn)  # 死掉的 running 先落 crashed
        if not unlimited and budget.get("unit") == "calls":
            used = conn.execute(
                "SELECT COUNT(*) FROM agent_calls WHERE agent=? AND pid IS NOT NULL AND started_at>=?",
                (spec.name, budget.get("as_of", ""))).fetchone()[0]
            if used >= float(budget["remaining"]):
                return blocked("blocked_budget", f"{spec.name} 本地调用预算耗尽")
        # 锁内临近启动处再查：声明的产物若已存在即为旧文件，拒绝复用。
        for rel in spec.artifacts:
            if os.path.lexists(os.path.join(spec.workdir, rel)):
                return blocked("blocked_stale_artifact",
                               f"预期产物 {rel} 已存在；拒绝复用旧文件，先清理或换 workdir")
        log_dir = util.ensure_dir(os.path.join(cfg.ensure_private_dir(), "logs"), 0o700)
        call_id_hint = util.now().strftime("%Y%m%dT%H%M%S%f")
        log_path = os.path.join(log_dir, f"{spec.name}-{call_id_hint}.log")
        usage_export = os.path.join(spec.workdir, ".usage", f"{spec.name}-{call_id_hint}.json")
        if spec.name == "devin":
            util.ensure_dir(os.path.dirname(usage_export), 0o700)
        argv = [a.replace("{cwd}", spec.workdir).replace("{usage_export}", usage_export)
                for a in spec.argv]
        logf = open(log_path, "wb")
        os.chmod(log_path, 0o600)
        try:
            proc = subprocess.Popen(
                argv, cwd=spec.workdir, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if spec.pipe_logs else logf,
                stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            logf.close()
            return blocked("blocked_spawn", f"启动失败: {e}")

        drain = None
        if spec.pipe_logs:
            def copy_output():
                while chunk := proc.stdout.read(65536):
                    logf.write(chunk)
                    logf.flush()
            drain = threading.Thread(target=copy_output, daemon=True)
            drain.start()
        cid = store.start_agent_call(conn, spec.name, purpose, incident_id,
                                     proc.pid, prompt_file, log_path)
        conn.commit()
        try:
            proc.wait(timeout=spec.timeout_s)
        except subprocess.TimeoutExpired:
            util.kill_tree(proc.pid, force=False)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                util.kill_tree(proc.pid, force=True)
                proc.wait()
            store.finish_agent_call(conn, cid, "timeout", proc.returncode,
                                    detail=f"超过 {spec.timeout_s}s，已终止进程组")
            if incident_id:
                store.bump_incident_attempt(conn, incident_id)
            conn.commit()
            return CallOutcome("timeout", proc.returncode,
                               f"timeout {spec.timeout_s}s; process group killed", call_id=cid)
        finally:
            if drain:
                drain.join(timeout=2)
                if drain.is_alive():
                    # CLI 已退出但后代仍持有 stdout；清理本次调用的进程组。
                    util.kill_tree(proc.pid, force=False)
                    drain.join(timeout=1)
                    if drain.is_alive():
                        util.kill_tree(proc.pid, force=True)
                        drain.join(timeout=1)
                proc.stdout.close()
            logf.close()

        ok, artifacts, why = _verify_artifacts(spec)
        if ok and spec.terminal_protocol:
            ok, why = _verify_terminal(log_path, spec.terminal_protocol)
        row = conn.execute("SELECT status FROM agent_calls WHERE call_id=?", (cid,)).fetchone()
        if row["status"] == "aborted" or store.is_paused(conn):
            store.finish_agent_call(conn, cid, "aborted", proc.returncode, detail="用户暂停，保持中止状态")
            conn.commit()
            return CallOutcome("aborted", proc.returncode, "用户暂停", call_id=cid)
        if proc.returncode == 0 and ok:
            status, detail = "succeeded", "exit 0 + artifacts verified"
        elif proc.returncode != 0:
            status, detail = "failed", f"exit {proc.returncode}"
        else:
            status, detail = "artifact_invalid", f"exit 0 但 {why}"
        store.finish_agent_call(conn, cid, status, proc.returncode,
                                artifacts=artifacts or None, detail=_scrub(detail))
        if incident_id:
            store.bump_incident_attempt(conn, incident_id)
        conn.commit()
        return CallOutcome(status, proc.returncode, detail, call_id=cid, artifacts=artifacts)
    finally:
        os.close(lock_fd)


def _verify_terminal(log_path: str, protocol: str) -> tuple[bool, str]:
    """只判定 CLI 终态字段，不返回内部推理。"""
    with open(log_path, encoding="utf-8", errors="replace") as f:
        data = f.read()
    objects = []
    decoder = json.JSONDecoder()
    for start in [0] + [m.end() for m in re.finditer(r"\n", data)]:
        if data[start:start + 1] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(data[start:])
            if isinstance(obj, dict):
                objects.append(obj)
        except ValueError:
            continue
    if protocol == "cursor":
        good = any(o.get("type") == "result" and o.get("subtype") == "success"
                   and not o.get("is_error", False) for o in objects)
    elif protocol == "grok":
        good = any(o.get("stopReason") == "end_turn" for o in objects)
    else:
        return False, f"未支持的 terminal_protocol: {protocol}"
    return good, "CLI 终态已核验" if good else f"缺少 {protocol} 成功终态"


def spec_for(cfg, agent: str, prompt_file: str, artifacts: list[str] | None) -> AgentSpec:
    """组命令模板。grok/devin 用本机已核验形态（2026-09-20 核验 help/version，
    未发模型请求）；其他模型不写死，由 models.<name>.argv 模板提供
    （{bin}/{cwd}/{prompt} 占位）。models.<name>.extra_args 追加结构化 argv
    （不经 shell）；workdir 可用 models.<name>.workdir 指向已批准输入副本，
    默认项目根。不替模型追加任何自动批准/绕过确认的参数。"""
    m = cfg.model(agent)
    bin_ = m.get("bin") or agent
    if agent == "grok":
        argv = [bin_, "--cwd", "{cwd}", "--no-subagents", "--max-turns", "12",
                "--output-format", "json", "--prompt-file", prompt_file]
    elif agent == "devin":
        argv = [bin_, "-p", "--prompt-file", prompt_file]
    else:
        tmpl = m.get("argv")
        if not (isinstance(tmpl, list) and tmpl
                and all(isinstance(a, str) for a in tmpl)):
            raise ValueError(f"未知 agent: {agent}；需 models.{agent}.argv 模板")
        argv = [a.replace("{bin}", bin_).replace("{prompt}", prompt_file)
                for a in tmpl]
    extra = m.get("extra_args") or []
    if not (isinstance(extra, list) and all(isinstance(a, str) for a in extra)):
        raise ValueError(f"models.{agent}.extra_args 须为字符串数组（结构化 argv，不经 shell）")
    if agent == "devin" and "--export" not in extra:
        argv += ["--export", "{usage_export}"]
    workdir = cfg.resolve(m["workdir"]) if m.get("workdir") else cfg.root
    return AgentSpec(name=agent, argv=argv + list(extra), workdir=workdir,
                     timeout_s=int(m.get("timeout_s", 900)),
                     artifacts=artifacts or [])
