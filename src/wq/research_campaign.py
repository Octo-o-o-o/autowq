"""有界增量研究：不可变事件分配，tasks 预约事实源，旧请求只对账。"""
import hashlib
import json
from collections import Counter
from pathlib import Path
from . import util, catalog

SCHEMA = 'wq.research-campaign/v1'
IDS = ('H-N1','H-N2','H-N3','H-R1','H-R2','H-R3','H-O1','H-O2','H-O3','H-V1','H-V2','H-D0','H-U1','H-U2')


def baseline(p):
    root = Path(__file__).parent
    source = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest() for f in root.rglob('*.py')}
    return util.sha256_json({'source': source, 'policy': {k:v for k,v in p.items() if k != 'campaign'}, **({'profiles':p['campaign']['execution_profiles']} if (p.get('campaign') or {}).get('schema')=='wq.research-campaign/v2' else {})})


def validate(p):
    c = p.get('campaign')
    if c is None: return None
    if isinstance(c,dict) and c.get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.validate(p)
    if not isinstance(c, dict) or c.get('schema') != SCHEMA or type(c.get('enabled')) is not bool:
        raise ValueError('Campaign contract invalid')
    if not isinstance(c.get('id'), str) or not c['id'].strip() or type(c.get('version')) is not int or c['version'] < 1:
        raise ValueError('Campaign stable id/version required')
    if type(c.get('pilot_cap')) is not int or not 1 <= c['pilot_cap'] <= 56 or c.get('total_cap') != 240:
        raise ValueError('Campaign pilot cap must be 1..56; expansion beyond pilot is not implemented')
    if not isinstance(c.get('baseline'), str) or not c['baseline']:
        raise ValueError('Campaign source/binding/settings baseline required')
    for key in ('valid_until',):
        stamp = util.parse_iso(c[key])
        if stamp.tzinfo is None: raise ValueError('Campaign timezone required')
    hs = c.get('hypotheses')
    if not isinstance(hs, list) or len(hs) != 14 or {h.get('id') for h in hs} != set(IDS):
        raise ValueError('Campaign must retain all 14 opportunities')
    fields = {f for b in p['bindings'].values() for f in b['fields']}
    for h in hs:
        for key in ('claim','falsifier','control'):
            if not isinstance(h.get(key), str) or len(h[key].strip()) < 8 or catalog.mentions_field(h[key], fields):
                raise ValueError('Campaign model text must be substantive and field-free: '+h['id'])
        if h.get('state') not in ('ready','blocked') or not isinstance(h.get('reason'), str):
            raise ValueError('Campaign opportunity state/reason required')
        if type(h.get('request_cap')) is not int or not 1 <= h['request_cap'] <= 4:
            raise ValueError('Campaign per-opportunity pilot cap must be 1..4')
        if type(h.get('max_cycles')) is not int or not 1 <= h['max_cycles'] <= 4:
            raise ValueError('Campaign generation attempts must be bounded to 1..4')
        if not isinstance(h.get('required_roles'), list) or len(set(h['required_roles'])) != len(h['required_roles']):
            raise ValueError('Campaign required roles invalid')
        if not isinstance(h.get('settings'), dict): raise ValueError('Independent settings scope required')
        if h['state'] == 'ready':
            if h['id'] == 'H-V2': raise ValueError('UNSUPPORTED_EVENTWISE_WEIGHTING')
            if h['settings'] != p['settings']: raise ValueError('Campaign scope mismatch; separate verified policy required')
            if not h['required_roles'] or any(r not in p['bindings'] or p['bindings'][r].get('group_field') for r in h['required_roles']):
                raise ValueError('Campaign missing role binding')
            for role in h['required_roles']:
                catalog.validate_binding_scope(p, p['bindings'][role])
            contract = h.get('data_contract') or {}
            for key in ('measurement','availability','missing','source'):
                if not isinstance(contract.get(key), str) or len(contract[key].strip()) < 8:
                    raise ValueError('Campaign data semantics evidence missing: '+key)
            proof = contract.get('evidence') or {}
            if proof not in p.get('evidence_files', []): raise ValueError('Campaign data contract must bind hashed evidence')
            if util.sha256_json(util.read_json(proof['path'])) != proof['sha256']: raise ValueError('Campaign semantic evidence changed')
            if not util.parse_iso(contract['verified_at']) <= util.now() < util.parse_iso(contract['valid_until']):
                raise ValueError('Campaign semantic evidence expired')
    if sum(h['request_cap'] for h in hs) > c['pilot_cap']:
        raise ValueError('Campaign bucket caps exceed pilot cap')
    return c


