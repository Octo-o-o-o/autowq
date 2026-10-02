"""将账本事实显示为双语进度；不修改任务或读取模型的内部推理日志。

术语统一（CLI 与桌面端共用）：researching=研究中、simulating=回测中、
passed=筛选通过（仍需正式提交验收）、unknown=质量待核实。账本内由
autopilot 写入的中文 message/outcome 经 i18n.translate 在显示层翻译。
"""
import json
import os
from collections import Counter

from . import store, usage, util
from .i18n import text, translate

# 一次历史渲染里复用任务索引。sqlite3.Connection 不能挂自定义属性。
_TASKS_BY_PURPOSE = {}

TITLES = {
    "week1-01-preregister": ("Grok：修订研究方案", "明确研究假设、样本范围和放弃标准。",
                             "Grok: revise the research plan", "State the hypothesis, sample scope and abandonment criteria."),
    "week1-02-engineering-check": ("Devin：检查自动运行代码", "检查调度、去重、超时及产物校验，输出问题清单。",
                                   "Devin: inspect the automation code", "Check scheduling, dedup, timeouts and artifact validation; report issues."),
    "week1-03-liquidity": ("Grok：研究流动性机制", "研究一个不同于应计质量的公开市场机制。",
                           "Grok: research a liquidity mechanism", "Study a public-market mechanism other than accrual quality."),
    "week1-04-research-check": ("Devin：审查研究设计", "审查两份研究设计的可证伪性、未来信息和证据缺口。",
                                "Devin: review the research designs", "Review falsifiability, look-ahead bias and evidence gaps of both designs."),
    "week1-05-week-review": ("Grok：汇总并选择下一步", "最多保留一个待验证基线，或明确放弃，并列出缺失证据。",
                             "Grok: summarize and pick next steps", "Keep at most one baseline to verify, or drop it explicitly; list missing evidence."),
}
STATES = {"succeeded": "已完成", "running": "运行中", "claimed": "已领取",
          "queued": "等待执行", "blocked": "已阻断", "failed": "失败",
          "unknown": "结果待核实", "aborted": "已中止"}
STATES_EN = {"succeeded": "Done", "running": "Running", "claimed": "Claimed",
             "queued": "Queued", "blocked": "Blocked", "failed": "Failed",
             "unknown": "Outcome unverified", "aborted": "Aborted"}
CYCLE_STATES = {'closed': '已归档', 'researching': '研究中', 'reviewing': '审查中', 'simulating': '回测中'}
CYCLE_STATES_EN = {'closed': 'Archived', 'researching': 'Researching', 'reviewing': 'Reviewing', 'simulating': 'Simulating'}
KINDS = {"brain_feedback": "真实结果诊断与分段资料收集", "brain_submission": "BRAIN 正式提交与接收核验",
         "brain_simulation": "BRAIN 自动模拟与入账", "simulation": "BRAIN 模拟任务", "submission": "Alpha 提交任务",
         "import": "导入结果", "reconcile": "核对未知结果", "agent_call": "模型任务"}
KINDS_EN = {"brain_feedback": "Real-result diagnosis and staged data collection", "brain_submission": "BRAIN submission and receipt verification",
            "brain_simulation": "BRAIN automatic simulation and recording", "simulation": "BRAIN simulation task", "submission": "Alpha submission task",
            "import": "Import results", "reconcile": "Reconcile unknown outcomes", "agent_call": "Model task"}
QUALITY = {'passed': '筛选通过', 'failed': '筛选未通过', 'unchecked': '检查未齐', 'unknown': '质量待核实'}
QUALITY_EN = {'passed': 'Passed screening', 'failed': 'Failed screening', 'unchecked': 'Checks incomplete', 'unknown': 'Quality unverified'}


def state_label(status, lang='zh'):
    return text(lang, STATES.get(status, status), STATES_EN.get(status, status))


