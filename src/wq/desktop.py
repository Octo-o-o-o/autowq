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
        entries.append({'title': row['remote_id'] + ' · ' + beijing(receipt.get('dateSubmitted'), text(lang, '提交时间未记录', 'submission time not recorded'), lang=lang),
            'badge': 'submitted' if row['status'] in ('accepted', 'final_valid') else None,
            'lines': [text(lang, '状态：', 'Status: ') + row['status'] + ' / ' + str(receipt.get('platform_status', unknown)),
                      text(lang, '轮次：', 'Cycle: ') + (str(cycle['cycle_id']) if cycle else text(lang, '直接／人工实验', 'direct / manual experiment')),
                      *model_lines, text(lang, '指标：', 'Metrics: ') + metrics, *params,
                      text(lang, '表达式：', 'Expression: ') + row['expression'],
                      text(lang, '本地接收账本，不代表平台现金收益。', 'Locally recorded receipt; not proof of platform cash income.')]})
    return {'entries': entries, 'count': len(entries)}
