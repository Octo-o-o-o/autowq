"""Deterministic registered interventions and descriptive, fixed-checkpoint comparisons."""
import copy
import datetime as dt
import math
import statistics
from . import research_dsl, util

SCHEMA = 'wq.campaign-pair/v1'


def node_at(ast, path):
    if not isinstance(path, list) or any(k not in ('arg', 'left', 'right') for k in path):
        raise ValueError('Registered AST path invalid')
    node = ast
    for key in path:
        if not isinstance(node, dict) or key not in node: raise ValueError('Registered AST path absent')
        node = node[key]
    return node


def edited(ast, recipe, bindings):
    """One predeclared intervention; arbitrary replacement subtrees are not supported."""
    if not isinstance(recipe, dict): raise ValueError('Registered edit required')
    result = copy.deepcopy(ast); path = recipe.get('path'); before = node_at(result, path)
    if before != recipe.get('before'): raise ValueError('Registered edit does not match parent')
    kind = recipe.get('kind')
    if kind == 'remove_binary_condition':
        if before.get('op') != 'mul' or recipe.get('keep') not in ('left','right'):
            raise ValueError('Removal must retain one registered multiplicative child')
        after = copy.deepcopy(before[recipe['keep']])
    elif kind == 'replace_operator':
        allowed = {('mean','delta'),('delta','mean'),('mean','sum'),('sum','mean')}
        target = recipe.get('operator')
        if (before.get('op'),target) not in allowed: raise ValueError('Unsupported operator intervention')
        after = {**before, 'op': target}
    elif kind == 'replace_vector_reducer':
        if before.get('op') != 'field': raise ValueError('Reducer intervention requires one field leaf')
        target = recipe.get('role'); a = bindings.get(before['name'],{}); b = bindings.get(target,{})
        ra, rb = a.get('vector_reduction'), b.get('vector_reduction')
        if not ra or not rb or a.get('fields') != b.get('fields'):
            raise ValueError('Reducer intervention requires the identical event field')
        # The existing binding compiler independently validates both reducer contracts.
        operators = {a.get('expression','').split('(')[0],b.get('expression','').split('(')[0]}
        if operators != {'vec_avg','vec_sum'}: raise ValueError('Only avg/sum reducer substitution supported')
        after = {'op':'field','name':target}
    else: raise ValueError('Unsupported intervention recipe')
    if before == after: raise ValueError('Intervention must change the measured expression')
    if path:
        node_at(result,path[:-1])[path[-1]] = after
    else: result = after
    research_dsl.compile_ast(ast,bindings)
    research_dsl.compile_ast(result,bindings)
    return result


def candidates(parent, recipe, bindings, roles_by_arm):
    research_dsl.validate_candidate(parent,bindings)
    control = {**copy.deepcopy(parent), 'ast': edited(parent['ast'],recipe,bindings),
               'title': '注册对照：'+parent['title'][:90],
               'hypothesis': '本臂只执行预登记的单一干预，用于与同父处理臂比较。原主张：'+parent['hypothesis'][:1700],
               'counterexample': '本臂不能证明原机制有效；若观测集合、时间或量纲不一致则不可比较。'+parent['counterexample'][:1700]}
    pair = {'treatment':copy.deepcopy(parent),'control':control}
    if not isinstance(roles_by_arm,dict) or set(roles_by_arm) != set(pair): raise ValueError('Exact arm roles required')
    for arm, candidate in pair.items():
        research_dsl.validate_candidate(candidate,bindings)
        if research_dsl.roles_used(candidate['ast']) != sorted(roles_by_arm[arm]):
            raise ValueError('Registered arm roles do not match intervention')
    return pair


def transform(candidate, spec, bindings):
    if not isinstance(spec,dict) or set(spec) != {'op','window'} or spec['op'] not in ('mean','delay'):
        raise ValueError('Only registered common past-only transform supported')
    out = copy.deepcopy(candidate)
    out['ast'] = {**spec,'arg':out['ast']}
    research_dsl.validate_candidate(out,bindings)
    return out


