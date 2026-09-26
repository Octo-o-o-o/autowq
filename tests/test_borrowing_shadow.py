"""2026-09-26 社区借鉴第一刀：三态自相关（Sharpe 溢价假说）、检查词表、提交排序、尝试计数、影子死区/早停、
拥挤度档、只读账户快照。全部不改变派发与官方门禁。"""
import json
import tempfile
import unittest
from pathlib import Path
from helpers import make_env
from wq import feedback, autopilot, account, util, catalog
from wq.brain_submission import REQUIRED, setup as sub_setup
from wq.errors import AdapterError


def recordset(names, rows):
    return {'schema': {'properties': [{'name': n} for n in names]}, 'records': rows}


def pnl(scale=1.0, n=300):
    import datetime as dt
    rows = [[(dt.date(2020, 1, 1) + dt.timedelta(days=i)).isoformat(), scale * float((i * 7) % 11)] for i in range(n)]
    return recordset(['date', 'pnl'], rows)


def passing_alpha(aid, sharpe, fitness=1.2):
    return {'id': aid, 'regular': {'code': 'rank(x)'}, 'settings': {},
            'is': {'sharpe': sharpe, 'fitness': fitness, 'turnover': 0.1,
                   'checks': [{'name': n, 'result': 'PASS'} for n in REQUIRED]},
            'train': {'sharpe': 1.5, 'fitness': 1.1}, 'test': {'sharpe': 1.4, 'fitness': 1.05}}


