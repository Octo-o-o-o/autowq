"""One frozen engineering optimization; never changes financial policy or code."""
import datetime as dt
import json
import math
import statistics
import time
from pathlib import Path
from . import util, store, research_campaign_v2 as events

BUNDLE={'schema':'wq.meta/v1','dimension':'gap_recheck','modes':['full','dependency_cache'],
        'minimum_pairs':8,'maximum_opportunities':12,'hours':72,'median_gain':.20,
        'audit_hits':8,'audit_hours':6,'financial_claim':None,'cache_scope':'no_steps_v1'}


def enabled(cfg):return cfg.get('research_dual_loop','enabled',default=False) is True


def authority(cfg):
    deadline=cfg.get('research_dual_loop','valid_until')
    return enabled(cfg) and bool(deadline) and util.now()<util.parse_iso(deadline)


def state(conn):
    raw=store.get_flag(conn,'research_meta_state')
    return json.loads(raw) if raw else {'mode':'full','status':'not_started'}


def signature(conn,p):
    from . import research_framework as framework
    # Steps recurse through external semantic and collector contracts. Until a complete
    # dependency reader is implemented for those paths, they are never cacheable.
    if any(h.get('steps') for h in (p.get('campaign') or {}).get('hypotheses',[])):
        return None
    sources=framework.latest(conn,'research_evidence_source','gap_id');materials=[]
    for gid,s in sorted(sources.items()):
        identity=framework.file_identity(s['source']['path']) if s['adapter']=='local_material_v1' else util.sha256_json(s)
        if identity is None:return None
        materials.append([gid,identity,util.now()>=util.parse_iso(s['valid_until'])])
    return util.sha256_json({'bundle':BUNDLE,'policy':p,'sources':sources,'materials':materials,
                            'gaps':framework.latest(conn,'research_gap','gap_id')})


def sync_gaps(conn,cfg,p,full):
    if not authority(cfg):
        if enabled(cfg):
            current=state(conn);current.update(mode='full',status='authority_expired');store.set_flag(conn,'research_meta_state',json.dumps(current))
        return full(conn,p)
    started=time.monotonic();key=signature(conn,p);s=state(conn)
    now=util.now();deadline=min(util.parse_iso(cfg.get('research_dual_loop','valid_until')),
                                util.parse_iso(s.get('started_at',util.now_iso()))+dt.timedelta(hours=BUNDLE['hours']))
    if s.get('last_at') and now<util.parse_iso(s['last_at']):key=None
    try:cached=json.loads(store.get_flag(conn,'research_meta_cache','null'))
    except (ValueError,TypeError):cached=None;store.set_flag(conn,'research_meta_cache','null')
    hit=key is not None and cached and isinstance(cached,dict) and cached.get('input_hash')==key and 'output' in cached and util.sha256_json(cached['output'])==cached.get('output_hash')
    cache_s=time.monotonic()-started
    if s.get('status')=='not_started':
        s={'mode':'full','status':'shadow','started_at':util.now_iso(),'pairs':[], 'opportunities':0,'hits':0,'bundle_hash':util.sha256_json(BUNDLE)}
        events.append_once(conn,'meta-proposal:'+s['bundle_hash'],'research_policy_proposed',{'bundle':BUNDLE,'financial_claim':None,'scope':'no_steps_v1'})
    if s['status']=='shadow' and (now>=deadline or s['opportunities']>=BUNDLE['maximum_opportunities']):
        s.update(mode='full',status='inconclusive',reason='bounded_trial_ended')
    audit=s.get('mode')=='dependency_cache' and (s.get('hits',0)>=BUNDLE['audit_hits'] or
        not s.get('audit_at') or now-util.parse_iso(s['audit_at'])>=dt.timedelta(hours=BUNDLE['audit_hours']))
    if hit and s['mode']=='dependency_cache' and not audit:
        output=cached['output'];s['hits']=s.get('hits',0)+1
        s['actual_cache_hits']=s.get('actual_cache_hits',0)+1
        s['actual_cache_seconds']=s.get('actual_cache_seconds',0)+cache_s
    else:
        opportunity=bool(hit and (s['status']=='shadow' or audit))
        if opportunity:
            s['opportunities']+=1
            store.set_flag(conn,'research_meta_state',json.dumps(s));conn.commit()
        before=signature(conn,p);full_started=time.monotonic()
        try:output=full(conn,p)
        except Exception:
            conn.rollback()
            s.update(mode='full',status='rejected',reason='full_check_failed',financial_comparability='needs_review')
            store.set_flag(conn,'research_meta_state',json.dumps(s));conn.commit()
            raise
        full_s=time.monotonic()-full_started;after=signature(conn,p)
        equal=bool(hit and before==after and output==cached['output'])
        if opportunity:
            pair={'at':util.now_iso(),'equal':equal,'full_seconds':full_s,'cache_seconds':cache_s,
                  'gain':1-cache_s/full_s if full_s else 0,'input_hash':key}
            events.append_once(conn,'meta-pair:'+str(s['opportunities']),'research_policy_shadow',pair)
            if not equal:
                s.update(mode='full',status='rejected',reason='semantic_or_state_difference',affected_since=s.get('activated_at'),financial_comparability='needs_review')
                events.append_once(conn,'meta-rollback:'+s['bundle_hash'],'research_policy_rolled_back',dict(s))
            elif s['status']=='shadow':
                s['pairs'].append(pair)
                if util.now()>=deadline:
                    s.update(mode='full',status='inconclusive',reason='bounded_trial_ended')
                elif len(s['pairs'])==BUNDLE['minimum_pairs']:
                    gain=statistics.median(x['gain'] for x in s['pairs'])
                    s.update(mode='dependency_cache' if gain>=BUNDLE['median_gain'] else 'full',
                             status='active_engineering' if gain>=BUNDLE['median_gain'] else 'inconclusive',median_gain=gain)
                    if s['mode']=='dependency_cache':s['activated_at']=util.now_iso()
                    events.append_once(conn,'meta-evaluation:'+s['bundle_hash'],'research_policy_evaluated',dict(s))
            if audit:s.update(hits=0,audit_at=util.now_iso())
        # Only stable full calls are cached, binding the unchanged pre-state.
        if before and before==after:
            store.set_flag(conn,'research_meta_cache',json.dumps({'input_hash':before,'output':output,'output_hash':util.sha256_json(output)}))
        else:store.set_flag(conn,'research_meta_cache','null')
    if s['status']=='shadow' and (now>=deadline or s['opportunities']>=BUNDLE['maximum_opportunities']):
        s.update(mode='full',status='inconclusive',reason='bounded_trial_ended')
    s['last_at']=util.now_iso();store.set_flag(conn,'research_meta_state',json.dumps(s))
    return output



