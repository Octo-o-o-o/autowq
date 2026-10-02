"""Explicit epoch transitions with inherited resources and permanent owner stops."""
import json
from . import util


def stop_kind(conn, experiment_id):
    rows=list(conn.execute('SELECT stop_type FROM learning_stop_types WHERE experiment_id=?',(experiment_id,)))
    if not rows:
        return 'owner_stop' if conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(experiment_id,)).fetchone() else None
    kinds={r[0] for r in rows}
    if 'owner_stop' in kinds:return 'owner_stop'
    return next(k for k in ('authority_expired','budget_exhausted','technical_blocked','baseline_superseded') if k in kinds)


def document(conn, experiment_id):
    row=conn.execute('SELECT document_json FROM learning_experiments WHERE experiment_id=?',(experiment_id,)).fetchone()
    if not row:raise ValueError('Unknown experiment')
    return json.loads(row[0])


def lineage(conn, experiment_id):
    doc=document(conn,experiment_id);root=doc.get('root_experiment',experiment_id)
    return root,[r['experiment_id'] for r in conn.execute('SELECT * FROM learning_experiments')
                 if json.loads(r['document_json']).get('root_experiment',r['experiment_id'])==root]


def arm_cycles(conn, experiment_id, arm):
    _,ids=lineage(conn,experiment_id)
    return {r['cycle_id'] for r in conn.execute('SELECT * FROM learning_assignments') if r['experiment_id'] in ids and r['arm']==arm}


