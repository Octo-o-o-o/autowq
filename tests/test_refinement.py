import copy
import unittest
from wq.refinement import assess
from wq.brain_submission import REQUIRED


class RefinementTests(unittest.TestCase):
    def checks(self):
        rows=[{'name':n,'result':'PASS'} for n in sorted(REQUIRED)]
        for r in rows:
            if r['name']=='LOW_SHARPE':r.update(result='FAIL',value=1.15,limit=1.25)
            if r['name']=='LOW_FITNESS':r.update(result='FAIL',value=.92,limit=1.)
            if r['name']=='SELF_CORRELATION':r['result']='PENDING'
        return rows

    def test_near_candidate_only_review_not_submission(self):
        result=assess(self.checks())
        self.assertTrue(result['worth_reviewing'])
        self.assertIn('不能提交',result['pending'][0])

    def test_weak_fitness_cannot_hide_behind_near_sharpe(self):
        rows=self.checks()
        next(r for r in rows if r['name']=='LOW_FITNESS')['value']=.62
        self.assertFalse(assess(rows)['worth_reviewing'])

    def test_missing_duplicate_and_structural_fail_do_not_pass(self):
        rows=self.checks()
        for bad in (rows[:-1], rows+[copy.deepcopy(rows[0])]):
            self.assertFalse(assess(bad)['worth_reviewing'])
        next(r for r in rows if r['name']=='CONCENTRATED_WEIGHT')['result']='FAIL'
        self.assertFalse(assess(rows)['worth_reviewing'])

    def test_nonfinite_metric_and_all_pass_do_not_trigger_tuning(self):
        rows=self.checks()
        next(r for r in rows if r['name']=='LOW_SHARPE')['value']=float('nan')
        self.assertFalse(assess(rows)['worth_reviewing'])
        rows=self.checks()
        for r in rows:r['result']='PASS'
        self.assertFalse(assess(rows)['worth_reviewing'])
