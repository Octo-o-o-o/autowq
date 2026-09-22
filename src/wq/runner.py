"""run-once：单并发调度。领取一个到期任务，分派，落账，返回真实退出码。"""
from __future__ import annotations

import json
import os
import datetime as dt

from . import store, util
from .errors import AdapterError, BLOCKED, ERR, NOT_IMPLEMENTED, OK, PAUSED, WqExit
from .adapters.brain import build_adapter
from .wrappers import agent as agent_runner


def _sims_this_week(conn) -> int:
    week = util.iso_week()
    n = 0
    for r in conn.execute("SELECT imported_at FROM simulations WHERE synthetic=0").fetchall():
        try:
            if util.iso_week(util.parse_iso(r["imported_at"])) == week:
                n += 1
        except (ValueError, TypeError):
            continue
    return n


def dispatch_task(conn, cfg, task: dict) -> tuple[str, dict, str | None]:
    """返回 (outcome_status, detail, error)。不抛 AdapterError。"""
    kind = task["kind"]
    payload = json.loads(task["payload_json"])
    adapter = build_adapter(cfg)
    try:
        if kind == "brain_feedback":
            from .feedback import step
            return step(conn, cfg, task, payload)
        if kind == "brain_submission":
            from .brain_submission import step
            return step(conn, cfg, task, payload)
        if kind == "brain_simulation":
            from .brain_jobs import step
            return step(conn, cfg, task, payload)
        if kind == "import":
            from . import importer
            res = importer.import_result_obj(conn, cfg, payload["document"],
                                             allow_real=bool(payload.get("real")))
            return "succeeded", res, None
        if kind == "simulation":
            cap = int(cfg.get("limits", "sims_per_week", default=24))
            used = _sims_this_week(conn)
            if used >= cap:
                return "blocked", {"reason": f"本周模拟 {used}/{cap} 已达本地上限"}, "weekly sim cap"
            receipt = adapter.simulate(payload)
            return "succeeded", {"receipt": receipt,
                                 "note": "已接受≠最终结果；等待/轮询由普通程序另行负责"}, None
        if kind == "submission":
            receipt = adapter.submit(payload)
            return "succeeded", {"receipt": receipt}, None
        if kind == "reconcile":
            res = adapter.reconcile(store.unknown_items(conn))
            return "succeeded", res, None
        if kind == "agent_call":
            if payload.get("routing"):
                from .routing import dispatch_routed
                return dispatch_routed(conn, cfg, task, payload)
            return _dispatch_agent(conn, cfg, payload)
        return "failed", {"reason": f"未知任务类型 {kind}"}, f"unknown kind {kind}"
    except AdapterError as e:
        if e.kind == AdapterError.AUTH:
            store.set_flag(conn, "paused", "1")
            store.set_flag(conn, "pause_origin", "auth")
            store.set_flag(conn, "pause_reason", f"auth: {e}")
            return "blocked", {"auth": str(e), "action": "已自动暂停，需本人处理身份/权限"}, str(e)
        if e.kind == AdapterError.RATE_LIMIT:
            return "rate_limited", {"retry_after": e.retry_after}, str(e)
        if e.kind == AdapterError.QUOTA:
            return "blocked", {"quota": str(e)}, str(e)
        if e.kind in (AdapterError.UNKNOWN_REMOTE, AdapterError.NETWORK):
            return "unknown", {"reason": str(e)}, str(e)
        if e.kind == AdapterError.NOT_IMPLEMENTED:
            return "not_implemented", {"reason": str(e)}, str(e)
        return "blocked", {"policy": str(e)}, str(e)
    except WqExit as e:
        return "failed", {"error": str(e)}, str(e)
    except (KeyError, ValueError, TypeError, OSError) as e:
        if kind == 'brain_submission':
            row = conn.execute('SELECT state FROM brain_submissions WHERE task_id=?', (task['task_id'],)).fetchone()
            if row and row[0] in ('post_started', 'polling', 'verifying'):
                return 'unknown', {'error': str(e)}, '提交后证据异常，需只读对账'
        return "failed", {"error": str(e)}, str(e)


