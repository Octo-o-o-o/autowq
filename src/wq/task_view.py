"""将账本事实显示为中文进度；不修改任务或读取模型的内部推理日志。"""
import json
import os
from collections import Counter

from . import store, usage, util

TITLES = {
    "week1-01-preregister": ("Grok：修订研究方案", "明确研究假设、样本范围和放弃标准。"),
    "week1-02-engineering-check": ("Devin：检查自动运行代码", "检查调度、去重、超时及产物校验，输出问题清单。"),
    "week1-03-liquidity": ("Grok：研究流动性机制", "研究一个不同于应计质量的公开市场机制。"),
    "week1-04-research-check": ("Devin：审查研究设计", "审查两份研究设计的可证伪性、未来信息和证据缺口。"),
    "week1-05-week-review": ("Grok：汇总并选择下一步", "最多保留一个待验证基线，或明确放弃，并列出缺失证据。"),
}
STATES = {"succeeded": "已完成", "running": "运行中", "claimed": "已领取",
          "queued": "等待执行", "blocked": "已阻断", "failed": "失败",
          "unknown": "结果待核实", "aborted": "已中止"}
KINDS = {"brain_simulation": "BRAIN 自动模拟与入账","simulation": "BRAIN 模拟任务", "submission": "Alpha 提交任务",
         "import": "导入结果", "reconcile": "核对未知结果", "agent_call": "模型任务"}


def title(row):
    payload = json.loads(row["payload_json"])
    purpose = payload.get("purpose", "")
    return TITLES.get(purpose, (payload.get("title") or purpose or
                               KINDS.get(row["kind"], row["kind"]), ""))


def local_time(value):
    return util.parse_iso(value).astimezone().strftime("%m-%d %H:%M:%S")