def required_epoch(conn,cfg,cycle_id=None):
    from . import research_learning as learning,research_lifecycle as lifecycle
    root=cfg.get('research_dual_loop','learning_root')
    row=conn.execute('SELECT * FROM learning_experiments ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
    if not root or not row:raise ValueError('Dual-loop requires its approved experiment root')
    doc=json.loads(row['document_json']);eid=row['experiment_id']
    if doc.get('root_experiment',eid)!=root:raise ValueError('Unrelated experiment cannot own dual-loop work')
    if any(lifecycle.stop_kind(conn,e)=='owner_stop' for e in lifecycle.lineage(conn,eid)[1]):raise ValueError('Owner stopped dual-loop lineage')
    if lifecycle.stop_kind(conn,eid):raise ValueError('Dual-loop epoch stopped; exact successor required')
    if learning.current_baseline(cfg)!=doc['baseline']:raise ValueError('Dual-loop baseline not approved')
    # Registered paired research has its own campaign request ledger. It must not
    # be placed into the ordinary baseline/learning comparison to get permission.
    from . import research_campaign
    if cycle_id and research_campaign.assignment(conn,cycle_id):
        resource=conn.execute('SELECT * FROM learning_resource_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()
        if resource and (resource['experiment_id']!=eid or resource['work_kind']!='campaign'):
            raise ValueError('Campaign belongs to another epoch or population')
        return eid
    own=conn.execute('SELECT * FROM learning_assignments WHERE cycle_id=?',(cycle_id,)).fetchone() if cycle_id else None
    left=lifecycle.remaining(conn,eid)
    if not own and left['cycles']<=0:raise ValueError('Dual-loop cumulative cycles exhausted')
    if own:
        if own['experiment_id']!=eid:raise ValueError('Cycle belongs to another epoch')
        arm=own['arm']
    else:
        from . import autopilot
        cycle=conn.execute('SELECT policy_json FROM research_cycles WHERE cycle_id=?',(cycle_id,)).fetchone() if cycle_id else None
        policy=json.loads(cycle[0]) if cycle else autopilot.policy(cfg)
        stratum=util.sha256_json({'settings':policy.get('settings'),'bindings':policy.get('bindings')})
        if doc.get('population')=='ordinary_only_v2':
            counts={a:conn.execute('SELECT COUNT(*) FROM learning_assignments a JOIN learning_allocation_scopes s ON s.cycle_id=a.cycle_id WHERE a.experiment_id=? AND a.arm=? AND s.stratum=?',(eid,a,stratum)).fetchone()[0] for a in doc['arms']}
            arm=min(doc['arms'],key=lambda a:(counts[a],doc['arms'].index(a)))
        else:
            count=conn.execute('SELECT COUNT(*) FROM learning_assignments WHERE experiment_id=?',(eid,)).fetchone()[0]
            arm=doc['arms'][count%len(doc['arms'])]
    if left['requests_by_arm'][arm]<=0:raise ValueError('Dual-loop next arm request budget exhausted')
    return eid

def discovery_permission(conn,cfg,p,cycle_id=None):
    """Two no-information discoveries per frozen input; reservations survive restart."""
    if not authority(cfg):return {'allowed':False,'reason':'dual_loop_authority_expired'}
    from . import research_framework as framework
    try:required_epoch(conn,cfg,cycle_id)
    except ValueError as exc:return {'allowed':False,'reason':str(exc)}
    sources=framework.latest(conn,'research_evidence_source','gap_id')
    material={k:framework.file_identity(v['source']['path']) for k,v in sources.items() if v['adapter']=='local_material_v1'}
    # Neither generated issue IDs, closed-cycle watermarks nor our own events refill this allowance.
    inputs={'policy':p,'sources':sources,'material':material,'bundle':BUNDLE}
    try:observations=observation_fingerprint(conn,cfg)
    except ValueError:
        return {'allowed':False,'reason':'waiting_unreadable_or_changed_consumed_receipt'}
    # Preserve the pre-upgrade episode identity when there is no new information.
    if observations is not None:inputs['observations']=observations
    identity=util.sha256_json(inputs)
    if any(value is None or value!=sources[k]['source']['sha256'] for k,value in material.items()):return {'allowed':False,'reason':'waiting_unreadable_or_changed_registered_material'}
    row=store.get_flag(conn,'discovery_episode:'+identity)
    doc=json.loads(row) if row else {'input_hash':identity,'empty_cycles':[]}
    budget_left=int(store.get_flag(conn,'dual_model_reservations:'+cfg.get('research_dual_loop','root_id'),'0'))<64
    allowed=len(doc['empty_cycles'])<2 and budget_left
    reason='bounded_discovery' if allowed else 'waiting_changed_observation' if budget_left else 'dual_model_budget_exhausted'
    return {'allowed':allowed,'reason':reason, 'episode':identity,'empty_cycles':doc['empty_cycles']}


def observation_fingerprint(conn,cfg):
    """Only verified, already consumed real receipts can change discovery inputs.

    A closed-cycle counter or generated proposal is not new information. This
    keeps two old failed discoveries from suppressing later genuine observations.
    Cumulative model/request budgets remain attached to the original root.
    """
    receipts=[]
    for row in conn.execute("SELECT key,value FROM state_flags WHERE key LIKE 'information_consumed:%' ORDER BY key"):
        try:cid=int(row['value'])
        except (ValueError,TypeError):continue
        if store.get_flag(conn,'dual_cycle:'+str(cid))!=cfg.get('research_dual_loop','learning_root'):continue
        tid=row['key'].split(':',1)[1]
        if not information_receipt(conn,cid,tid):
            raise ValueError('Consumed observation no longer validates')
        run=conn.execute('SELECT alpha_id,evidence_path FROM brain_runs WHERE task_id=?',(tid,)).fetchone()
        try:digest=util.sha256_json(util.read_json(run['evidence_path']))
        except (OSError,ValueError,TypeError) as exc:
            raise ValueError('Consumed observation is unreadable') from exc
        receipt={'task':tid,'cycle':cid,'alpha':run['alpha_id'],'sha256':digest}
        key='discovery_observation:'+tid
        previous=store.get_flag(conn,key)
        if previous and json.loads(previous)!=receipt:
            raise ValueError('Consumed observation identity changed')
        # Keep the original fingerprint format for upgrades. Missing or edited
        # files cannot remove an accepted observation or mint a fresh episode.
        if not previous:store.set_flag(conn,key,json.dumps(receipt))
        receipts.append(receipt)
    return util.sha256_json(receipts) if receipts else None


def record_discovery(conn,cfg,cid,has_information,p):
    if not enabled(cfg):return
    reservation=store.get_flag(conn,'discovery_reservation:'+str(cid))
    if reservation:identity=reservation
    else:
        permit=discovery_permission(conn,cfg,p,cid);identity=permit.get('episode')
        if not identity:return
        store.set_flag(conn,'discovery_reservation:'+str(cid),identity)
    key='discovery_episode:'+identity
    doc=json.loads(store.get_flag(conn,key,json.dumps({'input_hash':identity,'empty_cycles':[]})))
    if not has_information and cid not in doc['empty_cycles']:doc['empty_cycles'].append(cid)
    if has_information and cid in doc['empty_cycles']:doc['empty_cycles'].remove(cid)
    store.set_flag(conn,key,json.dumps(doc))


def information_receipt(conn,cid,tid):
    if not tid:return False
    task=conn.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone()
    if not task or task['status']!='succeeded' or json.loads(task['payload_json']).get('research_cycle_id')!=cid:return False
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='brain_runs'").fetchone():return False
    run=conn.execute("SELECT * FROM brain_runs WHERE task_id=? AND state='complete'",(tid,)).fetchone()
    if not run or not run['location'] or not run['alpha_id'] or not run['evidence_path']:return False
    try:alpha=util.read_json(run['evidence_path'])
    except (OSError,ValueError):return False
    if not isinstance(alpha,dict) or alpha.get('id')!=run['alpha_id'] or not isinstance(alpha.get('is'),dict):return False
    sharpe=alpha['is'].get('sharpe')
    if type(sharpe) not in (int,float) or not math.isfinite(sharpe):return False
    from . import research_learning
    simulation=store.find_simulation_by_remote(conn,run['alpha_id'])
    if not simulation or simulation['synthetic'] or simulation['source']!='api' or simulation['status'] not in research_learning.SIMULATION_COMPLETED or simulation['imported_at']<task['created_at']:return False
    key='information_consumed:'+tid
    previous=store.get_flag(conn,key)
    if previous:return previous==str(cid)
    store.set_flag(conn,key,str(cid));return True


def reserve_model(conn,cfg,task_id=None,provider=None):
    """Durable reservation under a write lock shared by every model worker."""
    if not enabled(cfg):return
    owns_transaction=not conn.in_transaction
    if owns_transaction:conn.execute('BEGIN IMMEDIATE')
    try:
        _reserve_model(conn,cfg,task_id,provider)
        conn.commit()
    except BaseException:
        if owns_transaction:conn.rollback()
        raise


def _reserve_model(conn,cfg,task_id,provider):
    if not authority(cfg):raise ValueError('Dual-loop model authority expired')
    if task_id:
        task=conn.execute('SELECT payload_json FROM tasks WHERE task_id=?',(task_id,)).fetchone()
        if not task:raise ValueError('Dual-loop model task missing')
        payload=json.loads(task[0]);cid=payload.get('autopilot_cycle');role=payload.get('role')
        if cid is not None or role in ('research','review'):
            if type(cid) is not int or cid<=0 or role not in ('research','review'):raise ValueError('Dual-loop model requires an assigned Quant cycle')
            cycle=conn.execute('SELECT * FROM research_cycles WHERE cycle_id=?',(cid,)).fetchone()
            owns_stage=bool(cycle and cycle[role+'_task']==task_id)
            if cycle and role=='review':
                from . import research_campaign_v2
                pair=research_campaign_v2.pair_for_cycle(conn,cid)
                if pair:
                    owns_stage=task_id in research_campaign_v2.review_refs(conn,pair['pair_id']).values()
            if not cycle or cycle['state']!=('researching' if role=='research' else 'reviewing') or not owns_stage:raise ValueError('Dual-loop model task does not own the active cycle stage')
            eid=required_epoch(conn,cfg,cid)
            from . import research_campaign
            population='campaign' if research_campaign.assignment(conn,cid) else 'ordinary'
            resource=conn.execute('SELECT * FROM learning_resource_cycles WHERE cycle_id=?',(cid,)).fetchone()
            if population=='campaign':
                if not resource or resource['experiment_id']!=eid or resource['work_kind']!=population:
                    raise ValueError('Dual-loop campaign has no frozen resource ownership')
            elif not conn.execute('SELECT 1 FROM learning_assignments WHERE cycle_id=?',(cid,)).fetchone():
                raise ValueError('Dual-loop model cycle has no frozen assignment')
    root=cfg.get('research_dual_loop','root_id');key='dual_model_reservations:'+root
    used=int(store.get_flag(conn,key,'0'))
    if used>=64:raise ValueError('Dual-loop model start reservation cap 64 reached')
    store.set_flag(conn,key,str(used+1))
    if task_id:store.add_attempt(conn,task_id,'model_start_reserved','reserved',{'root':root,'reservation':used+1,'provider':provider})


def restore_deployment_pause(conn,cfg,receipt):
    actual=[dict(r) for r in conn.execute("SELECT * FROM state_flags WHERE key IN ('paused','pause_origin','pause_reason') ORDER BY key")]
    if actual!=sorted(receipt,key=lambda x:x['key']):raise ValueError('Pause ownership changed; preserve later owner action')
    from . import research_lifecycle
    latest=conn.execute('SELECT experiment_id,document_json FROM learning_experiments ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
    if enabled(cfg) and (not latest or json.loads(latest['document_json']).get('root_experiment',latest[0])!=cfg.get('research_dual_loop','learning_root')):raise ValueError('Deployment learning root changed')
    if latest and any(research_lifecycle.stop_kind(conn,e)=='owner_stop' for e in research_lifecycle.lineage(conn,latest[0])[1]):raise ValueError('Owner stopped lineage')
    for k,v in (('paused','0'),('pause_origin',''),('pause_reason','')):store.set_flag(conn,k,v)
