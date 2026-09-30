"""Versioned paired campaigns. Original tasks own reservations; events own decisions."""
import copy
import json
from collections import Counter
from pathlib import Path
from . import util, research_contracts, research_pair, research_dsl

SCHEMA = 'wq.research-campaign/v2'
ASSIGNMENT = 'campaign_v2_assignment'


def records(conn, kind):
    from .research_campaign import events
    return events(conn,kind)


def append_once(conn, key, kind, doc, cid=None):
    from . import autopilot
    conn.execute('CREATE TABLE IF NOT EXISTS campaign_record_keys(record_key TEXT PRIMARY KEY,event_id INTEGER NOT NULL UNIQUE)')
    old=conn.execute('SELECT e.detail FROM campaign_record_keys k JOIN research_events e ON e.event_id=k.event_id WHERE k.record_key=?',(key,)).fetchone()
    if old:
        if json.loads(old[0]) != doc: raise ValueError('Immutable campaign record conflict: '+key)
        return doc
    autopilot.event(conn,cid,kind,json.dumps(doc,ensure_ascii=False))
    eid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.execute('INSERT INTO campaign_record_keys VALUES(?,?)',(key,eid))
    return doc


def validate(p):
    from .research_campaign import IDS
    c=p.get('campaign')
    if not isinstance(c,dict) or c.get('schema')!=SCHEMA or type(c.get('enabled')) is not bool:raise ValueError('Invalid V2 campaign')
    if not isinstance(c.get('id'),str) or not c['id'].strip() or type(c.get('version')) is not int or c['version']<1:raise ValueError('Stable campaign ID/version required')
    if c.get('pilot_cap')!=56 or c.get('total_cap')!=240:raise ValueError('V2 pilot remains 56; 240 is not execution authorization')
    research_contracts.timestamp(c.get('valid_until'))
    if not c.get('baseline') or not c.get('account_alias'):raise ValueError('Campaign baseline/account required')
    if c.get('scheduler') != 'mixed_work_conserving_v2':raise ValueError('Explicit V2 scheduler contract required')
    hypotheses=c.get('hypotheses')
    if not isinstance(hypotheses,list) or not set(IDS)<={h.get('id') for h in hypotheses} or len({h.get('id') for h in hypotheses})!=len(hypotheses):raise ValueError('Retain all 14 seed opportunities with unique hypothesis IDs')
    if len(hypotheses)>14 and c.get('registry')!='extensible_v1':raise ValueError('Additional hypotheses require the registered agenda contract')
    profiles=c.get('execution_profiles')
    if not isinstance(profiles,dict) or not profiles:raise ValueError('Frozen execution profiles required')
    for pid, profile in profiles.items():
        if not isinstance(pid,str) or not isinstance(profile,dict) or 'campaign' in profile or not isinstance(profile.get('settings'),dict) or not isinstance(profile.get('bindings'),dict):raise ValueError('Independent profile contract required')
    for h in hypotheses:
        if h['id'] not in IDS:
            research_contracts.requirements(h['id'],h.get('required_assertions'))
            registration=h.get('registration') or {}
            if not registration.get('issue_hash') or not registration.get('approved_by') or registration.get('additional_total_budget')!=0:
                raise ValueError('New direction needs an exact owner registration without an implicit budget increase')
        if h.get('state') not in ('ready','blocked') or h.get('request_cap')!=4 or h.get('max_cycles')!=4:raise ValueError('V2 directions retain four cycle/request caps')
        if not all(isinstance(h.get(k),str) and len(h[k])>=8 for k in ('claim','control','falsifier')):raise ValueError('Substantive registered claims required')
        steps=h.get('steps',[])
        if not isinstance(steps,list) or len(steps)>2:raise ValueError('At most two registered pairs')
        if h['state']=='ready' and not steps:raise ValueError('Ready direction needs registered pairs')
        for i, step in enumerate(steps):
            if step.get('id') != ('primary' if i==0 else 'confirmation') or step.get('profile') not in profiles:raise ValueError('Registered step/profile invalid')
            if step.get('condition') not in (('always',) if i==0 else ('if_continue','always')):raise ValueError('Fixed step condition required')
            if not isinstance(step.get('roles_by_arm'),dict) or set(step['roles_by_arm'])!={'treatment','control'}:raise ValueError('Exact roles per arm required')
            for roles in step['roles_by_arm'].values():
                if not isinstance(roles,list) or not roles or roles!=sorted(set(roles)):raise ValueError('Sorted unique arm roles required')
            research_pair.validate_evaluation(step.get('evaluation'))
        if len(steps)==2 and steps[0]['profile']==steps[1]['profile'] and not steps[1].get('transform'):
            raise ValueError('Followup must apply a registered common transform or a different profile')
        cross=any(s['evaluation']['decision_unit']=='cross_scope' for s in steps)
        if (h['id'] in ('H-D0','H-U1','H-U2') or cross) and steps:
            if any(s['evaluation']['decision_unit']!='cross_scope' for s in steps):raise ValueError('D0/U require a whole four-arm decision')
            if len(steps)==2 and steps[0]['evaluation']!=steps[1]['evaluation']:raise ValueError('Freeze identical evaluation scales and whole function across scopes')
            if len(steps)!=2 or steps[1]['condition']!='always' or steps[0]['profile']==steps[1]['profile']:raise ValueError('Cross-scope design needs two internally paired profiles with no quality-based stopping')
            scopes=[{k:profiles[s['profile']]['settings'].get(k) for k in ('region','universe','delay')} for s in steps]
            if scopes[0]==scopes[1]:raise ValueError('Distinct profile labels do not establish distinct scopes')
            changed={k for k in scopes[0] if scopes[0][k]!=scopes[1][k]}
            if h['id']=='H-D0' and changed!={'delay'}:raise ValueError('D0 isolates the registered delay scope')
            if h['id'] in ('H-U1','H-U2') and changed!={'universe'}:raise ValueError('U isolates the registered universe scope')
    return c


def template(p, account_alias):
    from . import research_campaign as old
    c=old.template(p)
    c.update(schema=SCHEMA,scheduler='mixed_work_conserving_v2',account_alias=account_alias,
             execution_profiles={'base':copy.deepcopy({k:v for k,v in p.items() if k!='campaign'})})
    for h in c['hypotheses']:
        h.update(steps=[],data_contract=research_contracts.draft(h['id']))
    c['baseline']=old.baseline({**p,'campaign':c})
    return c


