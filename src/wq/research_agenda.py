"""Versioned research questions; proposals do not allocate tasks or authorize scope."""
import copy
import json
import re
from pathlib import Path
from . import util,research_contracts
from . import research_campaign_v2 as events


def propose(conn,p,doc,cycle_id=None):
    from . import catalog
    required={'id','revision','mechanism','measurement','falsifier','profile','required_assertions','template'}
    if not isinstance(doc,dict) or set(doc)!=required:raise ValueError('Exact research issue contract required')
    if not isinstance(doc['id'],str) or not re.fullmatch('H-[A-Z][A-Z0-9-]{1,48}',doc['id']):raise ValueError('Stable hypothesis ID required')
    if type(doc['revision']) is not int or doc['revision']<1:raise ValueError('Positive issue revision required')
    if doc['template']!='paired_intervention_v1':raise ValueError('Only the bounded paired template is registered')
    c=p.get('campaign') or {}
    if c.get('schema')!=events.SCHEMA or doc['profile'] not in c['execution_profiles']:raise ValueError('A preauthorized execution profile is required')
    for key in ('mechanism','measurement','falsifier'):
        if not isinstance(doc[key],str) or not 8<=len(doc[key])<=1600:raise ValueError('Substantive bounded issue reasoning required')
        fields={f for b in c['execution_profiles'][doc['profile']]['bindings'].values() for f in b['fields']}
        if catalog.mentions_field(doc[key],fields):raise ValueError('Issue text must use abstract roles, not private fields')
    research_contracts.requirements(doc['id'],doc['required_assertions'])
    record={'issue':doc,'issue_hash':util.sha256_json(doc),'state':'proposed','cycle_id':cycle_id,
            'campaign_id':c['id'],'policy_hash':util.sha256_json(p),'execution_authority':0}
    events.append_once(conn,'issue:'+doc['id']+':'+str(doc['revision']),'research_issue_proposed',record,cycle_id)
    return record


def observe(conn,p,cycle_id,artifact):
    proposals=artifact.get('research_issues',[])
    if not isinstance(proposals,list) or len(proposals)>2:
        proposals=[]
        events.append_once(conn,'issue-invalid:'+str(cycle_id),'research_issue_rejected',{'cycle_id':cycle_id,'reason':'At most two structured optional issues are accepted'},cycle_id)
    for i,doc in enumerate(proposals):
        try:propose(conn,p,doc,cycle_id)
        except (ValueError,TypeError,KeyError) as exc:
            events.append_once(conn,'issue-invalid:'+str(cycle_id)+':'+str(i),'research_issue_rejected',{'cycle_id':cycle_id,'reason':str(exc)[:200]},cycle_id)


def register(conn,cfg,approval):
    """Explicit CLI owner action, recoverable if interrupted between policy and DB writes."""
    from . import autopilot,research_campaign
    autopilot.setup(conn)
    required={'issue_id','issue_revision','issue_hash','approved_by','reason','source_policy_hash','valid_until','hypothesis'}
    if not isinstance(approval,dict) or set(approval)!=required:raise ValueError('Exact issue registration approval required')
    if not approval['approved_by'] or len(approval['reason'].strip())<8:raise ValueError('Named owner and concrete registration reason required')
    if util.now()>=util.parse_iso(approval['valid_until']):raise ValueError('Issue registration approval expired')
    proposal=next((d for _,d in events.records(conn,'research_issue_proposed') if d['issue_hash']==approval['issue_hash'] and d['issue']['id']==approval['issue_id'] and d['issue']['revision']==approval['issue_revision']),None)
    if not proposal:raise ValueError('Exact issue proposal not found')
    key=util.sha256_json(approval)
    completed=next((d for _,d in events.records(conn,'research_issue_registered') if d['approval_hash']==key),None)
    if completed:return completed
    intent=next((d for _,d in events.records(conn,'research_issue_registration_intent') if d['approval_hash']==key),None)
    if not intent:
        p=autopilot.policy(cfg)
        if util.sha256_json(p)!=approval['source_policy_hash']:raise ValueError('Policy changed since registration approval')
        c=p['campaign'];issue=proposal['issue']
        if c['account_alias']!=cfg.get('account_alias') or c['id']!=proposal['campaign_id']:raise ValueError('Issue account/campaign mismatch')
        if any(h['id']==issue['id'] for h in c['hypotheses']):raise ValueError('An existing campaign direction cannot be replaced by an issue proposal')
        h=copy.deepcopy(approval['hypothesis'])
        if (h.get('id')!=issue['id'] or h.get('claim')!=issue['mechanism'] or h.get('falsifier')!=issue['falsifier'] or
            h.get('required_assertions')!=issue['required_assertions'] or h.get('request_cap')!=4 or h.get('max_cycles')!=4):
            raise ValueError('Registration must bind the exact issue and existing per-direction limits')
        if not h.get('steps') or any(step.get('profile')!=issue['profile'] for step in h['steps']):
            raise ValueError('Issue steps must use its preauthorized execution profile')
        if h.get('settings')!=c['execution_profiles'][issue['profile']]['settings']:raise ValueError('Issue cannot change its authorized settings')
        h['registration']={'issue_hash':approval['issue_hash'],'approved_by':approval['approved_by'],
                           'additional_total_budget':0,'measurement':issue['measurement']}
        new=copy.deepcopy(p);new['campaign']['hypotheses'].append(h)
        new['campaign'].update(registry='extensible_v1',version=c['version']+1)
        new['campaign']['baseline']=research_campaign.baseline(new)
        autopilot.check_policy(new)
        intent={'approval_hash':key,'old_policy':p,'new_policy':new,'approval':approval}
        events.append_once(conn,'issue-registration-intent:'+key,'research_issue_registration_intent',intent)
        conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        actual=autopilot.policy(cfg)
        if actual not in (intent['old_policy'],intent['new_policy']):raise ValueError('Registration recovery refuses to overwrite unrelated policy changes')
        if conn.execute("SELECT 1 FROM research_cycles WHERE state!='closed'").fetchone() or conn.execute("SELECT 1 FROM tasks WHERE status IN ('claimed','running','unknown')").fetchone():
            raise ValueError('Register a research issue only at an idle reconciled boundary')
        original=intent['old_policy']['campaign']
        if not any(d['id']==original['id'] and d['version']==original['version'] for _,d in events.records(conn,'campaign_v2_frozen')):
            events.append_once(conn,f"freeze:{original['id']}:{events.SCHEMA}:{original['version']}",'campaign_v2_frozen',original)
        migration=events.migrate(conn,intent['new_policy'],approval['reason'])
        result={'issue_id':approval['issue_id'],'approval_hash':key,'campaign_version':intent['new_policy']['campaign']['version'],
                'state':'registered','migration':migration,'execution_authority':'existing_gates_only'}
        events.append_once(conn,'issue-registered:'+key,'research_issue_registered',result)
        if actual!=intent['new_policy']:
            path=cfg.resolve(cfg.get('autopilot','policy_file',default='config/autopilot-policy.json'))
            util.write_json(path,intent['new_policy'])
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def report(conn):
    return {'proposals':[d for _,d in events.records(conn,'research_issue_proposed')],
            'registered':[d for _,d in events.records(conn,'research_issue_registered')],
            'rejected':[d for _,d in events.records(conn,'research_issue_rejected')]}
