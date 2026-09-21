"""对账：UNKNOWN 项列出 → adapter 查询或人工核实 → resolve 落账。"""
from __future__ import annotations

from . import store, util
from .adapters.brain import build_adapter
from .errors import AdapterError, INVALID, WqExit

RESOLVE_OUTCOMES = {
    "accepted": "succeeded",   # 远端确实接受了该动作
    "lost": "failed",          # 远端没有该动作 → 安全，可重发（重新 enqueue）
    "rejected": "failed",      # 远端明确拒绝
}


def collect(conn, cfg) -> dict:
    unknown = store.unknown_items(conn)
    adapter = build_adapter(cfg)
    try:
        return adapter.reconcile(unknown)
    except AdapterError as e:
        if e.kind == AdapterError.NOT_IMPLEMENTED:
            return {"adapter": adapter.name, "state": "reconcile 未接通",
                    "pending": unknown,
                    "note": "逐项在 BRAIN UI 核实后用 --resolve 登记；"
                            "接通官方 API 前不自动重发"}
        raise


def resolve(conn, task_id: str, outcome: str, note: str, alpha_id: str | None = None) -> str:
    if outcome not in RESOLVE_OUTCOMES:
        raise WqExit(INVALID, f"outcome 应为 {sorted(RESOLVE_OUTCOMES)}")
    row = conn.execute("SELECT status,kind FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise WqExit(INVALID, f"无此任务 {task_id}")
    if row["status"] != "unknown":
        raise WqExit(INVALID, f"任务 {task_id} 状态 {row['status']}，仅 unknown 可对账")
    if row['kind'] == 'brain_simulation':
        return _resolve_brain(conn, task_id, outcome, note, alpha_id)
    if alpha_id:
        raise WqExit(INVALID, '--alpha-id 仅适用于 BRAIN 模拟任务')
    new = RESOLVE_OUTCOMES[outcome]
    store.finish_task(conn, task_id, new, {"reconciled_by": "manual", "note": note})
    return new


def _resolve_brain(conn, tid, outcome, note, alpha_id):
    from . import brain_jobs
    brain_jobs.setup(conn)
    run = conn.execute('SELECT * FROM brain_runs WHERE task_id=?', (tid,)).fetchone()
    if not run or not note.strip():
        raise WqExit(INVALID, 'BRAIN对账需要持久化请求记录及 --note 核实依据')
    if alpha_id and (not alpha_id.isascii() or not alpha_id.isalnum()):
        raise WqExit(INVALID, 'Alpha ID 只能包含ASCII字母和数字')
    if alpha_id and outcome != 'accepted':
        raise WqExit(INVALID, '--alpha-id 只能与 accepted 一起使用')
    if alpha_id and run['alpha_id'] and alpha_id != run['alpha_id']:
        raise WqExit(INVALID, 'Alpha ID 与已保存回执不一致')
    aid = alpha_id or run['alpha_id']
    if outcome == 'accepted' and not aid and not run['location']:
        raise WqExit(INVALID, '缺少回执；需从官方页面核实 --alpha-id，不能仅凭 accepted 记成功')
    if outcome != 'accepted' and run['state'] == 'complete':
        raise WqExit(INVALID, '已有真实入账结果，不能宣称请求丢失或被拒绝')
    conn.execute('SAVEPOINT brain_reconcile')
    try:
        if outcome == 'accepted':
            # 接受仅恢复GET；表达式、设置和真实结果仍由原入账路径核验。
            state = 'fetching' if aid else 'polling'
            if run['state'] == 'complete': state = 'complete'
            conn.execute('UPDATE brain_runs SET state=?,alpha_id=?,updated_at=? WHERE task_id=?',
                         (state, aid, util.now_iso(), tid))
            conn.execute("UPDATE tasks SET status='queued',not_before=?,last_error=NULL,max_attempts=MAX(max_attempts,attempts+360) WHERE task_id=?",
                         (util.now_iso(), tid))
            new = 'queued'
        else:
            conn.execute('UPDATE brain_runs SET state=?,updated_at=? WHERE task_id=?',
                         ('reconciled_'+outcome, util.now_iso(), tid))
            store.finish_task(conn, tid, 'failed', {'reconciled_by':'manual','note':note})
            new = 'failed'
        store.add_attempt(conn, tid, 'brain_reconcile', new, {'outcome':outcome,'note':note,'alpha_id':aid})
        conn.execute('RELEASE brain_reconcile')
    except Exception:
        conn.execute('ROLLBACK TO brain_reconcile');conn.execute('RELEASE brain_reconcile');raise
    return new
