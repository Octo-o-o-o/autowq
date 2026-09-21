"""研究模拟须绑定本地验收与有限请求清单；声明完整不等于研究获证。"""
from pathlib import Path
from . import util, evidence_gate


def request_hash(doc):
    return util.sha256_json({k: doc.get(k) for k in ('request', 'config', 'purpose')})


def validate(cfg, doc):
    ref = doc.get('evidence', {}).get('research_review')
    if not isinstance(ref, str) or not ref:
        raise ValueError('缺本地研究验收文件，protocol_accepted=true不足以放行')
    root = (Path(cfg.private_dir) / 'research-approvals').resolve()
    path = Path(cfg.resolve(ref)).resolve()
    if not path.is_relative_to(root):
        raise ValueError('研究验收文件必须位于私有research-approvals目录')
    try:
        review = util.read_json(str(path))
        if not isinstance(review, dict):
            raise ValueError('研究验收格式错误')
        if review.get('status') != 'accepted_for_simulation' or review.get('synthetic') is not False:
            raise ValueError('研究验收未通过或为合成证据')
        stamp = evidence_gate.parse_timestamp(review.get('reviewed_at'))
        if stamp is None or stamp > util.now() or not review.get('reviewer'):
            raise ValueError('研究验收缺有效时间与审查者')
        scope = review.get('autopilot_policy')
        if scope:
            from . import autopilot
            current = autopilot.policy(cfg)
            if util.sha256_json(current) != scope.get('sha256') or util.now() >= util.parse_iso(current['valid_until']):
                raise ValueError('自动研究范围改变或授权到期，停止派发')
        loaded = {}
        for name in ('protocol', 'declarations'):
            spec = review.get(name, {})
            source = Path(cfg.resolve(spec['path']))
            loaded[name] = util.read_json(str(source))
            if util.sha256_json(loaded[name]) != spec.get('sha256'):
                raise ValueError(name + '已改变，需重新验收')
        protocol = loaded['protocol']
        if protocol.get('status') != 'accepted_for_simulation' or protocol.get('platform_ready') is not True:
            raise ValueError('协议尚未获准进行平台模拟')
        report, code = evidence_gate.evaluate(protocol.get('required_evidence'), loaded['declarations'], util.now())
        if code != 0 or report['synthetic']:
            raise ValueError('数据证据未齐或为合成声明：' + report['verdict'])
        allowed = review.get('allowed_request_hashes')
        if not isinstance(allowed, list) or not allowed or request_hash(doc) not in allowed:
            raise ValueError('请求不在验收过的有限清单内；禁止扩展参数网格')
        frozen = doc.get('evidence', {}).get('research_review_sha256')
        digest = util.sha256_json(review)
        if frozen and frozen != digest:
            raise ValueError('入队后验收文件改变，停止新派发')
        return digest
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError('研究验收文件缺失或结构错误') from exc
