import copy
import json
from pathlib import Path
import unittest

from wq import autopilot, desktop, util
import test_autopilot


class ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.candidate = test_autopilot.proposal()
        self.candidate['hypothesis'] = '高分端可以由单腿极端构成，两腿同时高的读法不成立。'
        self.obj = {'review': {'candidate_hash': 'digest', 'accept': False,
                    'checks': {key: key != 'measurement_valid' for key in autopilot.REVIEW_CHECKS},
                    'reason': '存在测量定义不一致，需要核对原句与表达式。',
                    'blocking_evidence': [{'check': 'measurement_valid', 'field': 'hypothesis',
                        'quote': self.candidate['hypothesis'], 'explanation': '测试引用绑定，不证明这项语义裁决正确。'}]}}

    def validate(self, obj):
        return autopilot.validate_review(obj, 'digest', self.candidate, 2)

    def test_grounded_rejection_does_not_become_acceptance(self):
        self.assertFalse(self.validate(self.obj))

    def test_fabricated_quote_and_uncovered_or_duplicate_checks_need_review(self):
        for change in ('quote', 'missing', 'duplicate', 'field', 'explanation', 'check'):
            obj = copy.deepcopy(self.obj)
            evidence = obj['review']['blocking_evidence']
            if change == 'quote': evidence[0]['quote'] = '高分端必然要求两腿同时高，这是候选所写的断言。'
            if change == 'missing': evidence.clear()
            if change == 'duplicate':
                obj['review']['checks']['economic_rationale'] = False
                evidence.append(copy.deepcopy(evidence[0]))
            if change == 'field': evidence[0]['field'] = 'history'
            if change == 'explanation': evidence[0]['explanation'] = '拒绝'
            if change == 'check': evidence[0]['check'] = []
            with self.subTest(change=change), self.assertRaises(autopilot.ReviewEvidenceError):
                self.validate(obj)

    def test_contradictory_decisions_and_unexpected_evidence_never_pass(self):
        self.obj['review']['accept'] = True
        with self.assertRaises(autopilot.ReviewEvidenceError): self.validate(self.obj)
        self.obj['review']['checks']['measurement_valid'] = True
        with self.assertRaises(autopilot.ReviewEvidenceError): self.validate(self.obj)
        self.obj['review']['blocking_evidence'] = []
        self.assertTrue(self.validate(self.obj))
        self.obj['review']['accept'] = False
        with self.assertRaises(autopilot.ReviewEvidenceError): self.validate(self.obj)

    def test_legacy_rejection_and_long_reasons_remain_valid(self):
        del self.obj['review']['blocking_evidence']
        self.obj['review']['reason'] *= 150
        self.assertFalse(autopilot.validate_review(self.obj, 'digest'))
        with self.assertRaises(ValueError): autopilot.validate_review(self.obj, 'other')
        with self.assertRaises(ValueError): autopilot.validate_review(self.obj, 'digest', contract_version=3)

    def test_prompt_distinguishes_hypotheses_and_fixed_combinations(self):
        prompt = autopilot.review_prompt(self.candidate, combination={'ast': self.candidate['ast']})
        for text in ('反例、未做控制/样本外', '完整原句', '不能仅因复用父式', 'blocking_evidence', '程序只核验引用存在'):
            self.assertIn(text, prompt)


class ReviewCycleTests(unittest.TestCase):
    def setUp(self):
        self.f = test_autopilot.AutopilotTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def review_artifact(self):
        f = self.f
        f.tick(); f.tick()
        row = f.cycle()
        payload = json.loads(autopilot.task(f.c, row['review_task'])['payload_json'])
        self.assertEqual(payload['review_contract_version'], 2)
        return row, Path(payload['job_dir']) / 'result.json'

    def test_bad_evidence_closes_without_simulation_or_retry(self):
        f = self.f
        f.reject_review = True
        row, path = self.review_artifact()
        obj = util.read_json(str(path))
        obj['review']['blocking_evidence'][0]['quote'] = '候选从未声称已经验证因果与稳定盈利。'
        util.write_json(str(path), obj)
        f.tick()
        self.assertEqual(f.counter, 3)
        payload = json.loads(autopilot.task(f.c, f.cycle()['review_task'])['payload_json'])
        second = Path(payload['job_dir'])/'result.json'
        obj = util.read_json(str(second)); obj['review'].pop('resolutions', None)
        util.write_json(str(second), obj)
        f.tick()
        self.assertEqual(f.cycle()['state'], 'closed')
        self.assertIn('审查依据待复核', f.cycle()['outcome'])
        self.assertEqual((f.posts, f.counter), (0, 3))
        self.assertIsNone(f.cycle()['simulation_task'])

    def test_old_pending_job_keeps_old_contract(self):
        f = self.f
        f.reject_review = True
        row, path = self.review_artifact()
        task = autopilot.task(f.c, row['review_task'])
        payload = json.loads(task['payload_json']); del payload['review_contract_version']
        f.c.execute('UPDATE tasks SET payload_json=? WHERE task_id=?', (json.dumps(payload), row['review_task']))
        obj = util.read_json(str(path)); del obj['review']['blocking_evidence']
        util.write_json(str(path), obj)
        f.tick(); f.tick()
        self.assertEqual(f.cycle()['outcome'], '模型审查拒绝，不回测')
        self.assertEqual(f.posts, 0)

    def test_history_shows_bound_reason_and_handles_missing_or_wrong_artifact(self):
        f = self.f
        f.reject_review = True
        row, path = self.review_artifact()
        f.tick(); f.tick()
        before = f.cycle()
        entry = desktop.history(f.c, f.cfg)['entries'][0]
        self.assertIn('经济逻辑', '\n'.join(entry['lines']))
        self.assertIn('审查理由全文', '\n'.join(entry['detail']))
        self.assertEqual(f.cycle(), before)
        payload = json.loads(autopilot.task(f.c, f.cycle()['review_task'])['payload_json'])
        path = Path(payload['job_dir'])/'result.json'
        obj = util.read_json(str(path)); obj['review']['candidate_hash'] = 'wrong'
        util.write_json(str(path), obj)
        self.assertIn('详情不可用', '\n'.join(desktop.history(f.c, f.cfg)['entries'][0]['lines']))
        path.unlink()
        self.assertIn('详情不可用', '\n'.join(desktop.history(f.c, f.cfg)['entries'][0]['lines']))
