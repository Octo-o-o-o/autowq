"""预登记设置变体与符号翻转复核：全部结果入账，只在预登记清单内派发。"""
import datetime as dt
import json
import unittest
from wq import autopilot, feedback, research_gate, util
from test_autopilot import AutopilotTests


class VariantTests(AutopilotTests):
    # 只运行本文件的用例；父类用例在 test_autopilot 中已覆盖单请求路径。
    def setUp(self):
        super().setUp()
        self.p['setting_variants'] = [{'label': 'decay8', 'decay': 8}, {'label': 'subindustry', 'neutralization': 'SUBINDUSTRY'}]
        self.save_policy()
        self.cfg.data['research_feedback'] = {'enabled': True}
        fb = self.add_patch('wq.feedback.BrainClient').return_value
        fb.jar = [True]; fb.preflight.return_value = (200, {}, {}); fb.request.side_effect = self.api
        self.sharpe = 0.1

    def api(self, method, path, body=None):
        if method == 'POST':
            self.posts += 1; self.last_request = body
            self.requests = getattr(self, 'requests', {}); self.requests[str(self.posts)] = body
            return 201, {'location': f'/simulations/test{self.posts}'}, {}
        if '/simulations/test' in path:
            return 200, {}, {'alpha': 'alpha' + path.rsplit('test', 1)[1]}
        if path.endswith('/recordsets/pnl'):
            rows = [[(dt.date(2020, 1, 1) + dt.timedelta(days=i)).isoformat(), float(i % 7)] for i in range(300)]
            return 200, {}, {'schema': {'properties': [{'name': 'date'}, {'name': 'pnl'}]}, 'records': rows}
        if path.endswith('/recordsets/yearly-stats'):
            return 200, {}, {'schema': {'properties': [{'name': n} for n in ('year', 'sharpe', 'fitness', 'pnl')]},
                             'records': [['2021', 0.2, 0.1, 5.0], ['2022', 0.2, 0.1, 5.0], ['2023', 0.2, 0.1, 5.0]]}
        n = path.split('/alphas/alpha', 1)[1].split('/')[0]
        request = self.requests[n]
        alpha = {'id': 'alpha' + n, 'regular': {'code': request['regular']},
                 'settings': request['settings'],
                 'is': {'sharpe': self.sharpe, 'fitness': 0.05, 'turnover': 0.3, 'checks': [{'name': 'LOW_SHARPE', 'result': 'FAIL'}]}}
        return 200, {}, alpha

    def sims(self):
        return [dict(r) for r in self.c.execute('SELECT label,task_id FROM cycle_simulations ORDER BY created_at')]

    def test_variants_are_preregistered_and_all_recorded(self):
        for _ in range(60): self.tick()
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertEqual(self.posts, 3)
        self.assertEqual([s['label'] for s in self.sims()], ['decay8', 'subindustry'])
        settings = [json.loads(r[0])['request']['settings'] for r in self.c.execute('SELECT payload_json FROM tasks WHERE kind="brain_simulation"')]
        self.assertEqual(sorted(s['decay'] for s in settings), [0, 0, 8])
        self.assertIn('SUBINDUSTRY', [s['neutralization'] for s in settings])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM simulations WHERE synthetic=0').fetchone()[0], 3)
        self.assertIn('变体：', self.cycle()['outcome'])
        review = util.read_json(str(self.root / 'private/research-approvals/auto-1/review.json'))
        self.assertEqual(len(review['allowed_request_hashes']), 4)  # base + 2 variants + sign flip
        # 未预登记的第四种设置被门禁拒绝
        doc = util.read_json(str(self.root / 'private/research-approvals/auto-1/request.json'))
        doc['request']['settings'] = {**doc['request']['settings'], 'decay': 30}
        doc['config']['decay'] = 30
        with self.assertRaisesRegex(ValueError, '有限清单'):
            research_gate.validate(self.cfg, doc)

    def test_sign_flip_only_when_base_is_strongly_negative_and_once(self):
        self.sharpe = -1.2
        for _ in range(60): self.tick()
        labels = [s['label'] for s in self.sims()]
        self.assertEqual(labels, ['decay8', 'subindustry', autopilot.RESCUE_LABEL])
        self.assertEqual(self.posts, 4)
        rescue = json.loads(self.c.execute("SELECT payload_json FROM tasks t JOIN cycle_simulations s ON s.task_id=t.task_id WHERE s.label=?", (autopilot.RESCUE_LABEL,)).fetchone()[0])
        self.assertTrue(rescue['request']['regular'].startswith('reverse('))
        self.assertEqual(self.cycle()['state'], 'closed')
        self.assertIn(autopilot.RESCUE_LABEL, self.cycle()['outcome'])
        # 翻转结果也进入模型上下文；只有档位与标签，没有精确数值
        context = feedback.model_context(self.c)
        self.assertEqual(context[0]['cycle'], 1)
        self.assertEqual({v['label'] for v in context[0]['variants']}, set(labels))
        self.assertNotIn('-1.2', json.dumps(context, ensure_ascii=False))
        self.assertIn('收益风险比档', context[0]['bands'])

    def test_no_sign_flip_when_disabled_or_mild(self):
        self.cfg.data['autopilot']['sign_flip_rescue_sharpe'] = None
        self.sharpe = -1.2
        for _ in range(60): self.tick()
        self.assertEqual(self.posts, 3)
        self.assertNotIn(autopilot.RESCUE_LABEL, [s['label'] for s in self.sims()])

    def test_invalid_variants_block_policy(self):
        for bad in ([{'label': 'x', 'universe': 'TOP500'}], [{'label': 'same', 'decay': 0}],
                    [{'label': 'a', 'decay': 1}, {'label': 'b', 'decay': 2}, {'label': 'c', 'decay': 3}],
                    [{'label': 'n', 'neutralization': 'PLANET'}], [{'label': 'base', 'decay': 4}]):
            self.p['setting_variants'] = bad; self.save_policy()
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                autopilot.policy(self.cfg)


for _name in list(vars(AutopilotTests)):
    if _name.startswith('test_'):
        setattr(VariantTests, _name, None)


if __name__ == '__main__':
    unittest.main()
