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


class EarlyAvoidanceTests(unittest.TestCase):
    """更早规避：本地自相关预筛、排队定时任务不阻塞、预算预测、提交频率可配置。"""

    def test_submitted_correlation_blocks_candidate_and_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); feedback.setup(c); autopilot.setup(c); autopilot.brain_jobs.setup(c)
            from wq import brain_submission; brain_submission.setup(c)
            rec = lambda k: {'schema': {'properties': [{'name': 'date'}, {'name': 'pnl'}]},
                             'records': [[f'2020-{1+i//28:02d}-{1+i%28:02d}', float(k(i))] for i in range(300)]}
            sub_pnl = Path(tmp) / 'sub.json'; util.write_json(str(sub_pnl), rec(lambda i: i * i % 97))
            tsub = store.enqueue_task(c, 'brain_submission', {}, 'k')[0]
            c.execute("INSERT INTO brain_submissions(task_id,alpha_id,sim_id,state,started_at,updated_at) VALUES(?,?,?,?,?,?)", (tsub, 'SUB', 's', 'accepted', util.now_iso(), util.now_iso()))
            c.execute("INSERT INTO research_feedback VALUES('SUB','id',?,?)", (json.dumps({'pnl_path': str(sub_pnl)}), util.now_iso()))
            same = feedback.submitted_correlation(c, rec(lambda i: i * i % 97))
            self.assertAlmostEqual(same['max'], 1.0); self.assertEqual(same['against'][0]['alpha_id'], 'SUB')
            alpha = {'id': 'X', 'is': {'sharpe': 1.5, 'fitness': 1.1, 'turnover': 0.1, 'checks': [{'name': n, 'result': 'PASS'} for n in brain_submission.REQUIRED]},
                     'train': {'sharpe': 1.5, 'fitness': 1.1}, 'test': {'sharpe': 1.5, 'fitness': 1.1}}
            yearly = {'schema': {'properties': [{'name': n} for n in ('year', 'sharpe', 'fitness', 'pnl')]}, 'records': [['2021', 1, 1, 1], ['2022', 1, 1, 1], ['2023', 1, 1, 1]]}
            clean = feedback.diagnose(alpha, yearly)
            self.assertTrue(clean['submission_candidate'])
            blocked = feedback.diagnose(alpha, yearly, submitted={'max': 0.72, 'against': [], 'missing': []})
            self.assertFalse(blocked['submission_candidate']); self.assertIn('与已提交信号高相关（本地估计）', blocked['diagnosis'])
            parent_only = feedback.diagnose(alpha, yearly, submitted={'max': 0.55, 'against': [], 'missing': []})
            self.assertTrue(parent_only['submission_candidate']); self.assertFalse(parent_only['retain_for_complementarity'])
            c.close()

    def test_queued_timer_task_does_not_block_new_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); autopilot.setup(c)
            future = (util.now() + __import__('datetime').timedelta(hours=3)).isoformat()
            store.enqueue_task(c, 'brain_submission', {}, 'k1')
            c.execute("UPDATE tasks SET not_before=?", (future,))
            blocking = c.execute("SELECT 1 FROM tasks WHERE status IN ('claimed','running','unknown') OR (status='queued' AND (not_before IS NULL OR not_before<=?)) LIMIT 1", (util.now_iso(),)).fetchone()
            self.assertIsNone(blocking)
            c.execute("UPDATE tasks SET not_before=?", ('2000-01-01T00:00:00+00:00',))
            self.assertIsNotNone(c.execute("SELECT 1 FROM tasks WHERE status IN ('claimed','running','unknown') OR (status='queued' AND (not_before IS NULL OR not_before<=?)) LIMIT 1", (util.now_iso(),)).fetchone())
            c.close()

    def test_budget_forecast_warns_before_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp, {'autopilot': {'max_simulations_per_week': 10, 'enabled': True}, 'routing': {'authorized_until': '2099-01-01T00:00:00Z'}})
            autopilot.setup(c); autopilot.brain_jobs.setup(c)
            self.assertIsNone(autopilot.budget_forecast(c, cfg)['warning'])
            for i in range(6):
                tid = store.enqueue_task(c, 'brain_simulation', {}, f'k{i}')[0]
                c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,simulation_task,created_at,updated_at) VALUES('closed','{}','h',?,?,?)", (tid, util.now_iso(), util.now_iso()))
            f = autopilot.budget_forecast(c, cfg)
            self.assertEqual(f['rate_per_day'], 6); self.assertEqual(f['left'], 4); self.assertIn('耗尽', f['warning'])
            c.close()

    def test_submission_rate_configurable(self):
        from wq import brain_submission
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp, {'brain_submission': {'max_posts_per_24h': 2}})
            self.assertEqual(int(cfg.get('brain_submission', 'max_posts_per_24h', default=1)), 2)
            cfg2, c2 = make_env(tmp + '/b')
            self.assertEqual(int(cfg2.get('brain_submission', 'max_posts_per_24h', default=1)), 1)
            c.close(); c2.close()