def validate_evaluation(spec):
    if not isinstance(spec,dict) or spec.get('metric') != 'paired_mean_daily_return':
        raise ValueError('Unsupported evaluation metric; do not silently fall back to Sharpe')
    if spec.get('predicted_direction') not in (-1,1) or isinstance(spec.get('predicted_direction'),bool):
        raise ValueError('Fixed predicted direction required')
    for key in ('effect_floor','tolerance','capital_basis'):
        value = spec.get(key)
        if type(value) not in (int,float) or not math.isfinite(value): raise ValueError('Finite evaluation value required: '+key)
    if spec['tolerance'] < 0 or spec['capital_basis'] <= 0: raise ValueError('Invalid tolerance/capital basis')
    if spec.get('frequency') != 'daily' or not spec.get('timezone') or not spec.get('value_unit') or not spec.get('cost_basis'):
        raise ValueError('Frozen observation units/time/cost basis required')
    dates = spec.get('date_grid')
    if not isinstance(dates,list) or len(dates)<181 or dates != sorted(set(dates)):
        raise ValueError('At least 180 fixed daily intervals required')
    for value in dates: dt.date.fromisoformat(value)
    cuts = spec.get('segment_ends')
    if not isinstance(cuts,list) or len(cuts)!=3 or any(type(x) is not int for x in cuts) or cuts[-1]!=len(dates)-1:
        raise ValueError('Three fixed nonoverlapping segments required')
    if any(b-a<60 for a,b in zip([0]+cuts[:-1],cuts)): raise ValueError('Each fixed segment needs at least 60 intervals')
    if spec.get('missing_policy') != 'inconclusive' or spec.get('revision_policy') != 'append_no_redispatch':
        raise ValueError('Fixed missing/revision policy required')
    if spec.get('reuse_policy') not in ('new_executions_only','descriptive_reuse'):
        raise ValueError('Explicit historical reuse policy required')
    if spec.get('decision_unit') not in ('pair','cross_scope'):
        raise ValueError('Fixed decision unit required')
    if spec['decision_unit']=='cross_scope' and spec.get('joint_function')!='both_scopes':
        raise ValueError('Explicit supported cross-scope decision function required')
    if spec.get('statistic_unit')!='mean_daily_return':
        raise ValueError('Whole-window and segment thresholds must use mean daily return')
    if spec.get('inference') != 'descriptive_pilot': raise ValueError('Only descriptive pilot conclusions supported')
    return spec


