"""Paired findings are separate from ordinary edit rules and never grant authority."""
import json
from . import util


def publish(conn,pair,evaluation):
    from . import research_campaign_v2 as events
    cycle=conn.execute('SELECT policy_json FROM research_cycles WHERE cycle_id=?',(pair['cycle_id'],)).fetchone()
    profile=json.loads(cycle[0])
    assessment=evaluation['assessment']
    if assessment=='awaiting_second_scope':kind='awaiting_comparison'
    elif evaluation['eligibility']!='verified':kind='technical_failure' if assessment=='technical_failure' else 'unknown'
    elif assessment in ('continue','eligible_for_expansion'):kind='supported'
    elif assessment=='falsified':kind='counterexample'
    else:kind='unknown'
    deadlines=[a['valid_until'] for a in pair['data_contract'].get('assertions',[]) if a.get('valid_until')]
    doc={'pair_id':pair['pair_id'],'hypothesis':pair['hypothesis'],'campaign_id':pair['campaign_id'],
         'scope':{k:profile['settings'][k] for k in ('region','universe','delay')},
         'binding_hash':pair['data_contract']['binding_hash'],'kind':kind,
         'assessment':assessment,'observation_hash':evaluation['observation_hash'],
         'spec_hash':evaluation['spec_hash'],'observation_keys':evaluation['observation_keys'],
         'method':pair['evaluation']['metric'],'decision_unit':pair['evaluation']['decision_unit'],
         'parent_ref':pair['parent_ref'],'valid_until':min(deadlines,key=util.parse_iso) if deadlines else pair['frozen_at'],
         'reason_codes':evaluation.get('reason_codes',[]),'quality_penalty':False,
         'execution_authority':0,'independent_sources':1}
    events.append_once(conn,'knowledge:'+pair['pair_id']+':'+evaluation['observation_hash']+':'+evaluation['spec_hash'],
                       'research_knowledge',doc,pair['cycle_id'])
    return doc


def latest(conn):
    from .research_campaign import events
    found={}
    for _,doc in events(conn,'research_knowledge'):found[doc['pair_id']]=doc
    return list(found.values())


def context(conn, bindings, settings, limit=8):
    """Only categorical, compatible findings are exposed, not raw PnL or private fields."""
    from . import research_contracts, research_campaign, research_dsl
    scope={k:settings.get(k) for k in ('region','universe','delay')}
    values=[]
    for doc in latest(conn):
        if doc['scope']!=scope or util.now()>=util.parse_iso(doc['valid_until']):continue
        pair=next((d for _,d in research_campaign.events(conn,'campaign_pair_frozen') if d['pair_id']==doc['pair_id']),None)
        if not pair:continue
        roles=sorted({r for candidate in pair['candidates'].values() for r in research_dsl.roles_used(candidate['ast'])})
        try:actual=research_contracts.binding_hash({'bindings':bindings},roles)
        except KeyError:continue
        if actual!=doc['binding_hash']:continue
        values.append(doc)
    values.sort(key=lambda d:(d['kind']!='counterexample',d['kind'] not in ('unknown','technical_failure'),d['pair_id']))
    return [{'pair_id':d['pair_id'],'hypothesis':d['hypothesis'],'kind':d['kind'],
             'scope':d['scope'],'decision_unit':d['decision_unit'],'reason_codes':d['reason_codes'],
             'inference':'registered_historical_prediction_only','new_execution_authority':0} for d in values[:limit]]


def resolve_proposal(conn, pair_id, status, reason, owner, authorization=None):
    from . import research_campaign_v2 as events
    if status not in ('accepted','rejected','deferred') or not owner or not isinstance(reason,str) or len(reason.strip())<8:
        raise ValueError('Owner proposal decision and concrete reason required')
    proposal=next((d for _,d in events.records(conn,'campaign_proposal_created') if d['pair_id']==pair_id),None)
    if not proposal:raise ValueError('Unknown expansion proposal')
    if status=='accepted' and (not isinstance(authorization,dict) or not authorization.get('reference') or authorization.get('additional_requests')!=0):
        raise ValueError('Acceptance records planning only; execution expansion needs its separate bounded authorization')
    history=[d for _,d in events.records(conn,'campaign_proposal_resolved') if d['pair_id']==pair_id]
    if history and history[-1]['status'] in ('accepted','rejected'):
        if (history[-1]['status'],history[-1]['reason'],history[-1]['owner'])==(status,reason,owner):return history[-1]
        raise ValueError('A final proposal resolution cannot be overwritten')
    doc={'pair_id':pair_id,'revision':len(history)+1,'status':status,'reason':reason,'owner':owner,
         'authorization':authorization,'execution_authority':0,'basis':proposal['basis']}
    events.append_once(conn,'proposal-resolution:'+pair_id+':'+str(doc['revision']),'campaign_proposal_resolved',doc)
    return doc
