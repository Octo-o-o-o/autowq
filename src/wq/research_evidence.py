"""Delegated built-in evidence handling, with honest semantic handoffs."""
import json
from pathlib import Path
from . import util, store, research_campaign_v2 as events
from . import research_framework as framework, research_meta as meta


def receipt_candidates(conn,cfg):
    result=[]
    for gap in framework.latest(conn,'research_gap','gap_id').values():
        receipt=gap.get('receipt')
        if not receipt:continue
        source=framework.latest(conn,'research_evidence_source','gap_id').get(gap['gap_id'])
        inbox=gap.get('receipt_kind')=='delegated_inbox_envelope'
        if inbox:
            if not meta.authority(cfg):continue
        elif not source or util.now()>=util.parse_iso(source['valid_until']):continue
        key='evidence-verify:'+gap['gap_id']+':'+receipt['sha256']
        if conn.execute('SELECT 1 FROM tasks WHERE dedup_key=?',(key,)).fetchone():continue
        result.append({'id':key,'kind':'verify','gap_id':gap['gap_id'],'receipt':receipt,
                       'first_seen':gap['first_seen'],'reason':'Verify material identity, then route semantic gap',
                       'estimated_model_calls':0,'estimated_api_reads':0})
    return result


def reserve_read(conn,cfg,source):
    """Called once per transport attempt (BrainClient has no hidden retry loop)."""
    if not meta.authority(cfg):raise ValueError('Delegated evidence read authority expired')
    key=util.sha256_json({'adapter':source['adapter'],'account':cfg.get('account_alias'),'source':source['source']})
    # Revisions and gap IDs do not grant more attempts at an identical source.
    root=cfg.get('research_dual_loop','root_id');n=int(store.get_flag(conn,'evidence_reads:'+root,'0'))
    used=int(store.get_flag(conn,'evidence_source_reads:'+root+':'+key,'0'))
    if n>=24 or used>=3:raise ValueError('Delegated evidence attempts exhausted')
    store.set_flag(conn,'evidence_reads:'+root,str(n+1));store.set_flag(conn,'evidence_source_reads:'+root+':'+key,str(used+1))
    events.append_once(conn,'evidence-attempt:'+root+':'+str(n+1),'research_aux_attempt_started',{'root':root,'attempt':n+1,'source_identity':key})
    conn.commit()


def handoff(conn,cfg,gap,reason):
    root=Path(cfg.private_dir)/'research-inbox';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    doc={'gap_id':gap['gap_id'],'hypothesis':gap['hypothesis'],'predicate':gap['predicate'],
         'scope':gap['scope'],'fields':gap['fields'],'reason':reason,'source_material':gap.get('receipt'),
         'required_material':'Authoritative product/field definition with scope, timing, units and predicate-specific evidence; metadata existence alone is insufficient.',
         'valid_until':cfg.get('research_dual_loop','valid_until'),'semantic_status':'insufficient',
         'incoming_path':str(root/(gap['gap_id']+'.material.json')),'outbound_sent':False}
    digest=util.sha256_json(doc);target=root/(gap['gap_id']+'.request.json')
    if not target.exists() or util.read_json(target)!=doc:util.write_json(str(target),doc)
    events.append_once(conn,'handoff:'+digest,'research_external_request',doc)
    if not store.get_flag(conn,'handoff_batch_pending'):
        store.set_flag(conn,'handoff_batch_pending',json.dumps({'id':cfg.get('research_dual_loop','root_id'),'path':str(root)}))
    return doc


def prepare(conn,cfg,p):
    if not meta.authority(cfg):return
    from . import catalog,research_strategy
    query=catalog.query_from_settings(p['settings'])
    current=framework.latest(conn,'research_gap','gap_id')
    for card in research_strategy.measurement_opportunities(p['bindings'])['opportunity_cards']:
        role=card['role'];binding=p['bindings'][role]
        subject={'campaign_id':'ordinary:'+str((p.get('campaign') or {}).get('id','current')),'hypothesis':'ordinary:'+role,'step':'metadata',
                 'predicate':'measurement_metadata','scope':query,'binding_hash':util.sha256_json(binding)}
        gid=util.sha256_json(subject)
        if gid not in current:
            framework.record_gap(conn,{**subject,'gap_id':gid,'state':'owner_required','input_hash':util.sha256_json(binding),
                'source_hash':None,'source_identity':None,'query':query,'fields':sorted(binding['fields']),
                'reason':[{'code':'UNIT_FREQUENCY_AND_MISSINGNESS_REQUIRE_EVIDENCE'}],'owner_required':True,
                'next_trigger':'metadata_then_semantic_document'})
    sources=framework.latest(conn,'research_evidence_source','gap_id')
    for gap in framework.latest(conn,'research_gap','gap_id').values():
        if gap['state'] in ('validated','superseded','unsupported'):continue
        if gap['gap_id'] in sources:
            if gap['state'] not in ('collected','waiting_handoff'):handoff(conn,cfg,gap,'awaiting_bounded_collection_then_semantic_verification')
            continue
        if not gap['fields']:
            handoff(conn,cfg,gap,'binding_definition_required');continue
        profile=(p.get('campaign') or {}).get('execution_profiles',{}).get('base',{})
        from . import catalog
        # Scope changes do not inherit network or evidence registration permission.
        allowed_fields={f for b in profile.get('bindings',p['bindings']).values() for f in b['fields']}
        if not set(gap['fields']).issubset(allowed_fields):
            handoff(conn,cfg,gap,'field_not_in_delegation');continue
        if gap['query']!=catalog.query_from_settings(profile.get('settings',p['settings'])):
            handoff(conn,cfg,gap,'scope_not_in_delegation');continue
        field=gap['fields'][0];source=None
        for proof in profile.get('evidence_files',[]):
            try:
                doc=util.read_json(proof['path'])
                if not isinstance(doc,dict):continue
                if doc.get('query')==gap['query'] and doc.get('field',{}).get('id')==field and util.sha256_json(doc)==proof['sha256']:
                    source={'path':str(Path(proof['path']).resolve()),'sha256':framework.file_identity(proof['path']),'source_ref':doc['source']};break
            except (OSError,ValueError,KeyError,TypeError):continue
        registration={'gap_id':gap['gap_id'],'revision':1,'adapter':'local_material_v1' if source else 'brain_field_metadata_v1',
            'source':source or {'field_id':field,'query':gap['query']},'approved_by':'delegation:'+cfg.get('research_dual_loop','root_id'),
            'valid_until':cfg.get('research_dual_loop','valid_until'),'max_attempts':3}
        framework.register_source(conn,cfg,registration)
        if gap['campaign_id'].startswith('ordinary:'):
            framework.record_gap(conn,{**gap,'state':'fetchable','source_hash':util.sha256_json(registration),
                'source_identity':framework.file_identity(source['path']) if source else util.sha256_json(registration)})
        handoff(conn,cfg,gap,'awaiting_bounded_collection_then_semantic_verification')
    # Generated issues wake deterministic qualification work, never refill discovery.
    for _,proposal in events.records(conn,'research_issue_proposed'):
        issue=proposal['issue'];key='issue-qualification:'+proposal['issue_hash']
        if conn.execute('SELECT 1 FROM campaign_record_keys WHERE record_key=?',(key,)).fetchone():continue
        events.append_once(conn,key,'research_issue_qualification',{'issue_hash':proposal['issue_hash'],'issue_id':issue['id'],
            'state':'waiting_measurement_recipe','execution_authority':0,'required_assertions':issue['required_assertions'],
            'next_trigger':'exact_recipe_and_verified_contract','candidate_not_selected_by_name':True})


