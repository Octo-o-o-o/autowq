"""受限、因果的抽象研究表达式；模型不能提供平台代码或任意字段。"""
from . import util

UNARY = {'rank': 'rank', 'neg': 'reverse', 'zscore': 'zscore', 'log': 'log', 'sign': 'sign'}
BINARY = {'add': '+', 'sub': '-', 'mul': '*'}
# 除法只以受保护形式编译，分母为0/负时给NaN，不产生Inf。
RATIO = 'div'
TIMESERIES = {'mean': 'ts_mean', 'delta': 'ts_delta', 'std': 'ts_std_dev', 'time_rank': 'ts_rank',
              'time_zscore': 'ts_zscore', 'decay': 'ts_decay_linear', 'sum': 'ts_sum',
              'backfill': 'ts_backfill', 'delay': 'ts_delay', 'av_diff': 'ts_av_diff'}
# 双序列时间操作：过去窗口内两个序列的相关/协方差。
TIMESERIES_PAIR = {'corr': 'ts_corr'}
# group_mean 的平台签名含 weight 参数，暂不开放。
GROUP = {'group_rank': 'group_rank', 'group_zscore': 'group_zscore', 'group_neutralize': 'group_neutralize'}
GROUPS = ('market', 'sector', 'industry', 'subindustry')
WINDOWS = (5, 10, 20, 60, 120, 252)
# 每个候选的复杂度上限：与提示词、advance() 一致。
MAX_TIMESERIES = 3
MAX_BINARY = 3
MAX_GROUP = 2
MAX_ROLES = 3
# 程序预登记的等权组合（父信号都已各自审查与回测）允许更大的树；模型提案仍用 proposal。
LIMITS = {'proposal': {'nodes': 24, 'depth': 6, 'timeseries': MAX_TIMESERIES, 'binary': MAX_BINARY, 'group': MAX_GROUP, 'roles': MAX_ROLES},
          'combination': {'nodes': 48, 'depth': 9, 'timeseries': 6, 'binary': 6, 'group': 4, 'roles': 5}}
# 抽象角色由私有只读 BRAIN 字段快照支撑。核心角色必须存在于策略；
# 其他角色由 `wq policy add-role` 登记，并带模型可读的说明。平台字段名
# 留在本地策略里，不进入模型提示词。
ROLES = ('daily_return', 'activity_rank', 'cashflow_strength',
         'market_cap_rank', 'price_vwap_gap')
OPTIONAL_ROLES = ('relative_vwap_gap', 'cashflow_yield')
TEXT_LIMITS = {'title': 100, 'hypothesis': 2000, 'counterexample': 2000}

CORE_DESCRIPTIONS = {
    'relative_vwap_gap': '过去已知的(VWAP-收盘价)/正VWAP，同股票的比例偏离；不是买卖价差或交易冲击',
    'cashflow_yield': '过去已知经营现金流除以正市值的比率代理；市值单位为百万，不声称已校准现金流同币种同期间的精确收益率，也不是首次财报事件。time_rank仅为该代理在自身过去窗口的位置',
    'daily_return': '过去已知的单日股票回报，非未来标签；均值不是复合累计收益，标准差不是剔除市场/行业后的特质波动',
    'activity_rank': '过去已知的股票每日成交股数截面排名，0到1；不是成交金额、换手率、买卖价差、市场深度或分析师分歧。跨日排名变化也可能来自其他股票变化',
    'cashflow_strength': '过去已知的经营现金流/正总资产截面排名；排名差不是原始现金流增长，平滑排名不是新增盈利信息；不是首次财报事件或同会计期盈利检验',
    'market_cap_rank': '过去已知的每日市值截面排名，市值单位为百万；是规模观测量，不是风险调整收益、流动性或行业中性控制',
    'price_vwap_gap': '过去已知的收盘价排名减成交量加权平均价排名；是相对价格位置差，不是买卖价差、单位交易价格冲击、换手率或未来收益'}

