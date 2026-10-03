"""Idle, bounded learning maintenance under the runner's existing lock and permissions."""
import datetime as dt
import json
from pathlib import Path

from . import util, store
from .runtime_settings import integer as operating_limit


def state(conn):
    raw=store.get_flag(conn,'research_learning_state')
    if raw:return json.loads(raw)
    table=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_experiments'").fetchone()
    experiment=conn.execute('SELECT experiment_id FROM learning_experiments ORDER BY created_at DESC LIMIT 1').fetchone() if table else None
    return {'mode':'shadow','reason':'Prospective comparison registered; waiting for a cycle boundary','experiment':experiment[0]} if experiment else {'mode':'baseline','reason':'No prospective evidence evaluated'}


def rules_enabled(conn,cfg,cycle_id):
    if not cfg.get('research_learning','enabled',default=True): return False
    assignment=conn.execute('SELECT arm FROM learning_assignments WHERE cycle_id=?',(cycle_id,)).fetchone()
    if assignment:return assignment[0]=='learning'
    return state(conn).get('mode')=='active'


def stage_advice(conn):
    account=store.current_account_stage(conn)
    stage=account['stage']
    stale=not account.get('changed_at') or (util.now()-util.parse_iso(account['changed_at'])).days>7
    if stale:stage='UNKNOWN'
    # Advisory allocation never changes queue/permission caps or claims an invitation advantage.
    weights={'explore':40,'repair':20,'transfer':15,'combine':10,'maintain':15}
    if stage in ('GOLD','INVITED','ONBOARDING'):
        weights={'explore':25,'repair':15,'transfer':10,'combine':10,'maintain':40}
    return {'stage':stage,'recorded_stage':account['stage'],'record_stale':stale,'suggested_weights':weights,'applied_to_budget':False,
            'note':'External onboarding cannot be accelerated by more calls. No unverified reward or invitation multiplier.'}