def cycle_state(state, lang='zh'):
    return text(lang, CYCLE_STATES.get(state, state), CYCLE_STATES_EN.get(state, state))


def kind_label(kind, lang='zh'):
    return text(lang, KINDS.get(kind, kind), KINDS_EN.get(kind, kind))


def quality_label(status, lang='zh'):
    return text(lang, QUALITY.get(status, status), QUALITY_EN.get(status, status))


def title(row, lang='zh'):
    payload = json.loads(row["payload_json"])
    purpose = payload.get("purpose", "")
    if purpose in TITLES:
        zh_name, zh_desc, en_name, en_desc = TITLES[purpose]
        return (zh_name, zh_desc) if lang == 'zh' else (en_name, en_desc)
    return (payload.get("title") or purpose or kind_label(row["kind"], lang), "")


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
    if 'research_fallbacks' in tables:
        for fallback in conn.execute('SELECT cycle_id,original_task,fallback_task FROM research_fallbacks'):
            owners[fallback['original_task']] = fallback['cycle_id']
            owners[fallback['fallback_task']] = fallback['cycle_id']
    grouped = {}
    for row in rows:
        cid = owners.get(row['task_id'])
        grouped.setdefault(cid, []).append(row)
    result = [(c, grouped[c['cycle_id']]) for c in cycles if c['cycle_id'] in grouped]
    if None in grouped: result.append((None, grouped[None]))
    return result


def group_cost_data(conn, rows, cfg=None):
    """成本结构化数据；渲染见 render_cost，桌面端用同一数据做摘要。"""
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
        indexed = _TASKS_BY_PURPOSE.get(id(conn))
        if indexed is not None:
            same = indexed.get(purpose, []) if purpose else []
        else:
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
    return {'calls': len(calls), 'missing': missing,
            'known_dollars': sum(dollars) if dollars else None,
            'unknown_calls': len(values) - len(dollars) + missing,
            'known_tokens': sum(tokens), 'unknown_token_calls': len(values) - len(tokens) + missing}


def render_cost(data, lang='zh'):
    if not data['calls'] and not data['missing']:
        return text(lang, '模型总成本：$0 / 0 token（尚未调用模型；平台费用、订阅费另计）',
                    'Total model cost: $0 / 0 tokens (no model calls yet; platform fees and subscriptions excluded)')
    if data['known_dollars'] is not None:
        money = text(lang, '已知折合小计', 'Known subtotal') + f" ${data['known_dollars']:.4f}"
    else:
        money = text(lang, '已知折合小计：暂无', 'Known subtotal: none')
    money += (text(lang, f"；另有 {data['unknown_calls']} 次调用成本未知，总额未知",
                   f"; {data['unknown_calls']} calls of unknown cost, total unknown")
              if data['unknown_calls'] else text(lang, '；已覆盖全部调用', '; all calls covered'))
    token_text = text(lang, f"已知 {data['known_tokens']:,} token", f"Known {data['known_tokens']:,} tokens")
    if data['unknown_token_calls']:
        token_text += text(lang, f"，另有 {data['unknown_token_calls']} 次计量未知",
                           f", {data['unknown_token_calls']} calls unmeasured")
    tail = text(lang, '（含重试；CLI折合非账单，运行中计量未定稿）',
                ' (retries included; CLI-derived, not a bill; in-flight metering unsettled)')
    prefix = text(lang, '模型总成本：', 'Total model cost: ')
    return f"{prefix}{money}；{token_text}{tail}" if lang == 'zh' else f"{prefix}{money}; {token_text}{tail}"


def group_cost(conn, rows, cfg=None, lang='zh'):
    return render_cost(group_cost_data(conn, rows, cfg), lang)