def insert_sim(c, aid, sharpe, status='passed'):
    now = util.now_iso()
    c.execute("INSERT OR IGNORE INTO families(family_id,family_key,created_at) VALUES('fam','fam',?)", (now,))
    c.execute("INSERT INTO candidates(candidate_id,family_id,expression,config_json,config_hash,created_at) VALUES(?,?,?,?,?,?)",
              ('c' + aid, 'fam', 'rank(x)', '{}', 'h' + aid, now))
    c.execute("INSERT INTO simulations(sim_id,candidate_id,remote_id,source,synthetic,status,stats_json,checks_json,pnl_json,quality_json,evidence_json,observed_at,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ('s' + aid, 'c' + aid, aid, 'api', 0, status, json.dumps({'sharpe': sharpe}), '{}', None, '{}', '{}', now, now))


def insert_task(c, tid, status='succeeded'):
    now = util.now_iso()
    c.execute("INSERT INTO tasks(task_id,kind,payload_json,dedup_key,status,not_before,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
              (tid, 'brain_simulation', '{}', 'k' + tid, status, now, now, now))


YEARLY = recordset(['year', 'sharpe', 'fitness', 'pnl'], [['2021', 1.5, 1.1, 10], ['2022', 1.4, 1.0, 9], ['2023', 1.6, 1.2, 11]])


class PremiumTests(unittest.TestCase):
    def env(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        cfg, c = make_env(tmp.name); feedback.setup(c); sub_setup(c); autopilot.setup(c)
        self.addCleanup(c.close)
        return cfg, c, Path(tmp.name)

    def accept(self, c, root, aid, sharpe):
        path = root / (aid + '-pnl.json'); util.write_json(str(path), pnl())
        insert_task(c, 't' + aid)
        c.execute("INSERT INTO brain_submissions VALUES(?,?,?,?,?,?,?)", ('t' + aid, aid, 's' + aid, 'accepted', None, util.now_iso(), util.now_iso()))
        c.execute("INSERT INTO research_feedback VALUES(?,?,?,?)", (aid, 'id', json.dumps({'pnl_path': str(path)}), util.now_iso()))
        insert_sim(c, aid, sharpe)

    def test_premium_hit_removes_local_gap_but_keeps_label(self):
        cfg, c, root = self.env(); self.accept(c, root, 'peer1', 1.0)
        sub = feedback.submitted_correlation(c, pnl(), candidate_sharpe=1.2)
        self.assertAlmostEqual(sub['max'], 1.0)
        self.assertTrue(sub['premium']['hit']); self.assertAlmostEqual(sub['premium']['needed_sharpe'], 1.1)
        r = feedback.diagnose(passing_alpha('cand', 1.2), YEARLY, submitted=sub)
        self.assertIn('与已提交信号高相关（本地估计，Sharpe溢价假说命中，待官方核验）', r['diagnosis'])
        self.assertFalse(any('相关' in g for g in r['validation_gaps']))
        self.assertTrue(r['submission_candidate'])
        self.assertIn('溢价假说命中', r['correlation_state'])
        self.assertFalse(r['retain_for_complementarity'])  # >0.5 仍不作组合父信号
        self.assertAlmostEqual(r['margins']['corr_under_0.7'], -0.3)

    def test_premium_miss_or_unknown_peer_sharpe_keeps_block(self):
        cfg, c, root = self.env(); self.accept(c, root, 'peer1', 1.0)
        sub = feedback.submitted_correlation(c, pnl(), candidate_sharpe=1.05)
        self.assertFalse(sub['premium']['hit'])
        r = feedback.diagnose(passing_alpha('cand', 1.05), YEARLY, submitted=sub)
        self.assertTrue(any('>=0.7' in g for g in r['validation_gaps'])); self.assertFalse(r['submission_candidate'])
        # 同伴 Sharpe 缺失时不计算门槛，也不命中
        c.execute("UPDATE simulations SET stats_json='{}' WHERE remote_id='peer1'")
        sub = feedback.submitted_correlation(c, pnl(), candidate_sharpe=5.0)
        self.assertIsNone(sub['premium']['needed_sharpe']); self.assertFalse(sub['premium']['hit'])

    def test_low_correlation_has_no_premium_and_unknown_state_when_missing(self):
        cfg, c, root = self.env()
        sub = feedback.submitted_correlation(c, pnl(), candidate_sharpe=2.0)
        self.assertIsNone(sub['max']); self.assertIsNone(sub['premium'])
        self.assertEqual(feedback.diagnose(passing_alpha('cand', 2.0), YEARLY, submitted=sub)['correlation_state'], '未知')


class LabelsAndOrderTests(unittest.TestCase):
    def test_new_check_names_get_labels_and_warning_classes_without_changing_blockers(self):
        a = passing_alpha('x', 1.5)
        a['is']['checks'] += [{'name': 'PROD_CORRELATION', 'result': 'FAIL'}, {'name': 'MATCHES_THEMES', 'result': 'WARNING'},
                              {'name': 'HT_PNL_REALIZATION_HORIZON', 'result': 'WARNING'}, {'name': 'NEW_ONE', 'result': 'PENDING'}]
        r = feedback.diagnose(a, YEARLY)
        self.assertIn('与平台生产信号重叠', r['diagnosis'])
        self.assertEqual(r['warning_classes'], {'robustness': ['HT_PNL_REALIZATION_HORIZON'], 'classification': ['MATCHES_THEMES'], 'unknown': ['NEW_ONE']})
        self.assertIn('MATCHES_THEMES:WARNING', r['platform_blockers']); self.assertFalse(r['submission_candidate'])

    def test_submission_order_prefers_low_correlation_then_fitness(self):
        rows = [{'alpha_id': 'a', 'submission_candidate': True, 'submitted_correlation': {'max': 0.6}, 'margins': {'fitness_over_1.0': 0.5, 'sharpe_over_1.25': 0.1}, 'correlation_state': 's', 'temporal': []},
                {'alpha_id': 'b', 'submission_candidate': True, 'submitted_correlation': {'max': 0.2}, 'margins': {'fitness_over_1.0': 0.1, 'sharpe_over_1.25': 0.1}, 'correlation_state': 's', 'temporal': []},
                {'alpha_id': 'c', 'submission_candidate': True, 'submitted_correlation': {'max': None}, 'margins': {}, 'correlation_state': '未知', 'temporal': []},
                {'alpha_id': 'd', 'submission_candidate': False, 'submitted_correlation': {'max': 0.8, 'premium': {'hit': True}}, 'margins': {}, 'correlation_state': 'p', 'temporal': []},
                {'alpha_id': 'e', 'submission_candidate': False, 'submitted_correlation': {'max': 0.1}, 'margins': {}, 'temporal': []},
                {'alpha_id': 'f', 'submission_candidate': True, 'platform_submission': 'accepted', 'submitted_correlation': {'max': 0.0}, 'margins': {}, 'temporal': []}]
        order = feedback.submission_order(rows)
        self.assertEqual([x['alpha_id'] for x in order], ['b', 'a', 'd', 'c'])
        self.assertEqual(order[2]['basis'], 'premium_hypothesis_only')


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg, self.c = make_env(self.tmp.name); feedback.setup(self.c); autopilot.setup(self.c)
        self.addCleanup(self.c.close)
        self.c.execute('CREATE TABLE brain_runs(task_id TEXT,alpha_id TEXT)')

    def cycle(self, cid, roles, sharpe, status='failed', family='fam-' + 'x', diagnosis='收益风险比不足'):
        ast = {'op': 'mean', 'arg': {'op': 'field', 'name': roles[0]}, 'window': 20}
        for extra in roles[1:]: ast = {'op': 'add', 'left': ast, 'right': {'op': 'field', 'name': extra}}
        self.c.execute("INSERT INTO research_cycles(cycle_id,state,policy_json,policy_hash,candidate_json,family_hash,simulation_task,created_at,updated_at) VALUES(?,'closed','{}','h',?,?,?,?,?)",
                       (cid, json.dumps({'ast': ast}), family, 'task' + str(cid), util.now_iso(), util.now_iso()))
        insert_task(self.c, 'task' + str(cid))
        self.c.execute('INSERT INTO brain_runs VALUES(?,?)', ('task' + str(cid), 'alpha' + str(cid)))
        insert_sim(self.c, 'alpha' + str(cid), sharpe, status)
        self.c.execute("INSERT INTO research_feedback VALUES(?,?,?,?)", ('alpha' + str(cid), 'id', json.dumps({'alpha_id': 'alpha' + str(cid), 'diagnosis': [diagnosis], 'retain_for_complementarity': False, 'validation_gaps': [], 'submission_candidate': False, 'temporal': []}), util.now_iso()))

    def test_dead_zone_requires_three_weak_observations_and_no_pass(self):
        for cid in (1, 2): self.cycle(cid, ['daily_return'], 0.1)
        self.assertEqual(feedback.shadow_statistics(self.c)['dead_zones'], [])
        self.cycle(3, ['daily_return'], -0.2)
        dead = feedback.shadow_statistics(self.c)['dead_zones']
        self.assertEqual(len(dead), 1); self.assertEqual(dead[0]['roles'], ['daily_return']); self.assertEqual(dead[0]['observations'], 3)
        # 同角色集合出现一次强结果或通过，就不再是死区
        self.cycle(4, ['daily_return'], 1.3, status='passed')
        self.assertEqual(feedback.shadow_statistics(self.c)['dead_zones'], [])
        # 不同角色集合不合并
        for cid in (5, 6, 7): self.cycle(cid, ['daily_return', 'activity_rank'], 0.05)
        self.assertEqual(feedback.shadow_statistics(self.c)['dead_zones'][0]['roles'], ['activity_rank', 'daily_return'])

    def test_early_stop_checkpoint_only_when_five_fail_with_three_same_diagnosis(self):
        for cid in range(1, 5): self.cycle(cid, ['daily_return'], 0.1)
        self.assertIsNone(feedback.shadow_statistics(self.c)['early_stop'])
        self.cycle(5, ['activity_rank'], 0.2, diagnosis='换手过高')
        early = feedback.shadow_statistics(self.c)['early_stop']
        self.assertTrue(early['checkpoint']); self.assertEqual(early['dominant_count'], 4)
        self.cycle(6, ['activity_rank'], 1.5, status='passed')
        self.assertFalse(feedback.shadow_statistics(self.c)['early_stop']['checkpoint'])

    def test_attempt_counts_by_family_include_variants(self):
        self.cycle(1, ['daily_return'], 0.1, family='famA'); self.cycle(2, ['daily_return'], 0.3, family='famA')
        insert_task(self.c, 'v1', 'unknown')
        self.c.execute("INSERT INTO cycle_simulations VALUES(1,'decay8','v1','p',?)", (util.now_iso(),))
        counts = feedback.attempt_counts(self.c)
        self.assertEqual(counts['famA']['attempts'], 3); self.assertEqual(counts['famA']['by_label'], {'base': 2, 'decay8': 1})
        self.assertEqual(counts['famA']['unknown'], 1); self.assertEqual(counts['famA']['best_sharpe'], 0.3)
        report = feedback.report(self.c)
        self.assertIn('attempt_counts', report); self.assertIn('shadow', report); self.assertEqual(report['submission_order'], [])

    def test_prompt_carries_shadow_and_crowding_as_material(self):
        for cid in (1, 2, 3): self.cycle(cid, ['daily_return'], 0.1)
        shadow = autopilot.shadow_context(self.c)
        self.assertEqual(shadow['影子死区（≥3次真实结果全部无信号；不是禁令，是需要换信息来源的材料）'][0]['角色集合'], ['daily_return'])
        prompt = autopilot.generate_prompt(self.c, crowding={'daily_return': '高'}, shadow=shadow)
        self.assertIn('角色拥挤度档', prompt); self.assertIn('影子统计（材料，非指令）', prompt); self.assertIn('daily_return', prompt)
        self.assertNotIn('拥挤度档', autopilot.generate_prompt(self.c))

    def test_role_crowding_uses_catalog_terciles_without_leaking_field_ids(self):
        p = {'settings': {'region': 'USA', 'universe': 'TOP3000', 'delay': 1},
             'bindings': {'a': {'fields': ['f_low']}, 'b': {'fields': ['f_low', 'f_high']}, 'g': {'group_field': True, 'fields': ['industry']}, 'z': {'fields': ['absent']}}}
        self.assertIsNone(autopilot.role_crowding(self.cfg, p))
        query = catalog.query_from_settings(p['settings'])
        fields = [{'id': f'f{i}', 'type': 'MATRIX', 'alphaCount': i * 10} for i in range(9)]
        fields += [{'id': 'f_low', 'type': 'MATRIX', 'alphaCount': 0}, {'id': 'f_high', 'type': 'MATRIX', 'alphaCount': 1000}]
        util.write_json(catalog.catalog_path(self.cfg.private_dir, query), {'schema': catalog.CATALOG_SCHEMA, 'query': query, 'fields': fields})
        crowd = autopilot.role_crowding(self.cfg, p)
        self.assertEqual(crowd, {'a': '低', 'b': '高'})
        self.assertNotIn('f_high', json.dumps(crowd))


class AccountSnapshotTests(unittest.TestCase):
    def test_snapshot_records_unavailable_endpoints_and_strips_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); c.close()
            calls = []
            def get(client, cfg_, path):
                calls.append(path)
                if path == '/users/self': return 200, {}, {'id': 'u1', 'email': 'x@y', 'level': 'GOLD', 'permissions': ['MULTI_SIMULATION']}
                if path.endswith('pyramid-multipliers'): return 200, {}, {'pyramids': [{'category': {'id': 'option'}, 'region': 'USA', 'delay': 1, 'multiplier': 1.5}, {'category': {'id': 'pv'}, 'region': 'USA', 'delay': 1, 'multiplier': 1.0}]}
                if path.endswith('pyramid-alphas'): raise AdapterError(AdapterError.POLICY, 'BRAIN HTTP 404')
                return 200, {}, {'current': 3}
            snap = account.snapshot(get, object(), cfg)
            self.assertEqual(calls, [p for p, _ in account.ENDPOINTS])
            self.assertNotIn('email', json.dumps(snap))
            saved = util.read_json(snap['saved_to']); self.assertEqual(saved['schema'], 'wq.account-snapshot/v1')
            s = account.summary(snap)
            self.assertEqual(s['level'], {'level': 'GOLD'}); self.assertEqual(s['permissions'], ['MULTI_SIMULATION'])
            self.assertEqual([p['category'] for p in s['pyramids']], ['option', 'pv']); self.assertIsNone(s['pyramids'][0]['alpha_count'])
            self.assertTrue(str(s['available']['pyramid_alphas']).startswith('unavailable'))
            self.assertEqual(s['streak'], {'current': 3})

    def test_snapshot_stops_after_auth_or_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); c.close()
            calls = []
            def get(client, cfg_, path):
                calls.append(path); raise AdapterError(AdapterError.RATE_LIMIT, '限流', retry_after=60)
            snap = account.snapshot(get, object(), cfg)
            self.assertEqual(len(calls), 1); self.assertEqual(snap['endpoints']['profile']['kind'], 'rate_limit')


if __name__ == '__main__':
    unittest.main()