def active(conn,p):
    from .research_campaign import baseline
    c=validate(p)
    if not c['enabled']:raise ValueError('Campaign disabled; reconcile only')
    if util.now()>=research_contracts.timestamp(c['valid_until']):raise ValueError('Campaign expired')
    if c['baseline']!=baseline(p):raise ValueError('Campaign source/binding/settings drift')
    if any(d['campaign_id']==c['id'] for _,d in records(conn,'campaign_stop')):raise ValueError('Campaign stopped; migration cannot erase an explicit stop')
    if any(d['campaign_id']==c['id'] for _,d in records(conn,'campaign_v2_stop')):raise ValueError('Campaign stopped')
    versions=[d for _,d in records(conn,'campaign_frozen')+records(conn,'campaign_v2_frozen') if d['id']==c['id']]
    same=[d for d in versions if d['schema']==SCHEMA and d['version']==c['version']]
    if same and same[0]!=c:raise ValueError('Frozen campaign version changed')
    if versions and not same:
        migrations=[d for _,d in records(conn,'campaign_migrated') if d.get('campaign_id')==c['id'] and d.get('to_hash')==util.sha256_json(c)]
        if not migrations:raise ValueError('Explicit version migration required; historical budgets remain occupied')
    return c


def migrate(conn,p,reason):
    """Record a bounded schema revision; old owners, reservations and stops survive."""
    from . import research_campaign as old
    c=validate(p)
    if not isinstance(reason,str) or len(reason.strip())<8:raise ValueError('Concrete migration reason required')
    if c['baseline']!=old.baseline(p):raise ValueError('Migration baseline must match current source and profiles')
    conn.execute('SAVEPOINT campaign_migration')
    try:
        conn.execute('UPDATE tasks SET updated_at=updated_at WHERE 0')
        if conn.execute("SELECT 1 FROM tasks WHERE status IN ('claimed','running','unknown') LIMIT 1").fetchone():
            raise ValueError('Migrate only with no claimed/running/UNKNOWN; queued retains its old owner')
        versions=[d for _,d in records(conn,'campaign_frozen')+records(conn,'campaign_v2_frozen') if d['id']==c['id']]
        existing=next((d for _,d in records(conn,'campaign_migrated') if d.get('to_hash')==util.sha256_json(c)),None)
        if existing:
            conn.execute('RELEASE campaign_migration');return existing
        if not versions or c['version']<=max(v['version'] for v in versions):raise ValueError('Migration requires an existing lower campaign revision')
        owners={cid:a for cid,a in old.all_assignments(conn) if a['campaign_id']==c['id']}
        known={h['id'] for version in versions for h in version['hypotheses']}
        if any(a.get('hypothesis') not in known for a in owners.values()):raise ValueError('Historical campaign bucket cannot be reconciled')
        used=old.reservations(conn,c['id']);counts=Counter(r['allocation']['hypothesis'] for r in used)
        if len(used)>56 or any(n>4 for n in counts.values()):raise ValueError('Historical campaign exceeds frozen cap; reconcile only')
        doc={'campaign_id':c['id'],'to_hash':util.sha256_json(c),'to_version':c['version'],
             'from_hashes':[util.sha256_json(v) for v in versions],'reason':reason,
             'reserved':len(used),'remaining':56-len(used),'remaining_by_hypothesis':{h['id']:4-counts[h['id']] for h in c['hypotheses']},
             'stopped':any(d['campaign_id']==c['id'] for _,d in records(conn,'campaign_stop')+records(conn,'campaign_v2_stop')),
             'queued_owner_policy':'unchanged','additional_authorization':0}
        append_once(conn,'migration:'+doc['to_hash'],'campaign_migrated',doc)
        append_once(conn,f"freeze:{c['id']}:{SCHEMA}:{c['version']}",'campaign_v2_frozen',c)
        conn.execute('RELEASE campaign_migration');return doc
    except BaseException:
        conn.execute('ROLLBACK TO campaign_migration');conn.execute('RELEASE campaign_migration');raise


def profile_policy(p, profile_id):
    q=copy.deepcopy(p['campaign']['execution_profiles'][profile_id])
    q['campaign']=copy.deepcopy(p['campaign'])
    q['_campaign_root_hash']=util.sha256_json(p)
    q['_campaign_profile_id']=profile_id
    return q


def hypothesis(p,a):
    return next(h for h in p['campaign']['hypotheses'] if h['id']==a['hypothesis'])


def isolation_subject(step):
    return {'kind':step.get('recipe',{}).get('kind') if step['id']=='primary' else 'registered_followup',
            'recipe':step.get('recipe'),'transform':step.get('transform'),
            'profile':step['profile'],'roles_by_arm':step['roles_by_arm'],'step':step['id']}


def eligibility(p,h,step):
    from . import autopilot, catalog
    profile=p['campaign']['execution_profiles'][step['profile']]
    roles=sorted(set(step['roles_by_arm']['treatment']+step['roles_by_arm']['control']))
    status=research_contracts.inspect(step.get('data_contract'),h['id'],profile,p['campaign']['account_alias'],roles,recipe=isolation_subject(step),registered=h.get('required_assertions'))
    from . import research_observation
    capability=research_observation.capability(step['evaluation'],profile)
    status['collector_capability']=capability
    if not capability['ready']:
        status['ready']=False
        if status['state']=='verified':status['state']='missing'
        status['reasons'].extend({'code':code,'missing':capability.get('missing',[])} for code in capability['reason_codes'])
    try:
        autopilot.check_policy(profile)
        for role in roles:catalog.validate_binding_scope(profile,profile['bindings'][role])
        fields={f for b in profile['bindings'].values() for f in b['fields']}
        for value in (h['claim'],h['falsifier'],h['control']):
            if catalog.mentions_field(value,fields):raise ValueError('Private field in model-visible claim')
        if util.now()>=research_contracts.timestamp(profile['valid_until']):raise ValueError('Profile authorization expired')
    except (ValueError,KeyError,TypeError,OSError) as exc:
        status['ready']=False;status['state']='missing';status['reasons'].append({'code':'PROFILE_UNVERIFIED','detail':str(exc)})
    return status


def pairs(conn,campaign_id,hid):
    return [d for _,d in records(conn,'campaign_pair_frozen') if d['campaign_id']==campaign_id and d['hypothesis']==hid]


def evaluations(conn,pair_id):
    return [d for _,d in records(conn,'campaign_evaluated') if d['pair_id']==pair_id]


