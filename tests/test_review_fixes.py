"""交叉审查后补的回归：轮次终态统一判定、条件冻结、字段名边界、登记校验、配置有限数。"""
import json
import math
import tempfile
import unittest
from pathlib import Path
from wq import autopilot, catalog, feedback, store, util
from helpers import make_env
from test_autopilot_variants import VariantTests


class CycleTerminalTests(VariantTests):
    def setUp(self):
        super().setUp()
        self.p['setting_variants'] = [{'label': 'decay8', 'decay': 8}]
        self.save_policy()

    def run_until_simulating(self):
        for _ in range(6):
            if self.cycle_or_none() and self.cycle()['state'] == 'simulating': break
            self.tick()
        self.assertEqual(self.cycle()['state'], 'simulating')

    def cycle_or_none(self):
        return self.c.execute('SELECT 1 FROM research_cycles').fetchone()

    def test_base_failure_waits_for_active_variant(self):
        self.run_until_simulating()
        base = self.cycle()['simulation_task']
        variant = self.c.execute('SELECT task_id FROM cycle_simulations').fetchone()[0]
        # 基础任务在 POST 前失败（没有远端回执），变体仍在途。
        self.c.execute("DELETE FROM brain_runs WHERE task_id=?", (base,))
        self.c.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (base,))
        self.c.execute("UPDATE tasks SET status='running' WHERE task_id=?", (variant,))
        autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.cycle()['state'], 'simulating')
        self.assertIn('等待 decay8', store.get_flag(self.c, 'autopilot_message'))
        self.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?", (variant,))
        autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.cycle()['state'], 'simulating')
        self.assertIn('需要对账', store.get_flag(self.c, 'autopilot_message'))
        self.c.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (variant,))
        autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertIn('任务未完成', self.cycle()['outcome'])

    def test_blocked_variant_with_remote_receipt_freezes_cycle(self):
        self.run_until_simulating()
        for _ in range(3): self.tick()   # base posted and imported
        variant = self.c.execute('SELECT task_id FROM cycle_simulations').fetchone()[0]
        self.c.execute("UPDATE tasks SET status='blocked' WHERE task_id=?", (variant,))
        self.c.execute("INSERT OR REPLACE INTO brain_runs(task_id,state,started_at,updated_at) VALUES(?,?,?,?)", (variant, 'polling', util.now_iso(), util.now_iso()))
        autopilot.tick(self.c, self.cfg)
        self.assertEqual(self.cycle()['state'], 'simulating')
        self.assertIn('需要恢复已有平台请求', store.get_flag(self.c, 'autopilot_message'))

    def test_rescue_threshold_frozen_in_protocol(self):
        self.sharpe = -0.75
        self.run_until_simulating()
        protocol = util.read_json(str(self.root / 'private/research-approvals/auto-1/protocol.json'))
        self.assertEqual(protocol['variant_conditions'][autopilot.RESCUE_LABEL], {'max_sharpe': -0.8})
        self.cfg.data['autopilot']['sign_flip_rescue_sharpe'] = -0.6   # 事后放宽不影响本轮
        for _ in range(60): self.tick()
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertNotIn(autopilot.RESCUE_LABEL, [r[0] for r in self.c.execute('SELECT label FROM cycle_simulations')])

    def test_conditional_variant_dispatches_only_when_condition_met(self):
        self.p['setting_variants'] = [{'label': 'decay8', 'decay': 8, 'when': {'min_turnover': 0.2}}]
        self.save_policy()
        self.turnover = 0.05
        for _ in range(60): self.tick()
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertEqual(self.posts, 1)
        self.assertEqual([r[0] for r in self.c.execute('SELECT label FROM cycle_simulations')], [])
        store.set_flag(self.c, 'autopilot_next_at', '2000-01-01T00:00:00Z')
        self.turnover = 0.3
        for _ in range(60): self.tick()
        self.assertEqual(self.cycle()['cycle_id'], 2)
        self.assertEqual(self.posts, 3)
        self.assertEqual([r[0] for r in self.c.execute('SELECT label FROM cycle_simulations WHERE cycle_id=2')], ['decay8'])

    def test_week_budget_counts_variants_and_skips_when_exhausted(self):
        self.cfg.data['autopilot']['max_simulations_per_week'] = 2
        for _ in range(60): self.tick()
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertEqual(self.posts, 2)   # base + decay8 uses the whole budget
        store.set_flag(self.c, 'autopilot_next_at', '2000-01-01T00:00:00Z')
        self.tick()
        self.assertIn('周模拟上限', store.get_flag(self.c, 'autopilot_message'))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM research_cycles').fetchone()[0], 1)

    def api(self, method, path, body=None):
        code, headers, data = super().api(method, path, body)
        if isinstance(data, dict) and 'is' in data and hasattr(self, 'turnover'):
            data['is']['turnover'] = self.turnover
        return code, headers, data


