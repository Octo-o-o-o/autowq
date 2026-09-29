"""All-cycle evidence summaries and reviewed, bounded research priorities."""
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from . import util, store, routing

RULES = {
    'novel_measurement': '优先寻找不同的真实测量对象；避免旧机制的窗口、符号和表达式变体。',
    'measurement_validity': '在提出AST前说明观测量真正测量什么，检查代理变量到经济机制的映射。',
    'turnover_control': '优先有持有期依据的低交易频率机制；不可事后扫参数降低换手。',
    'return_efficiency': '论证收益来源与交易成本、风险暴露，避免只追求Sharpe而忽略Fitness。',
    'temporal_stability': '明确跨时间成立的机制与失效条件；保留训练/测试及年度差异，不冒充独立样本外。',
    'complementarity': '保留独立信息来源，只有真实PnL支持时才进行预登记的有限组合。',
    'evidence_completion': '先补足缺失的真实验证证据，不把未检查或任务成功当作质量通过。',
}


def setup(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS history_research(
        snapshot_hash TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL,
        state TEXT NOT NULL, research_task TEXT, review_task TEXT,
        recommendation_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def snapshot(conn):
    from . import autopilot, feedback, brain_jobs
    autopilot.setup(conn); feedback.setup(conn); brain_jobs.setup(conn)
    rows = conn.execute('''SELECT c.*,f.report_json FROM research_cycles c
        LEFT JOIN brain_runs b ON b.task_id=c.simulation_task
        LEFT JOIN research_feedback f ON f.alpha_id=b.alpha_id
        WHERE c.state='closed' ORDER BY c.cycle_id''').fetchall()
    from . import research_learning
    research_learning.sync(conn)
    research_learning.derive_rules(conn)
    cycles=[]
    for row in rows:
        outcome=row['outcome'] or ''; tags=[]
        if '重复' in outcome: tags.append('novel_measurement')
        if '拒绝' in outcome or '主动放弃' in outcome: tags.append('measurement_validity')
        report=json.loads(row['report_json']) if row['report_json'] else {}
        diagnosis=report.get('diagnosis',[])
        if any('换手过高' in d for d in diagnosis): tags.append('turnover_control')
        if any('收益效率不足' in d or '收益风险比不足' in d for d in diagnosis): tags.append('return_efficiency')
        if report.get('validation_gaps'):
            if any('分段' in d or 'train' in d or 'test' in d for d in report['validation_gaps']): tags.append('temporal_stability')
            else: tags.append('evidence_completion')
        if report.get('retain_for_complementarity'): tags.append('complementarity')
        if row['simulation_task'] and not report: tags.append('evidence_completion')
        # Only program-generated categories go to models. No expressions, raw metrics,
        # arbitrary platform messages, credentials, or PnL data enter this packet.
        cycles.append({'cycle':row['cycle_id'],'tags':sorted(set(tags)),
                       'simulated':bool(row['simulation_task']), 'feedback_available':bool(report)})
    counts=Counter(tag for row in cycles for tag in row['tags'])
    return {'version':1,'closed_cycles':len(cycles),'cycles':cycles,
            'counts':dict(sorted(counts.items())),
            'scope':'All closed automatic research cycles; direct/manual simulations are outside cycle coverage.',
            'interpretation':'Tags are diagnostic evidence, not causal proof or independent out-of-sample performance.'}


def validate_recommendation(obj, evidence):
    rec=obj.get('recommendation')
    if not isinstance(rec,dict) or set(rec)!={'snapshot_hash','priorities'} or rec['snapshot_hash']!=util.sha256_json(evidence):
        raise ValueError('Recommendation must bind the exact snapshot')
    priorities=rec['priorities']
    if not isinstance(priorities,list) or not 1<=len(priorities)<=3: raise ValueError('Choose 1..3 priorities')
    ids=set();rows={r['cycle']:r for r in evidence['cycles']}
    for item in priorities:
        if not isinstance(item,dict) or set(item)!={'rule','cycles','reason'}: raise ValueError('Invalid priority shape')
        rule=item['rule']; cited=item['cycles']
        if rule not in RULES or rule in ids: raise ValueError('Unknown/duplicate priority')
        ids.add(rule)
        if not isinstance(cited,list) or not cited or len(cited)>len(rows):
            raise ValueError('Citations must be a nonempty list bounded by snapshot cycle count')
        if any(type(i) is not int or i not in rows or rule not in rows[i]['tags'] for i in cited):
            raise ValueError('Each cited cycle must actually contain the selected diagnostic tag')
        if len(set(cited)) != len(cited):
            raise ValueError('Duplicate cycle citations are not allowed')
        if not isinstance(item['reason'],str) or not 8<=len(item['reason'])<=1200: raise ValueError('Concrete reasoning required')
    return rec


def enqueue(conn,cfg,evidence=None):
    setup(conn);evidence=evidence or snapshot(conn);digest=util.sha256_json(evidence)
    existing=conn.execute('SELECT * FROM history_research WHERE snapshot_hash=?',(digest,)).fetchone()
    if existing:return dict(existing),False
    if not evidence['closed_cycles']: raise ValueError('No closed cycles to review')
    if conn.execute("SELECT 1 FROM history_research WHERE state IN ('researching','reviewing')").fetchone():
        raise ValueError('History review already active; no duplicate call')
    path=Path(cfg.ensure_private_dir())/'history-research'/digest
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    prompt=path/'research.md'
    prompt.write_text('复盘全部历史轮次的程序诊断，选择最多3个后续研究重点。标签是诊断而非因果证明。不要发明平台数据，不建议扫参数、降低门槛或增加预算。引用实际含对应标签的cycle编号；每项可引用全部有效轮次，每个编号只能出现一次。不能根据这些数据证明改进会有效；说明反例和误判可能。\n'
        +json.dumps({'snapshot':evidence,'rules':RULES,'required_output':{'status':'completed','summary':'摘要','findings':[],
          'recommendation':{'snapshot_hash':digest,'priorities':[{'rule':'RULE_ID','cycles':[1],'reason':'证据、解释和反例'}]}}},ensure_ascii=False))
    prompt.chmod(0o600)
    tid,_=routing.enqueue_job(conn,cfg,'research',str(prompt),title='全历史Auto Research：复盘研究重点')
    now=util.now_iso()
    conn.execute('INSERT INTO history_research VALUES(?,?,?,?,?,?,?,?,?)',(digest,json.dumps(evidence), 'researching',tid,None,None,None,now,now))
    return {'snapshot_hash':digest,'research_task':tid,'state':'researching'},True


def progress(conn,cfg):
    from . import autopilot
    setup(conn)
    row=conn.execute("SELECT * FROM history_research WHERE state IN ('researching','reviewing') ORDER BY created_at LIMIT 1").fetchone()
    if not row:return
    row=dict(row);digest=row['snapshot_hash'];key='research_task' if row['state']=='researching' else 'review_task'
    task=conn.execute('SELECT status FROM tasks WHERE task_id=?',(row[key],)).fetchone()
    if not task or task[0] in ('queued','claimed','running','unknown'):return
    try:
        if task[0]!='succeeded':raise ValueError('History model task did not succeed: '+task[0])
        result=autopilot.artifact(conn,row[key]);evidence=json.loads(row['snapshot_json'])
        if row['state']=='researching':
            rec=validate_recommendation(result,evidence)
            source=autopilot.provider(conn,row['research_task'])
            path=Path(cfg.ensure_private_dir())/'history-research'/digest/'review.md'
            path.write_text('独立复核历史复盘。检查引用是否支持重点、是否将相关性误称因果、是否过拟合或试图弱化门槛。拒绝无证据的质量改善承诺。只返回result.json。\n'+json.dumps({'snapshot':evidence,'rules':RULES,'recommendation':rec,
              'required_output':{'status':'completed','summary':'摘要','findings':[],'history_review':{'recommendation_hash':util.sha256_json(rec),'accept':True,'reason':'具体反例与接受/拒绝依据'}}},ensure_ascii=False))
            path.chmod(0o600)
            tid,_=routing.enqueue_job(conn,cfg,'review',str(path),title='全历史Auto Research：独立复核')
            payload=json.loads(conn.execute('SELECT payload_json FROM tasks WHERE task_id=?',(tid,)).fetchone()[0]);payload['excluded_providers']=[source]
            conn.execute('UPDATE tasks SET payload_json=? WHERE task_id=?',(json.dumps(payload),tid))
            conn.execute("UPDATE history_research SET state='reviewing',review_task=?,recommendation_json=?,updated_at=? WHERE snapshot_hash=?",(tid,json.dumps(rec),util.now_iso(),digest))
        else:
            rec=json.loads(row['recommendation_json']);review=result.get('history_review',{})
            if autopilot.provider(conn,row['research_task'])==autopilot.provider(conn,row['review_task']): raise ValueError('Distinct review provider required')
            if review.get('recommendation_hash')!=util.sha256_json(rec) or type(review.get('accept')) is not bool or not isinstance(review.get('reason'),str) or not 8<=len(review['reason'])<=2000: raise ValueError('Invalid or unbound history review')
            conn.execute('UPDATE history_research SET state=?,updated_at=? WHERE snapshot_hash=?',('accepted' if review['accept'] else 'rejected',util.now_iso(),digest))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        conn.execute("UPDATE history_research SET state='failed',error=?,updated_at=? WHERE snapshot_hash=?",(str(exc)[:500],util.now_iso(),digest))


def tick(conn,cfg):
    setup(conn);progress(conn,cfg)
    settings=cfg.get('history_research',default={})
    if not settings.get('enabled'):return
    every=settings.get('every_cycles',5);hours=settings.get('min_interval_hours',24)
    if type(every) is not int or every<1 or type(hours) not in (int,float) or hours<1:raise ValueError('Invalid history research cadence')
    if conn.execute("SELECT 1 FROM tasks WHERE status IN ('queued','claimed','running','unknown')").fetchone():return
    if conn.execute("SELECT 1 FROM research_cycles WHERE state!='closed'").fetchone():return
    evidence=snapshot(conn)
    last=conn.execute('SELECT snapshot_json,created_at FROM history_research ORDER BY created_at DESC LIMIT 1').fetchone()
    previous=json.loads(last[0])['closed_cycles'] if last else 0
    if evidence['closed_cycles']-previous<every:return
    if last and util.now()-util.parse_iso(last[1])<dt.timedelta(hours=hours):return
    data=routing.catalog(cfg);routes=data['presets'][routing.active_preset(conn,cfg,data)]['routes']
    if not any(a!=b and not routing._unavailable(conn,cfg,a) and not routing._unavailable(conn,cfg,b) for a in routes['research'] for b in routes['review']):return
    enqueue(conn,cfg,evidence)


def context(conn,cfg):
    setup(conn)
    if not cfg.get('history_research','use_priorities',default=True):return ''
    row=conn.execute("SELECT * FROM history_research WHERE state='accepted' ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not row:return ''
    ttl=cfg.get('research_learning','priority_ttl_days',default=30)
    if type(ttl) is not int or not 1<=ttl<=90:raise ValueError('priority_ttl_days must be 1..90')
    if util.now()-util.parse_iso(row['updated_at'])>=dt.timedelta(days=ttl):return ''
    evidence=json.loads(row['snapshot_json']);rec=validate_recommendation({'recommendation':json.loads(row['recommendation_json'])},evidence)
    return '\n历史复盘研究重点（仅固定指导语；不是质量证明，不替代本轮验证）：\n'+json.dumps(
        {'snapshot_hash':row['snapshot_hash'],'covered_closed_cycles':evidence['closed_cycles'],
         'priorities':[{'guidance':RULES[p['rule']],'evidence_cycles':p['cycles']} for p in rec['priorities']]},ensure_ascii=False)


def command(args):
    from .i18n import text
    from .config import Config
    from . import db,autopilot
    cfg=Config.load(args.config,str(Path.cwd()));conn=db.connect(cfg.db_path)
    try:
        autopilot.setup(conn);setup(conn)
        if args.action=='report':
            result=snapshot(conn)
            last=conn.execute('SELECT state,created_at,updated_at,error FROM history_research ORDER BY created_at DESC LIMIT 1').fetchone()
            result['latest_review']=dict(last) if last else None
            result['active_guidance']=context(conn,cfg)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.action in ('enable','disable'):
            if not cfg.path:raise ValueError('Configure project first')
            original=json.loads(Path(cfg.path).read_text())
            original['history_research']={'enabled':args.action=='enable','every_cycles':args.every_cycles,'min_interval_hours':args.min_hours,'use_priorities':True}
            if args.every_cycles<1 or args.min_hours<1:raise ValueError('Cadence must be positive')
            util.write_json(cfg.path,original);Path(cfg.path).chmod(0o600)
            print(json.dumps(original['history_research']));return 0
        conn.execute('BEGIN IMMEDIATE')
        result,created=enqueue(conn,cfg);conn.commit()
        print(json.dumps({'created':created,**result,'note':text(getattr(args,'lang','zh'),'由现有队列执行；每次复盘至少2次模型调用，使用原预算，未自动增加研究轮数','Executed by the existing queue; each review uses at least 2 model calls on the original budget, adding no extra research cycles')},ensure_ascii=False,indent=2));return 0
    finally:conn.close()


def add_parser(sub, lang='zh'):
    from .i18n import text
    p=sub.add_parser('auto-research',help=text(lang, '全历史研究复盘', 'Review all research cycles'))
    p.add_argument('action',choices=['report','run','enable','disable'],nargs='?',default='report')
    p.add_argument('--every-cycles',type=int,default=5)
    p.add_argument('--min-hours',type=float,default=24)
    p.set_defaults(fn=command)