def compare(spec, observations):
    """Observations must be derived from verified ledger identities by the campaign collector."""
    validate_evaluation(spec)
    result={'assessment':'inconclusive','reason_codes':[], 'effect':None,'segment_effects':[],
            'eligibility':'blocked','next_action':'none','claim_scope':'registered_operational_prediction',
            'validation_kind':'preregistered_historical_backtest',
            'inference':'descriptive_pilot','claim_limit':'This registered operational prediction only; no scientific validity or income claim.'}
    if set(observations) != {'treatment','control'}:
        result['reason_codes']=['PAIR_INCOMPLETE']; return result
    expected=[list(x) for x in zip(spec['date_grid'][:-1],spec['date_grid'][1:])]
    for arm, obs in observations.items():
        if obs.get('execution')=='unknown':
            result['reason_codes'].append('UNKNOWN_EXECUTION');continue
        if obs.get('execution')=='failed' and (obs.get('ledger_identity_verified') is True or obs.get('identity_verified') is True):
            result['assessment']='technical_failure';result['reason_codes'].append('TECHNICAL_FAILURE');continue
        if obs.get('identity_verified') is not True:
            result['reason_codes'].append('OBSERVATION_IDENTITY_UNVERIFIED')
        if obs.get('synthetic') is not False or obs.get('origin')!='platform_collection':
            result['reason_codes'].append('REAL_PLATFORM_OBSERVATION_REQUIRED'); continue
        if obs.get('execution') != 'complete' or obs.get('evidence_complete') is not True:
            result['reason_codes'].append('FEEDBACK_INCOMPLETE'); continue
        for key in ('frequency','timezone','value_unit','capital_basis','cost_basis'):
            if obs.get(key) != spec[key]: result['reason_codes'].append('OBSERVATION_CONVENTION_MISMATCH:'+key)
        if obs.get('intervals') != expected: result['reason_codes'].append('DATE_GRID_MISMATCH')
        values=obs.get('values')
        if not isinstance(values,list) or len(values)!=len(expected) or any(type(x) not in (int,float) or not math.isfinite(x) for x in values):
            result['reason_codes'].append('OBSERVATION_VALUES_MISSING')
        if not obs.get('revision_evidence'): result['reason_codes'].append('DATA_REVISION_UNKNOWN')
        if obs.get('prior_observed') is not False and spec['reuse_policy']!='descriptive_reuse':
            result['reason_codes'].append('HISTORICAL_REUSE_NOT_AUTHORIZED')
    if result['reason_codes']:
        result['reason_codes']=sorted(set(result['reason_codes'])); return result
    left,right=observations['treatment'],observations['control']
    if left['revision_evidence'] != right['revision_evidence']:
        result['reason_codes']=['DATA_REVISION_MISMATCH']; return result
    values=[spec['predicted_direction']*(a-b)/spec['capital_basis'] for a,b in zip(left['values'],right['values'])]
    effect=statistics.mean(values);cuts=spec['segment_ends']
    segments=[statistics.mean(values[a:b]) for a,b in zip([0]+cuts[:-1],cuts)]
    floor,tolerance=spec['effect_floor'],spec['tolerance']
    if effect>floor+tolerance and sum(x>floor+tolerance for x in segments)>=2: assessment='continue'
    elif effect<=floor-tolerance and all(x<=floor-tolerance for x in segments): assessment='falsified'
    else:assessment='inconclusive'
    result.update(assessment=assessment,effect=effect,segment_effects=segments,observations=len(values),eligibility='verified')
    if any(o.get('prior_observed') is not False for o in observations.values()):result['validation_kind']='historical_reuse_descriptive'
    result['new_blind_executions']=0
    return result


def compare_cross_scope(specs, observation_sets):
    """The registered whole is four distinct executions, never the last pair alone."""
    result={'assessment':'inconclusive','eligibility':'blocked','next_action':'none',
            'reason_codes':[],'decision_unit':'cross_scope','joint_function':'both_scopes',
            'claim_scope':'registered_operational_prediction',
            'validation_kind':'preregistered_historical_backtest','inference':'descriptive_pilot'}
    if len(specs)!=2 or len(observation_sets)!=2:
        return {**result,'reason_codes':['CROSS_SCOPE_INCOMPLETE']}
    for spec in specs:
        validate_evaluation(spec)
        if spec['decision_unit']!='cross_scope' or spec.get('joint_function')!='both_scopes':
            raise ValueError('Cross-scope function must be frozen before the first request')
    if specs[0]!=specs[1]:
        return {**result,'reason_codes':['CROSS_SCOPE_EVALUATION_MISMATCH']}
    components=[compare(spec,obs) for spec,obs in zip(specs,observation_sets)]
    result['components']=components
    if any(x['eligibility']!='verified' for x in components):
        result['reason_codes']=sorted({r for x in components for r in x['reason_codes']})
        return result
    observations=[obs[arm] for obs in observation_sets for arm in ('treatment','control')]
    for key in ('task_id','alpha_id'):
        ids=[o.get(key) for o in observations]
        if any(not x for x in ids) or len(set(ids))!=4:
            return {**result,'reason_codes':['FOUR_DISTINCT_EXECUTIONS_REQUIRED']}
    states=[c['assessment'] for c in components]
    assessment='continue' if states==['continue','continue'] else 'falsified' if 'falsified' in states else 'inconclusive'
    return {**result,'eligibility':'verified','assessment':assessment}
