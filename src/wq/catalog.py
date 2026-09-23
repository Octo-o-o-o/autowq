"""BRAIN 字段目录快照与研究角色登记；只读 GET，快照留在私有目录。"""
import json
import re
import time
from pathlib import Path
from . import util

CATALOG_SCHEMA = 'wq.field-catalog/v1'
SNAPSHOT_SCHEMA = 'wq.field-snapshot/v1'
QUERY_KEYS = ('instrumentType', 'region', 'delay', 'universe')
PAGE = 50
# 角色名只允许模型可读的标识符；平台字段名本身不会进入提示词。
ROLE_NAME_MAX = 40


def query_from_settings(settings):
    return {'instrumentType': settings.get('instrumentType', 'EQUITY'), 'region': settings['region'],
            'delay': int(settings['delay']), 'universe': settings['universe']}


def catalog_path(private_dir, query):
    return str(Path(private_dir) / f"field-catalog-{query['region']}-{query['universe']}-delay{query['delay']}.json".lower())


def fetch_catalog(client, query, spacing_s=0.5, sleep=time.sleep, max_pages=400):
    """分页读取 /data-fields；每页之间留间隔，不并发。"""
    results, offset, count = [], 0, None
    base = '&'.join(f'{k}={query[k]}' for k in QUERY_KEYS)
    for _ in range(max_pages):
        _, _, data = client.request('GET', f'/data-fields?{base}&limit={PAGE}&offset={offset}')
        page = data.get('results')
        if not isinstance(page, list):
            raise ValueError('字段目录响应缺 results')
        count = data.get('count', count)
        results.extend(page)
        offset += len(page)
        if not page or (isinstance(count, int) and offset >= count):
            break
        sleep(spacing_s)
    if isinstance(count, int) and len(results) != count:
        raise ValueError(f'字段目录分页不完整：{len(results)}/{count}')
    fields = [{k: x.get(k) for k in ('id', 'type', 'description', 'coverage', 'userCount', 'alphaCount')}
              | {'dataset': (x.get('dataset') or {}).get('id'), 'category': (x.get('category') or {}).get('id')}
              for x in results if isinstance(x, dict) and x.get('id')]
    return {'schema': CATALOG_SCHEMA, 'query': query, 'queried_at': util.now_iso(), 'count': len(fields),
            'complete': count is None or len(fields) == count, 'source': 'https://api.worldquantbrain.com/data-fields',
            'fields': fields}


def load_catalog(private_dir, query):
    path = catalog_path(private_dir, query)
    doc = util.read_json(path)
    if doc.get('schema') != CATALOG_SCHEMA or doc.get('query') != query:
        raise ValueError('字段目录快照与当前设置不匹配，请 --refresh')
    return doc


def search(doc, text=None, dataset=None, field_type='MATRIX', min_coverage=0.0, limit=50):
    out = []
    needle = (text or '').lower()
    for f in doc['fields']:
        if field_type and f.get('type') != field_type: continue
        if dataset and f.get('dataset') != dataset: continue
        if (f.get('coverage') or 0) < min_coverage: continue
        if needle and needle not in (f['id'] + ' ' + (f.get('description') or '')).lower(): continue
        out.append(f)
    out.sort(key=lambda f: (-(f.get('userCount') or 0), f['id']))
    return out[:limit]


def datasets(doc):
    counts = {}
    for f in doc['fields']:
        key = (f.get('category'), f.get('dataset'))
        counts[key] = counts.get(key, 0) + 1
    return [{'category': c, 'dataset': d, 'fields': n} for (c, d), n in sorted(counts.items(), key=lambda x: (-x[1], str(x[0])))]


def field_snapshot(client, field_id, query):
    """与既有 evidence 文件同格式：字段元数据 + 当前设置下的覆盖上下文。"""
    if not field_id.replace('_', '').isalnum():
        raise ValueError('字段ID格式不合法')
    _, _, data = client.request('GET', '/data-fields/' + field_id)
    if data.get('id') != field_id:
        raise ValueError('字段元数据与请求不符：' + field_id)
    context = [x for x in data.get('data', []) if isinstance(x, dict)
               and x.get('region') == query['region'] and x.get('delay') == query['delay']
               and x.get('universe') == query['universe']]
    if not context:
        raise ValueError(f'字段 {field_id} 在当前 region/universe/delay 下无覆盖记录')
    field = {k: data.get(k) for k in ('id', 'type', 'description', 'category', 'dataset', 'subcategory', 'visualizable')}
    return {'schema': SNAPSHOT_SCHEMA, 'field': field, 'context': context, 'query': query,
            'queried_at': util.now_iso(), 'source': 'https://api.worldquantbrain.com/data-fields/' + field_id}


def evidence_path(private_dir, field_id, query):
    return str(Path(private_dir) / f"autopilot-{field_id}-delay{query['delay']}-{query['region']}-{query['universe']}.json".lower())


IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
NUMBER = re.compile(r'(?<![A-Za-z0-9_.])-\s*\d')
KEYWORDS = {'NaN', 'nan', 'true', 'false', 'True', 'False'}
# 绑定表达式允许的平台算子；来自本机 operator 证据快照的子集，保守列出。
BINDING_OPERATORS = {'rank', 'reverse', 'abs', 'log', 'sign', 'sqrt', 'if_else', 'divide', 'add', 'subtract',
                     'multiply', 'max', 'min', 'ts_delay', 'ts_mean', 'ts_sum', 'ts_delta', 'ts_backfill',
                     'ts_decay_linear', 'ts_std_dev', 'ts_rank', 'ts_zscore', 'zscore', 'scale', 'winsorize',
                     'group_rank', 'group_zscore', 'group_neutralize'}


def check_expression(expression, fields, group_field=False):
    """绑定表达式只能引用声明的字段与已知算子；每个声明字段都必须出现；不允许负数参数。"""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError('缺表达式')
    if group_field:
        if expression.strip() != fields[0] or len(fields) != 1:
            raise ValueError('分组绑定的表达式必须就是该分组字段')
        return
    calls = {m.group(1) for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', expression)}
    idents = {m.group(0) for m in IDENT.finditer(expression)}
    unknown_ops = calls - BINDING_OPERATORS
    if unknown_ops:
        raise ValueError('表达式含未核验算子：' + ', '.join(sorted(unknown_ops)))
    used = idents - calls - KEYWORDS
    declared = set(fields)
    if used - declared:
        raise ValueError('表达式引用未声明字段：' + ', '.join(sorted(used - declared)))
    if declared - used:
        raise ValueError('表达式未使用字段：' + ', '.join(sorted(declared - used)))
    if NUMBER.search(expression):
        raise ValueError('表达式不允许负数参数')


def mentions_field(text, fields):
    if not isinstance(text, str) or not text: return False
    words = set(IDENT.findall(text))
    return bool(words & set(fields))


def add_role(policy_path, name, expression, fields, description, evidence_paths, cluster=None,
             group_field=False, check=None):
    """把已核验字段登记为研究角色；写入 bindings 与 evidence_files，其他策略项不变。"""
    if not name or len(name) > ROLE_NAME_MAX or not name.replace('_', '').isalnum() or not name[0].isalpha():
        raise ValueError('角色名须为字母开头的标识符')
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError('缺表达式')
    if not fields or any(not isinstance(f, str) or not f for f in fields):
        raise ValueError('缺字段列表')
    if not isinstance(description, str) or len(description.strip()) < 8:
        raise ValueError('角色说明至少8字符，需写明它不代表什么')
    check_expression(expression, fields, group_field)
    policy = util.read_json(policy_path)
    if name in policy.get('bindings', {}):
        raise ValueError('角色已存在：' + name)
    snapshots = {}
    for path in evidence_paths:
        doc = util.read_json(path)
        if doc.get('schema') != SNAPSHOT_SCHEMA or doc.get('query') != query_from_settings(policy['settings']):
            raise ValueError('证据快照缺失或与策略设置不一致：' + path)
        if not doc.get('context'):
            raise ValueError('证据快照缺当前设置下的覆盖记录：' + path)
        want = 'GROUP' if group_field else 'MATRIX'
        if doc.get('field', {}).get('type') != want:
            raise ValueError(f"字段类型须为{want}：" + str(doc.get('field', {}).get('id')))
        snapshots[doc['field']['id']] = path
    missing = [f for f in fields if f not in snapshots]
    if missing:
        raise ValueError('字段缺证据快照：' + ', '.join(missing))
    binding = {'expression': expression.strip(), 'fields': list(fields), 'description': description.strip(),
               'source': 'https://api.worldquantbrain.com/data-fields/' + fields[0]}
    if cluster: binding['cluster'] = cluster
    if group_field: binding['group_field'] = True
    candidate = json.loads(json.dumps(policy))
    candidate['bindings'][name] = binding
    known = {e['path']: e for e in candidate.get('evidence_files', [])}
    for f in fields:
        path = snapshots[f]
        digest = util.sha256_json(util.read_json(path))
        if path in known: known[path]['sha256'] = digest   # 本次登记即重新核验该快照
        else: candidate.setdefault('evidence_files', []).append({'path': path, 'sha256': digest})
    candidate['verified_at'] = util.now_iso()
    if check: check(candidate)            # 完整策略校验通过后才落盘
    util.write_json(policy_path, candidate)
    return binding


def refresh_evidence_hash(policy_path, path):
    """操作者重新生成某个证据快照后，更新策略中该路径的摘要（视为本次重新核验）。"""
    policy = util.read_json(policy_path)
    changed = False
    for e in policy.get('evidence_files', []):
        if e.get('path') == path:
            e['sha256'] = util.sha256_json(util.read_json(path)); changed = True
    if changed:
        util.write_json(policy_path, policy)
    return changed