def experiment_cycle_budget(conn):
    """当前实验血缘的轮数：已用、上限、还剩。没有实验时返回 None。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_experiments'").fetchone():
        return None
    row = conn.execute('SELECT experiment_id FROM learning_experiments ORDER BY created_at DESC, rowid DESC LIMIT 1').fetchone()
    if not row:
        return None
    left = remaining(conn, row[0])
    limit = document(conn, left['root_experiment'])['max_cycles']
    return {'experiment_id': row[0], 'root': left['root_experiment'],
            'used': left['used_cycles'], 'limit': limit, 'remaining': left['cycles']}


def set_experiment_cycle_limit(conn, limit):
    """把实验总轮数改成用户选定的上限。不改基线哈希，请求上限至少跟到这个轮数，避免另一道隐藏封顶。"""
    if type(limit) is not int or isinstance(limit, bool) or not 2 <= limit <= 100000:
        raise ValueError('实验轮数上限需在 2–100000 之间')
    budget = experiment_cycle_budget(conn)
    if not budget:
        raise ValueError('当前没有进行中的实验')
    if limit < budget['used']:
        raise ValueError(f"实验已经用了 {budget['used']} 轮，上限不能低于已用轮数")
    _write_cycle_limit(conn, budget['root'], limit, limit)
    assigned = conn.execute('SELECT COUNT(*) FROM learning_assignments WHERE experiment_id=?', (budget['experiment_id'],)).fetchone()[0]
    if budget['experiment_id'] != budget['root']:
        _write_cycle_limit(conn, budget['experiment_id'], limit - (budget['used'] - assigned), limit)
    return experiment_cycle_budget(conn)


def _write_cycle_limit(conn, experiment_id, max_cycles, request_floor):
    doc = document(conn, experiment_id)
    doc['max_cycles'] = max_cycles
    doc['max_requests_per_arm'] = max(int(doc.get('max_requests_per_arm') or 0), request_floor)
    conn.execute('UPDATE learning_experiments SET document_json=? WHERE experiment_id=?', (json.dumps(doc), experiment_id))


def remaining(conn, experiment_id):
    root,ids=lineage(conn,experiment_id);contract=document(conn,root)
    cycles=sum(r[0] in ids for r in conn.execute('SELECT experiment_id FROM learning_assignments'))
    used={}
    for arm in contract['arms']:
        members=arm_cycles(conn,experiment_id,arm)
        used[arm]=sum(json.loads(r[0]).get('research_cycle_id') in members for r in conn.execute("SELECT payload_json FROM tasks WHERE kind='brain_simulation'"))
    return {'root_experiment':root,'cycles':max(0,contract['max_cycles']-cycles),
            'requests_by_arm':{a:max(0,contract['max_requests_per_arm']-n) for a,n in used.items()},
            'used_cycles':cycles,'used_requests_by_arm':used}


def approve_transition(conn, cfg, doc):
    """Owner CLI action only. Exact source/target hashes; does not edit an old experiment."""
    from . import research_learning as learning, research_campaign_v2 as events
    learning.setup(conn)
    required={'transition_id','from_experiment','from_baseline_hash','to_baseline','valid_until','reason','approved_by'}
    if not isinstance(doc,dict) or set(doc)!=required:raise ValueError('Exact epoch transition contract required')
    if any(not isinstance(doc[k],str) or not doc[k].strip() for k in ('transition_id','approved_by','reason')):raise ValueError('Named owner and concrete transition reason required')
    old=document(conn,doc['from_experiment'])
    if old['baseline_hash']!=doc['from_baseline_hash']:raise ValueError('Transition source baseline mismatch')
    if doc['to_baseline']!=learning.current_baseline(cfg):raise ValueError('Approve the actual current candidate baseline')
    if util.now()>=util.parse_iso(doc['valid_until']):raise ValueError('Transition authorization expired')
    if stop_kind(conn,doc['from_experiment'])!='baseline_superseded':raise ValueError('Only a superseded baseline may have a successor; explicit stops are permanent')
    if any(stop_kind(conn,e)=='owner_stop' for e in lineage(conn,doc['from_experiment'])[1]):raise ValueError('Owner stopped this experiment lineage')
    record={**doc,'additional_cycles':0,'additional_requests':0,'remaining_at_approval':remaining(conn,doc['from_experiment'])}
    events.append_once(conn,'epoch-approval:'+doc['transition_id'],'learning_epoch_approved',record)
    return record


def advance(conn,cfg):
    from . import research_learning as learning, research_campaign_v2 as events, autopilot
    learning.setup(conn)
    latest=conn.execute('SELECT * FROM learning_experiments ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
    if not latest:return {'state':'no_experiment'}
    eid=latest['experiment_id'];kind=stop_kind(conn,eid)
    if kind!='baseline_superseded':return {'state':kind or 'unchanged','experiment':eid}
    target=learning.current_baseline(cfg);old=json.loads(latest['document_json'])
    proposal={'from_experiment':eid,'from_baseline_hash':old['baseline_hash'],'to_baseline':target,
              'state':'owner_required','remaining':remaining(conn,eid)}
    digest=util.sha256_json(proposal)
    events.append_once(conn,'epoch-proposal:'+digest,'learning_epoch_proposed',proposal)
    approvals=[d for _,d in events.records(conn,'learning_epoch_approved') if d['from_experiment']==eid and d['to_baseline']==target and util.now()<util.parse_iso(d['valid_until'])]
    if not approvals:return proposal
    root,ancestors=lineage(conn,eid)
    if any(stop_kind(conn,e)=='owner_stop' for e in ancestors):return {'state':'owner_stop','experiment':eid}
    if not autopilot.enabled(conn,cfg):return {'state':'autopilot_disabled','experiment':eid}
    for deadline in (autopilot.policy(cfg)['valid_until'],cfg.get('brain_api','authorized_until'),cfg.get('routing','authorized_until')):
        if not deadline or util.now()>=util.parse_iso(deadline):return {'state':'authority_expired','experiment':eid}
    if conn.execute("SELECT 1 FROM tasks WHERE status IN ('claimed','running','unknown')").fetchone():return {'state':'waiting_inflight','experiment':eid}
    left=remaining(conn,eid)
    if left['cycles']<2 or any(n<=0 for n in left['requests_by_arm'].values()):return {'state':'budget_exhausted','remaining':left}
    approval=approvals[-1];new_id='epoch-'+util.sha256_json({'root':root,'approval':approval})[:20]
    existing=conn.execute('SELECT 1 FROM learning_experiments WHERE experiment_id=?',(new_id,)).fetchone()
    if existing:return {'state':'already_created','experiment':new_id}
    root_doc=document(conn,root)
    doc=learning.freeze_experiment(conn,new_id,target,left['cycles'],root_doc['max_requests_per_arm'])
    doc.update(root_experiment=root,parent_experiment=eid,transition_id=approval['transition_id'],
               inherited_remaining=left,additional_authorization=0)
    conn.execute('UPDATE learning_experiments SET document_json=? WHERE experiment_id=?',(json.dumps(doc),new_id))
    events.append_once(conn,'epoch-created:'+new_id,'learning_epoch_created',{'experiment':new_id,'parent':eid,'remaining':left})
    return {'state':'created','experiment':new_id,'remaining':left}