PLATFORM_THRESHOLDS = {
    'note': '官方样本内筛选门槛（公开文档；本地不放宽）：夏普比>1.25、适应度>1.0、年化换手1%–70%、单票权重不集中、子股票池夏普比达标、与本账号已提交信号的日收益相关<0.7。适应度≈夏普比*sqrt(|年化收益|/max(换手,0.125))，换手低于12.5%后不再提升适应度。',
    'failure_playbook': {
        '收益效率不足': '通常是换手过高或收益太薄：加decay、用更长窗口、改用变化更慢的观测量（基本面/分析师）、或用group_rank降低横截面噪声',
        '换手过高': '窗口拉长、decay平滑、用backfill/sum而非单日差分；日频价量信号最易触发',
        '换手过低': '信号几乎不变（如季度基本面原值），需加入变化项或与价量信号组合',
        '子股票池不稳健': '信号被小市值主导：用group_rank/group_neutralize在行业内比较，用基本面比率而非原值',
        '收益风险比不足': '机制本身弱或方向错：更换信息来源，不靠调窗口；反向若显著为负会由程序预登记一次符号翻转复核',
        '与已提交信号重叠': '换数据簇/经济逻辑，同簇调参无法解决'}}


def role_catalog(bindings):
    """模型可见的角色说明：核心角色用内置文本，登记角色用策略里的 description。"""
    roles = {}
    for name, spec in bindings.items():
        if spec.get('group_field'):
            continue
        text = spec.get('description') or CORE_DESCRIPTIONS.get(name)
        if not text:
            raise ValueError('角色缺模型可读说明：' + name)
        roles[name] = text + (f"（数据簇：{spec['cluster']}）" if spec.get('cluster') else '')
    return roles


def compile_ast(ast, bindings, profile='proposal'):
    limits = LIMITS[profile]
    nodes, fields, signature, roles = [], set(), [], set()
    counts = {'timeseries': 0, 'binary': 0, 'group': 0}

    def visit(node, depth=0):
        if depth > limits['depth'] or len(nodes) >= limits['nodes'] or not isinstance(node, dict):
            raise ValueError(f"AST最多{limits['nodes']}节点、深度{limits['depth']}，只接受对象")
        nodes.append(node)
        op = node.get('op')
        if op == 'field':
            name = node.get('name')
            if set(node) != {'op', 'name'} or name not in bindings or bindings[name].get('group_field'):
                raise ValueError('字段角色不在已核验范围')
            spec = bindings[name]
            fields.update(spec['fields']); signature.append('field:' + name); roles.add(name)
            return spec['expression']
        if op in UNARY:
            if set(node) != {'op', 'arg'}: raise ValueError('单目操作格式错误')
            # 符号翻转与重复rank不成为新机制族；zscore/log/sign 记入签名。
            if op not in ('rank', 'neg'): signature.append(op)
            return UNARY[op] + '(' + visit(node['arg'], depth+1) + ')'
        if op in TIMESERIES:
            if set(node) != {'op', 'arg', 'window'} or type(node['window']) is not int or node['window'] not in WINDOWS:
                raise ValueError('时间窗口只允许5/10/20/60/120/252个过去交易日')
            counts['timeseries'] += 1; signature.append(op)
            return f"{TIMESERIES[op]}({visit(node['arg'], depth+1)}, {node['window']})"
        if op in TIMESERIES_PAIR:
            if set(node) != {'op', 'left', 'right', 'window'} or type(node['window']) is not int or node['window'] not in WINDOWS:
                raise ValueError('双序列时间操作需left/right/window')
            counts['timeseries'] += 1; signature.append(op)
            left, right = visit(node['left'], depth+1), visit(node['right'], depth+1)
            if left == right: raise ValueError('拒绝同一表达式自相关')
            return f"{TIMESERIES_PAIR[op]}({left}, {right}, {node['window']})"
        if op in GROUP:
            if set(node) != {'op', 'arg', 'group'} or node['group'] not in GROUPS:
                raise ValueError('分组只允许market/sector/industry/subindustry')
            group = bindings.get(node['group'])
            if not group or not group.get('group_field'):
                raise ValueError('分组字段未在策略中核验：' + str(node['group']))
            fields.update(group['fields']); counts['group'] += 1; signature.append(op)
            return f"{GROUP[op]}({visit(node['arg'], depth+1)}, {group['expression']})"
        if op in BINARY or op == RATIO:
            if set(node) != {'op', 'left', 'right'}: raise ValueError('双目操作格式错误')
            counts['binary'] += 1; signature.append(op)
            left, right = visit(node['left'], depth+1), visit(node['right'], depth+1)
            if left == right: raise ValueError('拒绝同一表达式自加、自减、自乘、自除')
            if op == RATIO:
                return f'({left} / if_else({right} > 0, {right}, NaN))'
            return f'({left} {BINARY[op]} {right})'
        raise ValueError('未知操作；不接受原始表达式、未来值、无保护除法或自定义代码')

    expression = visit(ast)
    if all(x.startswith('field:') for x in signature):
        raise ValueError('拒绝单字段或单字段排名的已知教学/旧基线')
    if counts['timeseries'] > limits['timeseries'] or counts['binary'] > limits['binary'] or counts['group'] > limits['group']:
        raise ValueError(f"候选过于复杂，最多{limits['timeseries']}个时间操作、{limits['binary']}个组合操作、{limits['group']}个分组操作")
    if len(roles) > limits['roles']:
        raise ValueError(f"最多使用{limits['roles']}个字段角色")
    family = util.sha256_json(sorted(signature))
    return expression, sorted(fields), family


