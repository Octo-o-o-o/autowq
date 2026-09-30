"""Evidence-bound campaign semantics; hashes prove integrity, never historical truth."""
import datetime as dt
import hashlib
from pathlib import Path
from . import util

SCHEMA = 'wq.data-contract/v2'
COMMON = ('historical_availability', 'missing_semantics', 'measurement_isolation', 'platform_mapping')
REQUIRED = {
    'H-N1': ('novelty_direction', 'novelty_window', 'daily_aggregation', 'impulse_alignment'),
    'H-N2': ('sentiment_direction', 'revision_rules', 'freshness_window', 'daily_aggregation'),
    'H-N3': ('source_ordering', 'entity_alignment', 'daily_aggregation'),
    'H-R1': ('target_period_identity', 'period_roll', 'revision_history'),
    'H-R2': ('target_period_identity', 'units_currency', 'sales_earnings_alignment'),
    'H-R3': ('target_period_identity', 'forecast_cashflow', 'units_currency'),
    'H-O1': ('tenor_convention', 'option_side', 'moneyness', 'interpolation', 'annualization'),
    'H-O2': ('open_interest_stock', 'contract_roll', 'persistence'),
    'H-O3': ('volume_flow', 'contract_distribution', 'denominator_semantics'),
    'H-V1': ('event_identity', 'reducer_semantics', 'empty_vector'),
    'H-V2': ('eventwise_alignment', 'eventwise_weighting'),
    'H-D0': ('intraday_availability', 'completed_bar', 'delay_comparison'),
    'H-U1': ('historical_membership', 'coverage', 'scope_comparison'),
    'H-U2': ('historical_membership', 'joint_coverage', 'scope_comparison'),
}
ISOLATED_QUANTITIES = {
    'remove_binary_condition': {'weight_intensity_effect'},
    'replace_operator': {'temporal_filter_effect','accumulation_vs_average'},
    'replace_vector_reducer': {'event_count_scaling'},
    'registered_followup': {'same_intervention_under_transform','same_intervention_across_scope'},
}
SEMANTIC_SOURCE_TYPES = ('provider_documentation', 'platform_documentation', 'licensed_panel_contract')


def timestamp(value):
    if not isinstance(value, str): raise ValueError('Evidence timestamp required')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('Evidence timezone required')
    return parsed


def requirements(hypothesis_id, registered=None):
    if hypothesis_id in REQUIRED:return COMMON + REQUIRED[hypothesis_id]
    import re
    if (not isinstance(registered,list) or not set(COMMON)<set(registered) or
        len(registered)!=len(set(registered)) or len(registered)>24 or
        any(not isinstance(x,str) or not re.fullmatch('[a-z][a-z0-9_]{2,63}',x) for x in registered)):
        raise ValueError('A new hypothesis needs registered semantic predicates including the common contract')
    return tuple(registered)


def binding_hash(policy, roles):
    return util.sha256_json({r: policy['bindings'][r] for r in sorted(roles)})


def scope(policy, account_alias):
    return {'account_alias': account_alias, 'instrument_type': 'EQUITY', 'scenario': 'REGULAR',
            **{k: policy['settings'][k] for k in ('region', 'universe', 'delay')}}


def draft(hypothesis_id, registered=None):
    return {'schema': SCHEMA, 'contract_id': hypothesis_id, 'revision': 1, 'claim_version': 1,
            'scope': None, 'binding_hash': None, 'materials': [], 'assertions': [],
            'required_assertions': list(requirements(hypothesis_id,registered)), 'status': 'blocked'}


