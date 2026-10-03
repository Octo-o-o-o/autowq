"""Bounded research work selection. Existing tasks execute; events explain decisions."""
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from . import util, store
from .errors import AdapterError
from . import research_campaign_v2 as events

LOCAL_ADAPTERS = ('local_material_v1','brain_field_metadata_v1','brain_operators_v1')
TERMINAL_TASKS = ('succeeded','failed','blocked','aborted')


def enabled(cfg):
    return cfg.get('research_framework','enabled',default=cfg.get('research_learning','maintenance_enabled',default=False)) is True


def setup(conn):
    from . import autopilot,research_learning
    autopilot.setup(conn);research_learning.setup(conn)


def latest(conn, kind, key):
    return {doc[key]:doc for _,doc in events.records(conn,kind)}


def file_identity(path):
    try:return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:return None


def record_gap(conn, doc, previous=None):
    old=(latest(conn,'research_gap','gap_id') if previous is None else previous).get(doc['gap_id'])
    body={k:v for k,v in doc.items() if k not in ('revision','updated_at','first_seen')}
    if old and body=={k:v for k,v in old.items() if k not in ('revision','updated_at','first_seen')}:return old
    result={**body,'revision':old['revision']+1 if old else 1,'updated_at':util.now_iso(),
            'first_seen':old['first_seen'] if old else util.now_iso()}
    events.append_once(conn,'gap:'+result['gap_id']+':'+str(result['revision']),'research_gap',result)
    return result


def register_source(conn,cfg,doc):
    """Explicit owner action; model proposals cannot register a filesystem or network source."""
    setup(conn)
    required={'gap_id','revision','adapter','source','approved_by','valid_until','max_attempts'}
    if not isinstance(doc,dict) or set(doc)!=required:raise ValueError('Exact source registration contract required')
    if doc['gap_id'] not in latest(conn,'research_gap','gap_id'):raise ValueError('Unknown evidence gap')
    if doc['adapter'] not in LOCAL_ADAPTERS:raise ValueError('Only built-in evidence readers are supported')
    if not doc['approved_by'] or type(doc['revision']) is not int or doc['revision']<1:raise ValueError('Source owner/revision required')
    if util.now()>=util.parse_iso(doc['valid_until']):raise ValueError('Evidence source authorization expired')
    if type(doc['max_attempts']) is not int or not 1<=doc['max_attempts']<=5:raise ValueError('Source attempts must be 1..5')
    source=doc['source']
    if not isinstance(source,dict):raise ValueError('Source contract required')
    if doc['adapter']=='local_material_v1':
        if set(source)!={'path','sha256','source_ref'} or not Path(source['path']).is_absolute() or not source['source_ref'] or len(source['sha256'])!=64:
            raise ValueError('Local material needs absolute path, exact content hash and provenance')
        # Registration is an owner action, but credentials are never a research input.
        if Path(source['path']).name in ('session.json','credentials.json','cookies.json') or any(x in Path(source['path']).parts for x in ('.ssh','.aws','.gnupg')):
            raise ValueError('Credential files are not evidence sources')
    elif doc['adapter']=='brain_field_metadata_v1':
        if set(source)!={'field_id','query'} or not isinstance(source['field_id'],str):raise ValueError('Exact scoped field metadata source required')
        gap=latest(conn,'research_gap','gap_id')[doc['gap_id']]
        if source['query']!=gap['query'] or source['field_id'] not in gap['fields']:raise ValueError('Metadata source must belong to the frozen gap binding')
    elif source!={}:raise ValueError('Operator reader uses the fixed official endpoint')
    prior=latest(conn,'research_evidence_source','gap_id').get(doc['gap_id'])
    if prior and doc==prior:return prior
    if prior and doc['revision']<=prior['revision']:raise ValueError('Source revision must increase')
    events.append_once(conn,'evidence-source:'+doc['gap_id']+':'+str(doc['revision']),'research_evidence_source',doc)
    return doc


