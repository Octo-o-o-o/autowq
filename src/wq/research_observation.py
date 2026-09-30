"""Immutable observation inputs and explicit production collector capabilities."""
import hashlib
import json
from pathlib import Path

PARSER_VERSION = 'wq.cumulative-pnl/v1'


CONVENTIONS = ('frequency','capital_basis','value_unit','cost_basis','timezone','data_revision')
ADAPTER = 'brain_cumulative_pnl_v1'


def collector_contract(spec, profile):
    """Only a hashed, owner-registered profile contract can select a built-in parser."""
    from . import util
    cid=spec.get('collector_id')
    ref=(profile.get('observation_collectors') or {}).get(cid)
    if not ref:raise ValueError('OBSERVATION_CAPABILITY_UNSUPPORTED')
    raw=Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('COLLECTOR_CONTRACT_CHANGED')
    doc=json.loads(raw)
    if doc.get('schema')!='wq.observation-contract/v1' or doc.get('adapter')!=ADAPTER or doc.get('id')!=cid:
        raise ValueError('COLLECTOR_ADAPTER_UNSUPPORTED')
    if doc.get('verification_method')!='owner_attestation' or not doc.get('verified_by'):
        raise ValueError('COLLECTOR_OWNER_VERIFICATION_REQUIRED')
    if not util.parse_iso(doc['verified_at'])<=util.now()<util.parse_iso(doc['valid_until']):
        raise ValueError('COLLECTOR_CONTRACT_EXPIRED')
    scope={k:profile['settings'][k] for k in ('region','universe','delay')}
    if doc.get('scope')!=scope:raise ValueError('COLLECTOR_SCOPE_MISMATCH')
    materials={}
    for material in doc.get('materials',[]):
        data=Path(material['path']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=material['sha256'] or not material.get('source') or not material.get('product_version'):
            raise ValueError('COLLECTOR_SOURCE_UNVERIFIED')
        if material.get('source_type') not in ('provider_documentation','platform_documentation','licensed_panel_contract'):
            raise ValueError('COLLECTOR_SOURCE_UNSUPPORTED')
        materials[material['id']]=data.decode('utf-8')
    mappings=doc.get('mappings')
    if not isinstance(mappings,dict) or set(mappings)!=set(CONVENTIONS):raise ValueError('COLLECTOR_MAPPING_INCOMPLETE')
    for key,mapping in mappings.items():
        if mapping.get('source') not in ('alpha','pnl','contract'):raise ValueError('COLLECTOR_MAPPING_SOURCE_INVALID')
        if mapping['source']=='contract':
            if key=='data_revision':raise ValueError('DATA_REVISION_REQUIRES_RECEIPT_FIELD')
            if 'value' not in mapping:raise ValueError('COLLECTOR_CONSTANT_MISSING')
        elif not isinstance(mapping.get('path'),list) or not mapping['path'] or any(not isinstance(x,str) or not x for x in mapping['path']):
            raise ValueError('COLLECTOR_RECEIPT_PATH_REQUIRED')
        refs=mapping.get('evidence_refs')
        if not isinstance(refs,list) or not refs:raise ValueError('COLLECTOR_MAPPING_EVIDENCE_REQUIRED')
        for proof in refs:
            if not isinstance(proof.get('quote'),str) or not proof['quote'].strip() or proof['quote'] not in materials.get(proof.get('material_id'),''):
                raise ValueError('COLLECTOR_MAPPING_QUOTE_MISSING')
    return doc,raw


def capability(spec, profile=None):
    try:
        doc,raw=collector_contract(spec,profile or {})
        return {'ready':True,'reason_codes':[],'adapter':ADAPTER,'parser_version':PARSER_VERSION,
                'contract_sha256':hashlib.sha256(raw).hexdigest(),'valid_until':doc['valid_until']}
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {'ready':False,'reason_codes':[str(exc)],'missing':list(CONVENTIONS),
                'parser_version':PARSER_VERSION}


def mapped_conventions(contract, alpha, pnl):
    result={}
    for key,mapping in contract['mappings'].items():
        value=mapping.get('value')
        if mapping['source']!='contract':
            value={'alpha':alpha,'pnl':pnl}[mapping['source']]
            for part in mapping['path']:
                if not isinstance(value,dict) or part not in value:raise ValueError('COLLECTOR_RECEIPT_FIELD_MISSING:'+key)
                value=value[part]
        if key=='capital_basis':
            from .feedback import number
            if not number(value) or value<=0:raise ValueError('COLLECTOR_CAPITAL_INVALID')
        elif not isinstance(value,str) or not value.strip():raise ValueError('COLLECTOR_VALUE_INVALID:'+key)
        result['revision_evidence' if key=='data_revision' else key]=value
    return result


def retain_bytes(root, data):
    digest = hashlib.sha256(data).hexdigest()
    path = Path(root) / digest
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(0o600)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError('Immutable snapshot content mismatch')
    return {'path': str(path), 'sha256': digest, 'size': len(data)}


def read_snapshot(snapshot):
    data = Path(snapshot['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != snapshot['sha256']:
        raise ValueError('Immutable snapshot integrity mismatch')
    return json.loads(data)


def replay(observation):
    """Reconstruct parser values from retained bytes; never reopen mutable sources."""
    from . import feedback, util
    if not observation.get('raw_snapshot'):
        return dict(observation)
    if observation.get('parser_version') != PARSER_VERSION:
        raise ValueError('Unsupported retained parser version')
    pnl=read_snapshot(observation['raw_snapshot'])
    identity=read_snapshot(observation['identity_snapshot'])
    alpha=read_snapshot(observation['execution_snapshot'])
    if (identity['task_id']!=observation['task_id'] or alpha.get('id')!=observation['alpha_id'] or
        identity['outcome'].get('observation_id')!=observation['observation_id'] or
        util.sha256_json(pnl)!=observation['pnl_hash']):
        raise ValueError('Retained observation identity mismatch')
    values=feedback.daily_pnl(pnl);intervals=sorted(values)
    conventions=mapped_conventions(read_snapshot(observation['collector_snapshot']),alpha,pnl) if observation.get('collector_snapshot') else {}
    return {**observation,**conventions,'intervals':[list(x) for x in intervals],'values':[values[x] for x in intervals]}