for _name in list(vars(VariantTests)):
    if _name.startswith('test_'):
        setattr(CycleTerminalTests, _name, None)


class PolicyBoundaryTests(unittest.TestCase):
    def policy(self, tmp):
        proof = Path(tmp) / 'evidence.json'; util.write_json(str(proof), {'fixture': True})
        b = {n: {'expression': f'rank(f_{i})', 'fields': [f'f_{i}'], 'source': 's'} for i, n in enumerate(autopilot.research_dsl.ROLES)}
        return {'version': 1, 'scope': 'exploratory_only_no_submission', 'valid_until': '2099-01-01T00:00:00Z',
                'verified_at': '2026-01-01T00:00:00Z', 'source': 's', 'bindings': b,
                'settings': {'region': 'USA', 'universe': 'TOP3000', 'delay': 1, 'decay': 0, 'truncation': 0.08, 'neutralization': 'INDUSTRY'},
                'evidence_files': [{'path': str(proof), 'sha256': util.sha256_json({'fixture': True})}]}

    def test_visible_text_cannot_contain_field_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.policy(tmp); autopilot.check_policy(p)
            bad = json.loads(json.dumps(p)); bad['bindings']['f_1'] = {'expression': 'rank(f_1)', 'fields': ['f_1'], 'source': 's', 'description': '角色名等于字段'}
            with self.assertRaisesRegex(ValueError, '模型可见文本含平台字段ID'): autopilot.check_policy(bad)
            bad = json.loads(json.dumps(p)); bad['bindings']['x'] = {'expression': 'rank(f_2)', 'fields': ['f_2'], 'source': 's', 'description': '说明里提到 f_0 字段'}
            with self.assertRaisesRegex(ValueError, '模型可见文本含平台字段ID'): autopilot.check_policy(bad)
            bad = json.loads(json.dumps(p)); bad['setting_variants'] = [{'label': 'f_0', 'decay': 4}]
            with self.assertRaisesRegex(ValueError, '模型可见文本含平台字段ID'): autopilot.check_policy(bad)
            bad = json.loads(json.dumps(p)); bad['bindings']['industry'] = {'expression': 'rank(g)', 'fields': ['g'], 'source': 's', 'description': 'd'}
            with self.assertRaises(ValueError): autopilot.check_policy(bad)

    def test_binding_expressions_are_parsed(self):
        with self.assertRaisesRegex(ValueError, '未声明字段'):
            catalog.check_expression('ts_delay(f10 + secret_field, 5)', ['f1'])
        with self.assertRaisesRegex(ValueError, '负数'):
            catalog.check_expression('ts_delay(f1, -5)', ['f1'])
        with self.assertRaisesRegex(ValueError, '未核验算子'):
            catalog.check_expression('submit(f1)', ['f1'])
        with self.assertRaisesRegex(ValueError, '未使用字段'):
            catalog.check_expression('rank(f1)', ['f1', 'f2'])
        catalog.check_expression('(f1 / if_else(f2 > 0, f2, NaN))', ['f1', 'f2'])
        with self.assertRaises(ValueError):
            catalog.check_expression('rank(industry)', ['industry'], group_field=True)
        catalog.check_expression('industry', ['industry'], group_field=True)

    def test_add_role_is_atomic_and_checks_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / 'policy.json'; p = self.policy(tmp); util.write_json(str(policy), p)
            query = catalog.query_from_settings(p['settings'])
            snap = catalog.evidence_path(tmp, 'vec', query)
            util.write_json(snap, {'schema': catalog.SNAPSHOT_SCHEMA, 'query': query, 'field': {'id': 'vec', 'type': 'VECTOR'}, 'context': [{'universe': 'TOP3000'}]})
            with self.assertRaisesRegex(ValueError, '字段类型须为MATRIX'):
                catalog.add_role(str(policy), 'vec_role', 'rank(vec)', ['vec'], '过去已知的向量字段代理', [snap])
            snap2 = catalog.evidence_path(tmp, 'ok', query)
            util.write_json(snap2, {'schema': catalog.SNAPSHOT_SCHEMA, 'query': query, 'field': {'id': 'ok', 'type': 'MATRIX'}, 'context': [{'universe': 'TOP3000'}]})
            before = util.read_json(str(policy))
            with self.assertRaises(ValueError):   # check() 拒绝 → 不落盘
                catalog.add_role(str(policy), 'ok_role', 'rank(ok)', ['ok'], '说明含字段 ok 的名字', [snap2], check=autopilot.check_policy)
            self.assertEqual(util.read_json(str(policy)), before)
            catalog.add_role(str(policy), 'ok_role', 'rank(ok)', ['ok'], '过去已知的观测量代理', [snap2], check=autopilot.check_policy)
            self.assertIn('ok_role', util.read_json(str(policy))['bindings'])

    def test_segment_rules_reject_nan_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp, {'research_feedback': {'segment_rules': {'min_sharpe': float('nan')}}})
            with self.assertRaises(ValueError): feedback.segment_rules(cfg)
            cfg.data['research_feedback']['segment_rules'] = {'min_years': 2.5}
            with self.assertRaises(ValueError): feedback.segment_rules(cfg)
            cfg.data['research_feedback']['segment_rules'] = {'bogus': 1}
            with self.assertRaises(ValueError): feedback.segment_rules(cfg)
            cfg.data['research_feedback']['segment_rules'] = {'min_sharpe': 0.5, 'max_negative_years': 1}
            self.assertEqual(feedback.segment_rules(cfg)['max_negative_years'], 1)
            c.close()
        self.assertFalse(autopilot.condition_met({'max_sharpe': -0.8}, {'sharpe': float('nan')}))
        self.assertFalse(autopilot.condition_met({'max_sharpe': -0.8}, {}))
        self.assertTrue(autopilot.condition_met({'max_sharpe': -0.8, 'min_turnover': 0.01}, {'sharpe': -0.9, 'turnover': 0.05}))

    def test_model_context_marks_missing_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); autopilot.setup(c); feedback.setup(c); autopilot.brain_jobs.setup(c)
            t_base = store.enqueue_task(c, 'brain_simulation', {}, 'k1')[0]
            t_var = store.enqueue_task(c, 'brain_simulation', {}, 'k2')[0]
            c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,simulation_task,created_at,updated_at) VALUES(1,'closed','{}','x',?,?,?)", (t_base, util.now_iso(), util.now_iso()))
            c.execute("INSERT INTO cycle_simulations VALUES(1,'decay8',?,'p',?)", (t_var, util.now_iso()))
            for tid, aid in ((t_base, 'a_base'), (t_var, 'a_var')):
                c.execute("INSERT INTO brain_runs(task_id,state,alpha_id,started_at,updated_at) VALUES(?,?,?,?,?)", (tid, 'complete', aid, util.now_iso(), util.now_iso()))
            c.execute("INSERT INTO research_feedback VALUES('a_var','id',?,?)", (json.dumps({'diagnosis': ['收益效率不足'], 'retain_for_complementarity': True, 'bands': {'换手档': '0.01–0.2'}}), util.now_iso()))
            ctx = feedback.model_context(c)
            self.assertEqual(ctx[0]['diagnosis'], ['基础结果资料缺失'])
            self.assertFalse(ctx[0]['retain_for_complementarity'])
            self.assertEqual(ctx[0]['variants'][0]['label'], 'decay8')
            c.close()


if __name__ == '__main__':
    unittest.main()