def sync_gaps(conn,p):
    from . import research_contracts,catalog
    campaign=p.get('campaign') or {}
    if campaign.get('schema')!=events.SCHEMA:return []
    sources=latest(conn,'research_evidence_source','gap_id');previous=latest(conn,'research_gap','gap_id')
    seen=set();result=[]
    for h in campaign['hypotheses']:
        steps=h.get('steps') or [{'id':'primary','profile':'base','roles_by_arm':{},'data_contract':h.get('data_contract',{})}]
        for step in steps:
            profile=campaign['execution_profiles'].get(step['profile']) or next(iter(campaign['execution_profiles'].values()))
            if not h.get('steps'):profile={**profile,'settings':h.get('settings',profile['settings'])}
            roles=sorted(set(r for rs in step.get('roles_by_arm',{}).values() for r in rs))
            if not h.get('steps'):
                roles=[r for r in h.get('required_roles',[]) if r in profile['bindings']]
            contract=step.get('data_contract') or {}
            status=events.eligibility(p,h,step) if h.get('steps') else {'ready':False,'reasons':[]}
            requirements=research_contracts.requirements(h['id'],h.get('required_assertions'))
            for predicate in list(requirements)+['collector_capability']:
                subject={'campaign_id':campaign['id'],'hypothesis':h['id'],'step':step['id'],
                         'predicate':predicate,'scope':research_contracts.scope(profile,campaign['account_alias']),
                         'binding_hash':research_contracts.binding_hash(profile,roles) if roles else None}
                gid=util.sha256_json(subject);seen.add(gid)
                source=sources.get(gid);old=previous.get(gid)
                reasons=status['reasons']
                capability_codes=set(status.get('collector_capability',{}).get('reason_codes',[]))
                capability_reason=lambda r:r['code'] in capability_codes
                predicate_reasons=[r for r in reasons if (capability_reason(r) if predicate=='collector_capability' else not capability_reason(r) and r.get('subject') in (None,predicate))]
                assertions={a['predicate']:a for a in contract.get('assertions',[])}
                valid=(bool(h.get('steps')) and predicate in assertions and not predicate_reasons) if predicate!='collector_capability' else status.get('collector_capability',{}).get('ready',False)
                source_hash=util.sha256_json(source) if source else None
                state='validated' if valid else 'unsupported' if h['id']=='H-V2' else 'owner_required'
                source_identity=None
                if old and old.get('receipt_kind')=='delegated_inbox_envelope' and not valid:
                    state=old['state'] if old['state'] in ('collected','waiting_handoff') else state
                if source and not valid:
                    if util.now()>=util.parse_iso(source['valid_until']):state='expired'
                    else:
                        source_identity=file_identity(source['source']['path']) if source['adapter']=='local_material_v1' else source_hash
                        matching=old and old.get('source_hash')==source_hash and old.get('source_identity')==source_identity
                        state=old['state'] if matching and old['state'] in ('collected','waiting_handoff','rejected','waiting_retry','exhausted') else 'fetchable'
                basis={'contract':contract,'reasons':predicate_reasons,'collector':profile.get('observation_collectors'),
                       'source_hash':source_hash,'source_identity':source_identity}
                doc={**subject,'gap_id':gid,'state':state,'input_hash':util.sha256_json(basis),
                     'source_hash':source_hash,'source_identity':source_identity,
                     'query':catalog.query_from_settings(profile['settings']),
                     'fields':sorted({f for role in roles for f in profile['bindings'][role]['fields']}),
                     'reason':predicate_reasons or ([] if valid else [{'code':'OWNER_SEMANTIC_OR_ADAPTER_CONTRACT_REQUIRED'}]),
                     'owner_required':not valid,'next_trigger':'source_or_contract_revision' if state in ('owner_required','rejected','exhausted','unsupported') else 'evidence_or_time_change'}
                if old and old.get('source_hash')==source_hash and old.get('source_identity')==source_identity:
                    for key in ('task_id','receipt','attempts','next_attempt_at','last_error','verification','receipt_kind'):
                        if key in old:doc[key]=old[key]
                if state=='collected':doc['next_trigger']='semantic_contract_verification'
                result.append(record_gap(conn,doc,previous))
    for gid,old in previous.items():
        if old['campaign_id']==campaign['id'] and gid not in seen and old['state']!='superseded':
            result.append(record_gap(conn,{**old,'state':'superseded','next_trigger':'none'}))
    return result


