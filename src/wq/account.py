"""只读账户快照：等级、pyramid 乘数与数量、连击。社区工具（wqb-agent-tools MIT）记录的端点，
本账号可用性未核验；任一端点 4xx 即记录为不可用并继续，不重试、不枚举其他接口。快照写私有目录，不含凭证。"""
import os
from . import util

# 端点 → 快照键。用途：判断是否存在本账号可用的高乘数 pyramid，是否改变研究设置由人工另行决定。
ENDPOINTS = (('/users/self', 'profile'),
             ('/users/self/activities/pyramid-multipliers', 'pyramid_multipliers'),
             ('/users/self/activities/pyramid-alphas', 'pyramid_alphas'),
             ('/users/self/streak', 'streak'))
PROFILE_KEYS = ('id', 'level', 'geniusLevel', 'points', 'permissions', 'stage', 'dateCreated')


def snapshot(get, client, cfg):
    """get(client, cfg, path) -> (status, headers, data)；每个端点最多一次请求。"""
    from .errors import AdapterError
    out = {'schema': 'wq.account-snapshot/v1', 'queried_at': util.now_iso(), 'endpoints': {}}
    for path, key in ENDPOINTS:
        try:
            status, _, data = get(client, cfg, path)
            out['endpoints'][key] = {'path': path, 'status': status, 'data': data}
        except AdapterError as exc:
            out['endpoints'][key] = {'path': path, 'status': None, 'error': str(exc), 'kind': exc.kind}
            if exc.kind in (AdapterError.NETWORK, AdapterError.RATE_LIMIT, AdapterError.AUTH):
                break   # 网络/限流/认证问题不继续打其余端点
    profile = (out['endpoints'].get('profile') or {}).get('data') or {}
    if isinstance(profile, dict):
        # 只保留等级/权限类字段；邮箱、姓名等身份信息不落盘。
        out['endpoints']['profile']['data'] = {k: profile.get(k) for k in PROFILE_KEYS if k in profile}
    path = os.path.join(cfg.private_dir, 'brain-account', 'snapshot-' + out['queried_at'].replace(':', '').replace('+', 'Z') + '.json')
    os.makedirs(os.path.dirname(path), exist_ok=True, mode=0o700)
    util.write_json(path, out)
    os.chmod(path, 0o600)
    out['saved_to'] = path
    return out


def _rows(data):
    if isinstance(data, dict):
        for key in ('pyramids', 'results'):
            if isinstance(data.get(key), list): return data[key]
    return data if isinstance(data, list) else []


def summary(snap):
    """人读摘要：可用端点、pyramid 乘数排序（高→低）与本账号各 pyramid 的 alpha 数、连击。"""
    ep = snap['endpoints']
    profile = (ep.get('profile') or {}).get('data') or {}
    mult = {}
    for row in _rows((ep.get('pyramid_multipliers') or {}).get('data')):
        if not isinstance(row, dict): continue
        cat = row.get('category'); cat = cat.get('id') if isinstance(cat, dict) else cat
        mult[(str(cat), str(row.get('region')), str(row.get('delay')))] = row.get('multiplier')
    counts = {}
    for row in _rows((ep.get('pyramid_alphas') or {}).get('data')):
        if not isinstance(row, dict): continue
        cat = row.get('category'); cat = cat.get('id') if isinstance(cat, dict) else cat
        counts[(str(cat), str(row.get('region')), str(row.get('delay')))] = row.get('alphaCount')
    pyramids = [{'category': k[0], 'region': k[1], 'delay': k[2], 'multiplier': mult.get(k), 'alpha_count': counts.get(k)}
                for k in sorted(set(mult) | set(counts), key=lambda k: (-(mult.get(k) or 0), k))]
    return {'saved_to': snap.get('saved_to'),
            'available': {k: (v.get('status') if v.get('status') else 'unavailable: ' + str(v.get('error'))) for k, v in ep.items()},
            'level': {k: profile.get(k) for k in ('level', 'geniusLevel', 'points', 'stage') if k in profile},
            'permissions': profile.get('permissions'),
            'pyramids': pyramids,
            'streak': (ep.get('streak') or {}).get('data'),
            'note': '只读快照；乘数是否适用于本账号积分/报酬以平台说明为准。改变研究设置（region/delay）不由本命令自动触发。'}
