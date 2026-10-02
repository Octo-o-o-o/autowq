"""Read-only menu data from the local ledger and frozen provider snapshots.

双语：所有文案经 lang 参数渲染；账本内 autopilot 写入的中文 message/outcome
经 i18n.translate 在显示层翻译。时间统一换算为北京时间（UTC+8）。
"""
import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path

from . import routing, store, task_view, util
from .i18n import text, translate

BEIJING = dt.timezone(dt.timedelta(hours=8))
CLI_NAMES = {'grok': 'Grok Build CLI', 'devin': 'Devin CLI', 'claude': 'Claude Code CLI',
             'codex': 'Codex CLI', 'cursor': 'Cursor CLI', 'zcode': 'ZCode CLI'}
ISO_TIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)')


def beijing(value, missing='尚无记录', fmt='%Y年%m月%d日 %H:%M:%S', lang='zh'):
    if not value:
        return text(lang, missing, {'尚无记录': 'No records yet'}.get(missing, missing))
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return text(lang, '时间缺少时区，待核实', 'Timestamp missing timezone; unverified')
        if lang != 'zh':
            fmt = '%Y-%m-%d %H:%M:%S'
        return parsed.astimezone(BEIJING).strftime(fmt)
    except (ValueError, TypeError):
        return text(lang, '时间记录无效', 'Invalid timestamp')


def localize_message(message, lang='zh'):
    """账本消息：先按界面语言翻译已知文案，再把内嵌 ISO 时间换算为北京时间。"""
    return ISO_TIME.sub(lambda match: beijing(match.group(), fmt='%Y-%m-%d %H:%M:%S'), message or '')


