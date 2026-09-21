"""受限、因果的抽象研究表达式；模型不能提供平台代码或任意字段。"""
from . import util

UNARY = {'rank': 'rank', 'neg': 'reverse'}
BINARY = {'add': '+', 'sub': '-', 'mul': '*'}
TIMESERIES = {'mean': 'ts_mean', 'delta': 'ts_delta', 'std': 'ts_std_dev'}
WINDOWS = (5, 20, 60, 252)
ROLES = ('daily_return', 'activity_rank', 'cashflow_strength')


def compile_ast(ast, bindings):
    nodes, fields, signature = [], set(), []

    def visit(node, depth=0):
        if depth > 5 or len(nodes) >= 24 or not isinstance(node, dict):
            raise ValueError('AST最多24节点、深度5，只接受对象')
        nodes.append(node)
        op = node.get('op')
        if op == 'field':
            if set(node) != {'op', 'name'} or node['name'] not in ROLES or node['name'] not in bindings:
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
    for k, maximum in [('title',100),('hypothesis',1600),('counterexample',1600)]:
        if not isinstance(candidate[k], str) or not 8 <= len(candidate[k].strip()) <= maximum:
            raise ValueError(k + '需为有内容的有限长度文字')
    return compile_ast(candidate['ast'], bindings)


def public_contract():
    return {'roles': {
        'daily_return':'过去已知的单日股票回报，非未来标签',
        'activity_rank':'过去已知的股票成交活跃度截面排名，0到1',
        'cashflow_strength':'过去已知的经营现金流/正总资产截面排名；不是首次财报事件或同会计期盈利检验'},
        'ast': {'field':{'op':'field','name':'daily_return'},
                'unary':'op=rank或neg, arg=AST',
                'binary':'op=add/sub/mul, left=AST, right=AST',
                'past_only':'op=mean/delta/std, arg=AST, window=5/20/60/252'},
        'limits':'最多24节点、深度5。单个提案；不得通过改窗口、改符号、加rank救活旧候选。禁止原始代码、API、平台字段名。'}
