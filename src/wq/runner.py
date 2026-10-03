"""run-once 调度：协调与平台任务串行，模型调用按配置并行；单 runner 锁。

- 协调段：autopilot/history/framework 等状态机只在主线程 tick 中推进；
  轮次的创建、推进、关闭全部串行落账，多泳道只是让它们的模型调用同时在外执行。
- 串行段：非 agent_call 任务逐个领取分派。模拟/提交/反馈/对账保持
  sim_concurrency=1、单传输槽、24 小时提交限额与 UNKNOWN 冻结语义不变。
- 并行段：agent_call 任务由 worker 线程分派，每个 worker 用独立 SQLite 连接
  （WAL + busy_timeout）。同一 provider 仍由 flock 与活调用去重串行；
  渠道忙时任务 defer 稍后领取，不消耗重试次数，也不阻塞其它渠道。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import datetime as dt

from . import store, util
from .errors import AdapterError, BLOCKED, ERR, NOT_IMPLEMENTED, OK, PAUSED, WqExit
from .adapters.brain import build_adapter
from .wrappers import agent as agent_runner

PROVIDER_BUSY_S = 90          # 同渠道已有在途调用时，任务多久后重新到期
SUPERVISOR_POLL_S = 15        # supervisor 无完成事件时的协调/领取节奏
SERIAL_BATCH = 16             # 每轮协调间隙最多处理的串行任务数


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
        if kind in ('research_evidence','research_evidence_verify','research_reassess'):
            from . import research_framework
            return research_framework.dispatch(conn,cfg,task,payload)
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
            return _dispatch_agent(conn, cfg, payload, task)
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


def _defer_task(conn, task, seconds, reason):
    """本地资源忙（同渠道已有在途调用等）：不消耗尝试次数，稍后自动重新领取。"""
    nb = (util.now() + dt.timedelta(seconds=seconds)).isoformat(timespec='milliseconds')
    conn.execute("UPDATE tasks SET status='queued',not_before=?,last_error=?,attempts=MAX(attempts-1,0),updated_at=? WHERE task_id=?",
                 (nb, reason, util.now_iso(), task['task_id']))
    store.add_attempt(conn, task['task_id'], 'defer', 'scheduled', {'not_before': nb, 'reason': reason})
    return 'retry_scheduled', {'not_before': nb}, reason


def _dispatch_agent(conn, cfg, payload: dict, task=None) -> tuple[str, dict, str | None]:
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
    if task is not None and out.status in ("blocked_live_dup", "blocked_locked"):
        return _defer_task(conn, task, PROVIDER_BUSY_S,
                           f"{spec.name} 已有在途调用；稍后自动重新领取，不消耗重试次数")
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


def _settle(conn, cfg, task, outcome, detail, error) -> tuple[int, list[str]]:
    """把分派结果落成任务终态或重排；返回 (退出码, 日志行)。串行与并行路径共用。"""
    tid = task["task_id"]
    lines: list[str] = []
    if outcome == "retry_scheduled":
        lines.append(f"task {tid} 等待重试/备用渠道：{error}")
        code = OK
    elif outcome == "rate_limited":
        ra = float((detail or {}).get("retry_after") or 0)
        max_wait = float(cfg.get("limits", "rate_limit_max_wait_s", default=900))
        if ra > max_wait:
            store.finish_task(conn, tid, store.TASK_FAILED, detail,
                              f"rate-limit 等待 {ra}s 超过上限 {max_wait}s")
            lines.append(f"task {tid} failed: retry-after 超上限")
            code = BLOCKED
        else:
            nb = (util.now() + dt.timedelta(seconds=ra)).isoformat(timespec="milliseconds")
            store.requeue_task(conn, tid, nb, error or "rate_limited")
            lines.append(f"task {tid} rate-limited → {nb} 后重试（Retry-After 已遵守）")
            code = OK
    elif outcome == "unknown":
        store.mark_task_unknown(conn, tid, detail)
        lines.append(f"task {tid} UNKNOWN：远端可能已接受，对账前禁止重发"
                     "（wq reconcile / --resolve）")
        code = OK
    elif outcome == "not_implemented":
        store.finish_task(conn, tid, store.TASK_BLOCKED, detail, error)
        lines.append(f"task {tid} NOT_IMPLEMENTED: {error}")
        code = NOT_IMPLEMENTED
    elif outcome == "blocked":
        store.finish_task(conn, tid, store.TASK_BLOCKED, detail, error)
        lines.append(f"task {tid} BLOCKED: {error}")
        code = BLOCKED
    elif outcome == "succeeded":
        store.finish_task(conn, tid, store.TASK_SUCCEEDED, detail)
        lines.append(f"task {tid} succeeded: {json.dumps(detail, ensure_ascii=False)[:400]}")
        code = OK
    else:
        store.finish_task(conn, tid, store.TASK_FAILED, detail, error)
        lines.append(f"task {tid} failed: {error}")
        code = ERR
    return code, lines


def _agent_worker(cfg, task, results):
    """模型调用工作线程：独立 SQLite 连接完成分派与落账，结果回传 supervisor。"""
    from . import db as _db
    conn = None
    tid = task["task_id"]
    try:
        conn = _db.connect(cfg.db_path)
        store.set_task_running(conn, tid)
        outcome, detail, error = dispatch_task(conn, cfg, task)
        code, lines = _settle(conn, cfg, task, outcome, detail, error)
        conn.commit()
        results.put((tid, code, lines))
    except Exception as exc:
        try:
            if conn is not None:
                conn.rollback()
                store.finish_task(conn, tid, store.TASK_FAILED,
                                  {"error": str(exc)[:300]}, str(exc)[:300])
                conn.commit()
        except Exception:
            pass
        results.put((tid, ERR, [f"task {tid} worker error: {exc}"]))
    finally:
        if conn is not None:conn.close()


def agent_pool_size(cfg) -> int:
    """模型调用并行上限：默认跟随研究泳道数；limits.max_agent_parallel 可显式覆盖（1..8）。"""
    raw = cfg.get('limits', 'max_agent_parallel')
    if raw is not None:
        if type(raw) is not int or not 1 <= raw <= 8:
            raise ValueError('limits.max_agent_parallel 须为 1..8 的整数')
        return raw
    from . import autopilot
    return autopilot.lane_limit(cfg)


# 持续研究框架挂起新研究时返回的 state/reason → 界面可读原因。
_FRAMEWORK_WAITING_ZH = {
    'paused': '全部任务已暂停',
    'waiting_inflight': '等待 UNKNOWN 对账完成',
    'waiting_queue': '研究框架任务排队中',
    'maintenance': '学习/贡献维护进行中',
    'queued': '研究框架工作项已入队',
    'waiting_for_changed_evidence': '等待足以改变决策的新证据',
    'dual_loop_authority_expired': '双环路学习授权已到期',
    'dual_model_budget_exhausted': '双环路累计模型启动额度已用尽；重启或迁移不会补充额度',
    'Dual-loop baseline not approved': '双环路冻结基线未审批',
    'Dual-loop epoch stopped; exact successor required': '代码或配置已更新，需迁移研究基线；运行 wq research-framework prepare-epoch',
    'Dual-loop cumulative cycles exhausted': '实验累计轮数已用尽；检查研究资源预算',
    'Dual-loop next arm request budget exhausted': '下一实验组的模拟预算已用尽；检查研究资源预算',
    'waiting_unreadable_or_changed_registered_material': '已登记材料不可读或已变更',
    'waiting_unreadable_or_changed_consumed_receipt': '已消费回执缺失或已变更：恢复原回执后继续，不重置发现额度',
    'waiting_changed_observation': '发现额度暂尽：等待可改变观察的新证据登记',
    'bounded_discovery': '有界发现进行中',
}


def _coordinate(conn, cfg):
    """状态机推进只在 supervisor 主线程串行执行。返回不产生副作用之外的标志。"""
    from . import autopilot, history_research, research_maintenance, research_framework
    from . import runtime_settings
    if runtime_settings.pending(conn):
        result=runtime_settings.apply_pending(conn,cfg)
        if result['state']=='pending':
            # Finish already-owned stages without creating more work to drain.
            autopilot.tick(conn,cfg,allow_new=False)
            autopilot.message(conn,'运行设置待应用：现有任务结束后自动切换，当前生效值保持不变')
            conn.commit()
            return
    allow_research = True
    allow_new = True
    work = {}
    try:
        if research_framework.enabled(cfg):
            work = research_framework.tick(conn, cfg)
            allow_research = work['allow_research']
            allow_new = work.get('allow_new_quant_cycle', True)
        else:
            research_maintenance.tick(conn, cfg)
        store.set_flag(conn, 'research_learning_maintenance_error', '')
        conn.commit()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        conn.rollback()
        if research_framework.enabled(cfg):
            allow_research = False
            work = {'state': 'framework_error', 'reason': '框架检查失败：' + str(exc)[:160]}
        store.set_flag(conn, 'research_learning_maintenance_error', str(exc)[:200])
        conn.commit()
    if allow_research:
        history_research.tick(conn, cfg)
        autopilot.tick(conn, cfg, allow_new=allow_new)
    else:
        history_research.progress(conn, cfg)
        # 框架挂起新研究时仍照常推进：tick 更新 last_tick、推进在途泳道生命周期；
        # 无在途轮次时把框架给出的原因写进界面消息，不让界面停在「等待本地调度」。
        autopilot.tick(conn, cfg, allow_new=False)
        if cfg.get('autopilot') and not conn.execute("SELECT 1 FROM research_cycles WHERE state!='closed'").fetchone():
            reason = work.get('reason') or work.get('state') or 'waiting_for_changed_evidence'
            autopilot.message(conn, '自动研究待命：' + _FRAMEWORK_WAITING_ZH.get(reason, str(reason))[:160])
    conn.commit()


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

    autopilot.setup(conn)
    _coordinate(conn, cfg)

    pool = agent_pool_size(cfg)
    if pool <= 1:
        return _serial_once(conn, cfg, lease_s, lines)
    return _supervise(conn, cfg, lease_s, pool, lines)


def _serial_once(conn, cfg, lease_s, lines) -> tuple[int, list[str]]:
    """默认路径：一次领取一个到期任务，分派落账后返回（与原语义一致）。"""
    task = store.claim_task(conn, owner=f"wq-{os.getpid()}", lease_s=lease_s)
    conn.commit()
    if task is None:
        lines.append("idle: 无到期任务")
        return OK, lines
    store.set_task_running(conn, task["task_id"])
    outcome, detail, error = dispatch_task(conn, cfg, task)
    code, task_lines = _settle(conn, cfg, task, outcome, detail, error)
    lines.extend(task_lines)
    conn.commit()
    return code, lines


def _supervise(conn, cfg, lease_s, pool, lines) -> tuple[int, list[str]]:
    """并行模式：agent_call 进 worker 池，平台/程序任务串行，状态机周期性推进。

    supervisor 持有 runner 锁直到没有可领取任务且 worker 全部落地；
    到达 supervisor_max_s 后停止领取新任务，等在途调用自行收尾。"""
    results: queue.Queue = queue.Queue()
    workers: set = set()
    owner = f"wq-{os.getpid()}"
    raw_cap = cfg.get('limits', 'supervisor_max_s', default=3000)
    cap = raw_cap if type(raw_cap) in (int, float) and 300 <= raw_cap <= 14400 else 3000
    deadline = time.monotonic() + float(cap)
    agent_lease = max(lease_s, 4200)
    code = OK
    draining = False
    while True:
        progressed = False
        while True:
            try:
                tid, wcode, wlines = results.get_nowait()
            except queue.Empty:
                break
            workers.discard(tid)
            lines.extend(wlines)
            progressed = True
            if code == OK and wcode != OK:
                code = wcode
        paused = store.is_paused(conn)
        if not draining and not paused:
            for _ in range(SERIAL_BATCH):
                task = store.claim_task(conn, owner=owner + '-s', lease_s=lease_s,
                                        exclude={'agent_call'})
                conn.commit()
                if task is None:
                    break
                progressed = True
                store.set_task_running(conn, task["task_id"])
                try:
                    outcome, detail, error = dispatch_task(conn, cfg, task)
                except Exception as exc:
                    outcome, detail, error = "failed", {"error": str(exc)[:300]}, str(exc)[:300]
                tcode, task_lines = _settle(conn, cfg, task, outcome, detail, error)
                conn.commit()
                lines.extend(task_lines)
                if code == OK and tcode != OK:
                    code = tcode
            _coordinate(conn, cfg)
            if time.monotonic() < deadline:
                while len(workers) < pool:
                    task = store.claim_task(conn, owner=owner + '-w', lease_s=agent_lease,
                                            kinds={'agent_call'})
                    conn.commit()
                    if task is None:
                        break
                    workers.add(task["task_id"])
                    progressed = True
                    lines.append(f"task {task['task_id']} 并行分派（{len(workers)}/{pool}）")
                    threading.Thread(target=_agent_worker, args=(cfg, task, results),
                                     daemon=True, name='wq-agent-' + task['task_id'][-6:]).start()
            else:
                draining = True
                lines.append('supervisor: 到达 supervisor_max_s，停止领取新任务，等待在途调用收尾')
        if not workers:
            if draining or paused or not progressed:
                break
            continue
        try:
            tid, wcode, wlines = results.get(timeout=SUPERVISOR_POLL_S)
            workers.discard(tid)
            lines.extend(wlines)
            if code == OK and wcode != OK:
                code = wcode
        except queue.Empty:
            pass
    if store.is_paused(conn) and code == OK:
        code = PAUSED
        lines.append(f"paused: {store.get_flag(conn, 'pause_reason', '')}；恢复用 `wq resume`")
    if not lines or 'idle' not in lines[-1] and code == OK and not progressed and not workers:
        lines.append("idle: 无可领取任务")
    return code, lines
