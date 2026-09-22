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
KINDS = {"brain_feedback":"真实结果诊断与分段资料收集","brain_submission": "BRAIN 正式提交与接收核验","brain_simulation": "BRAIN 自动模拟与入账","simulation": "BRAIN 模拟任务", "submission": "Alpha 提交任务",
         "import": "导入结果", "reconcile": "核对未知结果", "agent_call": "模型任务"}


def title(row):
    payload = json.loads(row["payload_json"])
    purpose = payload.get("purpose", "")
    return TITLES.get(purpose, (payload.get("title") or purpose or
                               KINDS.get(row["kind"], row["kind"]), ""))


def local_time(value):
    return util.parse_iso(value).astimezone().strftime("%m-%d %H:%M:%S")


def task_groups(conn, rows):
    """按账本关联分组，不依赖可变的任务标题。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cycles = list(conn.execute('SELECT * FROM research_cycles ORDER BY cycle_id DESC')) if 'research_cycles' in tables else []
    owners = {}
    for cycle in cycles:
        for key in ('research_task', 'review_task', 'simulation_task'):
            if cycle[key]: owners[cycle[key]] = cycle['cycle_id']
    grouped = {}
    for row in rows:
        cid = owners.get(row['task_id'])
        grouped.setdefault(cid, []).append(row)
    result = [(c, grouped[c['cycle_id']]) for c in cycles if c['cycle_id'] in grouped]
    if None in grouped: result.append((None, grouped[None]))
    return result


def group_cost(conn, rows, cfg=None):
    calls = {}
    missing = 0
    for row in rows:
        if row['kind'] != 'agent_call': continue
        payload = json.loads(row['payload_json'])
        linked = {}
        for attempt in conn.execute('SELECT detail_json FROM attempts WHERE task_id=?', (row['task_id'],)):
            detail = json.loads(attempt[0] or '{}')
            cid = detail.get('call_id') if isinstance(detail, dict) else None
            if cid:
                call = conn.execute('SELECT * FROM agent_calls WHERE call_id=?', (cid,)).fetchone()
                if call: linked[cid] = call
        # 路由purpose是唯一job ID；包含崩溃前尚未来得及写provider_result的调用。
        purpose = payload.get('purpose')
        same = [r for r in store.list_tasks(conn) if json.loads(r['payload_json']).get('purpose') == purpose]
        if purpose and len(same) == 1:
            for call in conn.execute('SELECT * FROM agent_calls WHERE purpose=? AND started_at>=?', (purpose, row['created_at'])):
                linked[call['call_id']] = call
        starts = conn.execute("SELECT COUNT(*) FROM attempts WHERE task_id=? AND event='provider_start'", (row['task_id'],)).fetchone()[0]
        missing += max(0, starts - len(linked))
        if not linked and starts == 0 and (row['attempts'] or row['status'] not in ('queued', 'blocked')):
            missing += 1
        calls.update(linked)
    devin_dirs = []
    if cfg is not None:
        workdir = cfg.model('devin').get('workdir')
        if workdir:
            devin_dirs.append(cfg.resolve(workdir))
    values = [usage.for_call(dict(c), devin_dirs) if devin_dirs
              else usage.for_call(dict(c)) for c in calls.values()]
    dollars = [v['cost_usd'] for v in values if v and isinstance(v.get('cost_usd'), (int, float))]
    tokens = [v['total_tokens'] for v in values if v and isinstance(v.get('total_tokens'), int)]
    unknown_cost = len(values) - len(dollars) + missing
    unknown_tokens = len(values) - len(tokens) + missing
    if not calls and not missing:
        return '模型总成本：$0 / 0 token（尚未调用模型；平台费用、订阅费另计）'
    money = f'已知折合小计 ${sum(dollars):.4f}' if dollars else '已知折合小计：暂无'
    money += f'；另有 {unknown_cost} 次调用成本未知，总额未知' if unknown_cost else '；已覆盖全部调用'
    token_text = f'已知 {sum(tokens):,} token' + (f'，另有 {unknown_tokens} 次计量未知' if unknown_tokens else '')
    return f'模型总成本：{money}；{token_text}（含重试；CLI折合非账单，运行中计量未定稿）'


def group_summary(conn, cycle, rows, cfg=None):
    lines = [group_cost(conn, rows, cfg)]
    if cycle is None:
        counts = Counter(STATES.get(r['status'], r['status']) for r in rows)
        lines += ['状态：' + ' / '.join(f'{k} {v}' for k, v in counts.items()),
                  '最终质量：不合并判定；初始化、教学及独立任务见各任务结果。']
        return lines
    state = {'closed':'已归档', 'researching':'提案中', 'reviewing':'审查中', 'simulating':'回测/结果查询中'}.get(cycle['state'], cycle['state'])
    blockers = [r for r in rows if r['status'] in ('unknown','blocked','failed','aborted')]
    if blockers and cycle['state'] != 'closed':
        state += '；' + ' / '.join(STATES[r['status']] for r in blockers)
    stage_task = {'researching':'research_task', 'reviewing':'review_task', 'simulating':'simulation_task'}.get(cycle['state'])
    if stage_task and any(r['task_id']==cycle[stage_task] and r['status']=='succeeded' for r in rows):
        state += '（本阶段任务已完成，等待调度推进）'
    lines.append('状态：' + state)
    sim = None
    if cycle['simulation_task']:
        sim = conn.execute('SELECT s.* FROM simulations s JOIN brain_runs b ON b.alpha_id=s.remote_id WHERE b.task_id=? AND s.synthetic=0 ORDER BY s.imported_at DESC LIMIT 1', (cycle['simulation_task'],)).fetchone()
    if sim:
        stats = json.loads(sim['stats_json'] or '{}')
        checks = json.loads(sim['checks_json'] or '{}')
        counts = Counter(c.get('result', 'UNKNOWN') for c in checks.get('raw', []))
        quality = {'passed':'筛选通过，仍需正式提交验收', 'failed':'筛选未通过', 'unchecked':'检查未齐', 'unknown':'质量待核实'}.get(sim['status'], sim['status'])
        lines.append(f"最终质量：{quality} · Sharpe {stats.get('sharpe', '未知')} · Fitness {stats.get('fitness', '未知')} · FAIL {counts['FAIL']} / PENDING {counts['PENDING']}")
        lines.append('轮次结论：' + (cycle['outcome'] or '结果已入账，等待轮次归档'))
    else:
        lines.append('最终质量：' + (('未回测；' + (cycle['outcome'] or '无真实质量结果')) if cycle['state']=='closed' else '尚未产生真实回测结果'))
    return lines


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
    headers = {}
    ordered = []
    complete_groups = {(c['cycle_id'] if c else None): members
                       for c, members in task_groups(conn, list(all_rows.values()))}
    for cycle, visible in task_groups(conn, rows):
        cid = cycle['cycle_id'] if cycle else None
        complete = complete_groups.get(cid, visible)
        heading = f"第 {cid} 轮" if cycle else '其他任务（初始化 / 教学 / 独立任务）'
        headers[len(ordered)] = ['', '══ ' + heading + ' ══',
                                 *group_summary(conn, cycle, complete, cfg)]
        if len(visible) != len(complete):
            headers[len(ordered)].append(f'本组显示 {len(visible)}/{len(complete)} 项；汇总仍按整组计算。')
        ordered.extend(visible)
    for i, row in enumerate(ordered, 1):
        lines.extend(headers.get(i - 1, []))
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
            devin_dirs = []
            workdir = cfg.model('devin').get('workdir')
            if workdir:
                devin_dirs.append(cfg.resolve(workdir))
            lines.append(f"   {usage.display(usage.for_call(dict(call), devin_dirs), state in ('running', 'claimed'))}")
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
        count_label = "队列处理（含轮询）" if row["kind"] in ("brain_simulation", "brain_submission") else "已尝试"
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
