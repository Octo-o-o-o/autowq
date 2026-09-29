"""Bounded experimental advice from observed edits; never grants execution rights."""
import copy
import json
from collections import Counter

from . import research_dsl as dsl, util


def mechanism(ast):
    """Keep ordered topology and group identity, ignoring window/rank/sign variants."""
    if ast['op'] in ('rank', 'neg'):
        return mechanism(ast['arg'])
    return {k: mechanism(v) if isinstance(v, dict) else v
            for k, v in ast.items() if k != 'window'}


def compatible(trial, bindings, settings):
    from .research_learning import identities
    doc = trial['document']
    if not doc.get('ast') or doc.get('settings') != settings:
        return False
    try:
        return identities(doc['ast'], bindings, settings, 'combination')['scope_id'] == trial['scope_id']
    except (ValueError, KeyError, TypeError):
        return False


def retrieval(conn, bindings, settings, candidate=None, limit=12):
    from .research_learning import as_of, ast_diff
    history = [t for t in as_of(conn) if compatible(t, bindings, settings)]
    query_roles = set(dsl.roles_used(candidate)) if candidate else set()
    by_id = {t['trial_id']: t for t in history}
    def score(t):
        doc = t['document']; ast = doc['ast']
        exact = bool(candidate and ast == candidate)
        same_shape = bool(candidate and mechanism(ast) == mechanism(candidate))
        edit_distance = len(ast_diff(candidate, ast)) if candidate else 0
        return (exact, same_shape, len(query_roles & set(doc.get('roles', []))),
                -edit_distance, bool(doc.get('parent_id')), t['available_at'])
    ranked = sorted(history, key=score, reverse=True)
    # Explicit counterexample slot, then parent-child context; one result cannot masquerade as several.
    negative = [t for t in ranked if (t['outcome'] or {}).get('quality') == 'weak'][:max(1, limit//3)]
    selected = []; seen = set()
    for t in negative + ranked:
        for item in (t, by_id.get(t['document'].get('parent_id'))):
            if item and item['trial_id'] not in seen and len(selected) < limit:
                selected.append(item); seen.add(item['trial_id'])
    return selected


def actual_parent(history, ast, bindings, settings):
    from .research_learning import ast_diff
    possible = []
    for t in history:
        if not t.get('task_id') or not compatible(t, bindings, settings):
            continue
        edit = ast_diff({'ast': t['document']['ast'], 'settings': settings},
                        {'ast': ast, 'settings': settings})
        if len(edit) == 1:
            possible.append((t, edit))
    return max(possible, key=lambda x: x[0]['available_at']) if possible else (None, [])


def finite_edits(ast, bindings, maximum=6):
    """One leaf/operator/window change, validated by the compiler, never dispatched here."""
    if type(maximum) is not int or not 1 <= maximum <= 12:
        raise ValueError('Mutation cap must be 1..12')
    alternatives = []
    def visit(node, path):
        if node.get('window') in dsl.WINDOWS:
            index = dsl.WINDOWS.index(node['window'])
            for offset in (-1, 1):
                if 0 <= index+offset < len(dsl.WINDOWS):
                    alternatives.append((path+['window'], dsl.WINDOWS[index+offset], 'holding_period_sensitivity'))
        if node['op'] in ('mean', 'decay'):
            alternatives.append((path+['op'], 'decay' if node['op']=='mean' else 'mean', 'weighting_sensitivity'))
        for key in ('arg', 'left', 'right'):
            if isinstance(node.get(key), dict): visit(node[key], path+[key])
    visit(ast, [])
    result = []; seen = {util.sha256_json(ast)}
    for path, value, purpose in alternatives:
        changed = copy.deepcopy(ast); target = changed
        for key in path[:-1]: target = target[key]
        target[path[-1]] = value
        try: dsl.compile_ast(changed, bindings)
        except ValueError: continue
        digest = util.sha256_json(changed)
        if digest not in seen:
            result.append({'ast': changed, 'purpose': purpose, 'changed_path': path,
                           'prediction_required': True, 'dispatch': 'existing_review_and_family_budget_only'})
            seen.add(digest)
        if len(result) >= maximum: break
    return result


def templates(bindings, maximum=6):
    """Small observable-role representatives, not a Cartesian parameter grid."""
    result = []; concepts = set()
    for name, spec in sorted(bindings.items()):
        if spec.get('group_field'): continue
        metadata = spec.get('measurement') or {}
        concept = metadata.get('concept') or spec.get('cluster') or name
        if concept in concepts: continue
        ast = {'op': 'time_rank', 'arg': {'op': 'field', 'name': name}, 'window': 120}
        try: dsl.compile_ast(ast, bindings)
        except ValueError: continue
        concepts.add(concept)
        result.append({'ast': ast, 'purpose': 'measure_relative_position_not_event',
                       'frequency_verified': bool(metadata.get('update_frequency'))})
        if len(result) >= maximum: break
    return result


def rule_advice(conn, ast, bindings, settings, history=None):
    from .research_learning import as_of, rules
    history = history if history is not None else as_of(conn)
    parent, edit = actual_parent(history, ast, bindings, settings)
    result = {'parent_id': parent['trial_id'] if parent else None, 'edit': edit,
              'score': 0, 'matched_rules': [], 'evidence': 'observed_association_not_causality'}
    if parent is None: return result
    for rule in rules(conn):
        if rule['expired'] or rule['scope_id'] != parent['scope_id'] or rule['edit'] != edit: continue
        result['matched_rules'].append(rule['rule_id'])
        # Use independent families, not the number of nearly identical child trials.
        by_id = {t['trial_id']: t for t in history}
        support = {by_id[x['parent_id']]['family_id'] for x in rule['support'] if x['parent_id'] in by_id}
        contrary = {by_id[x['parent_id']]['family_id'] for x in rule['contradictions'] if x['parent_id'] in by_id}
        support.discard(None); contrary.discard(None)
        result['support_families'] = len(support-contrary)
        result['contradiction_families'] = len(contrary)
        if len(support | contrary) >= 3:
            result['score'] = (len(support-contrary)-len(contrary))/len(support | contrary)
    return result


def family_decision(conn, ast, family, cycle_id, policy, enabled=False):
    """Same legacy budget bucket; permit bounded topology diversity, never window/sign spam."""
    records = conn.execute('SELECT cycle_id,candidate_json FROM research_cycles WHERE family_hash=? AND cycle_id!=?',
                           (family, cycle_id)).fetchall()
    shapes = set(); exact = False
    for row in records:
        try:
            previous = json.loads(row['candidate_json'])['ast']
            shapes.add(util.sha256_json(mechanism(previous)))
            exact |= mechanism(previous) == mechanism(ast)
        except (ValueError, KeyError, TypeError):
            return {'blocked': True, 'reason': 'historical_structure_unavailable'}
    if family in policy.get('known_family_hashes', []):
        return {'blocked': True, 'reason': 'external_family_without_auditable_structure'}
    if not records: return {'blocked': False, 'reason': 'new_family'}
    if exact: return {'blocked': True, 'reason': 'same_mechanism_window_rank_or_sign_variant'}
    return {'blocked': not enabled or len(shapes) >= 2,
            'reason': 'bounded_structure_collision' if enabled and len(shapes)<2 else 'structure_collision_shadow',
            'legacy_family': family, 'existing_structures': len(shapes), 'max_structures': 2}


def measurement_contract(ast, bindings, settings):
    items = []
    for role in dsl.roles_used(ast):
        metadata = bindings[role].get('measurement') or {}
        coverage = metadata.get('coverage')
        valid_coverage = type(coverage) in (int, float) and 0 <= coverage <= 1
        # Metadata claims require provenance; absent metadata cannot be repaired by an LLM story.
        verified = bool(metadata.get('evidence_hash') and metadata.get('as_of') and metadata.get('scope') ==
                        {k: settings.get(k) for k in ('region', 'universe', 'delay')})
        items.append({'role': role, 'unit': metadata.get('unit'), 'update_frequency': metadata.get('update_frequency'),
                      'coverage': coverage if valid_coverage and verified else None,
                      'metadata_verified': verified, 'missingness': 'unmeasured' if not verified else metadata.get('missingness','unmeasured')})
    return {'roles': items, 'missing_combination_hypotheses': ['intersection_only', 'missing_is_neutral_requires_evidence'],
            'neutral_imputation_enabled': False, 'unknown_metadata_is_not_pass': True}


def search_packet(conn, bindings, settings):
    from .research_learning import as_of
    history = [t for t in as_of(conn) if t.get('task_id') and compatible(t, bindings, settings)]
    seeds = sorted(history, key=lambda t: t['available_at'], reverse=True)[:3]
    return {'bounded_edits': [{'parent_id': t['trial_id'], 'edits': finite_edits(t['document']['ast'], bindings, 2)} for t in seeds],
            'representative_measurements': templates(bindings, 3),
            'instruction': 'These are optional hypotheses, not validated improvements. Provide measurement, prediction and falsifier; keep current budget and review.'}


def sensitivity(conn):
    from .research_learning import as_of
    trials = as_of(conn); groups = {}
    for t in trials:
        doc=t['document']
        if not doc.get('parent_id') or not doc.get('edit'): continue
        group=groups.setdefault(doc['parent_id'], {'parent_id':doc['parent_id'],'trials':[], 'quality':Counter()})
        outcome=t['outcome'] or {}; group['quality'][outcome.get('quality','unassessed')]+=1
        group['trials'].append({'trial_id':t['trial_id'],'edit':doc['edit'],'execution':outcome.get('execution','not_run')})
    return [{**g, 'quality':dict(g['quality']), 'robustness_score':None,
             'interpretation':'All observed variants; incomplete/technical failures remain separate, no universal threshold.'} for g in groups.values()]