def events(conn, kind):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_events'").fetchone(): return []
    return [(r['cycle_id'],json.loads(r['detail'])) for r in conn.execute('SELECT cycle_id,detail FROM research_events WHERE kind=? ORDER BY event_id',(kind,))]


def all_assignments(conn):
    return events(conn,'campaign_assignment') + events(conn,'campaign_v2_assignment')


def assignment(conn, cid):
    return next((a for cycle,a in all_assignments(conn) if cycle == cid), None)


def reservations(conn, campaign_id):
    allocated = {cid:a for cid,a in all_assignments(conn) if a['campaign_id'] == campaign_id}
    out = []
    for row in conn.execute("SELECT task_id,payload_json,status FROM tasks WHERE kind='brain_simulation' ORDER BY rowid"):
        cid = json.loads(row['payload_json']).get('research_cycle_id')
        if cid in allocated: out.append({**dict(row), 'allocation':allocated[cid]})
    return out


def active(conn, p):
    if (p.get('campaign') or {}).get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.active(conn,p)
    c = validate(p)
    if not c or not c['enabled']: raise ValueError('Campaign disabled; existing receipts may reconcile only')
    if util.now() >= util.parse_iso(c['valid_until']): raise ValueError('Campaign expired')
    if c['baseline'] != baseline(p): raise ValueError('Campaign source/binding/settings drift')
    if any(d['campaign_id'] == c['id'] for _,d in events(conn,'campaign_stop')): raise ValueError('Campaign stopped')
    frozen = [d for _,d in events(conn,'campaign_frozen') if d['id'] == c['id'] and d['version'] == c['version']]
    if frozen and frozen[0] != c: raise ValueError('Frozen campaign version changed')
    return c


def select(conn, p):
    if (p.get('campaign') or {}).get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.select(conn,p)
    c = active(conn,p)
    used = reservations(conn,c['id'])
    if len(used) >= min(c['pilot_cap'], c['total_cap']): raise ValueError('Campaign review_required; pilot reservations exhausted')
    counts = Counter(a['hypothesis'] for _,a in events(conn,'campaign_assignment') if a['campaign_id'] == c['id'])
    requests = Counter(t['allocation']['hypothesis'] for t in used)
    ready = [h for h in c['hypotheses'] if h['state'] == 'ready' and counts[h['id']] < h['max_cycles'] and requests[h['id']] < h['request_cap']]
    if not ready: raise ValueError('Campaign waiting_evidence_or_review; no executable opportunity; no quota redistribution')
    h = min(ready,key=lambda h:(counts[h['id']],IDS.index(h['id'])))
    return {'campaign_id':c['id'],'version':c['version'],'hypothesis':h['id'],
            'phase':'representative' if counts[h['id']] == 0 else 'control' if counts[h['id']] == 1 else 'bounded_extension'}


