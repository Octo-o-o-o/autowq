import unittest
from wq import research_dsl


def bindings():
    b = {n: {'expression': 'returns' if i == 0 else f'rank(field{i})', 'fields': ['returns' if i == 0 else f'field{i}'], 'source': 'fixture://f'}
         for i, n in enumerate(research_dsl.ROLES)}
    b['sales_to_ev'] = {'expression': '(sales / if_else(enterprise_value > 0, enterprise_value, NaN))',
                        'fields': ['sales', 'enterprise_value'], 'source': 'fixture://f',
                        'description': '过去已知的销售额/正企业价值代理', 'cluster': 'fundamental'}
    b['industry'] = {'expression': 'industry', 'fields': ['industry'], 'source': 'fixture://f', 'group_field': True,
                     'description': '分组标签'}
    return b


F = lambda n: {'op': 'field', 'name': n}


class WideDslTests(unittest.TestCase):
    def test_new_operators_compile_to_platform_names(self):
        b = bindings()
        ast = {'op': 'group_rank', 'group': 'industry',
               'arg': {'op': 'decay', 'window': 10, 'arg': {'op': 'div', 'left': F('sales_to_ev'), 'right': F('daily_return')}}}
        expr, fields, family = research_dsl.compile_ast(ast, b)
        self.assertIn('group_rank(ts_decay_linear((', expr)
        self.assertIn('/ if_else(returns > 0, returns, NaN)', expr)
        self.assertEqual(expr.count('industry'), 1)
        self.assertEqual(fields, ['enterprise_value', 'industry', 'returns', 'sales'])
        pair = {'op': 'corr', 'left': F('sales_to_ev'), 'right': F('daily_return'), 'window': 60}
        self.assertTrue(research_dsl.compile_ast(pair, b)[0].startswith('ts_corr('))

    def test_group_requires_verified_group_binding(self):
        b = bindings()
        with self.assertRaises(ValueError):
            research_dsl.compile_ast({'op': 'group_rank', 'group': 'sector', 'arg': {'op': 'mean', 'arg': F('daily_return'), 'window': 20}}, b)
        # 分组字段不能当作观测量。
        with self.assertRaises(ValueError):
            research_dsl.compile_ast({'op': 'mean', 'arg': F('industry'), 'window': 20}, b)

    def test_complexity_and_role_caps(self):
        b = bindings()
        ts = lambda a: {'op': 'mean', 'arg': a, 'window': 20}
        too_many_ts = ts(ts(ts(ts(F('daily_return')))))
        with self.assertRaisesRegex(ValueError, '过于复杂'):
            research_dsl.compile_ast(too_many_ts, b)
        roles = {'op': 'add', 'left': {'op': 'add', 'left': F('daily_return'), 'right': F('activity_rank')},
                 'right': {'op': 'add', 'left': F('market_cap_rank'), 'right': ts(F('cashflow_strength'))}}
        with self.assertRaisesRegex(ValueError, '字段角色'):
            research_dsl.compile_ast(roles, b)
        self.assertEqual(research_dsl.roles_used(roles), ['activity_rank', 'cashflow_strength', 'daily_return', 'market_cap_rank'])

    def test_window_and_ratio_guards(self):
        b = bindings()
        with self.assertRaises(ValueError):
            research_dsl.compile_ast({'op': 'decay', 'arg': F('daily_return'), 'window': 15}, b)
        with self.assertRaisesRegex(ValueError, '自除'):
            research_dsl.compile_ast({'op': 'div', 'left': F('daily_return'), 'right': F('daily_return')}, b)
        with self.assertRaises(ValueError):
            research_dsl.compile_ast({'op': 'divide', 'left': F('daily_return'), 'right': F('sales_to_ev')}, b)

    def test_public_contract_uses_policy_descriptions_without_field_names(self):
        contract = research_dsl.public_contract(bindings())
        text = str(contract)
        self.assertIn('sales_to_ev', contract['roles'])
        self.assertIn('数据簇：fundamental', contract['roles']['sales_to_ev'])
        self.assertNotIn('industry', contract['roles'])
        self.assertIn('industry', contract['ast']['group'])
        self.assertNotIn('enterprise_value', text)
        self.assertIn('failure_playbook', contract['platform_thresholds'])
        self.assertNotIn('sharpe', text.lower())
        b = bindings(); b['mystery'] = {'expression': 'x', 'fields': ['x'], 'source': 's'}
        with self.assertRaisesRegex(ValueError, '角色缺模型可读说明'):
            research_dsl.role_catalog(b)


if __name__ == '__main__':
    unittest.main()