def verify(conn,cfg,task,payload):
    gap=framework.latest(conn,'research_gap','gap_id').get(payload['gap_id']);receipt=payload['receipt']
    if not gap or gap.get('receipt')!=receipt:return 'blocked',{},'Evidence superseded'
    raw=Path(receipt['path']).read_bytes()
    if framework.file_identity(receipt['path'])!=receipt['sha256']:return 'blocked',{},'Receipt identity changed'
    source=framework.latest(conn,'research_evidence_source','gap_id').get(gap['gap_id'])
    inbox=gap.get('receipt_kind')=='delegated_inbox_envelope'
    if inbox and not meta.authority(cfg):return 'blocked',{},'Inbox delegation expired'
    if not inbox and (not source or util.now()>=util.parse_iso(source['valid_until'])):return 'blocked',{},'Evidence authority expired'
    try:
        doc=json.loads(raw);field=doc.get('field',{})
        identity=(doc.get('gap_id')==gap['gap_id'] and doc.get('scope')==gap['scope'] and isinstance(doc.get('source_ref'),str) and isinstance(doc.get('text'),str)) if inbox else doc.get('query')==gap['query'] and field.get('id') in gap['fields'] and bool(field.get('type'))
    except (ValueError,TypeError,AttributeError):identity=False
    result={'gap_id':gap['gap_id'],'receipt':receipt,'material_verified':bool(identity),'semantic_status':'insufficient',
            'scope':gap['scope'],'verification_kind':'identity_only','l2_authority':0}
    request=handoff(conn,cfg,gap,'metadata_does_not_entail_'+gap['predicate'] if identity else 'specific_semantic_material_required')
    result['handoff_path']=request['incoming_path']
    framework.record_gap(conn,{**gap,'state':'waiting_handoff','verification':result,'next_trigger':'authoritative_material_or_contract_change'})
    events.append_once(conn,'evidence-verification:'+task['task_id'],'research_assertion_evaluated',result)
    return 'succeeded',result,None


def incoming(conn,cfg):
    """Fixed inbox: changed envelopes are retained; no automatic policy mutation."""
    root=Path(cfg.private_dir)/'research-inbox'
    for gap in framework.latest(conn,'research_gap','gap_id').values():
        path=root/(gap['gap_id']+'.material.json')
        if not path.is_file() or path.is_symlink() or path.stat().st_size>1024*1024:continue
        digest=framework.file_identity(path);key='incoming:'+gap['gap_id']+':'+str(digest)
        if any(d.get('input_hash')==digest and d.get('gap_id')==gap['gap_id'] for _,d in events.records(conn,'research_external_material_received')):continue
        try:
            doc=util.read_json(path)
            valid=isinstance(doc,dict) and doc.get('gap_id')==gap['gap_id'] and doc.get('scope')==gap['scope'] and isinstance(doc.get('source_ref'),str) and isinstance(doc.get('text'),str) and 8<=len(doc['text'])<=50000
        except (OSError,ValueError,TypeError):valid=False
        events.append_once(conn,key,'research_external_material_received',{'gap_id':gap['gap_id'],'input_hash':digest,
            'envelope_valid':valid,'semantic_status':'insufficient','next_trigger':'exact_contract_verification','policy_changed':False})
        if valid:
            from . import research_observation
            receipt=research_observation.retain_bytes(root/'snapshots',path.read_bytes())
            framework.record_gap(conn,{**gap,'receipt':receipt,'receipt_kind':'delegated_inbox_envelope','state':'collected','verification':None,'next_trigger':'semantic_contract_verification'})


def daily_capacity(conn):
    return conn.execute("SELECT COUNT(*) FROM tasks WHERE kind IN ('research_evidence','research_evidence_verify') AND created_at>=?",(util.now().date().isoformat(),)).fetchone()[0]<4