def allocate(conn, p, cid):
    if (p.get('campaign') or {}).get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.allocate(conn,p,cid)
    from . import autopilot
    autopilot.setup(conn)
    c = active(conn,p); a = select(conn,p)
    row = conn.execute('SELECT research_task,state FROM research_cycles WHERE cycle_id=?',(cid,)).fetchone()
    if not row or row['state'] != 'researching' or row['research_task'] or assignment(conn,cid):
        raise ValueError('Campaign allocation must precede research dispatch')
    if not any(d['id'] == c['id'] and d['version'] == c['version'] for _,d in events(conn,'campaign_frozen')):
        autopilot.event(conn,None,'campaign_frozen',json.dumps(c,ensure_ascii=False))
    autopilot.event(conn,cid,'campaign_assignment',json.dumps(a))
    return a


def hypothesis(p, a):
    return next(h for h in p['campaign']['hypotheses'] if h['id'] == a['hypothesis'])


def prompt(p, a):
    if a.get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.prompt(p,a)
    h = hypothesis(p,a)
    return '\n\n有界增量研究合同（不得代入平台字段；沿用原review/DSL）：'+json.dumps({
        'id':h['id'],'phase':a['phase'],'claim':h['claim'],'falsifier':h['falsifier'],
        'control':h['control'],'required_roles':h['required_roles'],
        'instruction':'必须使用且仅使用这些角色；无法提出符合测量合同的方案则返回 blocked。'},ensure_ascii=False)


def validate_candidate(conn, cid, p, candidate):
    a = assignment(conn,cid)
    if not a: return
    from . import research_dsl
    active(conn,p)
    if set(research_dsl.roles_used(candidate.get('ast'))) != set(hypothesis(p,a)['required_roles']):
        raise ValueError('Campaign candidate roles do not match preregistered hypothesis')


def check_budget(conn,cfg,cid,task_id=None):
    a = assignment(conn,cid)
    if not a: return
    if a.get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.check_budget(conn,cfg,cid,task_id)
    from . import autopilot
    p = autopilot.policy(cfg); c = active(conn,p)
    if (a['campaign_id'],a['version']) != (c['id'],c['version']): raise ValueError('Campaign assignment no longer authorized')
    h = hypothesis(p,a)
    if h['state'] != 'ready': raise ValueError('Campaign opportunity blocked')
    rows = reservations(conn,c['id']); bucket = [r for r in rows if r['allocation']['hypothesis'] == h['id']]
    if task_id is None:
        if len(rows) >= c['pilot_cap'] or len(rows) >= c['total_cap'] or len(bucket) >= h['request_cap']:
            raise ValueError('Campaign reservation cap reached; failed/UNKNOWN remain occupied')
        if autopilot.week_budget_left(conn,cfg) <= 0: raise ValueError('Existing weekly budget exhausted')
    else:
        ids = [r['task_id'] for r in rows]; bids = [r['task_id'] for r in bucket]
        if task_id not in ids or ids.index(task_id) >= min(c['pilot_cap'],c['total_cap']) or bids.index(task_id) >= h['request_cap']:
            raise ValueError('Campaign dispatch lacks valid reservation')


