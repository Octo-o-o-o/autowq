import copy
import datetime as dt
import unittest
from wq import research_pair as pair, research_dsl


class PairTests(unittest.TestCase):
    def setUp(self):
        self.bindings = {r: {'expression': 'fixture_'+r, 'fields': ['fixture_'+r]} for r in research_dsl.ROLES}
        self.base = {'op':'mean','arg':{'op':'field','name':'daily_return'},'window':20}
        self.ast = {'op':'mul','left':self.base,'right':{'op':'rank','arg':{'op':'field','name':'activity_rank'}}}
        self.candidate = {'title':'Registered historical interaction', 'hypothesis':'Historical interaction predicts subsequent returns.',
                          'counterexample':'Comparable no-condition measurement has no lower effect.', 'ast':self.ast}
        self.recipe = {'kind':'remove_binary_condition','path':[],'before':copy.deepcopy(self.ast),'keep':'left'}
        self.roles = {'treatment':['activity_rank','daily_return'],'control':['daily_return']}
        dates = [(dt.date(2020,1,1)+dt.timedelta(days=i)).isoformat() for i in range(181)]
        self.spec = {'reuse_policy':'new_executions_only','decision_unit':'pair','statistic_unit':'mean_daily_return','metric':'paired_mean_daily_return','predicted_direction':1,'effect_floor':0,'tolerance':0.00001,
              'capital_basis':100,'frequency':'daily','timezone':'UTC','value_unit':'USD','cost_basis':'gross',
              'date_grid':dates,'segment_ends':[60,120,180],'missing_policy':'inconclusive',
              'revision_policy':'append_no_redispatch','inference':'descriptive_pilot'}
        self.observation = {'identity_verified':True,'synthetic':False,'origin':'platform_collection','execution':'complete','evidence_complete':True,
              'frequency':'daily','timezone':'UTC','value_unit':'USD','cost_basis':'gross','capital_basis':100,
              'intervals':[list(x) for x in zip(dates[:-1],dates[1:])], 'values':[1.0]*180,
              'revision_evidence':'fixture-same-revision','prior_observed':False}

    def observations(self,treatment=2):
        return {'control':copy.deepcopy(self.observation),
                'treatment':{**copy.deepcopy(self.observation),'values':[treatment]*180}}

    def test_exact_same_parent_single_registered_removal(self):
        out = pair.candidates(self.candidate,self.recipe,self.bindings,self.roles)
        self.assertEqual(out['control']['ast'],self.base)
        self.assertEqual(out['treatment']['ast'],self.ast)
        self.assertEqual(self.candidate['ast'],self.ast)

    def test_parent_or_unregistered_difference_rejected(self):
        bad = copy.deepcopy(self.candidate);bad['ast']['left']['window']=60
        with self.assertRaises(ValueError):pair.candidates(bad,self.recipe,self.bindings,self.roles)
        bad = copy.deepcopy(self.recipe);bad['kind']='replace_subtree'
        with self.assertRaises(ValueError):pair.candidates(self.candidate,bad,self.bindings,self.roles)

    def test_both_roles_and_original_complexity_enforced(self):
        bad=copy.deepcopy(self.roles);bad['control']=['daily_return','activity_rank']
        with self.assertRaises(ValueError):pair.candidates(self.candidate,self.recipe,self.bindings,bad)
        bad=copy.deepcopy(self.candidate);bad['ast']['left']={'op':'field','name':'daily_return'}
        recipe={**self.recipe,'before':bad['ast']}
        with self.assertRaises(ValueError):pair.candidates(bad,recipe,self.bindings,self.roles)

    def test_second_pair_transform_is_fixed_and_causal(self):
        out=pair.candidates(self.candidate,self.recipe,self.bindings,self.roles)
        for arm in out:
            changed=pair.transform(out[arm],{'op':'mean','window':5},self.bindings)
            self.assertEqual(changed['ast']['arg'],out[arm]['ast'])
        with self.assertRaises(ValueError):pair.transform(self.candidate,{'op':'mean','window':1},self.bindings)
        with self.assertRaises(ValueError):pair.transform(self.candidate,{'op':'neg','window':5},self.bindings)

    def test_descriptive_continue_falsified_and_mixed(self):
        self.assertEqual(pair.compare(self.spec,self.observations())['assessment'],'continue')
        self.assertEqual(pair.compare(self.spec,self.observations(0))['assessment'],'falsified')
        obs=self.observations();obs['treatment']['values']=[3]*60+[0]*120
        self.assertEqual(pair.compare(self.spec,obs)['assessment'],'inconclusive')

    def test_missing_and_technical_failure_are_not_zero_effect(self):
        for execution in ('failed','unknown','pending'):
            obs=self.observations();obs['control']['execution']=execution
            result=pair.compare(self.spec,obs)
            self.assertIsNone(result['effect'])
            self.assertEqual(result['assessment'],'technical_failure' if execution=='failed' else 'inconclusive')
        obs=self.observations();obs['control']['values'][1]=None
        self.assertIsNone(pair.compare(self.spec,obs)['effect'])

    def test_no_posthoc_intersection_or_unit_fallback(self):
        for field,value in [('intervals',self.observation['intervals'][1:]),('capital_basis',None),
                            ('cost_basis','net'),('value_unit','percentage'),('revision_evidence','other'),
                            ('prior_observed',True),('synthetic',True)]:
            obs=self.observations();obs['control'][field]=value
            with self.subTest(field=field):
                self.assertEqual(pair.compare(self.spec,obs)['assessment'],'inconclusive')
                self.assertIsNone(pair.compare(self.spec,obs)['effect'])

    def test_evaluation_spec_cannot_silently_shrink_or_change_metric(self):
        for key,value in [('metric','sharpe'),('date_grid',self.spec['date_grid'][:180]),
                          ('segment_ends',[59,119,180]),('capital_basis',float('nan')),
                          ('inference','scientifically_validated'),('missing_policy','fill_zero')]:
            spec=copy.deepcopy(self.spec);spec[key]=value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):pair.validate_evaluation(spec)


    def test_signed_prediction_and_whole_segment_same_scale(self):
        self.spec['predicted_direction']=-1
        result=pair.compare(self.spec,self.observations(0))
        self.assertEqual(result['effect'],0.01)
        self.assertEqual(result['segment_effects'],[0.01]*3)
        self.assertEqual(result['claim_scope'],'registered_operational_prediction')
        for key in ('capital_basis','value_unit','revision_evidence','identity_verified'):
            obs=self.observations();obs['control'].pop(key)
            result=pair.compare(self.spec,obs)
            self.assertEqual(result['eligibility'],'blocked');self.assertEqual(result['next_action'],'none')

    def test_four_arm_function_does_not_follow_last_scope(self):
        self.spec.update(decision_unit='cross_scope',joint_function='both_scopes')
        first,second=self.observations(0),self.observations(2)
        for i,observations in enumerate((first,second)):
            for arm,obs in observations.items():obs.update(task_id=f'task-{i}-{arm}',alpha_id=f'alpha-{i}-{arm}')
        result=pair.compare_cross_scope([self.spec]*2,[first,second])
        self.assertEqual(result['assessment'],'falsified')
        self.assertEqual(pair.compare_cross_scope([self.spec]*2,[second,first])['assessment'],'falsified')
        second['control']['alpha_id']=first['control']['alpha_id']
        self.assertIn('FOUR_DISTINCT_EXECUTIONS_REQUIRED',pair.compare_cross_scope([self.spec]*2,[first,second])['reason_codes'])
        second.pop('control')
        self.assertEqual(pair.compare_cross_scope([self.spec]*2,[first,second])['eligibility'],'blocked')
