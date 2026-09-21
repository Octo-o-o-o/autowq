"""仓储层：所有状态变更都过这里，保证尝试留痕、状态机一致。"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
import datetime as dt

from . import util

REMOTE_KINDS = {"simulation", "submission"}     # 远端可能已接受 → 超时/崩溃进 unknown
LOCAL_KINDS = {"agent_call", "reconcile", "import"}

TASK_QUEUED, TASK_CLAIMED, TASK_RUNNING = "queued", "claimed", "running"
TASK_SUCCEEDED, TASK_FAILED, TASK_BLOCKED = "succeeded", "failed", "blocked"
TASK_UNKNOWN, TASK_ABORTED = "unknown", "aborted"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# ---------- families / candidates ----------

def find_family(conn, family_key: str):
    return conn.execute("SELECT * FROM families WHERE family_key=?", (family_key,)).fetchone()


def insert_family(conn, family_id: str | None, family_key: str, hypothesis_id: str | None,
                  origin: str, synthetic: bool, card: dict | None) -> str:
    fid = family_id or _id("fam")
    conn.execute(
        "INSERT INTO families(family_id, family_key, hypothesis_id, origin, synthetic, card_json, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (fid, family_key, hypothesis_id, origin, int(synthetic), _j(card) if card else None, util.now_iso()),
    )
    return fid


def find_candidate_by_hash(conn, config_hash: str):
    return conn.execute("SELECT * FROM candidates WHERE config_hash=?", (config_hash,)).fetchone()


def count_family_candidates(conn, family_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM candidates WHERE family_id=?", (family_id,)).fetchone()["c"]


def insert_candidate(conn, family_id: str, expression: str, config: dict,
                     config_hash: str, synthetic: bool) -> str:
    cid = _id("cand")
    conn.execute(
        "INSERT INTO candidates(candidate_id, family_id, expression, config_json, config_hash, synthetic, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (cid, family_id, expression, _j(config), config_hash, int(synthetic), util.now_iso()),
    )
    return cid


# ---------- tasks ----------

def enqueue_task(conn, kind: str, payload: dict, dedup_key: str | None = None,
                 max_attempts: int = 2) -> tuple[str, bool]:
    """返回 (task_id, created)。dedup_key 命中 → created=False，返回已有任务。"""
    now = util.now_iso()
    if dedup_key:
        row = conn.execute(
            "SELECT task_id FROM tasks WHERE dedup_key=? AND status NOT IN ('failed','aborted')",
            (dedup_key,)).fetchone()
        if row:
            return row["task_id"], False
    tid = _id("task")
    try:
        conn.execute(
            "INSERT INTO tasks(task_id, kind, payload_json, dedup_key, not_before, max_attempts,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (tid, kind, _j(payload), dedup_key, now, max_attempts, now, now),
        )
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT task_id FROM tasks WHERE dedup_key=?", (dedup_key,)).fetchone()
        return row["task_id"], False
    add_attempt(conn, tid, "enqueue", "ok", None)
    return tid, True


def add_attempt(conn, task_id: str, event: str, outcome: str | None, detail) -> None:
    conn.execute(
        "INSERT INTO attempts(task_id, event, outcome, detail_json, created_at) VALUES(?,?,?,?,?)",
        (task_id, event, outcome, _j(detail) if detail is not None else None, util.now_iso()),
    )


def claim_task(conn, owner: str, lease_s: int = 300):
    """原子领取一个到期任务；双触发只有一个成功。返回 task dict 或 None。"""
    now = util.now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status='queued' AND not_before<=? ORDER BY created_at",
            (now.isoformat(timespec="microseconds"),)).fetchall()
        row = None
        for candidate in rows:
            if candidate["attempts"] >= candidate["max_attempts"]:
                finish_task(conn, candidate["task_id"], TASK_BLOCKED,
                            error="task attempt limit reached")
                continue
            deps = json.loads(candidate["payload_json"]).get("depends_on", [])
            if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
                finish_task(conn, candidate["task_id"], TASK_BLOCKED,
                            error="invalid depends_on")
                continue
            states = [conn.execute("SELECT status FROM tasks WHERE task_id=?", (d,)).fetchone()
                      for d in deps]
            if any(s is None or s["status"] in {"failed", "blocked", "aborted", "unknown"}
                   for s in states):
                finish_task(conn, candidate["task_id"], TASK_BLOCKED,
                            {"depends_on": deps}, "dependency unavailable; review before retry")
                continue
            if any(s["status"] != TASK_SUCCEEDED for s in states):
                continue
            row = candidate
            break
        if row is None:
            conn.execute("COMMIT")
            return None
        token = uuid.uuid4().hex
        lease_until = (now + dt.timedelta(seconds=lease_s)).isoformat(timespec="milliseconds")
        conn.execute(
            "UPDATE tasks SET status='claimed', claim_owner=?, claim_token=?, lease_until=?,"
            " attempts=attempts+1, updated_at=? WHERE task_id=?",
            (owner, token, lease_until, util.now_iso(), row["task_id"]))
        add_attempt(conn, row["task_id"], "claim", "ok", {"owner": owner, "lease_until": lease_until})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return dict(row) | {"claim_token": token}


def set_task_running(conn, task_id: str) -> None:
    conn.execute("UPDATE tasks SET status='running', updated_at=? WHERE task_id=?",
                 (util.now_iso(), task_id))


def finish_task(conn, task_id: str, status: str, detail=None, error: str | None = None) -> None:
    conn.execute(
        "UPDATE tasks SET status=?, last_error=?, updated_at=? WHERE task_id=?",
        (status, error, util.now_iso(), task_id))
    add_attempt(conn, task_id, "finish", status, detail)


def requeue_task(conn, task_id: str, not_before: str, reason: str) -> None:
    conn.execute(
        "UPDATE tasks SET status='queued', not_before=?, last_error=?, updated_at=? WHERE task_id=?",
        (not_before, reason, util.now_iso(), task_id))
    add_attempt(conn, task_id, "rate_limit", "requeue", {"not_before": not_before, "reason": reason})


def mark_task_unknown(conn, task_id: str, detail) -> None:
    """远端类任务超时/崩溃：先记 UNKNOWN，对账前不得重发（dedup_key 已挡重复入队）。"""
    conn.execute(
        "UPDATE tasks SET status='unknown', last_error='remote outcome unknown', updated_at=?"
        " WHERE task_id=?", (util.now_iso(), task_id))
    add_attempt(conn, task_id, "finish", "unknown", detail)


def recover_stale(conn, lease_s: int = 300) -> list[dict]:
    """租约过期的 claimed/running 任务：本地类回 queued，远端类进 unknown。"""
    now = util.now_iso()
    rows = conn.execute(
        "SELECT task_id, kind, status FROM tasks"
        " WHERE status IN ('claimed','running') AND lease_until<?", (now,)).fetchall()
    out = []
    for r in rows:
        if r['kind'] == 'brain_simulation':
            from . import brain_jobs
            brain_jobs.setup(conn)
            brain = conn.execute('SELECT state FROM brain_runs WHERE task_id=?', (r['task_id'],)).fetchone()
            if brain and brain['state'] == 'post_started':
                mark_task_unknown(conn, r['task_id'], {'reason':'interrupted POST; reconcile before continuing'})
                out.append({'task_id':r['task_id'],'action':'unknown'})
                continue
        route = conn.execute("SELECT phase,attempt_dir FROM task_routes WHERE task_id=?", (r["task_id"],)).fetchone()
        if route and route["phase"] == "running":
            calls = conn.execute("SELECT pid FROM agent_calls WHERE status='running' AND prompt_file=?",
                                 (os.path.join(route["attempt_dir"], "prompt.md"),)).fetchall()
            if any(c["pid"] and pid_alive(c["pid"]) for c in calls):
                continue
            mark_task_unknown(conn, r["task_id"], {"reason": "routed attempt interrupted; inspect before retry"})
            out.append({"task_id": r["task_id"], "action": "unknown"})
            continue
        if r["kind"] in REMOTE_KINDS:
            conn.execute(
                "UPDATE tasks SET status='unknown', last_error='lease expired; remote state unknown',"
                " updated_at=? WHERE task_id=?", (now, r["task_id"]))
            add_attempt(conn, r["task_id"], "reclaim", "unknown",
                        {"reason": "lease expired on remote-kind task"})
            out.append({"task_id": r["task_id"], "action": "unknown"})
        else:
            conn.execute(
                "UPDATE tasks SET status='queued', claim_owner=NULL, claim_token=NULL,"
                " lease_until=NULL, last_error='reclaimed after lease expiry', updated_at=?"
                " WHERE task_id=?", (now, r["task_id"]))
            add_attempt(conn, r["task_id"], "reclaim", "queued",
                        {"reason": "lease expired; safe to retry local task"})
            out.append({"task_id": r["task_id"], "action": "requeued"})
    return out


def list_tasks(conn, status: str | None = None):
    if status:
        return conn.execute("SELECT * FROM tasks WHERE status=? ORDER BY created_at", (status,)).fetchall()
    return conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()


def unknown_items(conn) -> dict:
    tasks = conn.execute("SELECT task_id, kind, last_error FROM tasks WHERE status='unknown'").fetchall()
    sims = conn.execute("SELECT sim_id, remote_id FROM simulations WHERE status='unknown'").fetchall()
    subs = conn.execute("SELECT submission_id, remote_id FROM submissions WHERE status='unknown'").fetchall()
    return {"tasks": [dict(r) for r in tasks],
            "simulations": [dict(r) for r in sims],
            "submissions": [dict(r) for r in subs]}


# ---------- simulations / submissions / ledger ----------

def insert_simulation(conn, candidate_id: str, remote_id: str | None, source: str,
                      synthetic: bool, status: str, sim: dict,
                      quality: dict | None, evidence: dict | None) -> str:
    sid = _id("sim")
    conn.execute(
        "INSERT INTO simulations(sim_id, candidate_id, remote_id, source, synthetic, status,"
        " stats_json, checks_json, pnl_json, quality_json, evidence_json, observed_at, imported_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, candidate_id, remote_id, source, int(synthetic), status,
         _j(sim.get("stats")) if sim.get("stats") is not None else None,
         _j(sim.get("checks")) if sim.get("checks") is not None else None,
         _j(sim.get("pnl")) if sim.get("pnl") is not None else None,
         _j(quality) if quality is not None else None,
         _j(evidence) if evidence is not None else None,
         sim.get("observed_at"), util.now_iso()))
    return sid


def find_simulation_by_remote(conn, remote_id: str):
    return conn.execute("SELECT * FROM simulations WHERE remote_id=?", (remote_id,)).fetchone()


def add_submission(conn, sim_id: str, remote_id: str | None, status: str, receipt: dict | None) -> str:
    sid = _id("sub")
    conn.execute(
        "INSERT INTO submissions(submission_id, sim_id, remote_id, status, receipt_json, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (sid, sim_id, remote_id, status, _j(receipt) if receipt else None, util.now_iso(), util.now_iso()))
    return sid


def add_payment(conn, submission_id: str | None, kind: str, amount: float,
                currency: str, occurred_at: str, evidence: str) -> str:
    pid = _id("pay")
    conn.execute(
        "INSERT INTO payments(payment_id, submission_id, kind, amount, currency, occurred_at, evidence)"
        " VALUES(?,?,?,?,?,?,?)",
        (pid, submission_id, kind, amount, currency, occurred_at, evidence))
    return pid


def add_expense(conn, kind: str, amount: float, unit: str, occurred_at: str, note: str) -> str:
    eid = _id("exp")
    conn.execute(
        "INSERT INTO expenses(expense_id, kind, amount, unit, occurred_at, note) VALUES(?,?,?,?,?,?)",
        (eid, kind, amount, unit, occurred_at, note))
    return eid


# ---------- agent calls / incidents ----------

def live_agent_calls(conn, agent: str | None = None):
    if agent:
        return conn.execute(
            "SELECT * FROM agent_calls WHERE agent=? AND status='running'", (agent,)).fetchall()
    return conn.execute("SELECT * FROM agent_calls WHERE status='running'").fetchall()


def pid_alive(pid: int) -> bool:
    # 先回收自己的 zombie 子进程（waitpid 对非子进程抛 ChildProcessError，忽略）
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_agent_calls(conn) -> list[dict]:
    """进程已死但状态仍 running → crashed。PID 复用是已知风险，故同时校验
    进程组与启动时间暂不实现：保守处理为只在 pid 明确不存在时标 crashed。"""
    out = []
    for r in live_agent_calls(conn):
        pid = r["pid"]
        if pid is not None and not pid_alive(pid):
            conn.execute(
                "UPDATE agent_calls SET status='crashed', finished_at=?,"
                " detail=COALESCE(detail,'') || ' | process gone' WHERE call_id=?",
                (util.now_iso(), r["call_id"]))
            out.append({"call_id": r["call_id"], "action": "crashed"})
    return out


def start_agent_call(conn, agent: str, purpose: str, incident_id: str | None,
                     pid: int, prompt_file: str, log_path: str) -> str:
    cid = _id("call")
    conn.execute(
        "INSERT INTO agent_calls(call_id, agent, purpose, incident_id, status, pid, prompt_file,"
        " log_path, week, started_at) VALUES(?,?,?,?,'running',?,?,?,?,?)",
        (cid, agent, purpose, incident_id, pid, prompt_file, log_path,
         util.iso_week(), util.now_iso()))
    return cid


def finish_agent_call(conn, call_id: str, status: str, exit_code: int | None,
                      artifacts=None, detail: str | None = None) -> None:
    conn.execute(
        "UPDATE agent_calls SET status=?, exit_code=?, artifacts_json=?, detail=?, finished_at=?"
        " WHERE call_id=?",
        (status, exit_code, _j(artifacts) if artifacts is not None else None,
         detail, util.now_iso(), call_id))


def record_agent_call_terminal(conn, agent: str, purpose: str, incident_id: str | None,
                               status: str, detail: str) -> str:
    """被闸门拦下的调用也入账（blocked_*），便于审计真实尝试次数。"""
    cid = _id("call")
    conn.execute(
        "INSERT INTO agent_calls(call_id, agent, purpose, incident_id, status, detail, week,"
        " started_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (cid, agent, purpose, incident_id, status, detail, util.iso_week(),
         util.now_iso(), util.now_iso()))
    return cid


def agent_calls_this_week(conn, agent: str, week: str | None = None) -> int:
    week = week or util.iso_week()
    return conn.execute(
        "SELECT COUNT(*) c FROM agent_calls WHERE agent=? AND week=? AND status NOT LIKE 'blocked%'",
        (agent, week)).fetchone()["c"]


def get_incident(conn, incident_id: str):
    return conn.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()


def ensure_incident(conn, incident_id: str, description: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO incidents(incident_id, description, created_at, updated_at)"
        " VALUES(?,?,?,?)", (incident_id, description, util.now_iso(), util.now_iso()))


def bump_incident_attempt(conn, incident_id: str) -> None:
    conn.execute("UPDATE incidents SET attempts=attempts+1, updated_at=? WHERE incident_id=?",
                 (util.now_iso(), incident_id))


# ---------- flags / account ----------

def set_flag(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO state_flags(key, value, updated_at) VALUES(?,?,?)",
                 (key, value, util.now_iso()))


def get_flag(conn, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM state_flags WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def is_paused(conn) -> bool:
    return get_flag(conn, "paused", "0") == "1"


def set_account_stage(conn, stage: str, evidence: str) -> None:
    conn.execute("INSERT INTO account_status(stage, evidence, changed_at) VALUES(?,?,?)",
                 (stage, evidence, util.now_iso()))


def current_account_stage(conn) -> dict:
    r = conn.execute("SELECT stage, evidence, changed_at FROM account_status ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else {"stage": "UNKNOWN", "evidence": "never recorded", "changed_at": None}
