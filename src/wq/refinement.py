"""本地调优分诊：筛选复核对象，不授权派发或提交，不向模型外发平台数据。"""
import json
import math
from .brain_submission import REQUIRED

NEAR_RATIO = 0.85  # 本地资源分配规则，不是平台要求或统计显著性门槛。


def assess(checks):
    blockers, pending, ratios = [], [], {}
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        return {'worth_reviewing': False, 'ratios': {}, 'blockers': ['平台检查缺失或格式无效'], 'pending': []}
    by_name = {c.get('name'): c for c in checks}
    if len(by_name) != len(checks): blockers.append('平台检查名称重复')
    if REQUIRED - set(by_name): blockers.append('缺少必要平台检查')
    for name, c in by_name.items():
        result = c.get('result')
        if name in ('LOW_SHARPE', 'LOW_FITNESS'):
            value, limit = c.get('value'), c.get('limit')
            if not all(type(x) in (int, float) and math.isfinite(x) for x in (value, limit)) or limit <= 0:
                blockers.append(name + ' 缺有效数值/门槛'); continue
            ratios[name] = value / limit
            if ratios[name] < NEAR_RATIO: blockers.append(f'{name} 仅达门槛的 {ratios[name]:.0%}，不优先调参')
            if result not in ('PASS', 'FAIL'): blockers.append(name + ' 结果未确认')
        elif name == 'SELF_CORRELATION' and result == 'PENDING':
            pending.append('SELF_CORRELATION仍待核验，不能提交')
        elif result != 'PASS':
            blockers.append(str(name) + ': ' + str(result))
    if not any(c.get('result') == 'FAIL' for c in checks):
        blockers.append('没有明确FAIL；先补齐检查或走提交验收，不做参数挖掘式挽救')
    return {'worth_reviewing': not blockers, 'ratios': ratios, 'blockers': blockers, 'pending': pending}


def report(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = []
    if {'research_cycles', 'brain_runs'} <= tables:
        for r in conn.execute('''SELECT c.cycle_id, s.remote_id, s.checks_json
                FROM simulations s JOIN brain_runs b ON s.remote_id=b.alpha_id
                LEFT JOIN research_cycles c ON b.task_id=c.simulation_task
                WHERE s.synthetic=0 AND s.source='api' ORDER BY c.cycle_id DESC'''):
            raw = json.loads(r['checks_json'] or '{}')
            verdict = assess(raw.get('raw'))
            rows.append({'cycle_id': r['cycle_id'], 'alpha_id': r['remote_id'], **verdict})
    return {'near_ratio': NEAR_RATIO, 'automatic_refinement': False,
            'note': '本地快照分诊，非平台规则；值得复核不等于准许调优。仍须机制/数据审查、预登记变更、父子谱系和独立验证。',
            'candidates': rows}
