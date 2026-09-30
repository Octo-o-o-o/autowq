"""只重排连续可比的 waiting 区段；UNKNOWN/不同域保持 FIFO 分隔。"""
import json
import math
from . import util, feedback


def evidence(alpha, report):
    if report.get('collection_status') != 'complete' or not report.get('submission_candidate') or report.get('validation_gaps') or report.get('platform_blockers'):
        return None
    corr = report.get('submitted_correlation') or {}
    peers = corr.get('against') or []
    pool = corr.get('pool_contract') or {}
    if corr.get('missing') or not peers or not pool.get('members_hash') or pool.get('official') is not False:
        return None
    snapshot = []
    for peer in peers:
        contract = peer.get('contract') or {}
        keys = ('algorithm','minimum','right_content_hash','aligned_intervals_hash')
        if any(contract.get(k) is None for k in keys): return None
        snapshot.append({'alpha_id':peer['alpha_id'], **{k:contract[k] for k in keys}})
    temporal = {x['segment']:x for x in report.get('temporal',[]) if 'segment' in x}
    test = temporal.get('test') or {}
    values = [(alpha.get('is') or {}).get('fitness'), test.get('sharpe'), test.get('fitness'), corr.get('max')]
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in values): return None
    if not 0 <= values[-1] <= 1 or not report.get('segment_rules'): return None
    pnl = util.read_json(report['pnl_path'])
    intervals = sorted(feedback.daily_pnl(pnl))
    if len(intervals) < 252: return None
    scope = feedback.simulation_settings(alpha.get('settings') or {})
    if any(k not in scope for k in ('region','universe','delay','neutralization','decay','truncation')): return None
    domain = util.sha256_json({'settings':scope,'intervals':intervals,'quality':'is-fitness_test-sharpe_test-fitness-v1',
        'segment_rules':report['segment_rules'],'pool':pool,'snapshot':sorted(snapshot,key=lambda x:x['alpha_id'])})
    return domain, tuple(values[:-1]+[-values[-1]])


def pareto_segment(items):
    remaining = list(items); out = []
    while remaining:
        layer = []
        for x in remaining:
            dominated = any(all(a >= b for a,b in zip(y[2],x[2])) and any(a > b for a,b in zip(y[2],x[2])) for y in remaining)
            if not dominated: layer.append(x)
        out.extend(x[0] for x in layer)
        chosen = {x[0]['alpha_id'] for x in layer}
        remaining = [x for x in remaining if x[0]['alpha_id'] not in chosen]
    return out


def order(rows, load):
    """Input is frozen FIFO (created_at, stable id); never use a pairwise mixed comparator."""
    out = []; segment = []; domain = None
    for row in rows:
        try: info = evidence(*load(row['alpha_id']))
        except (ValueError, OSError, KeyError, TypeError): info = None
        if info is None or info[0] != domain:
            out.extend(pareto_segment(segment)); segment = []
        if info is None:
            out.append(row); domain = None
        else:
            domain = info[0]; segment.append((row,info[0],info[1]))
    out.extend(pareto_segment(segment))
    return out
