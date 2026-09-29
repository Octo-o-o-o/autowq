"""Local point-in-time coverage audits; raw panels never enter model packets."""
import math
from collections import Counter
from . import util


def audit_panel(doc):
    required={'scope','source','rows'}
    if not isinstance(doc,dict) or set(doc)!=required or not isinstance(doc['source'],str) or not doc['source']:
        raise ValueError('Panel requires scope/source/rows')
    if not isinstance(doc['scope'],dict) or set(doc['scope'])!={'region','universe','delay'}:
        raise ValueError('Exact market scope required')
    rows=doc['rows']
    if not isinstance(rows,list) or not 1<=len(rows)<=100000:
        raise ValueError('Panel requires 1..100000 aligned date/asset observations')
    seen=set();counts=Counter();previous={};updates=Counter();intervals=Counter()
    for r in rows:
        if not isinstance(r,dict) or set(r)!={'date','asset','available_at','left','right'}:
            raise ValueError('Rows require date/asset/available_at/left/right')
        if not isinstance(r['asset'],str) or not r['asset']:raise ValueError('Asset required')
        date=util.parse_iso(r['date']);available=util.parse_iso(r['available_at'])
        if available>date:raise ValueError('Observation was unavailable at decision time')
        key=(r['date'],r['asset'])
        if key in seen:raise ValueError('Duplicate panel observation')
        seen.add(key)
        for side in ('left','right'):
            value=r[side]
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                raise ValueError('Values must be finite numbers or explicit null')
            counts[side]+=value is not None
            old=previous.get((r['asset'],side))
            if old:
                if date<=old[0]:raise ValueError('Asset dates must increase')
                if value is not None and old[1] is not None:
                    intervals[side]+=1;updates[side]+=value!=old[1]
            previous[(r['asset'],side)]=(date,value)
        counts['intersection']+=r['left'] is not None and r['right'] is not None
        counts['union']+=r['left'] is not None or r['right'] is not None
    n=len(rows)
    return {'evidence_hash':util.sha256_json(doc),'scope':doc['scope'],'observations':n,
            'coverage':{k:counts[k]/n for k in ('left','right','intersection','union')},
            'observed_change_fraction':{k:updates[k]/intervals[k] if intervals[k] else None for k in ('left','right')},
            'hypotheses':{'intersection':'Only both observed', 'neutral_missing':'Uses union; missing information is assumed neutral, which still needs prospective testing'},
            'neutral_missing_validated':False,'official_metric':False,
            'note':'Same-value updates are not detectable; observed change frequency is not publication frequency.'}


def policy_coverage(cfg, policy):
    from . import catalog
    query=catalog.query_from_settings(policy['settings'])
    try:doc=catalog.load_catalog(cfg.private_dir,query)
    except (OSError,ValueError,KeyError):return {'status':'unavailable','reason':'No matching local catalog snapshot','roles':[]}
    fields={f['id']:f for f in doc['fields']};roles=[]
    for name,binding in policy['bindings'].items():
        if binding.get('group_field'):continue
        inputs=[fields.get(f) for f in binding['fields']]
        coverages=[f.get('coverage') if f else None for f in inputs]
        known=all(type(v) in (int,float) and 0<=v<=1 for v in coverages)
        roles.append({'role':name,'input_count':len(inputs),'input_coverage':coverages,
                      'intersection_lower_bound':max(0,sum(coverages)-(len(coverages)-1)) if known else None,
                      'expression_coverage':None,'update_frequency':(binding.get('measurement') or {}).get('update_frequency')})
    return {'status':'available','query':query,'catalog_complete':doc.get('complete') is True,
            'queried_at':doc.get('queried_at'),'evidence_hash':util.sha256_json(doc),'roles':roles,
            'note':'Marginal input coverage bounds are not expression coverage or synchronized missingness; no neutral imputation inferred.'}