def connect_readonly(cfg):
    conn = sqlite3.connect(Path(cfg.db_path).as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('BEGIN')
    return conn


def provider_label(name, definition, lang='zh'):
    cli = CLI_NAMES.get(name, definition.get('label') or name)
    transport = definition.get('transport', '')
    if isinstance(transport, str) and (transport.startswith('openai') or transport == 'anthropic_messages'):
        cli = name + ' API'
    return f"{cli} · {definition.get('model') or text(lang, '模型未记录', 'model not recorded')}"


def planned_provider(conn, cycle_id, role):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_events'").fetchone():
        return None
    row = conn.execute("SELECT detail FROM research_events WHERE cycle_id=? AND kind='route_plan' ORDER BY event_id DESC LIMIT 1", (cycle_id,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        plan = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return plan.get('research_preferred' if role == 'research' else 'review_preferred')


def cycle_role_model(conn, cfg, cycle, role, lang='zh', compact=False):
    tid = cycle['research_task' if role == 'research' else 'review_task']
    if tid:
        return task_model(conn, tid, compact=compact, lang=lang)
    planned = planned_provider(conn, cycle['cycle_id'], role)
    if not planned:
        return '—' if compact else text(lang, '尚未安排', 'Not scheduled')
    try:
        definition = routing.catalog(cfg)['providers'].get(planned) or {}
    except (OSError, ValueError, KeyError, TypeError):
        definition = {}
    shown = (definition.get('model') if compact and definition.get('model') else None) or (
        provider_label(planned, definition, lang) if definition else planned)
    return shown + text(lang, '（待开始）', ' (not started)')


def task_model(conn, tid, compact=False, lang='zh'):
    if not tid:
        return '—' if compact else text(lang, '无关联任务', 'No linked task')
    row = conn.execute('SELECT snapshot_json,provider_index FROM task_routes WHERE task_id=?', (tid,)).fetchone()
    if row:
        snapshot = json.loads(row['snapshot_json'])
        index = row['provider_index']
        # The successful result is stronger evidence than a current fallback index.
        result = conn.execute("SELECT detail_json FROM attempts WHERE task_id=? AND event='provider_result' AND outcome='succeeded' ORDER BY attempt_id DESC LIMIT 1", (tid,)).fetchone()
        name = json.loads(result[0]).get('provider') if result else None
        if not name and index < len(snapshot['chain']):
            name = snapshot['chain'][index]
        if name:
            definition = snapshot.get('providers', {}).get(name, {})
            if compact:
                return definition.get('model') or name
            return provider_label(name, definition, lang)
    return text(lang, '模型未记录', 'Model not recorded')


def next_models(conn, cfg, lang='zh'):
    """预估下一轮要新建的路由。待生效的临时预设优先；进行中轮次已冻结的预设不代表下一轮。"""
    data = routing.catalog(cfg)
    pending = store.get_flag(conn, 'preset_once') or ''
    if pending in data['presets']:
        name = pending
    elif cfg.get('workflow', 'file'):
        name = 'advanced'
    else:
        name = store.get_flag(conn, 'active_preset', data['default'])
        if name not in data['presets']:
            name = data['default']
    routes = data['presets'][name]['routes']
    selected = {}
    titles = []
    for role in ('research', 'review'):
        chain = routes[role]
        eligible = [item for item in chain if item != selected.get('research') or role == 'research']
        name = next((item for item in eligible if not routing._unavailable(conn, cfg, item)), None)
        selected[role] = name
        label = text(lang, '研究', 'Research') if role == 'research' else text(lang, '审查', 'Review')
        shown = provider_label(name, data['providers'][name], lang) if name else text(lang, '暂无可用渠道', 'no provider available')
        preferred = next((item for item in eligible if item != name), None)
        if name and preferred and preferred == eligible[0] and routing._unavailable(conn, cfg, preferred):
            shown += text(lang, f"（{provider_label(preferred, data['providers'][preferred], lang)} 不可用：{routing._unavailable(conn, cfg, preferred)}）",
                          f" ({provider_label(preferred, data['providers'][preferred], lang)} unavailable: {routing._unavailable(conn, cfg, preferred)})")
        titles.append({'title': label + '：' + shown})
    return titles


def lane_rows(conn, cfg, auto=None, lang='zh'):
    """每条泳道的当前状态：开放轮次明细，多泳道时空闲泳道附冷却时间与预测渠道对。"""
    from . import autopilot
    if auto is None:
        auto = autopilot.status(conn, cfg)
    open_cycles = auto.get('open_cycles') or []
    n = int(auto.get('concurrent_lanes') or 1)
    try:
        data = routing.catalog(cfg)
        provider_defs = data['providers']
    except (OSError, ValueError, KeyError, TypeError):
        data = None
        provider_defs = {}

    def _label(name):
        return provider_label(name, provider_defs.get(name) or {}, lang) if name else ''

    state_names = {'researching': ('研究', 'researching'), 'reviewing': ('审查', 'reviewing'),
                   'simulating': ('模拟', 'simulating')}
    open_by_lane = {(r.get('lane') or 0): r for r in open_cycles}
    try:
        pins = autopilot.lane_pins(cfg)
    except (ValueError, TypeError):
        pins = {}
    rows = []
    for lane_no in sorted(open_by_lane):
        r = open_by_lane[lane_no]
        rows.append({'lane': r.get('lane'), 'cycle_id': r.get('cycle_id'), 'pinned': lane_no in pins,
                     'state': r.get('state') or '',
                     'state_label': text(lang, *state_names.get(r.get('state'), (r.get('state') or '', r.get('state') or ''))),
                     'research': _label(r.get('research_preferred')), 'review': _label(r.get('review_preferred')),
                     'research_task': r.get('research_task'), 'review_task': r.get('review_task')})
    if n <= 1:
        return rows
    try:
        taken = autopilot._open_route_prefs(conn, open_cycles)
        preset = None
        if data is not None:
            pending = store.get_flag(conn, 'preset_once') or ''
            if pending in data['presets']:
                name = pending
            elif cfg.get('workflow', 'file'):
                name = 'advanced'
            else:
                name = store.get_flag(conn, 'active_preset', data['default'])
                if name not in data['presets']:
                    name = data['default']
            preset = data['presets'].get(name)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.OperationalError):
        return rows
    for lane_no in range(n):
        if lane_no in open_by_lane:
            continue
        if auto.get('paused'):
            state_label = text(lang, '已暂停', 'paused')
        elif not (auto.get('enabled') or auto.get('run_next_requested')):
            state_label = text(lang, '已停用', 'disabled')
        else:
            try:
                gate = autopilot._lane_gate_at(conn, lane_no, cfg)
            except sqlite3.OperationalError:
                gate = None
            if gate and util.parse_iso(gate) > util.now():
                when = beijing(gate, '', fmt='%H:%M:%S', lang=lang)
                state_label = text(lang, f'冷却至 {when}', f'cooldown until {when}')
            else:
                state_label = text(lang, '待调度开放', 'awaiting scheduler')
        pair = None
        if preset is not None:
            try:
                pair = autopilot._lane_pair(conn, cfg, data, preset, taken, lane=lane_no)
            except (ValueError, KeyError, TypeError):
                pair = None
        if pair:
            taken['research'].add(pair[0])
            taken['review'].add(pair[1])
        rows.append({'lane': lane_no, 'cycle_id': None, 'state': 'idle', 'state_label': state_label,
                     'pinned': lane_no in pins,
                     'research': _label(pair[0]) if pair else '', 'review': _label(pair[1]) if pair else '',
                     'research_task': None, 'review_task': None})
    rows.sort(key=lambda r: r.get('lane') or 0)
    return rows


def cycle_directive(conn, cycle, lang='zh'):
    """本轮预登记的研究指令：单角色基线 / 有限组合 / 自由探索。"""
    cid = cycle['cycle_id']
    from . import research_campaign
    campaign = research_campaign.assignment(conn, cid)
    if campaign:
        return text(lang, '增量研究 · ', 'Campaign · ') + campaign['hypothesis'] + ' · ' + campaign['phase']
    focus = store.get_flag(conn, f'autopilot_focus_{cid}')
    if focus:
        return text(lang, '单角色基线 · ', 'Single-role baseline · ') + focus
    try:
        plan = conn.execute('SELECT plan_json FROM combination_plans WHERE cycle_id=?', (cid,)).fetchone()
    except sqlite3.OperationalError:
        plan = None  # 旧账本可能没有 feedback 表
    if plan:
        parents = json.loads(plan[0]).get('parent_cycles') or []
        suffix = '+'.join(str(p) for p in parents)
        if not suffix:
            return text(lang, '组合实验', 'Combination experiment')
        return text(lang, '组合实验 · 父轮 ', 'Combination experiment · parents ') + suffix
    return text(lang, '自由探索', 'Free exploration')


def standby_alphas(conn, cycle):
    """内部已通过、因 24 小时提交上限还在等待自动提交的 Alpha。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='submission_standby'").fetchone():
        return []
    rows = conn.execute('''SELECT b.alpha_id FROM brain_runs b JOIN submission_standby s ON s.alpha_id=b.alpha_id
        WHERE b.task_id=? AND s.state IN ('waiting','queued')
        AND NOT EXISTS (SELECT 1 FROM submissions sub WHERE sub.remote_id=s.alpha_id
            AND sub.status IN ('accepted','final_valid','final_invalid'))
        UNION SELECT b.alpha_id FROM cycle_simulations v JOIN brain_runs b ON b.task_id=v.task_id
        JOIN submission_standby s ON s.alpha_id=b.alpha_id
        WHERE v.cycle_id=? AND s.state IN ('waiting','queued')
        AND NOT EXISTS (SELECT 1 FROM submissions sub WHERE sub.remote_id=s.alpha_id
            AND sub.status IN ('accepted','final_valid','final_invalid'))''', (cycle['simulation_task'], cycle['cycle_id']))
    return [row[0] for row in rows if row[0]]


def submitted_alphas(conn, cycle):
    """本轮（含变体）产出且平台已接收的 Alpha。"""
    rows = conn.execute('''SELECT b.alpha_id FROM brain_runs b WHERE b.task_id=?
        UNION SELECT b.alpha_id FROM cycle_simulations v JOIN brain_runs b ON b.task_id=v.task_id WHERE v.cycle_id=?''',
        (cycle['simulation_task'], cycle['cycle_id']))
    alphas = [row[0] for row in rows if row[0]]
    if not alphas:
        return []
    marks = ','.join('?' * len(alphas))
    rows = conn.execute(f"SELECT remote_id FROM submissions WHERE remote_id IN ({marks})"
                        " AND status IN ('accepted','final_valid')", alphas)
    return [row[0] for row in rows]


PROBLEM_PREFIXES = ('任务未完成', '反馈资料未完成', '输入或验收错误', '研究范围配置改变', '模型主动放弃', '审查依据待复核')


def review_details(conn, cycle, force=False, lang='zh'):
    """只读原始审查产物；不改变历史裁决，也不将旧产物按新契约重新裁决。"""
    from . import autopilot
    if not force and not (cycle['outcome'] or '').startswith(('模型审查拒绝', '审查依据待复核')):
        return [], []
    try:
        obj = autopilot.artifact(conn, cycle['review_task'])
        autopilot.validate_review(obj, cycle['candidate_hash'])
        review = obj['review']
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        return [text(lang, '审查详情不可用：原始产物缺失、损坏或候选绑定不符',
                     'Review details unavailable: original artifact missing, damaged, or candidate mismatch')], \
               [text(lang, '历史裁决保持原样；未重新审查。', 'Historical verdicts unchanged; nothing re-reviewed.')]
    failed = '、'.join(autopilot.review_label(key, lang) for key, ok in review['checks'].items() if not ok)
    reason = review['reason'].strip()
    compact = ' '.join(reason.split())
    none_label = text(lang, '无', 'None')
    lines = [text(lang, '未通过项：', 'Failed checks: ') + (failed or (text(lang, '无（模型裁决拒绝）', 'None (model verdict: reject)') if not review['accept'] else none_label)),
             text(lang, '审查理由：', 'Review reason: ') + compact[:100] + (text(lang, '…（悬停查看全文）', '… (hover for full text)') if len(compact) > 100 else '')]
    detail = [text(lang, '模型裁决：', 'Model verdict: ') + (text(lang, '接受', 'Accept') if review['accept'] else text(lang, '拒绝', 'Reject')),
              text(lang, '未通过项：', 'Failed checks: ') + (failed or none_label),
              text(lang, '审查理由全文：', 'Full review reason: ') + reason]
    if review['accept'] != all(review['checks'].values()):
        detail.append(text(lang, '注意：模型裁决与检查项不一致，历史仍未放行。',
                           'Note: the model verdict disagrees with the checks; history stays unapproved.'))
    for item in review.get('blocking_evidence') or []:
        if isinstance(item, dict):
            detail.append(text(lang, '模型所列依据（未作语义复核）：', 'Model-cited evidence (not semantically re-checked): ') +
                          str(item.get('check', '')) + ' / ' + str(item.get('quote', '')) + ' / ' + str(item.get('explanation', '')))
    for item in review.get('resolutions') or []:
        if isinstance(item, dict):
            detail.append(text(lang, '补充审查回应：', 'Follow-up review response: ') + str(item.get('check', '')) + ' / ' +
                          str(item.get('disposition', '')) + ' / ' + str(item.get('quote', '')) + ' / ' + str(item.get('explanation', '')))
    return lines, detail


def cycle_badge(conn, cycle, lang='zh'):
    """返回 (kind, 文案)：submitted/failed/progress；正常完结不标注。"""
    if cycle['state'] != 'closed':
        return 'progress', task_view.cycle_state(cycle['state'], lang)
    if submitted_alphas(conn, cycle):
        return 'submitted', text(lang, '已提交', 'Submitted')
    if standby_alphas(conn, cycle):
        return 'standby', text(lang, '备选提交', 'Queued to submit')
    outcome = cycle['outcome'] or ''
    if outcome.startswith(PROBLEM_PREFIXES) or '错误' in outcome or '未通过' in outcome:
        return 'failed', text(lang, '失败', 'Failed')
    return None, None


def history_token(conn):
    """轮次或任务一变，菜单就知道历史需要重画。"""
    cycle = conn.execute('SELECT cycle_id, state, updated_at FROM research_cycles ORDER BY cycle_id DESC LIMIT 1').fetchone()
    task = conn.execute('SELECT MAX(updated_at) FROM tasks').fetchone()
    if not cycle:
        return '0'
    return f"{cycle['cycle_id']}|{cycle['state']}|{cycle['updated_at']}|{task[0] or ''}"


def history(conn, cfg, lang='zh'):
    tasks = {row['task_id']: row for row in store.list_tasks(conn)}
    by_purpose = {}
    for row in tasks.values():
        purpose = json.loads(row['payload_json'] or '{}').get('purpose')
        if purpose:
            by_purpose.setdefault(purpose, []).append(row)
    task_view._TASKS_BY_PURPOSE[id(conn)] = by_purpose
    try:
        return _history(conn, cfg, lang, tasks)
    finally:
        task_view._TASKS_BY_PURPOSE.pop(id(conn), None)


def _history(conn, cfg, lang, tasks):
    groups = {cycle['cycle_id']: members for cycle, members in task_view.task_groups(conn, list(tasks.values())) if cycle}
    has_fallbacks = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_fallbacks'").fetchone()
    starts = {row[0]: row[1] for row in conn.execute(
        "SELECT task_id, MIN(created_at) FROM attempts WHERE event='provider_start' GROUP BY task_id")}
    fallback_by_cycle = {}
    if has_fallbacks:
        for row in conn.execute('SELECT * FROM research_fallbacks'):
            fallback_by_cycle[row['cycle_id']] = row
    try:
        from . import autopilot
        multi_lane = autopilot.lane_limit(cfg) > 1 or any(
            (r[0] or 0) > 0 for r in conn.execute("SELECT DISTINCT lane FROM research_cycles"))
    except (ImportError, AttributeError, sqlite3.OperationalError):
        multi_lane = False
    entries = []
    for cycle in conn.execute('SELECT * FROM research_cycles ORDER BY cycle_id DESC'):
        members = groups.get(cycle['cycle_id'], [])
        cost_data = task_view.group_cost_data(conn, members, cfg)
        cost = task_view.render_cost(cost_data, lang)
        if cost_data['calls'] or cost_data['missing']:
            if cost_data['known_dollars'] is not None:
                short = text(lang, '小计', 'Subtotal') + f" ${cost_data['known_dollars']:.4f}"
            else:
                short = text(lang, '成本未知', 'Cost unknown')
            if cost_data['unknown_calls']:
                short += text(lang, ' + 未知', ' + unknown')
        else:
            short = text(lang, '尚未调用', 'No calls')
        start = starts.get(cycle['research_task'])
        badge, badge_text = cycle_badge(conn, cycle, lang)
        raw_outcome = cycle['outcome'] or ''
        if raw_outcome.startswith('任务未完成'):
            reasons = [t['last_error'] for t in members if t['status'] in ('failed', 'blocked', 'aborted') and t['last_error']]
            for reason in reasons:
                if reason not in raw_outcome:
                    raw_outcome += '；' + reason
        outcome = translate(raw_outcome, lang) or text(lang, '进行中', 'In progress')
        lane_no = cycle['lane'] if 'lane' in cycle.keys() else 0
        lane_tag = text(lang, f"泳道{(lane_no or 0) + 1} · ", f"lane {(lane_no or 0) + 1} · ") if multi_lane else ''
        title = (text(lang, f"第 {cycle['cycle_id']} 轮", f"Cycle {cycle['cycle_id']}") + ' · ' + lane_tag
                 + cycle_directive(conn, cycle, lang) + (f" · {badge_text}" if badge_text else ''))
        fallback = fallback_by_cycle.get(cycle['cycle_id'])
        retry = tasks.get(fallback['fallback_task']) if fallback else None
        review_lines, review_detail = review_details(conn, cycle, force=bool(fallback and fallback['stage'] == 'review' and retry and retry['status'] == 'succeeded'), lang=lang)
        if fallback:
            fallback_label = text(lang, '补充尝试 1/1：', 'Fallback attempt 1/1: ')
            review_lines.append(fallback_label + (task_view.state_label(retry['status'], lang) if retry else text(lang, '任务缺失', 'task missing')))
            review_detail.extend([text(lang, '首次任务：', 'Original task: ') + fallback['original_task'],
                                  text(lang, '首次原因：', 'Original reason: ') + translate(fallback['reason'], lang),
                                  text(lang, '补充任务：', 'Fallback task: ') + fallback['fallback_task'],
                                  text(lang, '补充渠道：', 'Fallback provider: ') + task_model(conn, fallback['fallback_task'], lang=lang)])
            if fallback['stage'] == 'review':
                original_cycle = dict(cycle, review_task=fallback['original_task'], outcome='模型审查拒绝')
                _, original_detail = review_details(conn, original_cycle, lang=lang)
                review_detail.extend([text(lang, '首次审查记录（未改写）：', 'Original review record (unedited):'), *original_detail])
        if cycle['state'] == 'closed':
            time_line = beijing(cycle['updated_at'], '—', '%m-%d %H:%M', lang=lang) + text(lang, ' 完成', ' closed')
        else:
            time_line = (beijing(start, '—', '%m-%d %H:%M', lang=lang) + text(lang, ' 开始', ' started')) if start else text(lang, '尚未开始', 'Not started')
        label = lambda zh, en: text(lang, zh, en)
        entries.append({'title': title, 'badge': badge, 'lines': [
                time_line,
                label('研究 ', 'Research ') + cycle_role_model(conn, cfg, cycle, 'research', lang, compact=True) +
                ' · ' + label('审查 ', 'Review ') + cycle_role_model(conn, cfg, cycle, 'review', lang, compact=True),
                short,
                label('结果 ', 'Outcome ') + outcome, *review_lines],
            'detail': [label('创建：', 'Created: ') + beijing(cycle['created_at'], lang=lang),
                       label('开始：', 'Started: ') + beijing(start, text(lang, '无模型启动记录', 'No model start recorded'), lang=lang),
                       label('完成：', 'Closed: ') + (beijing(cycle['updated_at'], lang=lang) if cycle['state'] == 'closed' else text(lang, '尚未完成', 'Not closed yet')),
                       label('研究：', 'Research: ') + cycle_role_model(conn, cfg, cycle, 'research', lang),
                       label('审查：', 'Review: ') + cycle_role_model(conn, cfg, cycle, 'review', lang),
                       *cost.split('；' if lang == 'zh' else '; '),
                       label('结果：', 'Outcome: ') + outcome, *review_detail]})
    return {'entries': entries, 'count': len(entries),
            'note': text(lang, '时间均为北京时间（UTC+8）。', 'All times are Beijing time (UTC+8).')}


def alpha_cycle(conn, alpha_id):
    return conn.execute('''SELECT c.* FROM research_cycles c JOIN brain_runs b ON b.task_id=c.simulation_task
        WHERE b.alpha_id=? UNION SELECT c.* FROM research_cycles c JOIN cycle_simulations v ON v.cycle_id=c.cycle_id
        JOIN brain_runs b ON b.task_id=v.task_id WHERE b.alpha_id=? LIMIT 1''', (alpha_id, alpha_id)).fetchone()


def notifications_enabled(cfg):
    return cfg.get('desktop', 'notifications', default=True) is not False


# 通知事件：按 updated_at 增量消费（状态转为 accepted/final_valid 或 failed 的时刻），
# 每类各存一条水位线；菜单栏未运行期间不推进，恢复后一次性补报（每次最多 10 条）。
def _notify_builders(lang):
    return (
        lambda row: {'id': f"submitted-{row['remote_id']}-{row['status']}", 'kind': 'submitted',
                     'title': text(lang, 'Alpha 提交成功', 'Alpha submitted'),
                     'body': text(lang, f"{row['remote_id']} 已提交（{row['status']}）",
                                  f"{row['remote_id']} submitted ({row['status']})")},
        lambda row: {'id': f"failed-{row['task_id']}", 'kind': 'failed',
                     'title': text(lang, '任务失败', 'Task failed'),
                     'body': text(lang, f"{row['kind']} {row['task_id']}：{(row['last_error'] or '原因未记录')[:120]}",
                                  f"{row['kind']} {row['task_id']}: {(row['last_error'] or 'reason not recorded')[:120]}")},
    )


NOTIFY_SOURCES = (
    ('desktop_notified_submissions',
     """SELECT sub.remote_id, sub.status, sub.updated_at FROM submissions sub
        JOIN simulations s ON s.sim_id=sub.sim_id
        WHERE s.synthetic=0 AND sub.status IN ('accepted','final_valid') AND sub.updated_at>?
        ORDER BY sub.updated_at LIMIT 10"""),
    ('desktop_notified_failures',
     """SELECT task_id,kind,last_error,updated_at FROM tasks
        WHERE status='failed' AND updated_at>? ORDER BY updated_at LIMIT 10"""),
)


def pending_notifications(conn, lang='zh'):
    """消费自上次检查以来的新事件并推进水位线；返回待通知列表（需写连接）。"""
    items = []
    pending=store.get_flag(conn,'handoff_batch_pending')
    if pending:
        batch=json.loads(pending)
        if store.get_flag(conn,'desktop_handoff_notified')!=batch['id']:
            items.append({'id':'research-handoff:'+batch['id'],'kind':'research','title':text(lang,'研究取证包已整理','Research evidence requests ready'),
                'body':text(lang,'请查看本地 research-inbox；元数据核验后仍需语义证据。','Review the local research-inbox; semantic evidence is still required.')+' '+batch['path']})
            store.set_flag(conn,'desktop_handoff_notified',batch['id'])
    builders = _notify_builders(lang)
    for (key, query), build in zip(NOTIFY_SOURCES, builders):
        mark = store.get_flag(conn, key)
        if mark is None:
            store.set_flag(conn, key, util.now_iso())   # 首次只建基线，不回放历史
            continue
        rows = conn.execute(query, (mark,)).fetchall()
        if rows:
            store.set_flag(conn, key, rows[-1]['updated_at'])
            items.extend(build(row) for row in rows)
    return items


def cycle_phrase(cycle_id, lang='zh'):
    if not cycle_id:
        return None
    return text(lang, f'第 {cycle_id} 轮', f'Cycle {cycle_id}')


def alpha_menu_title(alpha_id, when, cycle_id, lang, missing):
    """列表行要能和轮次历史对上：历史只显示轮号，这里把轮号放在 Alpha ID 前面。"""
    stamp = beijing(when, missing, lang=lang)
    phrase = cycle_phrase(cycle_id, lang)
    return ' · '.join(part for part in (phrase, alpha_id, stamp) if part)


def submitted(conn, lang='zh'):
    entries = []
    rows = conn.execute('''SELECT sub.*,s.stats_json,c.config_json,c.expression FROM submissions sub
        JOIN simulations s ON s.sim_id=sub.sim_id JOIN candidates c ON c.candidate_id=s.candidate_id
        WHERE s.synthetic=0 AND sub.status IN ('accepted','final_valid','final_invalid') ORDER BY sub.created_at DESC''')
    for row in rows:
        receipt = json.loads(row['receipt_json'] or '{}')
        settings = json.loads(row['config_json'])
        stats = json.loads(row['stats_json'] or '{}')
        cycle = alpha_cycle(conn, row['remote_id'])
        if cycle:
            model_lines = [text(lang, '研究：', 'Research: ') + task_model(conn, cycle['research_task'], lang=lang),
                           text(lang, '审查：', 'Review: ') + task_model(conn, cycle['review_task'], lang=lang)]
        else:
            model_lines = [text(lang, '模型：未关联自动轮次', 'Model: not linked to an automatic cycle')]
        unknown = text(lang, '未知', 'unknown')
        metrics = ' · '.join(f'{key} {stats.get(key, unknown)}' for key in ('sharpe', 'fitness', 'turnover', 'returns', 'drawdown', 'margin'))
        brain_settings = settings.get('extra', {}).get('brain_settings', settings)
        params = [f'{key}：{brain_settings[key]}' for key in ('region', 'universe', 'instrumentType', 'delay', 'decay', 'neutralization', 'truncation', 'testPeriod', 'nanHandling', 'pasteurization', 'unitHandling', 'language') if key in brain_settings]
        cycle_id = cycle['cycle_id'] if cycle else None
        entries.append({'title': alpha_menu_title(row['remote_id'], receipt.get('dateSubmitted'), cycle_id, lang,
                                                   text(lang, '提交时间未记录', 'submission time not recorded')),
            'badge': 'submitted' if row['status'] in ('accepted', 'final_valid') else None,
            'lines': [text(lang, '状态：', 'Status: ') + row['status'] + ' / ' + str(receipt.get('platform_status', unknown)),
                      text(lang, '轮次：', 'Cycle: ') + (cycle_phrase(cycle_id, lang) or text(lang, '直接／人工实验', 'direct / manual experiment')),
                      *model_lines, text(lang, '指标：', 'Metrics: ') + metrics, *params,
                      text(lang, '表达式：', 'Expression: ') + row['expression'],
                      text(lang, '本地接收账本，不代表平台现金收益。', 'Locally recorded receipt; not proof of platform cash income.')]})
    return {'entries': entries, 'count': len(entries)}


def standby(conn, lang='zh'):
    """内部已通过、因提交上限尚未发出的 Alpha。明细结构与已提交列表一致。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='submission_standby'").fetchone():
        return {'entries': [], 'count': 0}
    entries = []
    rows = conn.execute('''SELECT s.alpha_id, s.cycle_id, s.state, s.created_at, s.updated_at, sim.stats_json, c.config_json, c.expression
        FROM submission_standby s
        LEFT JOIN simulations sim ON sim.remote_id=s.alpha_id AND sim.synthetic=0
        LEFT JOIN candidates c ON c.candidate_id=sim.candidate_id
        WHERE s.state IN ('waiting','queued')
        AND NOT EXISTS (SELECT 1 FROM submissions sub WHERE sub.remote_id=s.alpha_id
            AND sub.status IN ('accepted','final_valid','final_invalid'))
        ORDER BY s.created_at''')
    quota_note = text(lang, '等待 24 小时提交空位；空位出现后自动提交，发出前会再读官方检查。',
                      'Waiting for a 24-hour submission slot; submitted automatically when one opens, after a fresh official check.')
    for row in rows:
        stats = json.loads(row['stats_json'] or '{}')
        settings = json.loads(row['config_json'] or '{}')
        cycle = alpha_cycle(conn, row['alpha_id'])
        if cycle:
            model_lines = [text(lang, '研究：', 'Research: ') + task_model(conn, cycle['research_task'], lang=lang),
                           text(lang, '审查：', 'Review: ') + task_model(conn, cycle['review_task'], lang=lang)]
        else:
            model_lines = [text(lang, '模型：未关联自动轮次', 'Model: not linked to an automatic cycle')]
        unknown = text(lang, '未知', 'unknown')
        metrics = ' · '.join(f'{key} {stats.get(key, unknown)}' for key in ('sharpe', 'fitness', 'turnover', 'returns', 'drawdown', 'margin'))
        brain_settings = settings.get('extra', {}).get('brain_settings', settings)
        params = [f'{key}：{brain_settings[key]}' for key in ('region', 'universe', 'instrumentType', 'delay', 'decay', 'neutralization', 'truncation', 'testPeriod', 'nanHandling', 'pasteurization', 'unitHandling', 'language') if isinstance(brain_settings, dict) and key in brain_settings]
        state = text(lang, '等待空位', 'waiting for a slot') if row['state'] == 'waiting' else text(lang, '已入队，等待发出', 'queued, waiting to post')
        cycle_id = cycle['cycle_id'] if cycle else row['cycle_id']
        entries.append({'title': alpha_menu_title(row['alpha_id'], row['created_at'], cycle_id, lang, unknown),
            'badge': 'standby',
            'lines': [text(lang, '状态：备选提交 / ', 'Status: queued to submit / ') + state,
                      text(lang, '轮次：', 'Cycle: ') + (cycle_phrase(cycle_id, lang) or text(lang, '直接／人工实验', 'direct / manual experiment')),
                      *model_lines, text(lang, '指标：', 'Metrics: ') + metrics, *params,
                      text(lang, '表达式：', 'Expression: ') + (row['expression'] or unknown),
                      quota_note]})
    return {'entries': entries, 'count': len(entries)}


def research_token(conn, cfg):
    """研究进展只在回测资料、组合计划或策略文件变化时重算。"""
    feedback_row = conn.execute('SELECT COUNT(*), MAX(updated_at) FROM research_feedback').fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_feedback'").fetchone() else (0, '')
    plans = conn.execute('SELECT COUNT(*), MAX(created_at) FROM combination_plans').fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='combination_plans'").fetchone() else (0, '')
    closed = conn.execute("SELECT COUNT(*) FROM research_cycles WHERE state='closed'").fetchone()[0]
    policy_file = cfg.get('autopilot', 'policy_file', default='')
    try:
        policy_mtime = Path(cfg.resolve(policy_file)).stat().st_mtime if policy_file else 0
    except OSError:
        policy_mtime = 0
    return f"{feedback_row[0]}|{feedback_row[1]}|{plans[0]}|{plans[1]}|{closed}|{policy_mtime}"


def _combination_summary(conn, cfg, policy, cap):
    from . import feedback
    token = research_token(conn, cfg) + '|' + str(cap)
    cache = Path(cfg.db_path).parent / 'cache' / 'combination-summary.json'
    try:
        cached = json.loads(cache.read_text())
        if cached.get('token') == token:
            return cached['summary']
    except (OSError, ValueError, TypeError):
        pass
    full = feedback.combination_diagnostics(conn, cap, current_policy=policy)
    summary = {key: full[key] for key in ('registered', 'cap', 'counts', 'first_reason_counts')}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'token': token, 'summary': summary}, ensure_ascii=False))
        os.replace(temporary, cache)
    except OSError:
        pass
    return summary