def group_summary(conn, cycle, rows, cfg=None, lang='zh'):
    lines = [group_cost(conn, rows, cfg, lang)]
    if cycle is None:
        counts = Counter(state_label(r['status'], lang) for r in rows)
        joiner = ' / '
        lines += [text(lang, '状态：', 'Status: ') + joiner.join(f'{k} {v}' for k, v in counts.items()),
                  text(lang, '最终质量：不合并判定；初始化、教学及独立任务见各任务结果。',
                       'Final quality: no merged verdict; see each task for setup/tutorial/standalone results.')]
        return lines
    state = cycle_state(cycle['state'], lang)
    current = {cycle[key] for key in ('research_task','review_task','simulation_task')}
    blockers = [r for r in rows if r['task_id'] in current and r['status'] in ('unknown','blocked','failed','aborted')]
    if blockers and cycle['state'] != 'closed':
        sep = '；' if lang == 'zh' else '; '
        state += sep + ' / '.join(state_label(r['status'], lang) for r in blockers)
    stage_task = {'researching':'research_task', 'reviewing':'review_task', 'simulating':'simulation_task'}.get(cycle['state'])
    if stage_task and any(r['task_id']==cycle[stage_task] and r['status']=='succeeded' for r in rows):
        state += text(lang, '（本阶段任务已完成，等待调度推进）', ' (stage task done; waiting for the scheduler)')
    lines.append(text(lang, '状态：', 'Status: ') + state)
    sim = None
    if cycle['simulation_task']:
        sim = conn.execute('SELECT s.* FROM simulations s JOIN brain_runs b ON b.alpha_id=s.remote_id WHERE b.task_id=? AND s.synthetic=0 ORDER BY s.imported_at DESC LIMIT 1', (cycle['simulation_task'],)).fetchone()
    if sim:
        stats = json.loads(sim['stats_json'] or '{}')
        checks = json.loads(sim['checks_json'] or '{}')
        counts = Counter(c.get('result', 'UNKNOWN') for c in checks.get('raw', []))
        quality = quality_label(sim['status'], lang)
        if sim['status'] == 'passed' and lang == 'zh':
            quality = '筛选通过，仍需正式提交验收'
        elif sim['status'] == 'passed':
            quality = 'Passed screening; formal submission review still required'
        lines.append(text(lang, f"最终质量：{quality} · Sharpe {stats.get('sharpe', '未知')} · Fitness {stats.get('fitness', '未知')} · FAIL {counts['FAIL']} / PENDING {counts['PENDING']}",
                          f"Final quality: {quality} · Sharpe {stats.get('sharpe', 'unknown')} · Fitness {stats.get('fitness', 'unknown')} · FAIL {counts['FAIL']} / PENDING {counts['PENDING']}"))
        outcome = translate(cycle['outcome'], lang) or text(lang, '结果已入账，等待轮次归档', 'Results recorded; awaiting cycle close')
        lines.append(text(lang, '轮次结论：', 'Cycle outcome: ') + outcome)
    else:
        if cycle['state']=='closed':
            detail = (translate(cycle['outcome'], lang) or text(lang, '无真实质量结果', 'no real quality results'))
            lines.append(text(lang, '最终质量：', 'Final quality: ') + text(lang, '未回测；', 'No backtest; ') + detail)
        else:
            lines.append(text(lang, '最终质量：尚未产生真实回测结果', 'Final quality: no real backtest results yet'))
    return lines


