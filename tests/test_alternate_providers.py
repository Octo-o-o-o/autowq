"""单双轮互换研究/审查渠道：单数轮 A 研究 B 审查，双数轮 B 研究 A 审查；只改优先顺序，不改渠道不同的约束。"""
import json
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import autopilot, routing, store, util
from test_autopilot import AutopilotTests


class AlternateOrderTests(unittest.TestCase):
    def test_alternate_order_by_parity_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp, {'autopilot': {'alternate_research_providers': ['grok', 'devin']}}); c.close()
            self.assertEqual(autopilot.alternate_order(cfg, 1, 'research'), ['grok', 'devin'])
            self.assertEqual(autopilot.alternate_order(cfg, 1, 'review'), ['devin', 'grok'])
            self.assertEqual(autopilot.alternate_order(cfg, 2, 'research'), ['devin', 'grok'])
            self.assertEqual(autopilot.alternate_order(cfg, 2, 'review'), ['grok', 'devin'])
            cfg.data['autopilot'].pop('alternate_research_providers')
            self.assertIsNone(autopilot.alternate_order(cfg, 2, 'research'))
            cfg.data['autopilot']['alternate_research_providers'] = ['grok', 'grok']
            with self.assertRaisesRegex(ValueError, '两个不同渠道'):
                autopilot.alternate_order(cfg, 1, 'research')

    def test_snapshot_reorders_only_preset_members_and_keeps_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp)
            data = {'default': 'core-only', 'presets': {'core-only': {'routes': {'research': ['grok', 'devin'], 'review': ['devin', 'grok']}}},
                    'providers': {'grok': {'cli': 'x'}, 'devin': {'cli': 'y'}, 'codex': {'cli': 'z'}}}
            with patch('wq.routing.catalog', return_value=data), patch('wq.routing.active_preset', return_value='core-only'):
                tid, _ = store.enqueue_task(c, 'agent_call', {'role': 'research'}, dedup_key='j1')
                row = routing._snapshot(c, cfg, tid, {'role': 'research', 'provider_order': ['devin', 'codex', 'grok']})
                self.assertEqual(json.loads(row['snapshot_json'])['chain'], ['devin', 'grok'])  # codex 不在预设，不引入
                tid2, _ = store.enqueue_task(c, 'agent_call', {'role': 'review'}, dedup_key='j2')
                row = routing._snapshot(c, cfg, tid2, {'role': 'review', 'provider_order': ['grok', 'devin'], 'excluded_providers': ['devin']})
                self.assertEqual(json.loads(row['snapshot_json'])['chain'], ['grok'])
                tid3, _ = store.enqueue_task(c, 'agent_call', {'role': 'review'}, dedup_key='j3')
                row = routing._snapshot(c, cfg, tid3, {'role': 'review'})
                self.assertEqual(json.loads(row['snapshot_json'])['chain'], ['devin', 'grok'])  # 无 order 时保持预设
            c.close()


class AlternateCycleTests(AutopilotTests):
    def setUp(self):
        super().setUp()
        self.cfg.data['autopilot']['alternate_research_providers'] = ['a', 'b']
        util.write_json(self.cfg.path,self.cfg.data)

    def payload(self, tid):
        return json.loads(self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?', (tid,)).fetchone()[0])

    def test_odd_and_even_cycles_swap_preferred_providers(self):
        self.full_cycle()
        one = self.cycle(); self.assertEqual(one['cycle_id'], 1); self.assertEqual(one['state'], 'closed')
        self.assertEqual(self.payload(one['research_task'])['provider_order'], ['a', 'b'])
        review = self.payload(one['review_task'])
        self.assertEqual(review['provider_order'], ['b', 'a']); self.assertEqual(review['excluded_providers'], ['a'])
        store.set_flag(self.c, 'autopilot_next_at', '2000-01-01T00:00:00Z')
        self.full_cycle()
        two = self.cycle(); self.assertEqual(two['cycle_id'], 2)
        self.assertEqual(self.payload(two['research_task'])['provider_order'], ['b', 'a'])
        self.assertEqual(self.payload(two['review_task'])['provider_order'], ['a', 'b'])
        plans = [json.loads(r[0]) for r in self.c.execute("SELECT detail FROM research_events WHERE kind='route_plan' ORDER BY event_id")]
        self.assertEqual([(x['parity'], x['research_preferred']) for x in plans], [('odd', 'a'), ('even', 'b')])

    def test_without_config_no_order_and_no_event(self):
        self.cfg.data['autopilot'].pop('alternate_research_providers')
        self.full_cycle()
        self.assertNotIn('provider_order', self.payload(self.cycle()['research_task']))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM research_events WHERE kind='route_plan'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()


class ReviewReasonLengthTests(unittest.TestCase):
    def test_long_reason_is_a_valid_rejection_not_an_input_error(self):
        checks = {k: True for k in autopilot.REVIEW_CHECKS}; checks['measurement_valid'] = False
        obj = {'review': {'candidate_hash': 'd', 'accept': False, 'checks': checks, 'reason': '理' * 2093}}
        self.assertFalse(autopilot.validate_review(obj, 'd'))
        obj['review']['reason'] = '   短  '
        with self.assertRaisesRegex(ValueError, '缺具体审查理由'):
            autopilot.validate_review(obj, 'd')