def research(conn, cfg, lang='zh'):
    """Read a consistent ledger snapshot without creating tables or changing research state."""
    from . import autopilot, research_campaign, research_learning
    entries = []
    def add(zh, en, lines):
        entries.append({'title': text(lang, zh, en), 'lines': lines})
    p = autopilot.policy(cfg)
    deadlines = [
        ('研究策略', 'Research policy', p.get('valid_until')),
        ('模型路由', 'Model routing', cfg.get('routing','authorized_until')),
        ('BRAIN API', 'BRAIN API', cfg.get('brain_api','authorized_until')),
        ('模型预算豁免', 'Model budget waiver', cfg.get('debug_authorization','expires_at')),
        ('正式提交', 'Formal submission', cfg.get('brain_submission','authorized_until')),
    ]
    add('授权期限（北京时间）', 'Authorization deadlines (Beijing time)',
        [text(lang,zh,en) + '：' + beijing(value,lang=lang) for zh,en,value in deadlines] +
        [text(lang,'各授权分别生效；正式提交延期不会延长研究或模型授权。',
                   'Each authorization is separate; extending submission does not extend research or models.')])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    learning = [text(lang,'模式：','Mode: ') + ('enabled' if cfg.get('research_learning','enabled',default=False) else 'disabled')]
    from . import research_framework
    health=research_framework.progress(conn,cfg)
    actual=health['learning_state']
    learning += [text(lang,'实际学习状态：','Actual learning state: ')+actual['mode'],
                 text(lang,'状态原因：','State reason: ')+actual['reason']]
    if health['experiment']:
        experiment_health=health['experiment']
        learning += [text(lang,'实验停止类型：','Experiment stop type: ')+str(experiment_health['stop_type'] or 'none'),
                     text(lang,'冻结基线变化：','Frozen baseline changes: ')+', '.join(experiment_health['baseline_changes']),
                     text(lang,'同一研究预算剩余：','Remaining lineage budget: ')+json.dumps(experiment_health['remaining'],ensure_ascii=False),
                     text(lang,'后续动作：','Next action: ')+experiment_health['next_action']]
    if 'learning_experiments' in tables:
        exp = conn.execute('SELECT * FROM learning_experiments ORDER BY created_at DESC LIMIT 1').fetchone()
        if exp:
            doc = json.loads(exp['document_json'])
            stop = conn.execute('SELECT reason FROM learning_experiment_stops WHERE experiment_id=?',(exp['experiment_id'],)).fetchone() if 'learning_experiment_stops' in tables else None
            cycles = {r[0] for r in conn.execute('SELECT cycle_id FROM learning_assignments WHERE experiment_id=?',(exp['experiment_id'],))} if 'learning_assignments' in tables else set()
            learning += [exp['experiment_id'], text(lang,'已分配轮次：','Allocated cycles: ') + f"{len(cycles)} / {doc['max_cycles']}"]
            learning += [text(lang,'已停止：','Stopped: ') + stop[0]] if stop else [text(lang,'冻结实验状态以调度器准入检查为准。','Dispatch remains subject to the frozen experiment checks.')]
            if {'tasks','attempts','agent_calls'} <= tables:
                cost = research_learning.model_cost(conn,cycles)
                learning += [text(lang,'已知模型成本：','Known model cost: ') + f"${cost['known_usd']:.4f}",
                             text(lang,'已关联调用 / 成本未知调用：','Linked calls / calls with unknown cost: ') + f"{cost['linked_calls']} / {cost['unknown_cost_calls']}",
                             text(lang,'未关联调用、人工时间和缺失成本不计为零。','Unlinked calls, human time and missing costs are not zero.')]
    learning += [text(lang,'优越性未证实；后续市场日期与真实回执仍需持续积累。','Superiority is unproven; new market dates and real receipts are still needed.')]
    add('自学习与成本', 'Learning and costs', learning)
    if cfg.get('research_dual_loop','enabled',default=False):
        meta=health['meta_mechanism'];root=cfg.get('research_dual_loop','root_id')
        add('研究机制自动改进','Research mechanism improvement',[
            text(lang,'工程策略状态：','Engineering policy: ')+str(meta.get('status'))+' / '+str(meta.get('mode')),
            text(lang,'有效影子配对：','Valid shadow pairs: ')+str(len(meta.get('pairs',[])))+' / 8',
            text(lang,'局部耗时中位改善：','Median local runtime gain: ')+str(meta.get('median_gain','not_evaluated')),
            text(lang,'资料采集尝试：','Evidence collection attempts: ')+store.get_flag(conn,'evidence_reads:'+root,'0')+' / 24',
            text(lang,'模型启动预约：','Model start reservations: ')+store.get_flag(conn,'dual_model_reservations:'+root,'0')+' / 64',
            text(lang,'材料核验不等于语义资格；工程晋级不等于金融学习晋级。原权限、期限和预算仍有效。',
                 'Material identity is separate from semantic eligibility; engineering promotion is not financial promotion. Original authority and quotas remain.')])

    add('近期研究质量','Recent research quality',[
        text(lang,'最近已结束轮次：','Recent closed cycles: ')+str(health['recent_closed']),
        text(lang,'基础回测状态：','Base simulation status: ')+json.dumps(health['base_status_counts'],ensure_ascii=False),
        text(lang,'仅统计最近20个结束轮次的真实基础回测；未运行、等待、缺失与失败分别计数。平台通过不等于可提交或收益改善。',
                   'Real base simulations in the last 20 closed cycles; not run, pending, missing and failure stay separate. Platform passes do not establish submission readiness or performance improvement.')])
    if not p.get('campaign'): p = {**p, 'campaign': research_campaign.template(p)}
    report = research_campaign.report(conn,p)
    status = report.get('stop_reason') or text(lang,'有可执行机会，仍需原审查与额度检查','Executable opportunity; original review and quota checks remain')
    add('增量研究预算', 'Campaign budget', [status,
        text(lang,'已预约 / 首阶段上限：','Reserved / pilot cap: ') + f"{report['reserved']} / {report['pilot_cap']}",
        text(lang,'已确认 POST / 发出状态不明：','Confirmed POSTs / uncertain dispatch: ') + f"{report['confirmed_posts']} / {report['dispatch_uncertain']}",
        text(lang,'完整反馈 / 可提交候选：','Complete feedback / submission candidates: ') + f"{report['complete_feedback']} / {report['submission_candidates']}",
        text(lang,'失败和 UNKNOWN 占用预约；复用不计新样本；第 57 个预约需另行批准。','Failed and UNKNOWN tasks retain reservations; reuse is not a new sample; reservation 57 requires separate approval.')])
    for h in report['opportunities']:
        label = text(lang,'待证据','Blocked') if h['state'] == 'blocked' else text(lang,'证据就绪','Evidence ready')
        title = h['hypothesis_id'] + ' · ' + label
        add(title,title,[h['claim'],h['reason'] or text(lang,'仍需当前授权与审查通过','Current authorization and review still required'),
            f"{h['settings'].get('region')} / {h['settings'].get('universe')} / D{h['settings'].get('delay')}",
            text(lang,'已分配轮次 / 已预约请求：','Allocated cycles / reserved requests: ') + f"{h['allocated_cycles']} / {h['reserved']}"])
        if report.get('schema')=='wq.research-campaign/v2':
            details=[text(lang,'资料状态 / 评估：','Data / assessment: ')+f"{h['data_state']} / {h['assessment']}",
                text(lang,'完成配对 / 已登记配对：','Complete / registered pairs: ')+f"{h['complete_pairs']} / {h['pairs']}",
                text(lang,'独立机制来源 / 新执行：','Mechanism sources / completed executions: ')+f"{h['independent_mechanism_sources']} / {h['unique_completed_executions']}",
                text(lang,'后续动作：','Next action: ')+h['next_action']]
            details += [text(lang,'缺项：','Missing: ')+r['code']+(' · '+r['subject'] if r.get('subject') else '') for r in h['data_gaps']]
            add(h['hypothesis_id']+' · 配对评估',h['hypothesis_id']+' · Paired assessment',details)
    from . import workflow
    advanced=workflow.load(cfg)
    cap=advanced['combinations']['max_plans'] if advanced else cfg.get('research_feedback','max_combination_plans',default=2)
    combinations=_combination_summary(conn, cfg, p, cap)
    from . import research_framework
    framework=research_framework.report(conn)
    selection=framework['last_selection'] or {}
    labels={'validated':('已核验','validated'),'collected':('已采集待语义核验','collected; semantics pending'),
            'owner_required':('待人工核验','owner required'),'fetchable':('可补证','fetchable'),
            'waiting_retry':('等待重试','waiting retry'),'unsupported':('能力未支持','unsupported'),
            'exhausted':('本版重试已用完','attempts exhausted'),'expired':('已过期','expired'),
            'rejected':('证据不符','rejected'),'superseded':('已替代','superseded')}
    gap_lines=[text(lang,'框架调度：','Framework scheduling: ')+text(lang,'启用' if research_framework.enabled(cfg) else '停用','enabled' if research_framework.enabled(cfg) else 'disabled'),
               text(lang,'当前状态：','Current state: ')+framework['status']['state'],
               text(lang,'证据事项：','Evidence work: ')+str(len(framework['gaps']))]
    gap_lines += [text(lang,*labels.get(k,(k,k)))+': '+str(v) for k,v in framework['gap_counts'].items()]
    kinds={'evidence':('补充证据','collect evidence'),'reassess':('重评新证据','reassess evidence'),
           'maintenance':('研究维护','research maintenance'),'research':('新研究机会','new research')}
    gap_lines += [text(lang,'最近选择：','Last selection: ')+text(lang,*kinds.get(selection.get('kind'),('尚无记录','none recorded'))),
                  text(lang,'支持／反证／未知记录：','Paired findings: ')+str(len(framework['paired_knowledge'])),
                  text(lang,'由任务账本推进；采集资料不等于语义核验，未知结果不算失败。','Advanced by the task ledger; collection is not semantic verification; unknown is not failure.')]
    if selection:gap_lines.append(text(lang,'选择原因：','Selection reason: ')+selection.get('reason',''))
    pending=sorted((g for g in framework['gaps'] if g['state'] not in ('validated','superseded')),key=lambda g:(g['state']!='fetchable',g['hypothesis'],g['predicate']))
    for gap in pending[:3]:
        gap_lines.append(gap['hypothesis']+' / '+gap['predicate']+' · '+gap['state']+' → '+gap['next_trigger'])
    add('持续研究框架','Continuous research',gap_lines)
    add('组合资格诊断','Combination eligibility',[
        text(lang,'已登记 / 上限：','Registered / cap: ')+f"{combinations['registered']} / {combinations['cap']}",
        text(lang,'可用 / 阻断 / 未知父对：','Eligible / blocked / unknown pairs: ')+
        ' / '.join(str(combinations['counts'].get(k,0)) for k in ('eligible','blocked','unknown')),
        text(lang,'首要阻断原因：','First blocking reasons: ')+json.dumps(combinations['first_reason_counts'],ensure_ascii=False),
        text(lang,'仅诊断现有父对；资料缺失不算失败耗尽。','Existing pairs only; missing evidence does not count as exhausted failures.')])
    order = cfg.get('brain_submission','standby_order',default='fifo')
    add('备选提交顺序','Standby submission order',[text(lang,'同条件、完整证据的连续候选按质量与本地相关性排序；未知证据保留 FIFO 边界。','Continuous comparable candidates use quality and local correlation; unknown evidence preserves FIFO boundaries.') if order == 'evidence' else 'FIFO',
        text(lang,'本地相关性不是官方 Uniqueness；已入队的提交不重排。','Local correlation is not official Uniqueness; queued submissions are not reordered.')])
    return {'entries':entries,'count':len(entries),'campaign':report}