def render_tasks(conn, cfg, rows):
    now = util.now()
    all_rows = {r["task_id"]: r for r in store.list_tasks(conn)}
    paused = store.is_paused(conn)
    active = any(r["status"] in ("running", "claimed") for r in all_rows.values())
    lines = [f"WorldQuant 任务进度 · {now.astimezone():%Y-%m-%d %H:%M:%S %Z}"]
    if not rows:
        return "\n".join(lines + ["没有符合条件的任务。"])
    counts = Counter(r["status"] for r in rows)
    lines.append("共 %d 项：%s" % (len(rows), " / ".join(
        f"{STATES.get(s, s)} {n}" for s, n in counts.items())))
    lines.append("调度：已暂停，需手动恢复。" if paused else
                 "执行策略：单并发；定时器负责领取到期任务。本命令只查看，不触发调用。")
    lines.append("以下时间为本机时区；已完成表示任务产物已生成，不代表回测通过或获得收入。")
    for i, row in enumerate(rows, 1):
        payload = json.loads(row["payload_json"])
        name, description = title(row)
        state = row["status"]
        lines += ["", f"{i}. [{STATES.get(state, state)}] {name}"]
        route = conn.execute('SELECT * FROM task_routes WHERE task_id=?', (row['task_id'],)).fetchone()
        if route:
            snap = json.loads(route['snapshot_json'])
            index = route['provider_index']
            provider = snap['chain'][index] if index < len(snap['chain']) else '无可用备用'
            lines.append(f"   预设：{snap['preset']}（本任务已冻结） · 渠道：{provider} · 重试 {route['retry_index']}/3")
            history = conn.execute("SELECT COUNT(*) FROM attempts WHERE task_id=? AND event='provider_start'", (row['task_id'],)).fetchone()[0]
            lines.append(f"   调用记录：共 {history} 次启动阶段；每次产物保留在独立副本。")
        elif payload.get('routing'):
            from .routing import active_preset
            lines.append(f"   预设：尚未冻结，首次领取时使用当前选择（现为 {active_preset(conn, cfg)}）。")
        if row['kind'] == 'brain_simulation':
            from .brain_jobs import setup
            setup(conn)
            run = conn.execute('SELECT * FROM brain_runs WHERE task_id=?', (row['task_id'],)).fetchone()
            labels = {'post_started':'派发结果待核对，禁止重发','polling':'已派发，等待平台计算',
                      'fetching':'已生成Alpha，读取真实结果','complete':'真实结果已入账',
                      'failed':'平台模拟失败','rejected':'请求被拒绝'}
            lines.append('   平台阶段：' + (labels.get(run['state'], run['state']) if run else '尚未派发'))
            if run and run['alpha_id']:
                lines.append('   Alpha ID：' + run['alpha_id'])
                result = conn.execute('SELECT status FROM simulations WHERE remote_id=? AND synthetic=0', (run['alpha_id'],)).fetchone()
                if result:
                    quality = {'passed':'通过', 'failed':'未通过', 'unknown':'待核实'}.get(result['status'], result['status'])
                    lines.append('   平台检查：' + quality + '；任务完成仅表示结果已取得。')
            if run and run['evidence_path']:
                lines.append('   证据：' + run['evidence_path'])
        if description:
            lines.append(f"   内容：{description}")
        call = _call_for_task(conn, row, payload)
        if call:
            lines.append(f"   {usage.display(usage.for_call(dict(call)), state in ('running', 'claimed'))}")
        elif route:
            lines.append("   消耗：历史尝试需汇总核对；此处不将等待重试写成零消耗。")
        elif row['kind'] == 'brain_simulation':
            lines.append('   本步骤不调用模型；平台额度与收益另计。')
        elif state in ("queued", "blocked"):
            lines.append("   消耗：0 token；折合 $0（本步骤尚未调用模型）")
        else:
            lines.append("   消耗：token 暂无可核对记录；折合 $：未知")
        if state == "queued":
            deps = payload.get("depends_on", [])
            waiting = [d for d in deps if d not in all_rows or all_rows[d]["status"] != "succeeded"]
            if paused:
                reason = "调度已暂停。"
            elif waiting:
                reason = "等待前置任务：" + "；".join(
                    f"{title(all_rows[d])[0]}（{STATES.get(all_rows[d]['status'], all_rows[d]['status'])}）"
                    if d in all_rows else f"{d}（记录缺失）" for d in waiting)
            elif util.parse_iso(row["not_before"]) > now:
                reason = f"尚未到执行时间：{local_time(row['not_before'])}。"
            elif active:
                reason = "已到执行时间，等待当前任务释放执行位置。"
            else:
                reason = "已到执行时间，等待调度器领取；实际能否启动仍需通过预算和授权检查。"
            lines.append(f"   等待原因：{reason}")
        elif state in ("running", "claimed"):
            call = conn.execute(
                "SELECT started_at FROM agent_calls WHERE purpose=? "
                "AND started_at>=? AND status='running' ORDER BY started_at DESC LIMIT 1",
                (payload.get("purpose"), row["created_at"])).fetchone()
            if call:
                elapsed = max(0, int((now - util.parse_iso(call["started_at"])).total_seconds()))
                lines.append(f"   开始：{local_time(call['started_at'])}，已运行 {elapsed // 60} 分 {elapsed % 60} 秒。")
                lines.append("   进度：模型正在处理，尚未落账完成；无法可靠估计百分比或剩余时长。")
            elif row['kind'] != 'brain_simulation':
                lines.append("   进度：任务已领取，尚无对应的在途模型记录；不能据此判定模型正在计算。")
        elif state == "succeeded":
            lines.append(f"   完成：{local_time(row['updated_at'])}")
            if payload.get('routing'):
                lines.append(f"   产物：{os.path.join(payload['job_dir'], 'result.json')}")
            workdir = cfg.model(payload.get("agent", "")).get("workdir")
            if workdir:
                for artifact in payload.get("artifacts", []):
                    lines.append(f"   产物：{os.path.join(cfg.resolve(workdir), artifact)}")
        elif state == "blocked" and row["kind"] == "simulation" and cfg.adapter_mode() == "manual":
            lines.append("   原因：这是旧 manual 模式任务，仍需网页结果导入；新自动模拟使用 wq brain enqueue。")
            lines.append("   影响：该模拟任务不会自动重试；不影响没有依赖它的 Grok / Devin 研究任务。")
        if row["last_error"] and not (state == "blocked" and row["kind"] == "simulation" and cfg.adapter_mode() == "manual"):
            label = '上次尝试' if route and state in ('running', 'claimed') else '原因记录'
            lines.append(f"   {label}：{row['last_error']}")
        count_label = "队列处理（含轮询）" if row["kind"] == "brain_simulation" else "已尝试"
        lines.append(f"   编号：{row['task_id']} · {count_label} {row['attempts']} 次")
    if cfg.get('autopilot'):
        lines += ['', '持续研究：' + store.get_flag(conn, 'autopilot_message', '尚未启动'), '详细状态：./wq autopilot status']
    lines += ["", "刷新：再次执行 ./wq tasks    原始数据：./wq tasks --json",
              '暂停：./wq pause --reason "用户暂停"']
    return "\n".join(lines)


def _call_for_task(conn, row, payload):
    if row["kind"] != "agent_call":
        return None
    if row["status"] in ("running", "claimed"):
        return conn.execute(
            "SELECT * FROM agent_calls WHERE status='running' AND purpose=? "
            "AND started_at>=? ORDER BY started_at DESC LIMIT 1",
            (payload.get("purpose"), row["created_at"]),
        ).fetchone()
    attempt = conn.execute(
        "SELECT detail_json FROM attempts WHERE task_id=? AND event='finish' "
        "ORDER BY attempt_id DESC LIMIT 1", (row["task_id"],)
    ).fetchone()
    if not attempt or not attempt["detail_json"]:
        return None
    try:
        detail = json.loads(attempt["detail_json"])
    except json.JSONDecodeError:
        return None
    call_id = detail.get("call_id") if isinstance(detail, dict) else None
    if not call_id:
        return None
    return conn.execute("SELECT * FROM agent_calls WHERE call_id=?", (call_id,)).fetchone()