def report(conn,p):
    if (p.get('campaign') or {}).get('schema')=='wq.research-campaign/v2':
        from . import research_campaign_v2
        return research_campaign_v2.report(conn,p)
    c = p.get('campaign')
    if not c: return {'enabled':False,'opportunities':[]}
    rows = reservations(conn,c['id']); ids = {r['task_id'] for r in rows}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    runs = [dict(r) for r in conn.execute('SELECT * FROM brain_runs') if r['task_id'] in ids] if 'brain_runs' in tables else []
    uncertain = sum(r['state'] == 'post_started' for r in runs)
    confirmed = sum(r['state'] not in ('post_started','not_sent') for r in runs)
    allocations = [(cid,a) for cid,a in events(conn,'campaign_assignment') if a['campaign_id'] == c['id']]
    cycles = {cid for cid,_ in allocations}
    references = {r[0] for r in conn.execute('SELECT task_id,cycle_id FROM cycle_simulations') if r[1] in cycles} if 'cycle_simulations' in tables else set()
    reports = {}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'research_feedback' in tables:
        reports = {r['alpha_id']:json.loads(r['report_json']) for r in conn.execute('SELECT alpha_id,report_json FROM research_feedback')}
    aids = {r['alpha_id'] for r in runs if r['alpha_id']}
    complete = {aid for aid in aids if reports.get(aid,{}).get('collection_status') == 'complete'}
    usable = {aid for aid in complete if reports[aid].get('submission_candidate') is True}
    failures = Counter(tag for aid in aids for tag in reports.get(aid,{}).get('diagnosis', []))
    submissions = [dict(r) for r in conn.execute('SELECT alpha_id,state FROM brain_submissions') if r['alpha_id'] in aids] if 'brain_submissions' in tables else []
    proposals = conn.execute('SELECT cycle_id,candidate_json FROM research_cycles').fetchall() if 'research_cycles' in tables else []
    out = {'enabled':c['enabled'],'id':c['id'],'version':c['version'],'pilot_cap':c['pilot_cap'],'total_cap':c['total_cap'],
           'proposed_cycles':sum(r['cycle_id'] in cycles and bool(r['candidate_json']) for r in proposals),
           'complete_feedback':len(complete),'submission_candidates':len(usable),
           'submission_states':dict(Counter(r['state'] for r in submissions)),
           'candidates_per_100_confirmed_posts':100*len(usable)/confirmed if confirmed and not uncertain else None,
           'candidate_yield_range':([100*len(usable)/len(runs),100*len(usable)/confirmed] if confirmed else None),
           'diagnosis_tags_overlapping':dict(failures),'task_states':dict(Counter(r['status'] for r in rows)),
           'confirmed_post_result_unknown':sum(r['state'] != 'post_started' and next(t['status'] for t in rows if t['task_id'] == r['task_id']) == 'unknown' for r in runs),
           'allocated_cycles':len(allocations),'reserved':len(rows),'not_dispatched':len(ids)-len(runs),
           'confirmed_posts':confirmed,'dispatch_uncertain':uncertain,'post_denominator_range':[confirmed,len(runs)],
           'run_states':dict(Counter(r['state'] for r in runs)),'reused_tasks':len(references-ids),
           'opportunities':[dict(hypothesis_id=h['id'],claim=h['claim'],state=h['state'],reason=h['reason'],
                required_roles=h['required_roles'],settings=h['settings'],required_evidence=h.get('required_evidence',[]),
                allocated_cycles=sum(a['hypothesis'] == h['id'] for _,a in allocations),
                reserved=sum(r['allocation']['hypothesis'] == h['id'] for r in rows)) for h in c['hypotheses']],
           'measurement_contract':'legacy_phase_labels_not_strict_pairs',
           'note':'Reservations include queued/failed/UNKNOWN/not_sent. Dispatch uncertainty is not zero POSTs. Reuse is not a new sample. No official uniqueness estimate.'}
    try: out['next'] = select(conn,p)
    except ValueError as exc: out['stop_reason'] = str(exc)
    return out


def command(args):
    from .config import Config
    from . import db, autopilot
    cfg = Config.load(args.config,str(Path.cwd())); conn = db.connect(cfg.db_path)
    try:
        p = autopilot.policy(cfg)
        from . import research_campaign_v2
        if args.action=='replay':
            result=research_campaign_v2.replay_evaluation(conn,args.pair_id,args.observation_hash)
        elif args.action=='migrate':
            result=research_campaign_v2.migrate(conn,p,args.reason);conn.commit()
        elif args.action=='stop':
            if not args.reason or len(args.reason.strip())<8:raise ValueError('Concrete stop reason required')
            conn.execute('BEGIN IMMEDIATE')
            result={'campaign_id':p['campaign']['id'],'reason':args.reason,'stopped':True}
            research_campaign_v2.append_once(conn,'stop:'+p['campaign']['id'],'campaign_stop',result)
            conn.commit()
        elif args.action=='combinations':
            from . import feedback,workflow
            advanced=workflow.load(cfg)
            cap=advanced['combinations']['max_plans'] if advanced else cfg.get('research_feedback','max_combination_plans',default=2)
            result=feedback.combination_diagnostics(conn,cap,current_policy=p)
        else:
            result=template(p) if args.action=='template' else research_campaign_v2.template(p,cfg.get('account_alias')) if args.action=='template-v2' else report(conn,p)
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    finally: conn.close()