def select(conn,p, mixed=True):
    from . import research_campaign as old
    c=active(conn,p)
    if conn.execute("SELECT 1 FROM tasks WHERE status='unknown' LIMIT 1").fetchone():raise ValueError('Global UNKNOWN blocks new work')
    used=old.reservations(conn,c['id'])
    counts=Counter(a['hypothesis'] for _,a in old.all_assignments(conn) if a['campaign_id']==c['id'])
    requests=Counter(r['allocation']['hypothesis'] for r in used)
    previous=conn.execute("SELECT cycle_id FROM research_cycles WHERE state='closed' ORDER BY cycle_id DESC LIMIT 1").fetchone()
    if mixed and previous and old.assignment(conn,previous[0]):return None
    ready=[]
    for h in c['hypotheses']:
        if h['state']!='ready' or counts[h['id']]>=4:continue
        prior=pairs(conn,c['id'],h['id']); index=len(prior)
        if index>=len(h['steps']):continue
        step=h['steps'][index]
        # A failed/rejected generation has no frozen pair, and remains a consumed attempt.
        if prior:
            result=evaluations(conn,prior[-1]['pair_id'])
            if not result:continue
            decision=next((d for _,d in records(conn,'campaign_action_decided') if d['pair_id']==prior[-1]['pair_id']),None)
            if not decision or decision['next_action']!='continue_registered_step':continue
            if step!=prior[0]['registered_next_steps'][0]:continue
        if (len(used)>=c['pilot_cap'] or requests[h['id']]>=h['request_cap']) and step['evaluation']['reuse_policy']!='descriptive_reuse':continue
        if not eligibility(p,h,step)['ready']:continue
        ready.append((counts[h['id']],next(i for i,x in enumerate(c['hypotheses']) if x['id']==h['id']),h,step))
    if not ready:return None
    _,_,h,step=min(ready,key=lambda x:x[:2])
    allocation={'schema':SCHEMA,'campaign_id':c['id'],'version':c['version'],'hypothesis':h['id'],
                'phase':step['id'],'profile':step['profile'],'step':step['id']}
    if step['id']=='confirmation':
        primary=next(x for x in pairs(conn,c['id'],h['id']) if x['step']=='primary')
        fixed=copy.deepcopy(primary['candidates']['treatment'])
        if step.get('transform'):fixed=research_pair.transform(fixed,step['transform'],c['execution_profiles'][step['profile']]['bindings'])
        allocation['fixed_candidate']=fixed
    return allocation


def allocate(conn,p,cid):
    from . import research_campaign as old
    a=select(conn,p)
    if not a:return None
    row=conn.execute('SELECT state,research_task FROM research_cycles WHERE cycle_id=?',(cid,)).fetchone()
    if not row or row['state']!='researching' or row['research_task'] or old.assignment(conn,cid):raise ValueError('Assign before research dispatch')
    c=active(conn,p)
    append_once(conn,f"freeze:{c['id']}:{SCHEMA}:{c['version']}",'campaign_v2_frozen',c)
    append_once(conn,'assign:'+str(cid),ASSIGNMENT,a,cid)
    resolved=profile_policy(p,a['profile'])
    conn.execute('UPDATE research_cycles SET policy_json=?,policy_hash=? WHERE cycle_id=?',(json.dumps(resolved),util.sha256_json(resolved),cid))
    return a


def step_spec(p,a):
    return next(s for s in hypothesis(p,a)['steps'] if s['id']==a['step'])


def prompt(p,a):
    h=hypothesis(p,a);step=step_spec(p,a)
    return '\nV2固定成对测量；只输出原四字段candidate，不输出plans，不改变已登记的干预：'+json.dumps({
        'hypothesis':a['hypothesis'],'step':a['step'],'claim':h['claim'],'falsifier':h['falsifier'],
        'control':h['control'],'recipe':step.get('recipe'),'roles_by_arm':step['roles_by_arm'],
        'fixed_candidate':a.get('fixed_candidate'),'fixed_instruction':'若给定fixed_candidate，candidate必须与其完全相同，不重新选参数。',
        'instruction':'候选作为处理臂；程序构造控制臂，两臂仍须分别审查。无合法测量则blocked。'},ensure_ascii=False)


def prepare_pair(conn,p,cid,candidate):
    from . import research_campaign as old
    a=old.assignment(conn,cid);c=active(conn,p);h=hypothesis(p,a);step=step_spec(p,a)
    if not eligibility(p,h,step)['ready']:raise ValueError('Direction data contract is not ready')
    q=profile_policy(p,a['profile'])
    previous=pairs(conn,c['id'],h['id'])
    if a['step']=='confirmation':
        primary=next((x for x in previous if x['step']=='primary'),None)
        if not primary:raise ValueError('Confirmation lacks frozen parent pair')
        if primary['registered_next_steps']!=[step]:raise ValueError('Followup registration differs from frozen parent protocol')
        author=conn.execute('SELECT candidate_json FROM research_cycles WHERE cycle_id=?',(primary['parent_ref']['cycle_id'],)).fetchone()
        if not author or util.sha256_json(json.loads(author[0]))!=primary['parent_ref']['candidate_hash']:
            raise ValueError('Original parent candidate changed')
        arms=copy.deepcopy(primary['candidates'])
        if step.get('transform'):
            arms={arm:research_pair.transform(doc,step['transform'],q['bindings']) for arm,doc in arms.items()}
        if candidate!=arms['treatment']:raise ValueError('Confirmation candidate must equal frozen registered candidate')
        for arm,doc in arms.items():
            research_dsl.validate_candidate(doc,q['bindings'])
            if research_dsl.roles_used(doc['ast'])!=step['roles_by_arm'][arm]:raise ValueError('Confirmation roles mismatch')
        parent_ref=primary['parent_ref']
    else:
        arms=research_pair.candidates(candidate,step.get('recipe'),q['bindings'],step['roles_by_arm'])
        parent_ref={'cycle_id':cid,'candidate_hash':util.sha256_json(candidate)}
    paused=set(q.get('paused_clusters') or [])
    if any(q['bindings'][role].get('cluster') in paused for arm in arms.values() for role in research_dsl.roles_used(arm['ast'])):
        raise ValueError('Campaign arm uses a paused data role')
    pair_id=f"{c['id']}:{h['id']}:{a['step']}"
    doc={'schema':research_pair.SCHEMA,'pair_id':pair_id,'campaign_id':c['id'],'version':c['version'],
         'hypothesis':h['id'],'step':a['step'],'cycle_id':cid,'parent_ref':parent_ref,'candidates':arms,
         'profile':a['profile'],'policy_hash':util.sha256_json(q),'data_contract':copy.deepcopy(step['data_contract']),
         'recipe':copy.deepcopy(step.get('recipe')),'evaluation':copy.deepcopy(step['evaluation']),
         'registered_next_steps':copy.deepcopy(h['steps'][1:]),'frozen_at':util.now_iso()}
    append_once(conn,'pair:'+pair_id,'campaign_pair_frozen',doc,cid)
    return doc