def render_tasks(conn, cfg, rows, lang='zh'):
    now = util.now()
    all_rows = {r["task_id"]: r for r in store.list_tasks(conn)}
    paused = store.is_paused(conn)
    active = any(r["status"] in ("running", "claimed") for r in all_rows.values())
    lines = [text(lang, f"WorldQuant 任务进度 · {now.astimezone():%Y-%m-%d %H:%M:%S %Z}",
                  f"WorldQuant task progress · {now.astimezone():%Y-%m-%d %H:%M:%S %Z}")]
    if not rows:
        return "\n".join(lines + [text(lang, "没有符合条件的任务。", "No matching tasks.")])
    counts = Counter(r["status"] for r in rows)
    lines.append(text(lang, "共 %d 项：%s" % (len(rows), " / ".join(f"{state_label(s, lang)} {n}" for s, n in counts.items())),
                        "%d tasks: %s" % (len(rows), " / ".join(f"{state_label(s, lang)} {n}" for s, n in counts.items()))))
    lines.append(text(lang, "调度：已暂停，需手动恢复。",
                        "Scheduler: paused; resume manually.") if paused else
                 text(lang, "执行策略：单并发；定时器负责领取到期任务。本命令只查看，不触发调用。",
                        "Policy: single concurrency; the timer claims due tasks. This command is read-only and triggers nothing."))
    lines.append(text(lang, "以下时间为本机时区；已完成表示任务产物已生成，不代表回测通过或获得收入。",
                        'Times are in your local timezone; "Done" means artifacts were produced, not that backtests passed or income was earned.'))
    headers = {}
    ordered = []
    complete_groups = {(c['cycle_id'] if c else None): members
                       for c, members in task_groups(conn, list(all_rows.values()))}
    for cycle, visible in task_groups(conn, rows):
        cid = cycle['cycle_id'] if cycle else None
        complete = complete_groups.get(cid, visible)
        heading = text(lang, f"第 {cid} 轮", f"Cycle {cid}") if cycle else \
            text(lang, '其他任务（初始化 / 教学 / 独立任务）', 'Other tasks (setup / tutorial / standalone)')
        headers[len(ordered)] = ['', '══ ' + heading + ' ══',
                                 *group_summary(conn, cycle, complete, cfg, lang)]
        if len(visible) != len(complete):
            headers[len(ordered)].append(text(lang, f'本组显示 {len(visible)}/{len(complete)} 项；汇总仍按整组计算。',
                                                f'Showing {len(visible)}/{len(complete)} of this group; totals cover the whole group.'))
        ordered.extend(visible)
    brain_labels = {'post_started': ('派发结果待核对，禁止重发', 'Dispatch pending verification; do not resend'),
                    'polling': ('已派发，等待平台计算', 'Dispatched; waiting for the platform'),
                    'fetching': ('已生成Alpha，读取真实结果', 'Alpha created; fetching real results'),
                    'complete': ('真实结果已入账', 'Real results recorded'),
                    'failed': ('平台模拟失败', 'Platform simulation failed'),
                    'rejected': ('请求被拒绝', 'Request rejected')}
    for i, row in enumerate(ordered, 1):
        lines.extend(headers.get(i - 1, []))
        payload = json.loads(row["payload_json"])
        name, description = title(row, lang)
        state = row["status"]
        lines += ["", f"{i}. [{state_label(state, lang)}] {name}"]
        route = conn.execute('SELECT * FROM task_routes WHERE task_id=?', (row['task_id'],)).fetchone()
        if route:
            snap = json.loads(route['snapshot_json'])
            index = route['provider_index']
            provider = snap['chain'][index] if index < len(snap['chain']) else text(lang, '无可用备用', 'no fallback available')
            lines.append(text(lang, f"   预设：{snap['preset']}（本任务已冻结） · 渠道：{provider} · 重试 {route['retry_index']}/3",
                                f"   Preset: {snap['preset']} (frozen for this task) · Provider: {provider} · Retry {route['retry_index']}/3"))
            history = conn.execute("SELECT COUNT(*) FROM attempts WHERE task_id=? AND event='provider_start'", (row['task_id'],)).fetchone()[0]
            lines.append(text(lang, f"   调用记录：共 {history} 次启动阶段；每次产物保留在独立副本。",
                                f"   Call history: {history} provider starts; each attempt keeps its own artifact copy."))
        elif payload.get('routing'):
            from .routing import active_preset
            lines.append(text(lang, f"   预设：尚未冻结，首次领取时使用当前选择（现为 {active_preset(conn, cfg)}）。",
                                f"   Preset: not frozen yet; the current choice applies at first claim (now {active_preset(conn, cfg)})."))
        if row['kind'] == 'brain_simulation':
            from .brain_jobs import setup
            setup(conn)
            run = conn.execute('SELECT * FROM brain_runs WHERE task_id=?', (row['task_id'],)).fetchone()
            stage = brain_labels.get(run['state'], (run['state'], run['state'])) if run else (text(lang, '尚未派发', 'Not dispatched'),)*2
            lines.append(text(lang, '   平台阶段：', '   Platform stage: ') + stage[0 if lang == 'zh' else 1])
            if run and run['alpha_id']:
                lines.append('   Alpha ID：' + run['alpha_id'])
                result = conn.execute('SELECT status FROM simulations WHERE remote_id=? AND synthetic=0', (run['alpha_id'],)).fetchone()
                if result:
                    zh_quality = {'passed':'通过', 'failed':'未通过', 'unknown':'待核实'}
                    lines.append(text(lang, '   平台检查：', '   Platform check: ') +
                                 (zh_quality.get(result['status'], result['status']) if lang == 'zh' else quality_label(result['status'], lang)) +
                                 text(lang, '；任务完成仅表示结果已取得。', '; task completion only means results were fetched.'))
            if run and run['evidence_path']:
                lines.append(text(lang, '   证据：', '   Evidence: ') + run['evidence_path'])
        if description:
            lines.append(text(lang, f"   内容：{description}", f"   About: {description}"))
        call = _call_for_task(conn, row, payload)
        if call:
            devin_dirs = []
            workdir = cfg.model('devin').get('workdir')
            if workdir:
                devin_dirs.append(cfg.resolve(workdir))
            lines.append("   " + usage.display(usage.for_call(dict(call), devin_dirs), state in ('running', 'claimed'), lang))
        elif route:
            lines.append(text(lang, "   消耗：历史尝试需汇总核对；此处不将等待重试写成零消耗。",
                                "   Cost: historical attempts need aggregation; waiting retries are not counted as zero cost."))
        elif row['kind'] == 'brain_simulation':
            lines.append(text(lang, '   本步骤不调用模型；平台额度与收益另计。',
                                '   This step calls no model; platform quota and income are tracked separately.'))
        elif state in ("queued", "blocked"):
            lines.append(text(lang, "   消耗：0 token；折合 $0（本步骤尚未调用模型）",
                                "   Cost: 0 tokens; $0 equivalent (no model call in this step yet)"))
        else:
            lines.append(text(lang, "   消耗：token 暂无可核对记录；折合金额：未知",
                                "   Cost: no verifiable token records; equivalent amount: unknown"))
        if state == "queued":
            deps = payload.get("depends_on", [])
            waiting = [d for d in deps if d not in all_rows or all_rows[d]["status"] != "succeeded"]
            if paused:
                reason = text(lang, "调度已暂停。", "Scheduler paused.")
            elif waiting:
                reason = text(lang, "等待前置任务：", "Waiting on prerequisites: ") + "；".join(
                    f"{title(all_rows[d], lang)[0]}（{state_label(all_rows[d]['status'], lang)}）"
                    if d in all_rows else f"{d}{text(lang, '（记录缺失）', ' (record missing)')}" for d in waiting)
            elif util.parse_iso(row["not_before"]) > now:
                reason = text(lang, f"尚未到执行时间：{local_time(row['not_before'])}。",
                                f"Not due until {local_time(row['not_before'])}.")
            elif active:
                reason = text(lang, "已到执行时间，等待当前任务释放执行位置。",
                                "Due; waiting for the running task to release the execution slot.")
            else:
                reason = text(lang, "已到执行时间，等待调度器领取；实际能否启动仍需通过预算和授权检查。",
                                "Due; waiting for the scheduler to claim. Actual start still requires budget and authorization checks.")
            lines.append(text(lang, f"   等待原因：{reason}", f"   Waiting because: {reason}"))
        elif state in ("running", "claimed"):
            call = conn.execute(
                "SELECT started_at FROM agent_calls WHERE purpose=? "
                "AND started_at>=? AND status='running' ORDER BY started_at DESC LIMIT 1",
                (payload.get("purpose"), row["created_at"])).fetchone()
            if call:
                elapsed = max(0, int((now - util.parse_iso(call["started_at"])).total_seconds()))
                lines.append(text(lang, f"   开始：{local_time(call['started_at'])}，已运行 {elapsed // 60} 分 {elapsed % 60} 秒。",
                                    f"   Started: {local_time(call['started_at'])}, running for {elapsed // 60}m {elapsed % 60}s."))
                lines.append(text(lang, "   进度：模型正在处理，尚未落账完成；无法可靠估计百分比或剩余时长。",
                                    "   Progress: the model is working; nothing finalized in the ledger yet. No reliable percentage or ETA."))
            elif row['kind'] != 'brain_simulation':
                lines.append(text(lang, "   进度：任务已领取，尚无对应的在途模型记录；不能据此判定模型正在计算。",
                                    "   Progress: task claimed but no in-flight model record yet; do not assume the model is computing."))
        elif state == "succeeded":
            lines.append(text(lang, f"   完成：{local_time(row['updated_at'])}", f"   Finished: {local_time(row['updated_at'])}"))
            if payload.get('routing'):
                lines.append(text(lang, f"   产物：{os.path.join(payload['job_dir'], 'result.json')}",
                                    f"   Artifacts: {os.path.join(payload['job_dir'], 'result.json')}"))
            workdir = cfg.model(payload.get("agent", "")).get("workdir")
            if workdir:
                for artifact in payload.get("artifacts", []):
                    lines.append(text(lang, f"   产物：{os.path.join(cfg.resolve(workdir), artifact)}",
                                        f"   Artifacts: {os.path.join(cfg.resolve(workdir), artifact)}"))
        elif state == "blocked" and row["kind"] == "simulation" and cfg.adapter_mode() == "manual":
            lines.append(text(lang, "   原因：这是旧 manual 模式任务，仍需网页结果导入；新自动模拟使用 wq brain enqueue。",
                                "   Reason: legacy manual-mode task; import web results. New automatic simulations use wq brain enqueue."))
            lines.append(text(lang, "   影响：该模拟任务不会自动重试；不影响没有依赖它的 Grok / Devin 研究任务。",
                                "   Impact: this simulation will not retry automatically; research tasks not depending on it are unaffected."))
        if row["last_error"] and not (state == "blocked" and row["kind"] == "simulation" and cfg.adapter_mode() == "manual"):
            label = text(lang, '上次尝试', 'Last attempt') if route and state in ('running', 'claimed') else text(lang, '原因记录', 'Recorded reason')
            lines.append(f"   {label}：{row['last_error']}" if lang == 'zh' else f"   {label}: {row['last_error']}")
        count_label = text(lang, "队列处理（含轮询）", "Queue processing (incl. polling)") if row["kind"] in ("brain_simulation", "brain_submission") else text(lang, "已尝试", "Attempts")
        lines.append(text(lang, f"   编号：{row['task_id']} · {count_label} {row['attempts']} 次",
                            f"   ID: {row['task_id']} · {count_label}: {row['attempts']}"))
    if cfg.get('autopilot'):
        lines += ['', text(lang, '自动研究：', 'Automatic research: ') +
                  translate(store.get_flag(conn, 'autopilot_message', '尚未启动'), lang),
                  text(lang, '详细状态：./wq autopilot status', 'Details: ./wq autopilot status')]
    lines += ["", text(lang, "刷新：再次执行 ./wq tasks    原始数据：./wq tasks --json",
                        "Refresh: run ./wq tasks again    Raw data: ./wq tasks --json"),
              text(lang, '暂停：./wq pause --reason "用户暂停"', 'Pause: ./wq pause --reason "manual"')]
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