def pair_input(conn,pair):
    from . import research_learning
    research_learning.sync(conn)
    group=[pair]
    if pair['evaluation']['decision_unit']=='cross_scope' and pair['step']=='confirmation':
        group += [d for d in events.pairs(conn,pair['campaign_id'],pair['hypothesis']) if d['step']=='primary']
    inputs=[]
    for item in group:
        refs=[d for _,d in events.records(conn,'campaign_request_ref') if d['pair_id']==item['pair_id']]
        obs={r['arm']:events.collect_observation(conn,item,r) for r in refs}
        inputs.append({'pair_id':item['pair_id'],'observations':obs})
    return util.sha256_json(inputs)


def reassessment_candidates(conn):
    processed=latest(conn,'research_reassessment_completed','pair_id');result=[]
    closed={r[0] for r in conn.execute("SELECT cycle_id FROM research_cycles WHERE state='closed'")}
    pairs=[d for _,d in events.records(conn,'campaign_pair_frozen') if d['cycle_id'] in closed]
    # Scan a rotating bounded slice; older incomplete pairs cannot disappear behind new ones.
    cursor=int(store.get_flag(conn,'framework_pair_scan_cursor','0'))
    if not pairs:return result
    batch=(pairs[cursor:]+pairs[:cursor])[:16]
    store.set_flag(conn,'framework_pair_scan_cursor',str((cursor+len(batch))%len(pairs)))
    for pair in batch:
        digest=pair_input(conn,pair)
        if processed.get(pair['pair_id'],{}).get('input_hash')==digest:continue
        key='reassess:'+pair['pair_id']+':'+digest
        old=conn.execute('SELECT status FROM tasks WHERE dedup_key=?',(key,)).fetchone()
        if old:continue
        result.append({'id':key,'kind':'reassess','pair_id':pair['pair_id'],'input_hash':digest,
                       'first_seen':pair['frozen_at'],'reason':'Changed paired observation or collector evidence',
                       'estimated_model_calls':0,'estimated_api_reads':0})
    return result


def evidence_candidates(conn,cfg):
    sources=latest(conn,'research_evidence_source','gap_id');result=[]
    for gap in latest(conn,'research_gap','gap_id').values():
        source=sources.get(gap['gap_id'])
        if gap['state'] not in ('fetchable','waiting_retry') or not source:continue
        if util.now()>=util.parse_iso(source['valid_until']):
            record_gap(conn,{**gap,'state':'expired','next_trigger':'authority_or_source_change'});continue
        if source['adapter']!='local_material_v1':
            until=cfg.get('brain_api','authorized_until')
            if not cfg.get('brain_api','enabled') or not until or util.now()>=util.parse_iso(until):continue
        if gap.get('next_attempt_at') and util.now()<util.parse_iso(gap['next_attempt_at']):continue
        version=util.sha256_json(source)
        tasks=[dict(t) for t in conn.execute("SELECT * FROM tasks WHERE kind='research_evidence'") if json.loads(t['payload_json']).get('source_hash')==version]
        if any(t['status'] not in TERMINAL_TASKS for t in tasks):continue
        if len(tasks)>=source['max_attempts']:
            record_gap(conn,{**gap,'state':'exhausted','attempts':len(tasks),'next_trigger':'new_owner_source_revision'});continue
        if any(t['status']=='succeeded' and json.loads(t['payload_json']).get('input_hash')==gap['input_hash'] for t in tasks):continue
        attempt=len(tasks)+1
        result.append({'id':'evidence:'+gap['gap_id']+':'+version+':'+str(attempt),
                       'kind':'evidence','gap_id':gap['gap_id'],'source_hash':version,
                       'input_hash':gap['input_hash'],'attempt':attempt,'first_seen':gap['first_seen'],
                       'reason':'Collect bounded evidence for '+gap['predicate'],
                       'estimated_model_calls':0,'estimated_api_reads':0 if source['adapter']=='local_material_v1' else 1})
    cap=cfg.get('research_framework','evidence_tasks_per_day',default=4)
    if type(cap) is not int or not 0<=cap<=10:raise ValueError('Evidence task daily cap must be 0..10')
    used=conn.execute("SELECT COUNT(*) FROM tasks WHERE kind='research_evidence' AND created_at>=?",(util.now().date().isoformat(),)).fetchone()[0]
    return result if used<cap else []


