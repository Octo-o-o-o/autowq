"""Explicit-column local exports; never include credentials, prompts or evidence paths."""
import csv
import json
import os
from pathlib import Path
from . import util


def rows(conn, kind, include_synthetic=False):
    if kind == 'tasks':
        return [dict(r) for r in conn.execute('SELECT task_id,kind,status,attempts,created_at,updated_at FROM tasks ORDER BY created_at')]
    if kind == 'results':
        query='SELECT sim_id,remote_id,source,synthetic,status,observed_at,imported_at,stats_json FROM simulations'
        if not include_synthetic: query+=' WHERE synthetic=0'
        result=[]
        for r in conn.execute(query+' ORDER BY imported_at'):
            item=dict(r);stats=json.loads(item.pop('stats_json') or '{}')
            for key in ('sharpe','fitness','turnover','returns','drawdown'):
                item[key]=stats.get(key)
            result.append(item)
        return result
    if kind != 'summary': raise ValueError('Unknown export kind')
    result=[]
    for table in ('tasks','simulations','submissions'):
        where=' WHERE synthetic=0' if table=='simulations' and not include_synthetic else ''
        query='SELECT status,COUNT(*) AS count FROM '+table+where+' GROUP BY status'
        if table=='submissions' and not include_synthetic:
            query='SELECT s.status,COUNT(*) AS count FROM submissions s JOIN simulations r ON r.sim_id=s.sim_id WHERE r.synthetic=0 GROUP BY s.status'
        for r in conn.execute(query):
            result.append({'category':table,'status':r['status'],'count':r['count']})
    return result


COLUMNS={'tasks':['task_id','kind','status','attempts','created_at','updated_at'],
         'results':['sim_id','remote_id','source','synthetic','status','observed_at','imported_at','sharpe','fitness','turnover','returns','drawdown'],
         'summary':['category','status','count']}


def csv_value(value):
    # Spreadsheet software can interpret untrusted strings as formulas.
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')): return "'"+value
    return value


def write(conn, path, kind='summary', format='json', include_synthetic=False):
    if format not in ('json','csv'): raise ValueError('Export format must be json or csv')
    data=rows(conn,kind,include_synthetic)
    target=Path(path).expanduser()
    target.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8',newline='') as f:
        if format=='json':
            json.dump({'schema':'wq.export/v1','kind':kind,'generated_at':util.now_iso(),
                       'include_synthetic':include_synthetic,'rows':data},f,ensure_ascii=False,indent=2)
            f.write('\n')
        else:
            writer=csv.DictWriter(f,fieldnames=COLUMNS[kind]);writer.writeheader()
            writer.writerows({k:csv_value(v) for k,v in row.items()} for row in data)
    return {'path':str(target.absolute()),'rows':len(data),'kind':kind,'format':format}