def _dispatch_agent(conn, cfg, payload: dict) -> tuple[str, dict, str | None]:
    try:
        spec = agent_runner.spec_for(cfg, payload["agent"], payload["prompt_file"],
                                     payload.get("artifacts"))
    except ValueError as e:
        return "failed", {"reason": str(e)}, str(e)
    out = agent_runner.run_agent(conn, cfg, spec,
                                 prompt_file=payload["prompt_file"],
                                 purpose=payload.get("purpose", "unspecified"),
                                 incident_id=payload.get("incident_id"),
                                 allow=bool(payload.get("allow")))
    detail = {"call_id": out.call_id, "call_status": out.status, "detail": out.detail}
    if out.status == "succeeded":
        if payload.get("require_completed"):
            states = {name: obj.get("status") if isinstance(obj, dict) else None
                      for name, obj in out.artifacts.items()}
            detail["artifact_statuses"] = states
            if not states or any(s != "completed" for s in states.values()):
                return "blocked", detail, "artifact status is not completed; process success is not task completion"
        return "succeeded", detail, None
    if out.status.startswith("blocked"):
        return "blocked", detail, out.detail
    if out.status == "timeout":
        return "failed", detail, out.detail   # 本地进程超时是确定失败，不是远端未知
    return "failed", detail, out.detail


def run_once(conn, cfg, lease_s: int = 300) -> tuple[int, list[str]]:
    lock = agent_runner._acquire_lock(cfg.run_dir, "runner")
    if lock is None:
        return OK, ["idle: another runner owns the queue"]
    try:
        return _run_once(conn, cfg, lease_s)
    finally:
        from .progress import write_progress
        try:
            write_progress(conn, cfg)
        finally:
            os.close(lock)


def _run_once(conn, cfg, lease_s: int) -> tuple[int, list[str]]:
    lines: list[str] = []
    recovered_calls = store.recover_agent_calls(conn)
    recovered_tasks = store.recover_stale(conn, lease_s)
    conn.commit()
    for r in recovered_calls + recovered_tasks:
        lines.append(f"recover: {r}")

    from . import autopilot
    if store.is_paused(conn):
        recovered = autopilot.auto_resume_after_auth(conn, cfg)
        conn.commit()
        if recovered is None or store.is_paused(conn):
            lines.append(f"paused: {store.get_flag(conn, 'pause_reason', '')}；恢复用 `wq resume`")
            return PAUSED, lines
        conn.commit()
        lines.append('auth pause automatically recovered from macOS Keychain; resumed safe reads: '
                     + (', '.join(recovered) if recovered else 'none'))

    autopilot.tick(conn, cfg)

    task = store.claim_task(conn, owner=f"wq-{os.getpid()}", lease_s=lease_s)
    conn.commit()
    if task is None:
        lines.append("idle: 无到期任务")
        return OK, lines

    store.set_task_running(conn, task["task_id"])
    outcome, detail, error = dispatch_task(conn, cfg, task)

    if outcome == "retry_scheduled":
        lines.append(f"task {task['task_id']} 等待重试/备用渠道：{error}")
        code = OK
    elif outcome == "rate_limited":
        ra = float((detail or {}).get("retry_after") or 0)
        max_wait = float(cfg.get("limits", "rate_limit_max_wait_s", default=900))
        if ra > max_wait:
            store.finish_task(conn, task["task_id"], store.TASK_FAILED,
                              detail, f"rate-limit 等待 {ra}s 超过上限 {max_wait}s")
            lines.append(f"task {task['task_id']} failed: retry-after 超上限")
            code = BLOCKED
        else:
            nb = (util.now() + dt.timedelta(seconds=ra)).isoformat(timespec="milliseconds")
            store.requeue_task(conn, task["task_id"], nb, error or "rate_limited")
            lines.append(f"task {task['task_id']} rate-limited → {nb} 后重试（Retry-After 已遵守）")
            code = OK
    elif outcome == "unknown":
        store.mark_task_unknown(conn, task["task_id"], detail)
        lines.append(f"task {task['task_id']} UNKNOWN：远端可能已接受，对账前禁止重发"
                     "（wq reconcile / --resolve）")
        code = OK
    elif outcome == "not_implemented":
        store.finish_task(conn, task["task_id"], store.TASK_BLOCKED, detail, error)
        lines.append(f"task {task['task_id']} NOT_IMPLEMENTED: {error}")
        code = NOT_IMPLEMENTED
    elif outcome == "blocked":
        store.finish_task(conn, task["task_id"], store.TASK_BLOCKED, detail, error)
        lines.append(f"task {task['task_id']} BLOCKED: {error}")
        code = BLOCKED
    elif outcome == "succeeded":
        store.finish_task(conn, task["task_id"], store.TASK_SUCCEEDED, detail)
        lines.append(f"task {task['task_id']} succeeded: {json.dumps(detail, ensure_ascii=False)[:400]}")
        code = OK
    else:
        store.finish_task(conn, task["task_id"], store.TASK_FAILED, detail, error)
        lines.append(f"task {task['task_id']} failed: {error}")
        code = ERR
    conn.commit()
    return code, lines