def choose(conn,candidates):
    if not candidates:return None
    previous=[d for _,d in events.records(conn,'research_work_selected')]
    # One new-research opportunity after at most two auxiliary selections.
    auxiliaries=0
    for doc in reversed(previous):
        if doc['kind']=='research':break
        auxiliaries+=1
    ordinary=next((x for x in candidates if x['kind']=='research'),None)
    if ordinary and auxiliaries>=2:return ordinary
    for candidate in sorted(candidates,key=lambda x:(x['first_seen'],x['id'])):
        skipped=0
        for selection in reversed(previous):
            if selection['kind']==candidate['kind'] or not any(x['kind']==candidate['kind'] for x in selection.get('options',[])):break
            skipped+=1
        if skipped>=4:return candidate
    priorities={'verify':0,'reassess':1,'evidence':2,'maintenance':3,'research':4}
    def score(x):
        age=(util.now()-util.parse_iso(x['first_seen'])).total_seconds()
        return (0 if age>=86400 else 1,priorities[x['kind']],x['first_seen'],x['id'])
    return min(candidates,key=score)


def research_available(conn,cfg,p):
    from . import autopilot
    if not autopilot.enabled(conn,cfg) or autopilot.cycle_limit_reached(conn,cfg):return False
    for deadline in (p['valid_until'],cfg.get('routing','authorized_until'),cfg.get('brain_api','authorized_until')):
        if not deadline or util.now()>=util.parse_iso(deadline):return False
    return autopilot.week_budget_left(conn,cfg)>0