def inspect(contract, hypothesis_id, policy, account_alias, roles, now=None, recipe=None, registered=None):
    """Pure read-only check. Owner attestation is bounded evidence, not a truth oracle."""
    now = now or util.now()
    reasons = []
    def reject(code, subject=None):
        item = {'code': code}
        if subject is not None: item['subject'] = subject
        if item not in reasons: reasons.append(item)
    required = requirements(hypothesis_id,registered)
    if hypothesis_id == 'H-V2': reject('UNSUPPORTED_EVENTWISE_WEIGHTING')
    if not isinstance(contract, dict) or contract.get('schema') != SCHEMA:
        reject('DATA_CONTRACT_MISSING')
        return {'state': 'unsupported' if hypothesis_id == 'H-V2' else 'missing', 'ready': False,
                'reasons': reasons, 'required_assertions': list(required)}
    if type(contract.get('revision')) is not int or contract['revision'] < 1 or type(contract.get('claim_version')) is not int or contract['claim_version'] < 1:
        reject('CONTRACT_REVISION_INVALID')
    if contract.get('scope') != scope(policy, account_alias): reject('SCOPE_MISMATCH')
    try:
        expected_binding = binding_hash(policy, roles)
    except (KeyError, TypeError):
        expected_binding = None
    if not roles or not expected_binding or contract.get('binding_hash') != expected_binding:
        reject('BINDING_MISMATCH')
    if contract.get('required_assertions') != list(required): reject('REQUIRED_ASSERTIONS_CHANGED')
    materials = contract.get('materials')
    if not isinstance(materials, list): materials = []; reject('MATERIALS_INVALID')
    by_id = {}
    for material in materials:
        if not isinstance(material, dict) or not isinstance(material.get('material_id'), str):
            reject('MATERIAL_INVALID'); continue
        mid = material['material_id']
        if mid in by_id: reject('DUPLICATE_MATERIAL', mid); continue
        valid = True
        try:
            if material.get('access_status') != 'content_verified' or material.get('source_type') not in SEMANTIC_SOURCE_TYPES:
                raise ValueError('SEMANTIC_EVIDENCE_UNSUPPORTED')
            if material.get('content_type') not in ('text/plain', 'text/markdown', 'application/json'):
                raise ValueError('MATERIAL_CONTENT_UNSUPPORTED')
            if not material.get('source') or not material.get('product_version'):
                raise ValueError('MATERIAL_PROVENANCE_MISSING')
            if timestamp(material.get('fetched_at')) > now: raise ValueError('MATERIAL_FUTURE_TIMESTAMP')
            data = Path(material['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != material.get('sha256'): raise ValueError('MATERIAL_HASH_CHANGED')
            text = data.decode('utf-8')
            if not text.strip(): raise ValueError('MATERIAL_EMPTY')
        except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
            reject(str(exc) if isinstance(exc, ValueError) else 'MATERIAL_UNAVAILABLE', mid)
            valid = False; text = ''
        by_id[mid] = (material, text, valid)
    assertions = contract.get('assertions')
    if not isinstance(assertions, list): assertions = []; reject('ASSERTIONS_INVALID')
    indexed = {}
    for assertion in assertions:
        if not isinstance(assertion, dict) or not isinstance(assertion.get('predicate'), str):
            reject('ASSERTION_INVALID'); continue
        pred = assertion['predicate']
        if pred in indexed: reject('DUPLICATE_ASSERTION', pred)
        indexed[pred] = assertion
    for pred in required:
        assertion = indexed.get(pred)
        if not assertion or assertion.get('status') != 'verified': reject('ASSERTION_UNVERIFIED', pred); continue
        if assertion.get('subject_ref') != expected_binding: reject('ASSERTION_SUBJECT_MISMATCH', pred)
        value = assertion.get('value')
        if value is None or value == '' or value == {} or value == []: reject('ASSERTION_VALUE_MISSING', pred)
        if pred == 'measurement_isolation':
            keys = ('measured_difference', 'common_sample_rule', 'zero_semantics',
                    'missing_semantics', 'warmup_semantics', 'verification_method')
            if (not isinstance(value, dict) or
                any(not isinstance(value.get(k), str) or len(value[k].strip()) < 12 for k in keys) or
                not isinstance(value.get('allowed_changes'), list) or not value['allowed_changes'] or
                value.get('common_sample_basis') != 'contract_identical_effective_set' or
                not isinstance(value.get('recipe_hash'),str) or len(value['recipe_hash'])!=64 or
                value.get('measured_quantity') not in ISOLATED_QUANTITIES.get(value.get('intervention_kind'),set()) or
                not isinstance(value.get('operator_semantics'),dict) or
                any(not isinstance(value['operator_semantics'].get(k),str) or len(value['operator_semantics'][k])<12 for k in ('before','after')) or
                (recipe is not None and (value.get('recipe_hash') != util.sha256_json(recipe) or value.get('intervention_kind')!=recipe.get('kind')))):
                reject('MEASUREMENT_NOT_ISOLATED', pred)
        if assertion.get('verification_method') != 'owner_attestation' or not assertion.get('verified_by'):
            reject('SEMANTIC_EVIDENCE_UNSUPPORTED', pred)
        try:
            verified, until = timestamp(assertion.get('verified_at')), timestamp(assertion.get('valid_until'))
            if not verified <= now < until: reject('ASSERTION_EXPIRED_OR_FUTURE', pred)
        except (ValueError, TypeError): reject('ASSERTION_TIME_INVALID', pred)
        refs = assertion.get('evidence_refs')
        if not isinstance(refs, list) or not refs: reject('ASSERTION_EVIDENCE_MISSING', pred); continue
        for ref in refs:
            if not isinstance(ref, dict): reject('ASSERTION_REFERENCE_INVALID', pred); continue
            material, text, valid = by_id.get(ref.get('material_id'), ({}, '', False))
            quote = ref.get('quote')
            if not valid or not isinstance(quote, str) or len(quote.strip()) < 12 or quote not in text:
                reject('ASSERTION_QUOTE_UNSUPPORTED', pred)
            if not ref.get('locator'): reject('ASSERTION_LOCATOR_MISSING', pred)
    if contract.get('status') != 'verified': reject('CONTRACT_NOT_VERIFIED')
    state = 'verified' if not reasons else 'missing'
    if any(r['code'] == 'ASSERTION_EXPIRED_OR_FUTURE' for r in reasons): state = 'expired'
    if hypothesis_id == 'H-V2': state = 'unsupported'
    return {'state': state, 'ready': not reasons, 'reasons': reasons, 'required_assertions': list(required),
            'verification_limit': 'Integrity, scope and recorded owner attestation checked; historical PIT and source truth are not independently proven.'}
