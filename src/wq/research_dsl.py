"""受限、因果的抽象研究表达式；模型不能提供平台代码或任意字段。"""
from . import util

UNARY = {'rank': 'rank', 'neg': 'reverse'}
BINARY = {'add': '+', 'sub': '-', 'mul': '*'}
TIMESERIES = {'mean': 'ts_mean', 'delta': 'ts_delta', 'std': 'ts_std_dev', 'time_rank': 'ts_rank'}
WINDOWS = (5, 20, 60, 252)
# Abstract roles backed by private, read-only BRAIN field snapshots.  Platform
# field names stay in the local policy and never enter model prompts.
ROLES = ('daily_return', 'activity_rank', 'cashflow_strength',
         'market_cap_rank', 'price_vwap_gap')
# 质量优先提示词需要容纳完整的机制与反例说明；仍保留硬上限，避免
# 模型产物无限膨胀。autopilot 的历史保留和候选校验都引用这里。
OPTIONAL_ROLES = ('relative_vwap_gap', 'cashflow_yield')
TEXT_LIMITS = {'title': 100, 'hypothesis': 2000, 'counterexample': 2000}


def compile_ast(ast, bindings):
    nodes, fields, signature = [], set(), []

    def visit(node, depth=0):
        if depth > 5 or len(nodes) >= 24 or not isinstance(node, dict):
            raise ValueError('AST最多24节点、深度5，只接受对象')
        nodes.append(node)
        op = node.get('op')
        if op == 'field':
            if set(node) != {'op', 'name'} or node['name'] not in ROLES + OPTIONAL_ROLES or node['name'] not in bindings:
                raise ValueError('字段角色不在已核验范围')
            spec = bindings[node['name']]
            fields.update(spec['fields']); signature.append('field:' + node['name'])
            return spec['expression']
        if op in UNARY:
            if set(node) != {'op', 'arg'}: raise ValueError('单目操作格式错误')
            # 符号翻转与重复rank不成为新机制族。
            return UNARY[op] + '(' + visit(node['arg'], depth+1) + ')'
        if op in TIMESERIES:
            if set(node) != {'op', 'arg', 'window'} or type(node['window']) is not int or node['window'] not in WINDOWS:
                raise ValueError('时间窗口只允许5/20/60/252个过去交易日')
            signature.append(op)
            return f"{TIMESERIES[op]}({visit(node['arg'], depth+1)}, {node['window']})"
        if op in BINARY:
            if set(node) != {'op', 'left', 'right'}: raise ValueError('双目操作格式错误')
            signature.append(op)
            left, right = visit(node['left'], depth+1), visit(node['right'], depth+1)
            if left == right: raise ValueError('拒绝同一表达式自加、自减、自乘')
            return f'({left} {BINARY[op]} {right})'
        raise ValueError('未知操作；不接受原始表达式、未来值、除法或自定义代码')

    expression = visit(ast)
    if all(x.startswith('field:') for x in signature):
        raise ValueError('拒绝单字段或单字段排名的已知教学/旧基线')
    family = util.sha256_json(sorted(signature))
    return expression, sorted(fields), family


def validate_candidate(candidate, bindings):
    if not isinstance(candidate, dict) or set(candidate) != {'title','hypothesis','counterexample','ast'}:
        raise ValueError('candidate须含title/hypothesis/counterexample/ast且无额外字段')
    for k, maximum in TEXT_LIMITS.items():
        if not isinstance(candidate[k], str) or not 8 <= len(candidate[k].strip()) <= maximum:
            raise ValueError(k + '需为有内容的有限长度文字')
    return compile_ast(candidate['ast'], bindings)


def public_contract():
    return {'roles': {
        'relative_vwap_gap':'过去已知的(VWAP-收盘价)/正VWAP，同股票的比例偏离；不是买卖价差或交易冲击',
        'cashflow_yield':'过去已知经营现金流除以正市值的比率代理；市值单位为百万，不声称已校准现金流同币种同期间的精确收益率，也不是首次财报事件。time_rank仅为该代理在自身过去窗口的位置',
        'daily_return':'过去已知的单日股票回报，非未来标签；均值不是复合累计收益，标准差不是剔除市场/行业后的特质波动',
        'activity_rank':'过去已知的股票每日成交股数截面排名，0到1；不是成交金额、换手率（缺流通股数）、买卖价差、市场深度或分析师分歧。跨日排名变化也可能来自其他股票变化',
        'cashflow_strength':'过去已知的经营现金流/正总资产截面排名；排名差不是原始现金流增长，平滑排名不是新增盈利信息；不是首次财报事件或同会计期盈利检验',
        'market_cap_rank':'过去已知的每日市值截面排名，市值单位为百万；是规模观测量，不是风险调整收益、流动性或行业中性控制',
        'price_vwap_gap':'过去已知的收盘价排名减成交量加权平均价排名；是相对价格位置差，不是买卖价差、单位交易价格冲击、换手率或未来收益'},
        'available_validation':'当前仅运行一次固定配置的样本内平台筛选。没有自动执行规模/beta/行业残差控制、事件窗、交易成本或样本外验证；不能把列出这些检验写成已经验收。',
        'family_dedup':'本地保守结构去重忽略窗口、neg、rank，并比较其余操作与字段角色的多重集合；即使叙述不同也可能被拒绝，不要靠这些变体重开。',
        'ast': {'field':{'op':'field','name':'daily_return'},
                'unary':'op=rank或neg, arg=AST',
                'binary':'op=add/sub/mul, left=AST, right=AST',
                'past_only':'op=mean/delta/std/time_rank, arg=AST, window=5/20/60/252'},
        'limits':'最多24节点、深度5，最多2个时间序列操作和2个组合操作。文字字段上限为title 100、hypothesis 2000、counterexample 2000字符；禁止单字段及仅用rank/neg包裹的单字段（例如neg(rank(daily_return))）；不得为绕过此规则机械添加算子。单个提案；不得通过改窗口、改符号、加rank救活旧候选。禁止原始代码、API、平台字段名。'}