def tick(conn,cfg,force=False):
    from . import research_learning as learning, research_metrics as metrics, feedback, autopilot
    learning.setup(conn);metrics.setup(conn)
    if not cfg.get('research_learning','maintenance_enabled',default=False):
        return {'status':'disabled'}
    if store.is_paused(conn):return {'status':'paused'}
    last=store.get_flag(conn,'research_learning_maintenance_at')
    if not force and last and (util.now()-util.parse_iso(last)).total_seconds()<operating_limit(cfg,'research_learning.maintenance_interval_s'):
        return {'status':'not_due'}
    # 多泳道下轮次常态开放，维护不能再等「周期边界」；它只做本地台账核对与
    # 有上限的回填任务登记。UNKNOWN 冻结一切，在途模型调用由渠道串行槽约束。
    if conn.execute("SELECT 1 FROM tasks WHERE status='unknown' LIMIT 1").fetchone():
        return {'status':'waiting_for_inflight_or_unknown'}
    from .research_measurement import policy_coverage
    result={'status':'completed','sync':learning.sync(conn),'new_rules':learning.derive_rules(conn),
            'coverage':metrics.coverage(conn),'measurement':policy_coverage(cfg,autopilot.policy(cfg)),'stage':stage_advice(conn),'refresh_tasks':[],
            'automatic_submission':False,'new_simulations':0}
    experiment=conn.execute('SELECT * FROM learning_experiments ORDER BY created_at DESC LIMIT 1').fetchone()
    current=state(conn)
    if experiment:
        eid=experiment['experiment_id'];contract=json.loads(experiment['document_json'])
        stopped=conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(eid,)).fetchone()
        drift=learning.current_baseline(cfg)!=contract['baseline']
        if stopped or drift:
            current={'mode':'baseline','reason':'Experiment stopped or source/policy/model/budget changed','experiment':eid}
            if drift and not stopped:learning.stop_experiment(conn,eid,'Frozen baseline changed; stop experimental dispatch and retain existing evidence',stop_type='baseline_superseded')
        else:
            comparison=metrics.comparison(conn,eid);result['comparison']=comparison
            # Promote the tested bundle only with a positive independent forward observation.
            forward=metrics.forward_report(conn)
            learning_cycles={r[0] for r in conn.execute("SELECT cycle_id FROM learning_assignments WHERE experiment_id=? AND arm='learning'",(eid,))}
            learning_trials={t['trial_id'] for t in learning.as_of(conn) if t['cycle_id'] in learning_cycles}
            eligible_pools={r['pool_id'] for r in conn.execute('SELECT * FROM learning_pool_contracts')
                            if json.loads(r['document_json']).get('candidate') in learning_trials}
            supported=[r for r in forward if r['pool_id'] in eligible_pools and r['status']=='evaluated' and r.get('marginal',{}).get('mean_interval_95',[0,0])[0]>0]
            if comparison['decision']=='eligible' and supported:
                current={'mode':'active','reason':'Prospective comparison and fixed-pool forward evidence support trial use',
                         'experiment':eid,'activated_at':current.get('activated_at') or util.now_iso()}
            elif comparison['decision']=='rollback' or (current.get('mode')=='active' and any(
                    r['pool_id'] in eligible_pools and r['status']=='evaluated' and r.get('marginal',{}).get('mean',0)<=0 for r in forward)):
                current={'mode':'baseline','reason':'Observed regression; rule ranking reverted','experiment':eid}
            else:
                current={'mode':'shadow','reason':'Waiting for comparable families, cost completeness and independent forward data',
                         'experiment':eid}
    from . import research_lifecycle
    result['epoch_transition']=research_lifecycle.advance(conn,cfg)
    if result['epoch_transition']['state']=='created':
        current={'mode':'shadow','reason':'Approved successor inherits remaining lineage budget','experiment':result['epoch_transition']['experiment']}
    # One bounded comparison per UTC week at most; never renew an explicitly stopped experiment.
    if cfg.get('research_learning','auto_experiments',default=False) and not learning.active_experiment(conn):
        authorized_until=cfg.get('brain_api','authorized_until')
        authorized=bool(authorized_until and util.now()<util.parse_iso(authorized_until))
        stop=bool(experiment and conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(experiment['experiment_id'],)).fetchone())
        recent=conn.execute('SELECT 1 FROM learning_experiments WHERE created_at>=?',
                            ((util.now()-dt.timedelta(days=7)).isoformat(),)).fetchone()
        if authorized and not stop and not recent and autopilot.enabled(conn,cfg) and current.get('mode')!='active':
            cycles=cfg.get('research_learning','experiment_cycles',default=40)
            requests=cfg.get('research_learning','requests_per_arm',default=40)
            if type(cycles) is not int or not 2<=cycles<=100 or type(requests) is not int or not 1<=requests<=100:
                raise ValueError('Automatic experiment limits must be cycles 2..100 and requests 1..100')
            eid='auto-'+util.now().strftime('%Y%m%dT%H%M%S')
            learning.freeze_experiment(conn,eid,learning.current_baseline(cfg),cycles,requests)
            current={'mode':'shadow','reason':'Bounded prospective comparison registered; existing permissions and budgets unchanged','experiment':eid}
            result['new_experiment']=eid
    store.set_flag(conn,'research_learning_state',json.dumps(current))
    # Retention and contribution preparation have separate completion conditions.
    trials=[t for t in learning.as_of(conn) if t.get('task_id') and (t['outcome'] or {}).get('quality')=='usable'
            and (t['outcome'] or {}).get('content_hash') and (t['outcome'] or {}).get('data_through')]
    frozen={m['trial_id'] for r in conn.execute('SELECT document_json FROM learning_pools') for m in json.loads(r[0])['members']}
    fresh=[t for t in trials if t['trial_id'] not in frozen]
    if fresh:
        candidate=fresh[0]; pid='auto-'+util.sha256_json(candidate['trial_id'])[:16]
        learning.freeze_pool(conn,pid,[candidate['trial_id']])
    result['contribution_preparation']=metrics.prepare_contributions(conn,trials)
    cap=cfg.get('research_learning','refresh_per_day',default=2)
    if type(cap) is not int or not 0<=cap<=5:raise ValueError('Learning refresh_per_day must be 0..5')
    today=util.now().date().isoformat();used=0
    for row in conn.execute("SELECT payload_json,created_at FROM tasks WHERE kind='brain_feedback' AND created_at>=?",(today,)):
        if json.loads(row['payload_json']).get('learning_maintenance'):used+=1
    # The oldest observation comes first; no new POST, no automatic reauthentication prompt.
    authorized_until=cfg.get('brain_api','authorized_until')
    authorized=bool(authorized_until and util.now()<util.parse_iso(authorized_until))
    if authorized and cfg.get('brain_api','enabled') and cfg.get('research_learning','refresh_enabled',default=False):
        def last_collection(trial):
            oid=(trial['outcome'] or {}).get('observation_id')
            row=conn.execute('SELECT available_at FROM learning_observations WHERE observation_id=?',(oid,)).fetchone()
            return row[0] if row else ''
        for trial in sorted(trials,key=last_collection):
            if used>=cap:break
            run=conn.execute('SELECT alpha_id FROM brain_runs WHERE task_id=?',(trial['task_id'],)).fetchone()
            if not run or not run[0]:continue
            tid,created=feedback.enqueue_refresh(conn,cfg,run[0])
            if created:
                row=conn.execute('SELECT payload_json FROM tasks WHERE task_id=?',(tid,)).fetchone()
                payload=json.loads(row[0]);payload['learning_maintenance']=True
                conn.execute('UPDATE tasks SET payload_json=? WHERE task_id=?',(json.dumps(payload),tid))
                used+=1;result['refresh_tasks'].append(tid)
    result['learning_state']=current
    store.set_flag(conn,'research_learning_maintenance_at',util.now_iso())
    store.set_flag(conn,'research_learning_last_result',json.dumps(result))
    conn.commit()
    util.write_json(str(Path(cfg.run_dir)/'research-learning-status.json'),result)
    return result