def validate_candidate(candidate, bindings, profile='proposal'):
    if not isinstance(candidate, dict) or set(candidate) != {'title','hypothesis','counterexample','ast'}:
        raise ValueError('candidate须含title/hypothesis/counterexample/ast且无额外字段')
    for k, maximum in TEXT_LIMITS.items():
        if not isinstance(candidate[k], str) or not 8 <= len(candidate[k].strip()) <= maximum:
            raise ValueError(k + '需为有内容的有限长度文字')
    return compile_ast(candidate['ast'], bindings, profile)


def roles_used(ast):
    out = set()
    def walk(node):
        if not isinstance(node, dict): return
        if node.get('op') == 'field': out.add(node.get('name')); return
        for key in ('arg', 'left', 'right'): walk(node.get(key))
    walk(ast)
    return sorted(x for x in out if isinstance(x, str))


def public_contract(bindings=None, profile='proposal'):
    limits = LIMITS[profile]
    roles = role_catalog(bindings) if bindings else dict(CORE_DESCRIPTIONS)
    groups = [g for g in GROUPS if bindings and bindings.get(g, {}).get('group_field')] if bindings else []
    return {'roles': roles,
        'available_validation':'当前运行固定配置的样本内平台筛选，另加程序按预登记条件派发的少量设置变体，全部结果都会记录。单角色基线实验是允许的：先说明观测量的更新频率（日频/季度），再选与之匹配的时序变换；不要为通过准入机械包一层均值。没有自动执行规模/beta/行业残差控制、事件窗、交易成本或样本外验证；不能把列出这些检验写成已经验收。',
        'family_dedup':'本地保守结构去重忽略窗口、neg、rank，并比较其余操作与字段角色的多重集合；即使叙述不同也可能被拒绝，不要靠这些变体重开。',
        'platform_thresholds': PLATFORM_THRESHOLDS,
        'ast': {'field':{'op':'field','name':'daily_return'},
                'unary':'op=rank/neg/zscore/log/sign, arg=AST（zscore为截面标准化；log仅对正数有意义）',
                'binary':'op=add/sub/mul/div, left=AST, right=AST（div为受保护除法：分母<=0给NaN）',
                'past_only':'op=mean/delta/std/time_rank/time_zscore/decay/sum/backfill/delay/av_diff, arg=AST, window=5/10/20/60/120/252（decay=线性衰减加权均值，用于降低换手；backfill=用过去窗口内最近有效值填充缺失，适合季度基本面；delay=取window日前的值；av_diff=当前值减过去窗口均值）',
                'pair':'op=corr, left=AST, right=AST, window=…（过去窗口内两个序列的相关系数）',
                'group':'op=group_rank/group_zscore/group_neutralize, arg=AST, group=' + ('/'.join(groups) if groups else '（本策略未核验分组字段）')},
        'limits':f"最多{limits['nodes']}节点、深度{limits['depth']}（根节点深度为0，每条父子边加1；重复子树按每次出现计数），最多{limits['timeseries']}个时间操作、{limits['binary']}个组合操作、{limits['group']}个分组操作、{limits['roles']}个字段角色。文字字段上限为title 100、hypothesis 2000、counterexample 2000字符；禁止单字段及仅用rank/neg包裹的单字段（例如neg(rank(daily_return))）；不得为绕过此规则机械添加算子。单个提案；不得通过改窗口、改符号、加rank救活旧候选。禁止原始代码、API、平台字段名。"}
