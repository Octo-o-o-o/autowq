"""Read-only menu data from the local ledger and frozen provider snapshots."""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

from . import routing, store, task_view, util

BEIJING = dt.timezone(dt.timedelta(hours=8))
STATES = {'researching': '研究中', 'reviewing': '审查中', 'simulating': '回测中'}
CLI_NAMES = {'grok': 'Grok Build CLI', 'devin': 'Devin CLI', 'claude': 'Claude Code CLI',
             'codex': 'Codex CLI', 'cursor': 'Cursor CLI', 'zcode': 'ZCode CLI'}
ISO_TIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)')


def beijing(value, missing='尚无记录', fmt='%Y年%m月%d日 %H:%M:%S'):
    if not value:
        return missing
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return '时间缺少时区，待核实'
        return parsed.astimezone(BEIJING).strftime(fmt)
    except (ValueError, TypeError):
        return '时间记录无效'


def localize_message(text):
    return ISO_TIME.sub(lambda match: beijing(match.group()), text or '')


def connect_readonly(cfg):
    conn = sqlite3.connect(Path(cfg.db_path).as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('BEGIN')
    return conn


def provider_label(name, definition):
    cli = CLI_NAMES.get(name, definition.get('label') or name)
    transport = definition.get('transport', '')
    if isinstance(transport, str) and (transport.startswith('openai') or transport == 'anthropic_messages'):
        cli = name + ' API'
    return f"{cli} · {definition.get('model') or '模型未记录'}"


def task_model(conn, tid, compact=False):
    if not tid:
        return '—' if compact else '无关联任务'
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
            return provider_label(name, definition)
    return '模型未记录'


def next_models(conn, cfg):
    data = routing.catalog(cfg)
    routes = data['presets'][routing.active_preset(conn, cfg, data)]['routes']
    selected = {}
    for role in ('research', 'review'):
        chain = routes[role]
        eligible = [name for name in chain if name != selected.get('research') or role == 'research']
        name = next((name for name in eligible if not routing._unavailable(conn, cfg, name)), None)
        selected[role] = name
    return [{'title': ('研究' if role == 'research' else '审查') + '：' +
             (provider_label(name, data['providers'][name]) if name else '暂无可用渠道')}
            for role, name in selected.items()]


def cycle_directive(conn, cycle):
    """本轮预登记的研究指令：单角色基线 / 有限组合 / 自由探索。"""
    cid = cycle['cycle_id']
    focus = store.get_flag(conn, f'autopilot_focus_{cid}')
    if focus:
        return '单角色基线 · ' + focus
    try:
        plan = conn.execute('SELECT plan_json FROM combination_plans WHERE cycle_id=?', (cid,)).fetchone()
    except sqlite3.OperationalError:
        plan = None  # 旧账本可能没有 feedback 表
    if plan:
        parents = json.loads(plan[0]).get('parent_cycles') or []
        suffix = '+'.join(str(p) for p in parents)
        return '组合实验 · 父轮 ' + suffix if suffix else '组合实验'
    return '自由探索'


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


PROBLEM_PREFIXES = ('任务未完成', '反馈资料未完成', '输入或验收错误', '研究范围配置改变', '模型主动放弃')


def cycle_badge(conn, cycle):
    """返回 (kind, 文案)：submitted/failed/progress；正常完结不标注。"""
    if cycle['state'] != 'closed':
        return 'progress', STATES.get(cycle['state'], cycle['state'])
    if submitted_alphas(conn, cycle):
        return 'submitted', '已提交'
    outcome = cycle['outcome'] or ''
    if outcome.startswith(PROBLEM_PREFIXES) or '错误' in outcome or '未通过' in outcome:
        return 'failed', '失败'
    return None, None


def history(conn, cfg):
    tasks = {row['task_id']: row for row in store.list_tasks(conn)}
    entries = []
    for cycle in conn.execute('SELECT * FROM research_cycles ORDER BY cycle_id DESC'):
        members = [tasks[cycle[key]] for key in ('research_task', 'review_task', 'simulation_task') if cycle[key] in tasks]
        cost = task_view.group_cost(conn, members, cfg)
        money = re.search(r'已知折合小计 \$[\d.]+', cost)
        short = money.group().replace('已知折合小计', '小计') if money else ('尚未调用' if '$0 / 0 token' in cost else '成本未知')
        if '总额未知' in cost:
            short += ' + 未知'
        start = conn.execute("SELECT MIN(created_at) FROM attempts WHERE task_id=? AND event='provider_start'", (cycle['research_task'],)).fetchone()[0]
        badge, badge_text = cycle_badge(conn, cycle)
        title = f"第 {cycle['cycle_id']} 轮 · {cycle_directive(conn, cycle)}" + (f" · {badge_text}" if badge_text else '')
        if cycle['state'] == 'closed':
            time_line = beijing(cycle['updated_at'], '—', '%m-%d %H:%M') + ' 完成'
        else:
            time_line = (beijing(start, '—', '%m-%d %H:%M') + ' 开始') if start else '尚未开始'
        entries.append({'title': title, 'badge': badge, 'lines': [
                time_line,
                '研究 ' + task_model(conn, cycle['research_task'], compact=True) + ' · 审查 ' + task_model(conn, cycle['review_task'], compact=True),
                short,
                '结果 ' + (cycle['outcome'] or '进行中')],
            'detail': ['创建：' + beijing(cycle['created_at']),
                       '开始：' + beijing(start, '无模型启动记录'),
                       '完成：' + (beijing(cycle['updated_at']) if cycle['state'] == 'closed' else '尚未完成'),
                       '研究：' + task_model(conn, cycle['research_task']),
                       '审查：' + task_model(conn, cycle['review_task']),
                       *cost.split('；'), '结果：' + (cycle['outcome'] or '进行中')]})
    return {'entries': entries, 'count': len(entries), 'note': '时间均为北京时间（UTC+8）。'}


def alpha_cycle(conn, alpha_id):
    return conn.execute('''SELECT c.* FROM research_cycles c JOIN brain_runs b ON b.task_id=c.simulation_task
        WHERE b.alpha_id=? UNION SELECT c.* FROM research_cycles c JOIN cycle_simulations v ON v.cycle_id=c.cycle_id
        JOIN brain_runs b ON b.task_id=v.task_id WHERE b.alpha_id=? LIMIT 1''', (alpha_id, alpha_id)).fetchone()


def notifications_enabled(cfg):
    return cfg.get('desktop', 'notifications', default=True) is not False


# 通知事件：按 updated_at 增量消费（状态转为 accepted/final_valid 或 failed 的时刻），
# 每类各存一条水位线；菜单栏未运行期间不推进，恢复后一次性补报（每次最多 10 条）。
NOTIFY_SOURCES = (
    ('desktop_notified_submissions',
     """SELECT sub.remote_id, sub.status, sub.updated_at FROM submissions sub
        JOIN simulations s ON s.sim_id=sub.sim_id
        WHERE s.synthetic=0 AND sub.status IN ('accepted','final_valid') AND sub.updated_at>?
        ORDER BY sub.updated_at LIMIT 10""",
     lambda row: {'id': f"submitted-{row['remote_id']}-{row['status']}", 'kind': 'submitted',
                  'title': 'Alpha 提交成功', 'body': f"{row['remote_id']} 已提交（{row['status']}）"}),
    ('desktop_notified_failures',
     """SELECT task_id,kind,last_error,updated_at FROM tasks
        WHERE status='failed' AND updated_at>? ORDER BY updated_at LIMIT 10""",
     lambda row: {'id': f"failed-{row['task_id']}", 'kind': 'failed', 'title': '任务失败',
                  'body': f"{row['kind']} {row['task_id']}：{(row['last_error'] or '原因未记录')[:120]}"}),
)


def pending_notifications(conn):
    """消费自上次检查以来的新事件并推进水位线；返回待通知列表（需写连接）。"""
    items = []
    for key, query, build in NOTIFY_SOURCES:
        mark = store.get_flag(conn, key)
        if mark is None:
            store.set_flag(conn, key, util.now_iso())   # 首次只建基线，不回放历史
            continue
        rows = conn.execute(query, (mark,)).fetchall()
        if rows:
            store.set_flag(conn, key, rows[-1]['updated_at'])
            items.extend(build(row) for row in rows)
    return items


def submitted(conn):
    entries = []
    rows = conn.execute('''SELECT sub.*,s.stats_json,c.config_json,c.expression FROM submissions sub
        JOIN simulations s ON s.sim_id=sub.sim_id JOIN candidates c ON c.candidate_id=s.candidate_id
        WHERE s.synthetic=0 AND sub.status IN ('accepted','final_valid','final_invalid') ORDER BY sub.created_at DESC''')
    for row in rows:
        receipt = json.loads(row['receipt_json'] or '{}')
        settings = json.loads(row['config_json'])
        stats = json.loads(row['stats_json'] or '{}')
        cycle = alpha_cycle(conn, row['remote_id'])
        model_lines = ['研究：' + task_model(conn, cycle['research_task']), '审查：' + task_model(conn, cycle['review_task'])] if cycle else ['模型：未关联自动轮次']
        metrics = ' · '.join(f'{key} {stats.get(key, "未知")}' for key in ('sharpe', 'fitness', 'turnover', 'returns', 'drawdown', 'margin'))
        brain_settings = settings.get('extra', {}).get('brain_settings', settings)
        params = [f'{key}：{brain_settings[key]}' for key in ('region', 'universe', 'instrumentType', 'delay', 'decay', 'neutralization', 'truncation', 'testPeriod', 'nanHandling', 'pasteurization', 'unitHandling', 'language') if key in brain_settings]
        entries.append({'title': row['remote_id'] + ' · ' + beijing(receipt.get('dateSubmitted'), '提交时间未记录'),
            'badge': 'submitted' if row['status'] in ('accepted', 'final_valid') else None,
            'lines': ['状态：' + row['status'] + ' / ' + str(receipt.get('platform_status', '未知')),
                      '轮次：' + (str(cycle['cycle_id']) if cycle else '直接／人工实验'),
                      *model_lines, '指标：' + metrics, *params,
                      '表达式：' + row['expression'], '本地接收账本，不代表平台现金收益。']})
    return {'entries': entries, 'count': len(entries)}