def add_parser(sub,lang='zh'):
    parser=sub.add_parser('research-campaign',help='Read campaign opportunity and reservation diagnostics')
    parser.add_argument('action',choices=['report','template','template-v2','combinations','migrate','stop','replay'],nargs='?',default='report')
    parser.add_argument('--reason')
    parser.add_argument('--pair-id')
    parser.add_argument('--observation-hash')
    parser.set_defaults(fn=command)


def template(p):
    """Produce a disabled, complete opportunity list; generating it grants no execution authority."""
    definitions = [
        ('H-N1','陈旧新闻触发的价格冲击可能更易短期反转','novelty方向、发布时点、缺失语义与冲击对齐'),
        ('H-N2','新鲜新闻情绪变化可能优于长期情绪水平','情绪字段语义、历史可得时点与新鲜度口径'),
        ('H-N3','新闻和分析师修正不同步可能提供增量信息','两个来源的时点和匹配规则，不能拿聚合相关当事件顺序'),
        ('H-R1','固定目标财期的盈利修正可能优于滚动序列变化','固定目标财期与换期识别，已有滚动预测不等价'),
        ('H-R2','销售和盈利同向修正可能识别经营变化','同目标财期、同币种单位与两个修正来源'),
        ('H-R3','盈利上调而现金流未跟随可能表示利润质量分歧','盈利和现金流预测的期间、单位、覆盖及发布顺序'),
        ('H-O1','短端相对长端波动定价压力可能影响冲击反转','已核验IV期限、合约聚合、缺失和标的匹配'),
        ('H-O2','持续未平仓参与可能区别趋势与一次性关注','未平仓规模及持续性口径，单个put/call比值不等价'),
        ('H-O3','异常看跌看涨成交分布可能包含方向信息','成交量分布与活动度对照，不能以未平仓代替成交量'),
        ('H-V1','事件平均情绪和总情绪可能捕捉不同机制','VECTOR事件单位、空向量语义和当前reducer证据'),
        ('H-V2','逐事件新鲜度加权可能优于公司日均值交互','UNSUPPORTED_EVENTWISE_WEIGHTING'),
        ('H-D0','已验证的快速衰减机制在合法零延迟信息集可能更有效','独立D0字段scope、可得时点和合法settings'),
        ('H-U1','较小高流动性股票池可能提高机制迁移稳定性','独立TOP1000字段scope与固定机制对照'),
        ('H-U2','高覆盖指数成分池可能改善事件机制可测量性','独立TOPSP500字段scope与固定事件机制对照'),
    ]
    hypotheses = []
    for hid,claim,missing in definitions:
        settings = dict(p['settings'])
        if hid == 'H-D0': settings['delay'] = 0
        if hid == 'H-U1': settings['universe'] = 'TOP1000'
        if hid == 'H-U2': settings['universe'] = 'TOPSP500'
        hypotheses.append({'id':hid,'state':'blocked','reason':missing,'required_evidence':[missing],
            'claim':claim,'falsifier':'预登记对照及后续分段中未显示稳定增量，或数据合同不能支持所称机制。',
            'control':'固定相同数据范围和设置，移除本假设的条件信息；在查看结果前明确对照。',
            'required_roles':[],'settings':settings,'request_cap':4,'max_cycles':4})
    return {'schema':SCHEMA,'id':'country-20260930','version':1,'enabled':False,
            'valid_until':p['valid_until'],'baseline':baseline(p),'pilot_cap':56,'total_cap':240,'hypotheses':hypotheses}