def pair_for_cycle(conn,cid):
    return next((d for cycle,d in records(conn,'campaign_pair_frozen') if cycle==cid),None)


def check_budget(conn,cfg,cid,task_id=None):
    from . import autopilot, research_campaign as old
    a=old.assignment(conn,cid);p=autopilot.policy(cfg);c=active(conn,p)
    if cfg.get('account_alias')!=c['account_alias']:raise ValueError('Campaign account mismatch')
    if (a['campaign_id'],a['version'])!=(c['id'],c['version']):raise ValueError('Campaign revision no longer authorized')
    h=hypothesis(p,a);step=step_spec(p,a)
    if h['state']!='ready' or not eligibility(p,h,step)['ready']:raise ValueError('Campaign data blocked')
    rows=old.reservations(conn,c['id']);bucket=[r for r in rows if r['allocation']['hypothesis']==h['id']]
    if task_id is None:
        if len(rows)>=56 or len(bucket)>=4:raise ValueError('Campaign reservation cap reached')
        if autopilot.week_budget_left(conn,cfg)<=0:raise ValueError('Existing weekly budget exhausted')
    else:
        ids=[r['task_id'] for r in rows];bids=[r['task_id'] for r in bucket]
        if task_id not in ids or ids.index(task_id)>=56 or bids.index(task_id)>=4:raise ValueError('Dispatch lacks valid original reservation')
        refs=[d for _,d in records(conn,'campaign_request_ref') if d['task_id']==task_id and d['cycle_id']==cid]
        if not refs:raise ValueError('Dispatch lacks frozen pair request reference')
        payload=json.loads(conn.execute('SELECT payload_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()[0])
        from . import research_gate
        if all(research_gate.request_hash(payload)!=r['request_hash'] for r in refs):raise ValueError('Dispatch request differs from frozen pair manifest')
        pair=pair_for_cycle(conn,cid)
        if not pair or pair['policy_hash']!=util.sha256_json(profile_policy(p,a['profile'])):raise ValueError('Frozen pair profile changed')


def report(conn,p):
    from . import research_campaign as old, research_learning
    c=p['campaign'];rows=old.reservations(conn,c['id']);ids={r['task_id'] for r in rows}
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    runs=[dict(r) for r in conn.execute('SELECT * FROM brain_runs') if r['task_id'] in ids] if 'brain_runs' in tables else []
    allocations=[(cid,a) for cid,a in old.all_assignments(conn) if a['campaign_id']==c['id']]
    cycles={cid for cid,_ in allocations}
    model_tasks=[dict(t) for t in conn.execute("SELECT task_id,payload_json,status FROM tasks WHERE kind='agent_call'")
                 if json.loads(t['payload_json']).get('autopilot_cycle') in cycles]
    generation_tasks=[t for t in model_tasks if json.loads(t['payload_json']).get('role')=='research']
    reports={r['alpha_id']:json.loads(r['report_json']) for r in conn.execute('SELECT alpha_id,report_json FROM research_feedback')} if 'research_feedback' in tables else {}
    aids={r['alpha_id'] for r in runs if r['alpha_id']}
    complete={aid for aid in aids if reports.get(aid,{}).get('collection_status')=='complete' and not reports[aid].get('validation_gaps')}
    actions=[d for _,d in records(conn,'campaign_action_decided')]
    refs=[d for _,d in records(conn,'campaign_request_ref') if d['cycle_id'] in cycles]
    entries=[]
    for h in c['hypotheses']:
        statuses=[eligibility(p,h,s) for s in h.get('steps',[])]
        data_state='unsupported' if h['id']=='H-V2' else 'verified' if statuses and all(s['ready'] for s in statuses) else 'expired' if any(s['state']=='expired' for s in statuses) else 'missing'
        ps=pairs(conn,c['id'],h['id']);results=[x for pair in ps for x in evaluations(conn,pair['pair_id'])]
        assessment=results[-1]['assessment'] if results else 'data_blocked' if data_state!='verified' else 'unassessed'
        tasks=[r for r in rows if r['allocation']['hypothesis']==h['id']]
        own_cycles={cid for cid,a in allocations if a['hypothesis']==h['id']}
        own_runs=[r for r in runs if r['task_id'] in {t['task_id'] for t in tasks}]
        decisions=[d for d in actions if d['pair_id'] in {x['pair_id'] for x in ps}]
        entries.append({'hypothesis_id':h['id'],'claim':h['claim'],'state':'ready' if data_state=='verified' and h['state']=='ready' else 'blocked',
            'reason':h.get('reason',''),'settings':h['settings'],'required_roles':h.get('required_roles',[]),
            'required_evidence':list(research_contracts.requirements(h['id'],h.get('required_assertions'))),'data_state':data_state,
            'data_gaps':[r for status in statuses for r in status['reasons']] if statuses else [{'code':'REGISTERED_PAIR_AND_DATA_CONTRACT_REQUIRED'}, {'code':'OBSERVATION_CAPABILITY_UNSUPPORTED'}]+[{'code':'ASSERTION_UNVERIFIED','subject':key} for key in research_contracts.requirements(h['id'],h.get('required_assertions'))],
            'assessment':assessment,'pairs':len(ps),'complete_pairs':sum(any(e.get('eligibility')=='verified' for e in evaluations(conn,x['pair_id'])) for x in ps),
            'allocated_cycles':len(own_cycles),'generation_tasks':sum(json.loads(t['payload_json']).get('autopilot_cycle') in own_cycles for t in generation_tasks),
            'reserved':len(tasks),'execution_states':dict(Counter(t['status'] for t in tasks)),
            'unique_completed_executions':len({r['alpha_id'] for r in own_runs if r['state']=='complete' and r['alpha_id']}),
            'independent_mechanism_sources':len({x['parent_ref']['candidate_hash'] for x in ps}),
            'observation_intervals':max((r.get('observations',0) for r in results),default=0),
            'next_action':decisions[-1]['next_action'] if decisions else 'none',
            'claim_scope':'registered_operational_prediction'})
    uncertain=sum(r['state']=='post_started' for r in runs)
    not_sent=sum(r['state']=='not_sent' for r in runs)
    confirmed=sum(r['state'] not in ('post_started','not_sent') for r in runs)
    cost=research_learning.model_cost(conn,cycles)
    generation_cost=research_learning.model_cost(conn,cycles,{t['task_id'] for t in generation_tasks})
    out={'schema':SCHEMA,'enabled':c['enabled'],'id':c['id'],'version':c['version'],'pilot_cap':56,'total_cap':240,
         'reserved':len(rows),'allocated_cycles':len(allocations),'confirmed_posts':confirmed,'dispatch_uncertain':uncertain,
         'not_sent':not_sent,'not_dispatched':len(ids)-len(runs)+not_sent,
         'complete_feedback':len(complete),'submission_candidates':sum(reports[aid].get('submission_candidate') is True for aid in complete),
         'opportunities':entries,'generation_tasks':len(generation_tasks),'model_tasks':len(model_tasks),
         'task_states':dict(Counter(r['status'] for r in rows)),'run_states':dict(Counter(r['state'] for r in runs)),
         'reused_request_refs':sum(not r['created'] for r in refs),
         'unique_completed_executions':len({r['alpha_id'] for r in runs if r['state']=='complete' and r['alpha_id']}),
         'resource_cost':cost,'actual_model_calls':cost['linked_calls'],'actual_generation_calls':generation_cost['linked_calls'],
         'pilot_expansion_authorized':0,'note':'At most two registered pairs; descriptive historical pilot only. Failed/UNKNOWN/not_sent reservations remain occupied. Reuse is not a new execution.'}
    try:
        out['next']=select(conn,p)
        if out['next'] is None:out['stop_reason']='No eligible campaign step; ordinary research requires its own authorization and shared budget.'
    except (ValueError,KeyError,TypeError,OSError) as exc:out['stop_reason']=str(exc)
    return out


def enqueue_review(conn,cfg,row,p,pair,arm):
    from . import autopilot, routing
    candidate=pair['candidates'][arm]
    history=[{'cycle':r[0],'candidate':json.loads(r[1])} for r in conn.execute('SELECT cycle_id,candidate_json FROM research_cycles WHERE candidate_json IS NOT NULL AND cycle_id!=? ORDER BY cycle_id DESC LIMIT 40',(row['cycle_id'],))]
    text=autopilot.review_prompt(candidate,history,bindings=p['bindings'])
    text+='\n固定配对审查；本臂身份与预登记差异：'+json.dumps({'arm':arm,'recipe':pair['recipe'],
         'paired_candidates':pair['candidates'],'inference':'descriptive pilot only'},ensure_ascii=False)
    solo=routing.preset_is_solo(cfg,routing.active_preset(conn,cfg))
    tid=autopilot.make_job(conn,cfg,row['cycle_id'],'review',text,[] if solo else [autopilot.provider(conn,row['research_task'])],
                          None if solo else autopilot.alternate_order(cfg,row['cycle_id'],'review'))
    append_once(conn,'review:'+tid,'campaign_review_enqueued',{'pair_id':pair['pair_id'],'arm':arm,'task_id':tid},row['cycle_id'])
    if arm=='treatment':conn.execute("UPDATE research_cycles SET state='reviewing',review_task=? WHERE cycle_id=?",(tid,row['cycle_id']))
    return tid


def review_refs(conn,pair_id):
    refs={}
    for _,d in records(conn,'campaign_review_enqueued'):
        if d['pair_id']==pair_id:refs[d['arm']]=d['task_id']
    return refs


def verify_review(conn,cfg,row,pair,arm,tid):
    from . import autopilot, routing
    task=autopilot.task(conn,tid)
    if task['status']!='succeeded':raise ValueError('Arm review did not complete')
    if autopilot.same_route_channel(conn,row['research_task'],tid) and not routing.preset_is_solo(cfg,routing.active_preset(conn,cfg)):
        raise ValueError('Both arm reviews require the original distinct-channel gate')
    candidate=pair['candidates'][arm];payload=json.loads(task['payload_json']);doc=autopilot.artifact(conn,tid)
    if not autopilot.validate_review(doc,util.sha256_json(candidate),candidate,payload.get('review_contract_version',1)):
        raise ValueError('Arm review rejected')
    autopilot.validate_resolutions(doc,candidate,payload.get('fallback_objections',[]))
    return {'task_id':tid,'candidate_hash':util.sha256_json(candidate),'review_hash':util.sha256_json(doc)}


def admit_pair(conn,cfg,row,root_policy,pair):
    conn.execute('SAVEPOINT campaign_admission')
    try:
        conn.execute('UPDATE tasks SET updated_at=updated_at WHERE 0')
        current=conn.execute('SELECT state FROM research_cycles WHERE cycle_id=?',(row['cycle_id'],)).fetchone()
        existing={d['arm']:d['task_id'] for _,d in records(conn,'campaign_request_ref') if d['pair_id']==pair['pair_id']}
        if existing:
            if set(existing)!={'treatment','control'}:raise ValueError('Partial persisted pair requires reconciliation')
            conn.execute('RELEASE campaign_admission');return existing
        if not current or current['state']!='reviewing':raise ValueError('Pair admission requires a reviewed cycle')
        result=_admit_pair(conn,cfg,row,root_policy,pair)
        conn.execute('RELEASE campaign_admission');return result
    except BaseException:
        conn.execute('ROLLBACK TO campaign_admission');conn.execute('RELEASE campaign_admission');raise


def _admit_pair(conn,cfg,row,root_policy,pair):
    from . import autopilot, brain_jobs, research_gate
    p=json.loads(row['policy_json']);cid=row['cycle_id'];refs=review_refs(conn,pair['pair_id'])
    if set(refs)!={'treatment','control'}:raise ValueError('Both original arm reviews required')
    approved={arm:verify_review(conn,cfg,row,pair,arm,tid) for arm,tid in refs.items()}
    root=Path(cfg.private_dir)/'research-approvals'/('auto-'+str(cid));root.mkdir(parents=True,exist_ok=True,mode=0o700)
    pp,dp,rp=(root/name for name in ('protocol.json','declarations.json','review.json'))
    protocol={'protocol_id':'auto-'+str(cid),'status':'accepted_for_simulation','platform_ready':True,
              'scope':'registered paired descriptive pilot; not scientific validation or income',
              'required_evidence':[{'id':'approved_scope','blocking':True}], 'pair':pair,'arm_reviews':approved,
              'automatic_submission':False,'variant_policy':'exact_preregistered_manifest_only',
              'preregistered_variants':['campaign_control'],'variant_conditions':{}}
    declarations={'synthetic':False,'declarations':[{'evidence_id':'approved_scope','status':'verified',
                  'source_ref':cfg.resolve(cfg.get('autopilot','policy_file')),'verified_at':p['verified_at'],
                  'note':'Evidence contract and both routed arm reviews; descriptive paired measurement only.'}]}
    docs={}
    for arm,candidate in pair['candidates'].items():
        expression,fields,_=research_dsl.validate_candidate(candidate,p['bindings'])
        settings=p['settings']
        docs[arm]={'research_cycle_id':cid,'title':candidate['title'],'purpose':'research_validation',
             'request':{'type':'REGULAR','regular':expression,'settings':settings},
             'config':{**{k:settings[k] for k in ('region','universe','delay','decay','neutralization','truncation')},
                       'fields':fields,'catalog_verified':True,'extra':{'brain_settings':settings,'purpose':'research_validation'}},
             'evidence':{'settings_verified':True,'source':p['source'],'research_review':str(rp)},
             'campaign_pair':{'pair_id':pair['pair_id'],'arm':arm}}
    acceptance={'status':'accepted_for_simulation','synthetic':False,'reviewer':'local contract gates and distinct routed arm reviewers',
                'reviewed_at':util.now_iso(),'protocol':{'path':str(pp),'sha256':util.sha256_json(protocol)},
                'declarations':{'path':str(dp),'sha256':util.sha256_json(declarations)},
                'allowed_request_hashes':[research_gate.request_hash(docs[a]) for a in ('treatment','control')],
                'autopilot_policy':{'path':cfg.resolve(cfg.get('autopilot','policy_file')),'sha256':util.sha256_json(root_policy)}}
    for path,obj in [(pp,protocol),(dp,declarations),(rp,acceptance)]+[(root/('request-'+arm+'.json'),doc) for arm,doc in docs.items()]:
        util.write_json(str(path),obj);path.chmod(0o600)
    conn.execute('SAVEPOINT campaign_pair_reservation')
    try:
        conn.execute('UPDATE tasks SET updated_at=updated_at WHERE 0')
        tasks={}
        for arm in ('treatment','control'):
            tid,created=brain_jobs.enqueue(conn,cfg,docs[arm]);tasks[arm]=tid
            if not created and pair['evaluation']['reuse_policy']=='new_executions_only':
                raise ValueError('Registered measurement requires new executions; historical dedup cannot create a new blind sample')
            ref={'pair_id':pair['pair_id'],'cycle_id':cid,'arm':arm,'task_id':tid,'created':created,
                 'request_hash':research_gate.request_hash(docs[arm]),'prior_observed':not created,
                 'request_path':str(root/('request-'+arm+'.json'))}
            append_once(conn,'request:'+pair['pair_id']+':'+arm,'campaign_request_ref',ref,cid)
        conn.execute("UPDATE research_cycles SET state='simulating',simulation_task=?,updated_at=? WHERE cycle_id=?",(tasks['treatment'],util.now_iso(),cid))
        conn.execute('INSERT INTO cycle_simulations VALUES(?,?,?,?,?)',(cid,'campaign_control',tasks['control'],str(root/'request-control.json'),util.now_iso()))
        conn.execute('RELEASE campaign_pair_reservation')
    except BaseException:
        conn.execute('ROLLBACK TO campaign_pair_reservation');conn.execute('RELEASE campaign_pair_reservation');raise


def collect_observation(conn,pair,ref):
    from . import research_learning, feedback, research_gate, research_observation
    task=conn.execute('SELECT * FROM tasks WHERE task_id=?',(ref['task_id'],)).fetchone()
    if not task:raise ValueError('Pair task missing')
    payload=json.loads(task['payload_json'])
    if research_gate.request_hash(payload)!=ref['request_hash']:raise ValueError('Pair request identity changed')
    out={'task_id':ref['task_id'],'request_hash':ref['request_hash'],'synthetic':False,'origin':None,
         'execution':'unknown' if task['status']=='unknown' else 'failed' if task['status'] in ('failed','blocked','aborted') else 'pending',
         'evidence_complete':False,'prior_observed':ref['prior_observed'],'ledger_identity_verified':True}
    run=conn.execute('SELECT * FROM brain_runs WHERE task_id=?',(ref['task_id'],)).fetchone()
    if not run or not run['alpha_id']:return out
    sim=conn.execute('SELECT * FROM simulations WHERE remote_id=? ORDER BY imported_at DESC LIMIT 1',(run['alpha_id'],)).fetchone()
    if not sim or sim['synthetic']!=0:return {**out,'synthetic':True}
    outcome=conn.execute('SELECT * FROM learning_outcomes WHERE trial_id=? ORDER BY available_at DESC,rowid DESC LIMIT 1',('task:'+ref['task_id'],)).fetchone()
    if not outcome:return out
    result=json.loads(outcome['document_json']);obs=conn.execute('SELECT * FROM learning_observations WHERE observation_id=?',(result.get('observation_id'),)).fetchone()
    if not obs:return out
    observation=json.loads(obs['document_json']);report=observation.get('report',{})
    if result.get('task_id')!=ref['task_id'] or obs['alpha_id']!=run['alpha_id']:
        return {**out,'collection_error':'OBSERVATION_IDENTITY_MISMATCH'}
    out.update(execution=result['execution'],origin=observation.get('origin'),evidence_complete=result['evidence_complete'],
               outcome_version=outcome['version_id'],observation_id=result.get('observation_id'),alpha_id=run['alpha_id'])
    try:
        if run['state']!='complete':raise ValueError('Execution is not complete')
        alpha_raw=Path(run['evidence_path']).read_bytes();alpha=json.loads(alpha_raw)
        if alpha.get('id')!=run['alpha_id'] or alpha.get('regular',{}).get('code')!=payload['request']['regular'] or any(alpha.get('settings',{}).get(k)!=v for k,v in payload['request']['settings'].items()):
            raise ValueError('Raw execution receipt identity mismatch')
        raw=Path(report['pnl_path']).read_bytes()
        pnl=json.loads(raw)
        if util.sha256_json(pnl)!=observation.get('content_hash'):raise ValueError('PnL snapshot revised')
        values=feedback.daily_pnl(pnl);intervals=sorted(values)
        out.update(intervals=[list(x) for x in intervals],values=[values[x] for x in intervals],pnl_hash=observation['content_hash'])
        root=Path(report['pnl_path']).parent/'campaign-snapshots'
        out['raw_snapshot']=research_observation.retain_bytes(root,raw)
        out['execution_snapshot']=research_observation.retain_bytes(root,alpha_raw)
        out['identity_snapshot']=research_observation.retain_bytes(root,json.dumps({
            'task_id':ref['task_id'],'request':payload['request'],'brain_run':dict(run),
            'simulation':dict(sim),'outcome':result,'observation':observation},sort_keys=True).encode())
        out['parser_version']=research_observation.PARSER_VERSION
        out['identity_verified']=True
        cycle=conn.execute('SELECT policy_json FROM research_cycles WHERE cycle_id=?',(pair['cycle_id'],)).fetchone()
        profile=json.loads(cycle[0])
        out['capability']=research_observation.capability(pair['evaluation'],profile)
        if not out['capability']['ready']:
            out['collection_error']='OBSERVATION_CAPABILITY_UNSUPPORTED'
            out['evidence_complete']=False
        else:
            contract,raw_contract=research_observation.collector_contract(pair['evaluation'],profile)
            out.update(research_observation.mapped_conventions(contract,alpha,pnl))
            out['collector_snapshot']=research_observation.retain_bytes(root,raw_contract)
            out['collector_materials']=[research_observation.retain_bytes(root,Path(m['path']).read_bytes()) for m in contract['materials']]
    except (ValueError,OSError,KeyError,TypeError) as exc:
        out.update(evidence_complete=False,collection_error=str(exc))
    return out


def freeze_observations(conn,pair,observations):
    keys={}
    for arm,obs in observations.items():
        key='observation:'+pair['pair_id']+':'+arm+':'+util.sha256_json(obs)
        append_once(conn,key,'campaign_observation_bound',{'pair_id':pair['pair_id'],'arm':arm,'observation':obs},pair['cycle_id'])
        keys[arm]=key
    return keys


def replay_evaluation(conn,pair_id,observation_hash):
    """Read-only historical recomputation, with no decision or dispatch side effects."""
    from . import research_observation
    pair=next((d for _,d in records(conn,'campaign_pair_frozen') if d['pair_id']==pair_id),None)
    evaluation=next((d for d in evaluations(conn,pair_id) if d['observation_hash']==observation_hash),None)
    if not pair or not evaluation:raise ValueError('Unknown frozen assessment')
    inputs={}
    for step,refs in evaluation['observation_keys'].items():
        inputs[step]={}
        for arm,key in refs.items():
            row=conn.execute('SELECT e.detail FROM campaign_record_keys k JOIN research_events e ON e.event_id=k.event_id WHERE k.record_key=?',(key,)).fetchone()
            if not row:raise ValueError('Historical observation reference missing')
            inputs[step][arm]=research_observation.replay(json.loads(row[0])['observation'])
    if pair['evaluation']['decision_unit']=='cross_scope' and pair['step']=='confirmation':
        first=next(d for d in pairs(conn,pair['campaign_id'],pair['hypothesis']) if d['step']=='primary')
        result=research_pair.compare_cross_scope([first['evaluation'],pair['evaluation']],[inputs['primary'],inputs['confirmation']])
    else:result=research_pair.compare(pair['evaluation'],inputs[pair['step']])
    return {'pair_id':pair_id,'observation_hash':observation_hash,'measurement':result,
            'historical_decision':evaluation['assessment'],'dispatch':False,'next_action':'none'}


def evaluate_pair(conn,p,pair,allow_actions=True):
    from . import research_learning
    research_learning.sync(conn)
    refs={d['arm']:d for _,d in records(conn,'campaign_request_ref') if d['pair_id']==pair['pair_id']}
    observations={arm:collect_observation(conn,pair,ref) for arm,ref in refs.items()}
    digest=util.sha256_json(observations)
    observation_keys={pair['step']:freeze_observations(conn,pair,observations)}
    result=research_pair.compare(pair['evaluation'],observations)
    cross=pair['evaluation']['decision_unit']=='cross_scope'
    if cross and pair['step']=='confirmation':
        primary=next((x for x in pairs(conn,pair['campaign_id'],pair['hypothesis']) if x['step']=='primary'),None)
        first_refs={d['arm']:d for _,d in records(conn,'campaign_request_ref') if primary and d['pair_id']==primary['pair_id']}
        first_obs={arm:collect_observation(conn,primary,ref) for arm,ref in first_refs.items()}
        if primary:observation_keys['primary']=freeze_observations(conn,primary,first_obs)
        result=research_pair.compare_cross_scope([primary['evaluation'],pair['evaluation']],[first_obs,observations]) if primary else {'assessment':'inconclusive','eligibility':'blocked','reason_codes':['CROSS_SCOPE_INCOMPLETE']}
        digest=util.sha256_json({'primary':first_obs,'confirmation':observations})
    previous=evaluations(conn,pair['pair_id'])
    spec_hash=util.sha256_json(pair['evaluation'])
    old=next((d for d in previous if d['observation_hash']==digest and d['spec_hash']==spec_hash),None)
    if old:return old
    action='none'
    if allow_actions and result['eligibility']=='verified':
        if cross and pair['step']=='primary':
            action='continue_registered_step'
        elif result['assessment']=='continue':
            action='continue_registered_step' if pair['step']=='primary' and pair['registered_next_steps'] else 'create_review_proposal'
        else:action='stop'
    # The first complete decision owns the action; new evidence cannot revive it.
    decision=next((d for _,d in records(conn,'campaign_action_decided') if d['pair_id']==pair['pair_id']),None)
    key='evaluation:'+pair['pair_id']+':'+spec_hash+':'+digest
    if decision:action='none'
    doc={**result,'pair_id':pair['pair_id'],'observation_hash':digest,'spec_hash':spec_hash,
         'input_versions':{a:o.get('outcome_version') for a,o in observations.items()},'observation_keys':observation_keys,'revision':bool(previous),
         'pilot_expansion_authorized':0,'next_action':action,'decision_unit':pair['evaluation']['decision_unit']}
    if cross and pair['step']=='primary' and result['eligibility']=='verified':
        doc['pair_measurement']=doc['assessment'];doc['assessment']='awaiting_second_scope'
    elif action=='create_review_proposal':doc['assessment']='eligible_for_expansion'
    append_once(conn,key,'campaign_evaluated',doc,pair['cycle_id'])
    if action!='none':
        append_once(conn,'action:'+pair['pair_id'],'campaign_action_decided',{
            'pair_id':pair['pair_id'],'basis':key,'next_action':action},pair['cycle_id'])
    if action=='create_review_proposal':
        append_once(conn,'proposal:'+pair['pair_id'],'campaign_proposal_created',{'pair_id':pair['pair_id'],
                    'additional_authorization':0,'status':'owner_review_required','basis':key},pair['cycle_id'])
    from . import research_knowledge
    research_knowledge.publish(conn,pair,doc)
    return doc


def advance(conn,cfg,row,p):
    from . import autopilot, research_campaign as old, research_strategy, workflow
    cid=row['cycle_id'];a=old.assignment(conn,cid)
    if row['state']=='simulating':
        autopilot._advance_standard(conn,cfg,row,p)
        state=conn.execute('SELECT state FROM research_cycles WHERE cycle_id=?',(cid,)).fetchone()[0]
        if state=='closed':evaluate_pair(conn,p,pair_for_cycle(conn,cid))
        return
    active(conn,p)
    resolved=profile_policy(p,a['profile'])
    if row['policy_hash']!=util.sha256_json(resolved):raise ValueError('Frozen campaign profile changed')
    h=hypothesis(p,a)
    if not eligibility(p,h,step_spec(p,a))['ready']:raise ValueError('Campaign evidence expired or missing before review')
    if conn.execute("SELECT 1 FROM tasks WHERE status='unknown' LIMIT 1").fetchone():return
    if row['state']=='researching':
        task=autopilot.task(conn,row['research_task'])
        if task['status'] in autopilot.ACTIVE_TASKS:return
        if task['status']!='succeeded':
            if a['step']=='primary' and task['status']=='failed':
                last=conn.execute("SELECT detail_json FROM attempts WHERE task_id=? AND event IN ('provider_result','artifact_validation') ORDER BY attempt_id DESC LIMIT 1",(task['task_id'],)).fetchone()
                if last and json.loads(last[0]).get('call_status') in ('failed','timeout','artifact_invalid'):
                    if autopilot.fallback_once(conn,cfg,row,resolved,'Campaign research execution failed'):return
            autopilot.finish(conn,cfg,row,'Campaign research task did not complete',problem=True);return
        try:
            proposal={'status':'completed','candidate':a['fixed_candidate']} if a['step']=='confirmation' else autopilot.artifact(conn,task['task_id'],allow_blocked=True)
        except json.JSONDecodeError as exc:
            if a['step']=='primary' and autopilot.fallback_once(conn,cfg,row,resolved,'Research JSON invalid: '+str(exc)):return
            raise
        from . import research_agenda,research_framework
        if research_framework.enabled(cfg):research_agenda.observe(conn,p,cid,proposal)
        if proposal.get('status')=='blocked':
            autopilot.finish(conn,cfg,row,'Campaign researcher reported measurement blocked');return
        if 'plans' in proposal:raise ValueError('V2 only accepts one registered candidate; no plan reselection')
        candidate=proposal.get('candidate');research_dsl.validate_candidate(candidate,resolved['bindings'])
        payload=json.loads(task['payload_json'])
        if a['step']=='primary' and 'fallback_ast' in payload and candidate['ast']!=payload['fallback_ast']:
            raise ValueError('Research fallback cannot change the frozen parent AST')
        _,_,family=research_dsl.validate_candidate(candidate,resolved['bindings'])
        if a['step']=='primary':
            decision=research_strategy.family_decision(conn,candidate['ast'],family,cid,resolved,
                     enabled=bool(cfg.get('research_learning','structural_diversity',default=False)))
            autopilot.event(conn,cid,'family_structure_decision',json.dumps(decision))
            if decision['blocked']:
                autopilot.finish(conn,cfg,row,'Campaign parent family rejected: '+decision['reason']);return
        pair=prepare_pair(conn,p,cid,candidate)
        if a['step']=='confirmation':
            # The original parent already passed family review; this exact registered
            # measurement has no independent-family qualification.
            autopilot.event(conn,cid,'registered_pair_followup',pair['parent_ref']['candidate_hash'])
        conn.execute('UPDATE research_cycles SET candidate_json=?,candidate_hash=?,family_hash=? WHERE cycle_id=?',
                     (json.dumps(candidate),util.sha256_json(candidate),family,cid))
        enqueue_review(conn,cfg,row,resolved,pair,'treatment');return
    pair=pair_for_cycle(conn,cid)
    if not pair:raise ValueError('Review lacks frozen pair')
    refs=review_refs(conn,pair['pair_id']);arm='control' if 'control' in refs else 'treatment';tid=refs[arm]
    task=autopilot.task(conn,tid)
    if task['status'] in autopilot.ACTIVE_TASKS:return
    try:verified=verify_review(conn,cfg,row,pair,arm,tid)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        candidate=pair['candidates'][arm]
        virtual={**row,'review_task':tid,'candidate_json':json.dumps(candidate),'candidate_hash':util.sha256_json(candidate)}
        try:original=autopilot.artifact(conn,tid)
        except (ValueError,KeyError,TypeError,OSError):original=None
        if autopilot.fallback_once(conn,cfg,virtual,resolved,str(exc),original):
            newtid=conn.execute('SELECT review_task FROM research_cycles WHERE cycle_id=?',(cid,)).fetchone()[0]
            append_once(conn,'review:'+newtid,'campaign_review_enqueued',{'pair_id':pair['pair_id'],'arm':arm,'task_id':newtid},cid)
            if arm=='control':conn.execute('UPDATE research_cycles SET review_task=? WHERE cycle_id=?',(row['review_task'],cid))
            return
        autopilot.finish(conn,cfg,row,'Campaign arm review failed; neither arm admitted: '+str(exc),problem=True);return
    append_once(conn,'accepted:'+pair['pair_id']+':'+arm,'campaign_arm_accepted',{'pair_id':pair['pair_id'],'arm':arm,**verified},cid)
    if arm=='treatment':enqueue_review(conn,cfg,row,resolved,pair,'control');return
    if not workflow.stage_enabled(cfg,'simulate'):
        autopilot.finish(conn,cfg,row,'Campaign reviewed; simulation stage disabled');return
    admit_pair(conn,cfg,row,p,pair)
