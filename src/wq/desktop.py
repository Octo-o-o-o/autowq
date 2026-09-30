"""Read-only menu data from the local ledger and frozen provider snapshots.

双语：所有文案经 lang 参数渲染；账本内 autopilot 写入的中文 message/outcome
经 i18n.translate 在显示层翻译。时间统一换算为北京时间（UTC+8）。
"""
import datetime as dt
import json
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
    data = routing.catalog(cfg)
    routes = data['presets'][routing.active_preset(conn, cfg, data)]['routes']
    selected = {}
    for role in ('research', 'review'):
        chain = routes[role]
        eligible = [name for name in chain if name != selected.get('research') or role == 'research']
        name = next((name for name in eligible if not routing._unavailable(conn, cfg, name)), None)
        selected[role] = name
    return [{'title': (text(lang, '研究', 'Research') if role == 'research' else text(lang, '审查', 'Review')) + '：' +
             (provider_label(name, data['providers'][name], lang) if name else text(lang, '暂无可用渠道', 'no provider available'))}
            for role, name in selected.items()]


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


def history(conn, cfg, lang='zh'):
    tasks = {row['task_id']: row for row in store.list_tasks(conn)}
    groups = {cycle['cycle_id']: members for cycle, members in task_view.task_groups(conn, list(tasks.values())) if cycle}
    has_fallbacks = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_fallbacks'").fetchone()
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
        start = conn.execute("SELECT MIN(created_at) FROM attempts WHERE task_id=? AND event='provider_start'", (cycle['research_task'],)).fetchone()[0]
        badge, badge_text = cycle_badge(conn, cycle, lang)
        raw_outcome = cycle['outcome'] or ''
        if raw_outcome.startswith('任务未完成'):
            reasons = [t['last_error'] for t in members if t['status'] in ('failed', 'blocked', 'aborted') and t['last_error']]
            for reason in reasons:
                if reason not in raw_outcome:
                    raw_outcome += '；' + reason
        outcome = translate(raw_outcome, lang) or text(lang, '进行中', 'In progress')
        title = (text(lang, f"第 {cycle['cycle_id']} 轮", f"Cycle {cycle['cycle_id']}") + ' · ' + cycle_directive(conn, cycle, lang)
                 + (f" · {badge_text}" if badge_text else ''))
        fallback = conn.execute('SELECT * FROM research_fallbacks WHERE cycle_id=?', (cycle['cycle_id'],)).fetchone() if has_fallbacks else None
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
                label('研究 ', 'Research ') + task_model(conn, cycle['research_task'], compact=True, lang=lang) +
                ' · ' + label('审查 ', 'Review ') + task_model(conn, cycle['review_task'], compact=True, lang=lang),
                short,
                label('结果 ', 'Outcome ') + outcome, *review_lines],
            'detail': [label('创建：', 'Created: ') + beijing(cycle['created_at'], lang=lang),
                       label('开始：', 'Started: ') + beijing(start, text(lang, '无模型启动记录', 'No model start recorded'), lang=lang),
                       label('完成：', 'Closed: ') + (beijing(cycle['updated_at'], lang=lang) if cycle['state'] == 'closed' else text(lang, '尚未完成', 'Not closed yet')),
                       label('研究：', 'Research: ') + task_model(conn, cycle['research_task'], lang=lang),
                       label('审查：', 'Review: ') + task_model(conn, cycle['review_task'], lang=lang),
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
    order = cfg.get('brain_submission','standby_order',default='fifo')
    add('备选提交顺序','Standby submission order',[text(lang,'同条件、完整证据的连续候选按质量与本地相关性排序；未知证据保留 FIFO 边界。','Continuous comparable candidates use quality and local correlation; unknown evidence preserves FIFO boundaries.') if order == 'evidence' else 'FIFO',
        text(lang,'本地相关性不是官方 Uniqueness；已入队的提交不重排。','Local correlation is not official Uniqueness; queued submissions are not reordered.')])
    return {'entries':entries,'count':len(entries),'campaign':report}
