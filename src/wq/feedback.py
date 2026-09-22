"""真实结果的本地诊断与有限组合。原始平台数据不进入模型工作区。"""
import datetime as dt
import json
import math
from pathlib import Path
from . import util, store
from .brain_submission import source, identity, problems
from .brain_jobs import get_with_reauth, later
from .brain_client import BrainClient, retry_delay
from .errors import AdapterError


def setup(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS research_feedback (
        alpha_id TEXT PRIMARY KEY, identity TEXT NOT NULL, report_json TEXT NOT NULL,
        updated_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS combination_plans (
        pair_key TEXT PRIMARY KEY, cycle_id INTEGER UNIQUE, plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL)''')


def records(doc):
    if not isinstance(doc, dict): raise ValueError('记录集格式错误')
    names = [x['name'] for x in doc.get('schema', {}).get('properties', [])]
    if not names or len(set(names)) != len(names): raise ValueError('记录集缺字段定义或重复字段')
    rows = doc.get('records')
    if not isinstance(rows, list) or not rows: raise ValueError('记录集为空')
    if any(not isinstance(x, list) or len(x) != len(names) for x in rows):
        raise ValueError('记录集行长不匹配')
    return [dict(zip(names, x)) for x in rows]


def number(x):
    return type(x) in (float, int) and math.isfinite(x)


def daily_pnl(doc):
    rows = records(doc)
    dates = [x.get('date') for x in rows]
    if any(not isinstance(x, str) for x in dates) or dates != sorted(set(dates)):
        raise ValueError('PnL日期必须严格递增且唯一')
    if any(not number(x.get('pnl')) for x in rows): raise ValueError('PnL缺有效数值')
    # 相邻累计PnL差分；不把相隔多个缺失交易日的差分对齐成一天。
    return {(rows[i-1]['date'], rows[i]['date']): rows[i]['pnl']-rows[i-1]['pnl']
            for i in range(1, len(rows))}


def correlation(left, right, minimum=252):
    a, b = daily_pnl(left), daily_pnl(right)
    dates = sorted(a.keys() & b.keys())
    if len(dates) < minimum: return {'value': None, 'observations': len(dates), 'reason': '共同日区间不足'}
    x, y = [a[d] for d in dates], [b[d] for d in dates]
    mx, my = sum(x)/len(x), sum(y)/len(y)
    vx, vy = sum((v-mx)**2 for v in x), sum((v-my)**2 for v in y)
    value = sum((u-mx)*(v-my) for u,v in zip(x,y))/math.sqrt(vx*vy) if vx > 0 and vy > 0 else None
    return {'value': value, 'observations': len(dates), 'from': dates[0][1], 'to': dates[-1][1],
            'note': '探索期累计PnL差分相关性；不是官方SELF_CORRELATION，也不是独立样本外证据'}


def diagnose(alpha, yearly=None):
    checks = alpha.get('is', {}).get('checks', [])
    blockers = problems(checks)
    failed = [c.get('name') for c in checks if c.get('result') == 'FAIL']
    names = {c.get('name'): c.get('result') for c in checks}
    categories = []
    mapping = {'LOW_SHARPE':'收益风险比不足', 'LOW_FITNESS':'收益效率不足',
               'HIGH_TURNOVER':'换手过高', 'LOW_TURNOVER':'换手过低',
               'CONCENTRATED_WEIGHT':'权重集中', 'LOW_SUB_UNIVERSE_SHARPE':'子股票池不稳健',
               'SELF_CORRELATION':'与已提交信号重叠'}
    categories = [mapping.get(x, '其他平台门槛失败') for x in failed]
    if not categories: categories = ['检查待完成' if blockers else '平台快照通过']
    stats = alpha.get('is', {})
    retain = (len(names)==len(checks) and not any(x.startswith('缺少') for x in blockers)
              and number(stats.get('sharpe')) and stats['sharpe'] > 0
              and all(names.get(x) == 'PASS' for x in ('CONCENTRATED_WEIGHT','LOW_SUB_UNIVERSE_SHARPE','LOW_TURNOVER','HIGH_TURNOVER')))
    temporal, gaps = [], []
    for split in ('train', 'test'):
        s = alpha.get(split)
        if not isinstance(s, dict) or not all(number(s.get(k)) for k in ('sharpe','fitness')):
            gaps.append(split+'指标缺失')
        else:
            temporal.append({'segment':split,'sharpe':s['sharpe'],'fitness':s['fitness']})
            if s['sharpe'] < 1.25 or s['fitness'] < 1.0: gaps.append(split+'未达本地分段要求：Sharpe>=1.25且Fitness>=1')
    if yearly is None: gaps.append('年度记录缺失')
    else:
        years = records(yearly)
        if len({str(x.get('year')) for x in years}) < 3: gaps.append('年度覆盖不足3年（本地要求）')
        for y in years:
            if not all(number(y.get(k)) for k in ('sharpe','fitness','pnl')):
                gaps.append('年度指标缺失'); continue
            temporal.append({k:y.get(k) for k in ('year','sharpe','fitness','pnl','stage')})
            if y['pnl'] <= 0 or y['sharpe'] <= 0: gaps.append('年度收益非正：'+str(y.get('year')))
    return {'alpha_id':alpha['id'], 'identity':identity(alpha), 'diagnosis':categories,
            'platform_blockers':blockers, 'retain_for_complementarity':bool(retain),
            'temporal':temporal, 'validation_gaps':gaps,
            'submission_candidate':not blockers and not gaps,
            'note':'保留不等于达标；年度正收益是本地筛选，非平台门槛。反馈选出的候选属于适应性研究，不能声称未污染样本外。'}


def enqueue(conn, cfg, aid):
    setup(conn)
    sim, alpha = source(conn, aid)
    if not conn.execute('SELECT 1 FROM research_feedback WHERE alpha_id=?',(aid,)).fetchone():
        initial=diagnose(alpha)
        initial.update(updated_at=sim['imported_at'],collection_status='pending')
        conn.execute('INSERT INTO research_feedback VALUES(?,?,?,?)',(aid,identity(alpha),json.dumps(initial,ensure_ascii=False),sim['imported_at']))
    return store.enqueue_task(conn, 'brain_feedback', {'alpha_id':aid, 'identity':identity(alpha),
        'title':'真实结果诊断/分段/相关性资料：'+aid}, 'brain-feedback-v1:'+aid, max_attempts=120)


def step(conn, cfg, task, payload):
    setup(conn)
    aid = payload['alpha_id']; _, alpha = source(conn, aid)
    if identity(alpha) != payload['identity']: return 'blocked', {}, '反馈对象改变'
    root = Path(cfg.private_dir)/'research-feedback'/aid
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cooldown = store.get_flag(conn, 'brain_not_before')
    if cooldown and util.now() < util.parse_iso(cooldown):
        return later(conn, task['task_id'], (util.parse_iso(cooldown)-util.now()).total_seconds(), '遵守平台冷却')
    try:
        # 每次任务只读一个远端资源；持久化后下个tick继续，不重复已完成下载。
        for name in ('alpha','pnl','yearly-stats','check'):
            path = root/(name+'.json')
            if name=='check':
                snapshot=util.read_json(str(root/'alpha.json'))
                if any(x.get('result')=='FAIL' for x in snapshot.get('is',{}).get('checks',[])): continue
            if path.exists(): continue
            endpoint='/alphas/'+aid if name=='alpha' else ('/alphas/'+aid+'/check' if name=='check' else '/alphas/'+aid+'/recordsets/'+name)
            code, headers, data = get_with_reauth(BrainClient(cfg.private_dir), cfg, endpoint)
            if headers.get('retry-after'):
                return later(conn, task['task_id'], retry_delay(headers['retry-after']), '等待'+name+'记录集')
            if code != 200: return 'blocked', {}, '记录集未就绪：'+name
            if name=='alpha':
                if identity(data)!=payload['identity']: return 'blocked', {}, '远端反馈Alpha身份改变'
            elif name=='check':
                if not isinstance(data.get('is'),dict) or not isinstance(data['is'].get('checks'),list):
                    return 'blocked', {}, '官方检查结构缺失'
            else: records(data)
            util.write_json(str(path), data); path.chmod(0o600)
            return later(conn, task['task_id'], 1, '已收集'+name+'；继续下一项')
        pnl = util.read_json(str(root/'pnl.json')); daily_pnl(pnl)
        alpha=util.read_json(str(root/'alpha.json'))
        if identity(alpha)!=payload['identity']: return 'blocked', {}, '缓存Alpha身份改变'
        if (root/'check.json').exists():
            alpha=dict(alpha)
            alpha['is']={**alpha['is'],'checks':util.read_json(str(root/'check.json'))['is']['checks']}
        result = diagnose(alpha, util.read_json(str(root/'yearly-stats.json')))
        result['collection_status']='complete'
        result['pnl_path'] = str(root/'pnl.json')
        result['updated_at'] = util.now_iso()
        conn.execute('INSERT OR REPLACE INTO research_feedback VALUES(?,?,?,?)',
                     (aid, identity(alpha), json.dumps(result, ensure_ascii=False), result['updated_at']))
        util.write_json(str(root/'report.json'), result)
        return 'succeeded', {'alpha_id':aid,'diagnosis':result['diagnosis'],
            'retain':result['retain_for_complementarity'],'submission_candidate':result['submission_candidate']}, None
    except AdapterError as exc:
        if exc.kind in (AdapterError.NETWORK, AdapterError.RATE_LIMIT):
            if exc.kind == AdapterError.RATE_LIMIT:
                store.set_flag(conn,'brain_not_before',(util.now()+dt.timedelta(seconds=exc.retry_after or 300)).isoformat())
            return later(conn,task['task_id'],exc.retry_after or 300,str(exc))
        raise


def report(conn):
    setup(conn)
    rows = [json.loads(r[0]) for r in conn.execute('SELECT report_json FROM research_feedback ORDER BY updated_at DESC')]
    tables={x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    accepted={x[0] for x in conn.execute("SELECT alpha_id FROM brain_submissions WHERE state='accepted'")} if 'brain_submissions' in tables else set()
    for row in rows: row['platform_submission']='accepted' if row['alpha_id'] in accepted else 'not_confirmed'
    pairs = []
    kept = [x for x in rows if x['retain_for_complementarity']]
    for i,a in enumerate(kept):
        for b in kept[i+1:]:
            try: corr=correlation(util.read_json(a['pnl_path']),util.read_json(b['pnl_path']))
            except (ValueError, OSError, KeyError): continue
            pairs.append({'parents':sorted([a['alpha_id'],b['alpha_id']]), **corr,
                          'worth_combination_review':corr['value'] is not None and abs(corr['value']) < .3})
    return {'candidates':rows,'pairs':pairs,'automatic_submission':False,
            'note':'相关性<0.3是本地组合候选规则；每对父信号最多一次等权rank组合，仍须Grok论证和Devin审查。'}


def model_context(conn):
    """只给固定枚举的诊断标签及模型自己已有提案的轮次，不外发数值/字段/序列。"""
    setup(conn)
    return [{'cycle':r['cycle_id'], 'diagnosis':json.loads(r['report_json'])['diagnosis'],
             'retain_for_complementarity':json.loads(r['report_json'])['retain_for_complementarity']}
            for r in conn.execute('''SELECT c.cycle_id,f.report_json FROM research_cycles c
                JOIN brain_runs b ON b.task_id=c.simulation_task
                JOIN research_feedback f ON f.alpha_id=b.alpha_id ORDER BY c.cycle_id DESC LIMIT 40''')]


def next_combination(conn, max_plans=2):
    setup(conn)
    if conn.execute('SELECT COUNT(*) FROM combination_plans').fetchone()[0] >= max_plans: return None
    # 只组合经过模型审查的独立父提案；直接诊断实验会显示在报告中，但不伪造其模型审查来源。
    parents = {}
    for r in conn.execute('''SELECT c.cycle_id,c.candidate_json,c.policy_json,b.alpha_id FROM research_cycles c
            JOIN brain_runs b ON b.task_id=c.simulation_task
            WHERE c.state='closed' AND c.cycle_id NOT IN (SELECT cycle_id FROM combination_plans)'''):
        if r['candidate_json']: parents[r['alpha_id']]=dict(r)
    for pair in sorted(report(conn)['pairs'],key=lambda x: abs(x['value']) if x['value'] is not None else 2):
        ids = pair['parents']; key = ':'.join(ids)
        if not pair['worth_combination_review'] or not all(x in parents for x in ids): continue
        if conn.execute('SELECT 1 FROM combination_plans WHERE pair_key=?',(key,)).fetchone(): continue
        a,b = [parents[x] for x in ids]
        if json.loads(a['policy_json'])['settings'] != json.loads(b['policy_json'])['settings']: continue
        ast = {'op':'add','left':{'op':'rank','arg':json.loads(a['candidate_json'])['ast']},
               'right':{'op':'rank','arg':json.loads(b['candidate_json'])['ast']}}
        from . import research_dsl
        try:
            research_dsl.compile_ast(ast,json.loads(a['policy_json'])['bindings'])
            nodes=json.dumps(ast)
            if sum(nodes.count('"'+op+'"') for op in research_dsl.TIMESERIES)>2 or sum(nodes.count('"'+op+'"') for op in research_dsl.BINARY)>2: continue
        except ValueError: continue
        return {'pair_key':key,'parents':ids,'parent_cycles':[a['cycle_id'],b['cycle_id']],
                'ast':ast,'correlation':pair,'experiment':'一次固定等权rank组合；不优化权重、窗口、符号；失败终止该父对'}
    return None


def require_submission_evidence(conn, alpha):
    setup(conn)
    row=conn.execute('SELECT * FROM research_feedback WHERE alpha_id=?',(alpha['id'],)).fetchone()
    if not row or row['identity'] != identity(alpha): raise ValueError('提交前须完成真实结果诊断及时间分段资料收集')
    result=json.loads(row['report_json'])
    if result['validation_gaps']: raise ValueError('时间分段验收未通过：'+'；'.join(result['validation_gaps']))
    if util.now()-util.parse_iso(row['updated_at']) > dt.timedelta(days=7): raise ValueError('提交研究资料过期')
    daily_pnl(util.read_json(result['pnl_path']))