class CombinationSpacingTests(unittest.TestCase):
    def test_recent_roles_listed_and_plan_skipped_after_plan_cycle(self):
        from wq import research_dsl
        history = [{'cycle': 3, 'candidate': {'ast': {'op': 'mean', 'arg': {'op': 'field', 'name': 'social_buzz'}, 'window': 20}}},
                   {'cycle': 2, 'candidate': {'ast': {'op': 'field', 'name': 'peer_return'}}},
                   {'cycle': 1, 'candidate': {'ast': {'op': 'field', 'name': 'leverage'}}},
                   {'cycle': 0, 'candidate': {'ast': {'op': 'field', 'name': 'old_role'}}}]
        usage = autopilot.role_usage(history, {})
        self.assertEqual(usage['最近3轮已用角色（本轮避免再用）'], ['leverage', 'peer_return', 'social_buzz'])
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); autopilot.setup(c); feedback.setup(c)
            c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,created_at,updated_at) VALUES(1,'closed','{}','h',?,?)", (util.now_iso(), util.now_iso()))
            c.execute("INSERT INTO combination_plans VALUES('a:b',1,'{}',?)", (util.now_iso(),))
            previous = c.execute('SELECT cycle_id FROM research_cycles WHERE cycle_id<? ORDER BY cycle_id DESC LIMIT 1', (2,)).fetchone()
            self.assertTrue(c.execute('SELECT 1 FROM combination_plans WHERE cycle_id=?', (previous[0],)).fetchone())
            c.close()


class FocusRoleTests(VariantTests):
    """聚焦角色队列：前 N 轮逐个做单角色基线，提案必须只用该角色，期间不登记组合。"""
    def setUp(self):
        super().setUp()
        self.p['setting_variants'] = []
        self.p['focus_roles'] = ['activity_rank', 'market_cap_rank']
        self.save_policy()
        self.focus_ok = True

    def dispatch(self, conn, cfg, t):
        payload = json.loads(t['payload_json'])
        if t['kind'] == 'agent_call' and payload['role'] == 'research':
            row = conn.execute("SELECT * FROM research_cycles WHERE state!='closed'").fetchone()
            focus = store.get_flag(conn, f"autopilot_focus_{row['cycle_id']}")
            role = focus if (focus and self.focus_ok) else 'daily_return'
            prompt_text = Path(payload['job_dir']).joinpath('prompt.txt').read_text()
            self.assertEqual(bool(focus), f'角色 {focus}' in prompt_text if focus else True)
            cand = {'title': '聚焦角色单基线实验', 'hypothesis': '按角色更新频率选择窗口，方向为正，反例是规模因子解释。', 'counterexample': '若被市值排名完全解释则放弃，不改窗口重试。',
                    'ast': {'op': 'mean', 'arg': {'op': 'field', 'name': role}, 'window': 60}}
            util.write_json(str(Path(payload['job_dir']) / 'result.json'), {'status': 'completed', 'candidate': cand})
            conn.execute('INSERT INTO task_routes(task_id,snapshot_json,phase,updated_at) VALUES(?,?,?,?)', (t['task_id'], json.dumps({'chain': ['a'], 'preset': 'steady'}), 'complete', util.now_iso()))
            return 'succeeded', {}, None
        return super().dispatch(conn, cfg, t)

    def test_focus_queue_orders_baselines_and_rejects_other_roles(self):
        for _ in range(60): self.tick()
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertEqual(store.get_flag(self.c, 'autopilot_focus_1'), 'activity_rank')
        self.assertEqual(research_dsl.roles_used(json.loads(self.cycle()['candidate_json'])['ast']), ['activity_rank'])
        store.set_flag(self.c, 'autopilot_next_at', '2000-01-01T00:00:00Z')
        self.focus_ok = False   # 模型无视聚焦指令 → 本轮以输入错误关闭，不回测
        for _ in range(6): self.tick()
        self.assertEqual(self.cycle()['cycle_id'], 2)
        self.assertEqual(store.get_flag(self.c, 'autopilot_focus_2'), 'market_cap_rank')
        self.assertIn('聚焦角色', self.cycle()['outcome'])
        self.assertEqual(self.posts, 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM combination_plans').fetchone()[0], 0)

    def test_policy_rejects_unknown_focus_role(self):
        self.p['focus_roles'] = ['not_a_role']; self.save_policy()
        with self.assertRaises(ValueError): autopilot.policy(self.cfg)


from wq import research_dsl
for _name in list(vars(VariantTests)):
    if _name.startswith('test_'):
        setattr(FocusRoleTests, _name, None)


class PausedClusterTests(unittest.TestCase):
    def test_paused_cluster_roles_rejected_in_free_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp) / 'e.json'; util.write_json(str(proof), {'x': 1})
            b = {n: {'expression': f'rank(f_{i})', 'fields': [f'f_{i}'], 'source': 's'} for i, n in enumerate(research_dsl.ROLES)}
            b['eps_dispersion'] = {'expression': 'rank(f_9)', 'fields': ['f_9'], 'source': 's', 'description': '分析师分歧代理', 'cluster': 'analyst'}
            p = {'version': 1, 'scope': 'exploratory_only_no_submission', 'valid_until': '2099-01-01T00:00:00Z', 'verified_at': '2026-01-01T00:00:00Z',
                 'source': 's', 'bindings': b, 'settings': {'region': 'USA', 'universe': 'TOP3000', 'delay': 1, 'decay': 0, 'truncation': 0.08, 'neutralization': 'INDUSTRY'},
                 'evidence_files': [{'path': str(proof), 'sha256': util.sha256_json({'x': 1})}], 'paused_clusters': ['analyst']}
            autopilot.check_policy(p)
            bad = dict(p); bad['paused_clusters'] = 'analyst'
            with self.assertRaises(ValueError): autopilot.check_policy(bad)