def _tick(conn,cfg):
    from . import autopilot,research_maintenance
    setup(conn)
    if not enabled(cfg):return {'state':'disabled','allow_research':True}
    if store.is_paused(conn):return {'state':'paused','allow_research':False}
    # UNKNOWN 冻结一切；泳道轮次的模型调用常态在途，不再挡住框架的证据/重评工作。
    if conn.execute("SELECT 1 FROM tasks WHERE status='unknown'").fetchone():return {'state':'waiting_inflight','allow_research':True}
    p=autopilot.policy(cfg)
    from . import research_meta,research_evidence
    research_meta.sync_gaps(conn,cfg,p,sync_gaps)
    if research_meta.enabled(cfg):
        research_evidence.prepare(conn,cfg,p);research_evidence.incoming(conn,cfg)
    # 框架自己派发的在途/待派发工作仍挡新选择；各泳道的模型任务不在此列。
    if conn.execute("SELECT 1 FROM tasks WHERE status='queued' AND kind!='agent_call' AND not_before<=?",(util.now_iso(),)).fetchone():
        return {'state':'waiting_queue','allow_research':False}
    candidates=reassessment_candidates(conn)
    if not research_meta.enabled(cfg) or research_evidence.daily_capacity(conn,cfg):
        candidates+=research_evidence.receipt_candidates(conn,cfg) if research_meta.enabled(cfg) else []
        candidates+=evidence_candidates(conn,cfg)
    last=store.get_flag(conn,'research_learning_maintenance_at')
    from .runtime_settings import integer as operating_limit
    if cfg.get('research_learning','maintenance_enabled',default=False) and (not last or (util.now()-util.parse_iso(last)).total_seconds()>=operating_limit(cfg,'research_learning.maintenance_interval_s')):
        candidates.append({'id':'maintenance:'+(last or 'initial'),'kind':'maintenance','first_seen':last or util.now_iso(),
                           'reason':'Due learning and contribution maintenance','estimated_model_calls':0,'estimated_api_reads':0})
    permit=research_meta.discovery_permission(conn,cfg,p) if research_meta.enabled(cfg) else {'allowed':True}
    if research_available(conn,cfg,p) and permit['allowed']:
        cycle=conn.execute('SELECT COALESCE(MAX(cycle_id),0) FROM research_cycles').fetchone()[0]
        previous=[d for _,d in events.records(conn,'research_work_selected')]
        pending=next((d for d in reversed(previous) if d.get('kind')=='research' and d.get('research_watermark')==cycle),None)
        candidates.append({'id':'research:'+str(cycle),'kind':'research','research_watermark':cycle,'first_seen':pending['first_seen'] if pending else util.now_iso(),
                           'reason':'Preserve bounded new-research exploration','estimated_model_calls':None,'estimated_api_reads':None})
    selected=choose(conn,candidates)
    if not selected:return {'state':permit.get('reason','waiting_for_changed_evidence') if not permit['allowed'] else 'waiting_for_changed_evidence','allow_research':False,'allow_existing_lifecycle':True,'allow_new_quant_cycle':False}
    decision={**selected,'options':[{'id':x['id'],'kind':x['kind'],'reason':x['reason']} for x in candidates],
              'policy_hash':util.sha256_json(p),'selection_version':'bounded_work_v1'}
    # Repeated waiting does not create repeated selection records or model calls.
    digest=util.sha256_json(decision)
    events.append_once(conn,'work:'+digest,'research_work_selected',decision)
    if selected['kind']=='maintenance':
        result=research_maintenance.tick(conn,cfg,force=True)
        return {'state':'maintenance','result':result,'allow_research':False}
    if selected['kind']=='research':return {'state':'research','allow_research':True}
    kind={'reassess':'research_reassess','evidence':'research_evidence','verify':'research_evidence_verify'}[selected['kind']]
    tid,created=store.enqueue_task(conn,kind,selected,selected['id'],max_attempts=2)
    return {'state':'queued','task_id':tid,'created':created,'allow_research':False}


def tick(conn,cfg):
    result=_tick(conn,cfg)
    previous=events.records(conn,'research_framework_status')
    state={k:v for k,v in result.items() if k not in ('result','created')}
    if not previous or previous[-1][1]!=state:
        events.append_once(conn,'framework-status:'+str(len(previous)+1),'research_framework_status',state)
    return result


