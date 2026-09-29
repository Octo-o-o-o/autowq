"""Reproducible local comparisons with explicit input and time boundaries."""
import datetime as dt
import json
import math
import statistics
import random
from pathlib import Path

from . import util, feedback


def setup(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS learning_pool_contracts(
        pool_id TEXT PRIMARY KEY, document_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS learning_effort(
        entry_id TEXT PRIMARY KEY, cycle_id INTEGER NOT NULL, seconds REAL NOT NULL,
        note TEXT NOT NULL, created_at TEXT NOT NULL)''')


def series_for(conn, trial, outcome=None):
    outcome = outcome or trial.get('outcome') or {}
    observation = conn.execute('SELECT document_json FROM learning_observations WHERE observation_id=?',
                               (outcome.get('observation_id'),)).fetchone()
    if not observation: raise ValueError('Observation snapshot unavailable')
    doc = json.loads(observation[0]); path = doc.get('report', {}).get('pnl_path')
    if not path or not Path(path).is_file(): raise ValueError('PnL snapshot unavailable')
    pnl = util.read_json(path)
    if not doc.get('content_hash') or util.sha256_json(pnl) != doc['content_hash']:
        raise ValueError('PnL snapshot identity unavailable or revised')
    values=feedback.daily_pnl(pnl)
    for start,end in values:
        dt.date.fromisoformat(start);dt.date.fromisoformat(end)
    return values


def _sd(values):
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _stats(values):
    if len(values)<2: return {'observations':len(values),'mean':None,'volatility':None,'mean_over_volatility':None}
    sd=_sd(values)
    return {'observations':len(values),'mean':statistics.mean(values),'volatility':sd,
            'mean_over_volatility':statistics.mean(values)/sd if sd else None}


def freeze_contract(conn, pool_id, members, candidate_id=None, minimum=60, forward_minimum=20):
    """Freeze equal risk weights/scales on common pre-freeze data. No future fit."""
    from .research_learning import as_of
    setup(conn)
    if type(minimum) is not int or minimum<20 or type(forward_minimum) is not int or forward_minimum<2:
        raise ValueError('Calibration requires >=20 intervals and forward >=2')
    all_trials={t['trial_id']:t for t in as_of(conn)}
    ids=list(dict.fromkeys(members))
    if not ids or candidate_id in ids: raise ValueError('Separate a nonempty reference pool and candidate')
    if candidate_id: ids.append(candidate_id)
    if any(i not in all_trials for i in ids): raise ValueError('Unknown pool trial')
    settings=[all_trials[i]['document'].get('settings') for i in ids]
    if any(s!=settings[0] for s in settings):raise ValueError('Pool simulation settings must match')
    if any((all_trials[i].get('outcome') or {}).get('quality')!='usable' or not (all_trials[i].get('outcome') or {}).get('evidence_complete') for i in ids):
        raise ValueError('Pool members require complete usable evidence')
    available={}; errors={}
    for i in ids:
        try: available[i]=series_for(conn,all_trials[i])
        except (ValueError, OSError, KeyError, TypeError) as exc: errors[i]=str(exc)
    cutoff=util.now().date().isoformat()
    contract={'reference':list(members),'candidate':candidate_id,'frozen_on':cutoff,
              'minimum_calibration':minimum,'minimum_forward':forward_minimum,
              'status':'unavailable','errors':errors,'cash_return':None,
              'method':'fixed_pre_freeze_inverse_vol_weights_fixed_unit_risk_v1'}
    if not errors:
        common=set.intersection(*(set(s) for s in available.values()))
        dates=sorted(d for d in common if d[1]<=cutoff)
        if len(dates)<minimum: contract['errors']={'calibration':'Insufficient common intervals'}
        else:
            dates=dates[-252:]
            sds={i:_sd([available[i][d] for d in dates]) for i in ids}
            if any(sd<=0 for sd in sds.values()): contract['errors']={'calibration':'Zero historical variance'}
            else:
                weights={i:1/sds[i] for i in ids}
                def fit(group):
                    total=sum(weights[i] for i in group)
                    ws={i:weights[i]/total for i in group}
                    portfolio=[sum(ws[i]*available[i][d] for i in group) for d in dates]
                    risk=_sd(portfolio)
                    if risk<=0: raise ValueError('Degenerate reference risk')
                    return {'weights':ws,'scale':1/risk}
                try:
                    reference_fit=fit(list(members));augmented_fit=fit(ids)
                except ValueError as exc:
                    contract['errors']={'calibration':str(exc)}
                else:
                    contract.update(status='frozen',reference_fit=reference_fit,augmented_fit=augmented_fit,
                                    calibration_intervals_hash=util.sha256_json(dates),calibration_observations=len(dates),
                                    calibration_end=dates[-1][1],
                                    outcome_versions={i:all_trials[i]['outcome_version'] for i in ids})
    conn.execute('INSERT INTO learning_pool_contracts VALUES(?,?,?)',(pool_id,json.dumps(contract),util.now_iso()))
    return contract


def forward_report(conn, at=None):
    from .research_learning import as_of, timestamp
    setup(conn); trials={t['trial_id']:t for t in as_of(conn,at)}; result=[]
    for row in conn.execute('SELECT * FROM learning_pool_contracts WHERE created_at<=?',(timestamp(at),)):
        c=json.loads(row['document_json']); item={'pool_id':row['pool_id'],'contract_status':c['status'],
            'status':'unavailable','reason':c.get('errors'),'cash_return':None,'official_metric':False}
        if c['status']!='frozen':result.append(item);continue
        ids=c['reference']+([c['candidate']] if c['candidate'] else [])
        series={}; errors={}
        for i in ids:
            t=trials.get(i); outcome=(t or {}).get('outcome') or {}
            try:
                if outcome.get('origin')!='platform_collection':raise ValueError('New platform observation required')
                series[i]=series_for(conn,t)
            except (ValueError,OSError,KeyError,TypeError) as exc:errors[i]=str(exc)
        if errors:item['reason']=errors;result.append(item);continue
        cutoff=max(c['frozen_on'],c['calibration_end'])
        dates=sorted(d for d in set.intersection(*(set(s) for s in series.values()))
                     if d[0]>=cutoff and d[1]<=timestamp(at)[:10])
        # Interval matching is exact; no pretending multi-day gaps are single daily returns.
        item.update(observations=len(dates),from_date=dates[0][0] if dates else None,
                    to_date=dates[-1][1] if dates else None,interval_hash=util.sha256_json(dates))
        if len(dates)<c['minimum_forward']:
            item['reason']='Waiting for genuinely post-freeze common market intervals';result.append(item);continue
        def evaluate(fit):
            return [fit['scale']*sum(w*series[i][d] for i,w in fit['weights'].items()) for d in dates]
        before=evaluate(c['reference_fit']); after=evaluate(c['augmented_fit'])
        differences=[b-a for a,b in zip(before,after)]
        marginal=_stats(differences)
        rng=random.Random(util.sha256_json({'pool':row['pool_id'],'dates':dates}))
        means=[];n=len(differences)
        for _ in range(400):
            sample=[]
            while len(sample)<n:
                start=rng.randrange(n)
                sample.extend(differences[(start+j)%n] for j in range(min(5,n)))
            means.append(statistics.mean(sample[:n]))
        means.sort();marginal['mean_interval_95']=[means[9],means[389]]
        marginal['interval_method']='circular_block_bootstrap_5_intervals_400_resamples_descriptive'
        item.update(status='evaluated',reason=None,reference=_stats(before),augmented=_stats(after),
                    marginal=marginal,
                    interpretation='Local PnL increments at fixed historical unit risk; not account cash, official contribution or proof of independent trials.')
        result.append(item)
    return result


def coverage(conn):
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    def count(sql):return conn.execute(sql).fetchone()[0]
    result={'queue_simulations':count("SELECT COUNT(*) FROM tasks WHERE kind='brain_simulation'"),
            'imported_queue_trials':count('SELECT COUNT(*) FROM learning_trials WHERE task_id IS NOT NULL'),
            'candidate_cycles':count('SELECT COUNT(*) FROM research_cycles WHERE candidate_json IS NOT NULL'),
            'imported_cycle_trials':count("SELECT COUNT(*) FROM learning_trials WHERE trial_id LIKE 'cycle:%'"),
            'unknown_ast_trials':count("SELECT COUNT(*) FROM learning_trials WHERE structure_id IS NULL"),
            'observations':count('SELECT COUNT(*) FROM learning_observations')}
    result['rejected_ast_cycles']=sum(json.loads(r[0]).get('source')=='rejected_ast' for r in conn.execute('SELECT document_json FROM learning_trials'))
    result['queue_coverage_complete']=result['queue_simulations']==result['imported_queue_trials']
    result['cycle_coverage_complete']=result['candidate_cycles']==result['imported_cycle_trials']
    result['historic_time_recovery']='Import-time only; overwritten old observations cannot be reconstructed'
    return result


def effort(conn, entry_id, cycle_id, seconds, note):
    setup(conn)
    if not entry_id or type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0 or not note:
        raise ValueError('Unique entry, nonnegative actual seconds and note required')
    if not conn.execute('SELECT 1 FROM research_cycles WHERE cycle_id=?',(cycle_id,)).fetchone():raise ValueError('Unknown cycle')
    conn.execute('INSERT INTO learning_effort VALUES(?,?,?,?,?)',(entry_id,cycle_id,seconds,note,util.now_iso()))


def economics(conn, at=None):
    from .research_learning import timestamp
    setup(conn); cutoff=timestamp(at); currencies={}
    for r in conn.execute('SELECT kind,amount,currency FROM payments WHERE occurred_at<=?',(cutoff,)):
        bucket=currencies.setdefault(r['currency'],{'received':0,'other_payment_records':0,'cash_expenses':0})
        bucket['received' if r['kind']=='received' else 'other_payment_records']+=r['amount']
    for r in conn.execute('SELECT kind,amount,unit FROM expenses WHERE occurred_at<=?',(cutoff,)):
        bucket=currencies.setdefault(r['unit'],{'received':0,'other_payment_records':0,'cash_expenses':0})
        # Only explicitly cash expenses; token/credit/time amounts are not currency cash.
        if r['kind']=='cash':bucket['cash_expenses']+=r['amount']
        else:bucket['noncash_or_unclassified_expenses']=bucket.get('noncash_or_unclassified_expenses',0)+r['amount']
    for b in currencies.values():b['recorded_net_cash']=b['received']-b['cash_expenses']
    rows=conn.execute('SELECT COUNT(*),SUM(seconds) FROM learning_effort WHERE created_at<=?',(cutoff,)).fetchone()
    return {'ledger_by_unit':currencies,'human_seconds':rows[1] if rows[0] else None,
            'human_entries':rows[0],'allocation_to_alpha':None,
            'note':'Recorded cash only; absent records are not proof of zero lifetime income; no cross-currency conversion or attribution.'}


def wilson(successes,total):
    if total==0:return [0,1]
    z=1.96;p=successes/total;den=1+z*z/total
    mid=(p+z*z/(2*total))/den;half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return [max(0,mid-half),min(1,mid+half)]


def comparison(conn, experiment_id):
    from .research_learning import as_of, model_cost
    setup(conn)
    row=conn.execute('SELECT document_json FROM learning_experiments WHERE experiment_id=?',(experiment_id,)).fetchone()
    if not row:raise ValueError('Unknown experiment')
    contract=json.loads(row[0]); assignments=list(conn.execute('SELECT * FROM learning_assignments WHERE experiment_id=?',(experiment_id,)))
    trials=as_of(conn); arms={};pending=0;family_sets={}
    for arm in ('baseline','learning'):
        cycles={r['cycle_id'] for r in assignments if r['arm']==arm}
        groups={}; requests=0
        for t in trials:
            if t['cycle_id'] not in cycles or not t.get('task_id'):continue
            outcome=t['outcome'] or {};requests+=bool(outcome.get('request_started'))
            family=t['family_id'] or t['execution_id'];g=groups.setdefault(family,{'usable':False,'pending':False})
            g['usable']|=outcome.get('quality')=='usable';g['pending']|=outcome.get('execution') in ('pending','unknown')
        family_sets[arm]=set(groups)
        successes=sum(g['usable'] for g in groups.values());pending+=sum(g['pending'] for g in groups.values())
        costs=model_cost(conn,cycles)
        arms[arm]={'cycles':len(cycles),'families':len(groups),'usable_families':successes,
                   'interval_95':wilson(successes,len(groups)),'requests':requests,'cost':costs}
    closed=all(conn.execute('SELECT state FROM research_cycles WHERE cycle_id=?',(r['cycle_id'],)).fetchone()[0]=='closed' for r in assignments)
    reasons=[]
    if family_sets['baseline'] & family_sets['learning']:reasons.append('shared_families_between_arms')
    if len(assignments)<contract['max_cycles'] or not closed:reasons.append('allocation_or_execution_incomplete')
    if pending:reasons.append('unresolved_outcomes')
    if any(a['families']<20 for a in arms.values()):reasons.append('fewer_than_20_families_per_arm')
    if any(a['cost']['linked_calls']==0 or a['cost']['unknown_cost_calls'] for a in arms.values()):reasons.append('cost_incomplete')
    a,b=arms['baseline'],arms['learning']
    if a['families'] and b['families'] and b['cost']['known_usd']/b['families']>a['cost']['known_usd']/a['families']:
        reasons.append('learning_cost_per_family_higher')
    superior=not reasons and b['interval_95'][0]>a['interval_95'][1]
    worse=closed and len(assignments)>=contract['max_cycles'] and a['interval_95'][0]>b['interval_95'][1]
    return {'experiment_id':experiment_id,'arms':arms,'decision':'eligible' if superior else 'rollback' if worse else 'inconclusive',
            'reasons':reasons,'minimum_families':20,'auto_promotion':False,
            'interpretation':'Conservative descriptive family intervals; alternating assignment is not a randomized causal estimate. Forward validation is additionally required.'}


def gate_audit(conn, proposed):
    """Counterfactual local temporal screen, never replaces platform or submission gates."""
    class ProposedConfig:
        def get(self,*keys,default=None): return proposed if keys==('research_feedback','segment_rules') else default
    rules=feedback.segment_rules(ProposedConfig());rows=[]
    for row in conn.execute('SELECT alpha_id,report_json FROM research_feedback'):
        doc=json.loads(row['report_json']);years=[y for y in doc.get('temporal',[]) if 'year' in y]
        segments=[y for y in doc.get('temporal',[]) if 'segment' in y]
        required=(('sharpe','fitness','pnl') if years else ())
        if len(years)<rules['min_years'] or any(not feedback.number(y.get(k)) for y in years for k in required):
            shadow=None;reason='Insufficient comparable temporal inputs'
        else:
            negative=sum(y['pnl']<=0 or y['sharpe']<=0 for y in years)
            shadow=negative<=rules['max_negative_years'] and all(
                feedback.number(y.get('sharpe')) and feedback.number(y.get('fitness')) and
                y['sharpe']>=rules['min_sharpe'] and y['fitness']>=rules['min_fitness'] for y in segments)
            if len(segments)<2:shadow=None
            reason='Adaptive historical counterfactual only'
        rows.append({'alpha_id':row['alpha_id'],'current_candidate':doc.get('submission_candidate'),
                     'shadow_temporal_pass':shadow,'platform_blocked':bool(doc.get('platform_blockers')),'reason':reason})
    return {'policy_hash':util.sha256_json(rules),'proposed_rules':rules,'rows':rows,'applied':False,
            'causal_or_forward_evidence':False,'warning':'Temporal shadow pass never overrides required platform, correlation or submission gates.'}