def evidence_step(conn,cfg,task,payload,resources=None):
    from . import research_observation,catalog
    gap=latest(conn,'research_gap','gap_id').get(payload['gap_id'])
    source=latest(conn,'research_evidence_source','gap_id').get(payload['gap_id'])
    if not gap or not source or util.sha256_json(source)!=payload['source_hash']:return 'blocked',{},'Evidence source superseded'
    if util.now()>=util.parse_iso(source['valid_until']):return 'blocked',{},'Evidence source authorization expired'
    try:
        from . import research_meta,research_evidence
        group=util.sha256_json({'adapter':source['adapter'],'source':source['source'],'account':cfg.get('account_alias')})
        prior=[d for _,d in events.records(conn,'research_source_collected') if d['source_identity']==group]
        if source['adapter']=='local_material_v1' and file_identity(source['source']['path'])!=source['source']['sha256']:
            raise ValueError('Registered material changed')
        if research_meta.enabled(cfg) and source['adapter']=='local_material_v1' and prior and file_identity(prior[-1]['receipt']['path'])==prior[-1]['receipt']['sha256']:
            receipt=prior[-1]['receipt']
            record_gap(conn,{**gap,'state':'collected','task_id':task['task_id'],'receipt':receipt,'attempts':payload['attempt'],'next_trigger':'semantic_contract_verification'})
            return 'succeeded',{'gap_id':gap['gap_id'],'receipt':receipt,'semantic_authority':0,'shared_material':True},None
        if source['adapter']=='local_material_v1':
            if research_meta.enabled(cfg):research_evidence.reserve_read(conn,cfg,source)
            raw=Path(source['source']['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=source['source']['sha256']:raise ValueError('Local material hash differs from registered source')
        else:
            from .brain_client import BrainClient
            until=cfg.get('brain_api','authorized_until')
            if not cfg.get('brain_api','enabled') or not until or util.now()>=util.parse_iso(until):return 'blocked',{},'Read authorization unavailable'
            cooldown=store.get_flag(conn,'brain_not_before')
            if cooldown and util.now()<util.parse_iso(cooldown):
                from .brain_jobs import later
                return later(conn,task['task_id'],(util.parse_iso(cooldown)-util.now()).total_seconds(),'Respect shared read cooldown')
            client=BrainClient(cfg.private_dir)
            if research_meta.enabled(cfg):research_evidence.reserve_read(conn,cfg,source)
            if resources is not None:resources['api_read_attempts']+=1
            doc=catalog.field_snapshot(client,source['source']['field_id'],source['source']['query']) if source['adapter']=='brain_field_metadata_v1' else catalog.operator_snapshot(client)
            raw=json.dumps(doc,ensure_ascii=False,sort_keys=True).encode()
        receipt=research_observation.retain_bytes(Path(cfg.private_dir)/'research-evidence',raw)
        if research_meta.enabled(cfg):events.append_once(conn,'source-collected:'+group+':'+receipt['sha256'],'research_source_collected',{'source_identity':group,'receipt':receipt})
        record_gap(conn,{**gap,'state':'collected','task_id':task['task_id'],'receipt':receipt,
                         'attempts':payload['attempt'],'next_trigger':'semantic_contract_verification',
                         'next_attempt_at':None})
        return 'succeeded',{'gap_id':gap['gap_id'],'receipt':receipt,'semantic_authority':0},None
    except (OSError,ValueError,AdapterError) as exc:
        if isinstance(exc,AdapterError) and exc.kind not in (AdapterError.NETWORK,AdapterError.UNKNOWN_REMOTE):raise
        state='rejected' if isinstance(exc,ValueError) else 'waiting_retry'
        record_gap(conn,{**gap,'state':state,'task_id':task['task_id'],'attempts':payload['attempt'],
                         'next_attempt_at':(util.now()+dt.timedelta(hours=2**(payload['attempt']-1))).isoformat(),
                         'last_error':str(exc)[:300]})
        return 'failed',{'gap_id':gap['gap_id'],'state':state},str(exc)


def reassess_step(conn,cfg,task,payload):
    from . import autopilot
    pair=next((d for _,d in events.records(conn,'campaign_pair_frozen') if d['pair_id']==payload['pair_id']),None)
    if not pair:return 'blocked',{},'Pair unavailable'
    p=autopilot.policy(cfg);allow=False
    try:
        c=events.active(conn,p)
        allow=c['id']==pair['campaign_id'] and c['version']==pair['version'] and not store.is_paused(conn)
    except (ValueError,KeyError):pass
    digest=pair_input(conn,pair)
    result=events.evaluate_pair(conn,p,pair,allow_actions=allow)
    from . import research_knowledge
    research_knowledge.publish(conn,pair,result)
    doc={'pair_id':pair['pair_id'],'input_hash':digest,'task_id':task['task_id'],
         'observation_hash':result['observation_hash'],'assessment':result['assessment'],'new_post':0}
    events.append_once(conn,'reassessed:'+pair['pair_id']+':'+digest,'research_reassessment_completed',doc)
    return 'succeeded',doc,None


def dispatch(conn,cfg,task,payload):
    started=time.monotonic()
    resources={'task_id':task['task_id'],'kind':task['kind'],'model_calls':0,
               'api_read_attempts':0,'outcome':'interrupted'}
    try:
        from . import research_evidence
        result=(research_evidence.verify(conn,cfg,task,payload) if task['kind']=='research_evidence_verify' else evidence_step(conn,cfg,task,payload,resources) if task['kind']=='research_evidence'
                else reassess_step(conn,cfg,task,payload))
        resources['outcome']=result[0]
        return result
    except AdapterError as exc:
        resources['outcome']='adapter_error:'+exc.kind
        raise
    finally:
        resources.update(runtime_seconds=time.monotonic()-started,model_cost_usd=0,
                         api_cost_usd=None if resources['api_read_attempts'] else 0,
                         runtime_cost_usd=None)
        # Count logical reads; transport retries, if any, remain in the API transport logs.
        prior=events.records(conn,'research_work_resources')
        events.append_once(conn,'work-resources:'+str(len(prior)+1),'research_work_resources',resources)


def progress(conn,cfg):
    """Read-only quality and experiment health from the current ledger snapshot."""
    from . import research_learning as learning,research_lifecycle as lifecycle,research_maintenance,research_meta
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result={'recent_window':20,'recent_closed':0,'meta_mechanism':research_meta.state(conn),'base_status_counts':{},'learning_configured':cfg.get('research_learning','enabled',default=True),
            'learning_state':research_maintenance.state(conn),'experiment':None}
    result['maintenance_error']=store.get_flag(conn,'research_learning_maintenance_error') or None
    result['runtime_source']=str(Path(__file__).resolve().parent)
    if research_meta.enabled(cfg):
        root=cfg.get('research_dual_loop','root_id')
        used=int(store.get_flag(conn,'dual_model_reservations:'+root,'0'))
        reads=int(store.get_flag(conn,'evidence_reads:'+root,'0'))
        from .runtime_settings import integer as operating_limit
        model_limit=operating_limit(cfg,'research_dual_loop.max_model_starts')
        read_limit=operating_limit(cfg,'research_dual_loop.max_evidence_reads')
        result['dual_resources']={'root':root,'valid_until':cfg.get('research_dual_loop','valid_until'),
            'model_starts':{'used':used,'limit':model_limit,'remaining':max(0,model_limit-used)},
            'evidence_reads':{'used':reads,'limit':read_limit,'remaining':max(0,read_limit-reads)},
            'renewed_by_restart':False}
    if 'research_cycles' in tables:
        cycles=list(conn.execute("SELECT cycle_id,simulation_task FROM research_cycles WHERE state='closed' ORDER BY cycle_id DESC LIMIT 20"))
        result['recent_closed']=len(cycles)
        result['recent_cycles']=[r['cycle_id'] for r in cycles]
        counts={}
        for row in cycles:
            status='not_run' if not row['simulation_task'] else 'missing_result'
            if row['simulation_task'] and 'tasks' in tables:
                task=conn.execute('SELECT status FROM tasks WHERE task_id=?',(row['simulation_task'],)).fetchone()
                if task and task[0] in ('queued','claimed','running'):status='pending'
            if row['simulation_task'] and {'brain_runs','simulations'}<=tables:
                sim=conn.execute('SELECT s.status FROM brain_runs b JOIN simulations s ON s.remote_id=b.alpha_id AND s.synthetic=0 WHERE b.task_id=?',(row['simulation_task'],)).fetchone()
                if sim:status=sim[0]
            counts[status]=counts.get(status,0)+1
        result['base_status_counts']=counts
    if 'learning_experiments' in tables:
        row=conn.execute('SELECT * FROM learning_experiments ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
        if row:
            doc=json.loads(row['document_json']);current=learning.current_baseline(cfg)
            stopped=lifecycle.stop_kind(conn,row['experiment_id'])
            drift=[key for key in current if current[key]!=doc['baseline'].get(key)]
            result['experiment']={'id':row['experiment_id'],'stop_type':stopped,'baseline_changes':drift,
                                  'remaining':lifecycle.remaining(conn,row['experiment_id']),
                                  'next_action':'approve_current_epoch' if stopped=='baseline_superseded' else 'retain_owner_stop' if stopped=='owner_stop' else 'review_baseline_change' if drift else 'observe_registered_comparison'}
            if stopped=='baseline_superseded' or drift:
                result['experiment']['recovery_commands']=[
                    'wq research-framework prepare-epoch --file epoch-transition.json',
                    'wq research-framework approve-epoch --file epoch-transition.json']
    return result


def report(conn):
    gaps=list(latest(conn,'research_gap','gap_id').values())
    work=events.records(conn,'research_work_selected')
    status=events.records(conn,'research_framework_status')
    from . import research_knowledge
    return {'gaps':gaps,'gap_counts':{state:sum(g['state']==state for g in gaps) for state in sorted({g['state'] for g in gaps})},
            'last_selection':work[-1][1] if work else None,'status':status[-1][1] if status else {'state':'not_observed'},'paired_knowledge':research_knowledge.latest(conn),
            'resources':[d for _,d in events.records(conn,'research_work_resources')],
            'queue_source':'tasks','execution_authority':0}


def command(args):
    from .config import Config
    from . import db,autopilot,research_agenda,research_lifecycle,research_knowledge
    cfg=Config.load(args.config,str(Path.cwd()));conn=db.connect(cfg.db_path)
    lock=None
    try:
        if args.action=='report':result={**report(conn),'agenda':research_agenda.report(conn),'enabled':enabled(cfg),'progress':progress(conn,cfg)}
        elif args.action=='prepare-epoch':
            result=research_lifecycle.prepare_transition(conn,cfg,args.owner,args.reason)
            if args.file:util.write_json(args.file,result)
        else:
            from .wrappers.agent import _acquire_lock
            lock=_acquire_lock(cfg.run_dir,'runner')
            if lock is None:raise ValueError('Runner is active; retry at a drained cycle boundary')
            setup(conn)
            doc=util.read_json(args.file) if args.file else None
            if args.action=='propose':result=research_agenda.propose(conn,autopilot.policy(cfg),doc)
            elif args.action=='register':result=research_agenda.register(conn,cfg,doc)
            elif args.action=='source':result=register_source(conn,cfg,doc)
            elif args.action=='approve-epoch':result=research_lifecycle.apply_transition(conn,cfg,doc)
            elif args.action=='resolve-proposal':
                if not isinstance(doc,dict):raise ValueError('Proposal resolution document required')
                result=research_knowledge.resolve_proposal(conn,doc['pair_id'],doc['status'],doc['reason'],doc['owner'],doc.get('authorization'))
            elif args.action=='tick':result=tick(conn,cfg)
            conn.commit()
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    finally:
        conn.close()
        if lock is not None:os.close(lock)


def add_parser(sub,lang='zh'):
    parser=sub.add_parser('research-framework',help='Continuous research gaps, work, knowledge and registered issues')
    parser.add_argument('action',choices=['report','tick','propose','register','source','prepare-epoch','approve-epoch','resolve-proposal'],nargs='?',default='report')
    parser.add_argument('--file',help='Explicit local owner input; report and tick need no file')
    parser.add_argument('--owner',default='local-owner',help='Owner recorded in a prepared epoch transition')
    parser.add_argument('--reason',default='Reviewed local code/configuration update',help='Reason for preparing an exact epoch transition')
    parser.set_defaults(fn=command)
