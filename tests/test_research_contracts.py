import copy
import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path
from helpers import make_env
from wq import research_contracts as c, util


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p = {'settings': {'region': 'USA', 'universe': 'TOP3000', 'delay': 1},
                  'bindings': {'news': {'expression': 'fixture_news', 'fields': ['fixture_news']}}}
        self.roles = ['news']; self.now = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
        text = 'Fixture only: explicit historical availability and missing semantics for this measured variable.'
        path = Path(self.tmp.name)/'provider.txt'; path.write_text(text)
        self.doc = c.draft('H-N1')
        self.doc.update(status='verified', scope=c.scope(self.p, 'testacct'), binding_hash=c.binding_hash(self.p, self.roles))
        self.doc['materials'] = [{'material_id': 'fixture', 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
             'source': 'fixture://provider-contract', 'source_type': 'provider_documentation', 'product_version': 'fixture-v1',
             'content_type': 'text/plain', 'access_status': 'content_verified', 'fetched_at': '2026-09-29T00:00:00Z'}]
        self.doc['assertions'] = [{'predicate': p, 'subject_ref': self.doc['binding_hash'], 'value': 'fixture semantics only',
             'status': 'verified', 'verification_method': 'owner_attestation', 'verified_by': 'fixture-owner',
             'verified_at': '2026-09-29T00:00:00Z', 'valid_until': '2026-10-01T00:00:00Z',
             'evidence_refs': [{'material_id': 'fixture', 'locator': 'line 1', 'quote': text}]} for p in c.requirements('H-N1')]

        isolation=next(a for a in self.doc['assertions'] if a['predicate']=='measurement_isolation')
        isolation['value']={k:'Fixture only documented and owner verified semantic contract.' for k in (
            'measured_difference','common_sample_rule','zero_semantics','missing_semantics','warmup_semantics','verification_method')}
        isolation['value'].update(common_sample_basis='contract_identical_effective_set',allowed_changes=['weights'],recipe_hash='0'*64,
            intervention_kind='remove_binary_condition',measured_quantity='weight_intensity_effect',
            operator_semantics={'before':'Fixture multiplicative weighting on same effective set','after':'Fixture weights removed while valid sample remains same'})

    def inspect(self, doc=None, policy=None, account='testacct'):
        return c.inspect(doc or self.doc, 'H-N1', policy or self.p, account, self.roles, self.now)

    def test_explicit_attestation_is_checked_but_not_claimed_independent_truth(self):
        result = self.inspect(); self.assertTrue(result['ready'])
        self.assertIn('not independently proven', result['verification_limit'])

    def test_metadata_cannot_prove_semantics(self):
        self.doc['materials'][0]['source_type'] = 'field_metadata'
        result = self.inspect(); self.assertFalse(result['ready'])
        self.assertIn('SEMANTIC_EVIDENCE_UNSUPPORTED', [r['code'] for r in result['reasons']])

    def test_model_self_attestation_rejected(self):
        self.doc['assertions'][0]['verification_method'] = 'model_assertion'
        self.assertFalse(self.inspect()['ready'])

    def test_all_14_have_fixed_requirements_and_no_draft_ready(self):
        for hid in c.REQUIRED:
            doc = c.draft(hid)
            self.assertFalse(c.inspect(doc, hid, self.p, 'testacct', self.roles, self.now)['ready'])
        self.assertEqual(len(c.REQUIRED), 14)
        self.assertEqual(c.inspect(c.draft('H-V2'), 'H-V2', self.p, 'testacct', self.roles)['state'], 'unsupported')

    def test_hash_quote_and_locator_required(self):
        for change in ('hash', 'quote', 'locator', 'error_page'):
            doc = copy.deepcopy(self.doc)
            if change == 'hash': doc['materials'][0]['sha256'] = '0'*64
            elif change == 'quote': doc['assertions'][0]['evidence_refs'][0]['quote'] = 'Not actually present in the material'
            elif change == 'locator': doc['assertions'][0]['evidence_refs'][0]['locator'] = ''
            else: doc['materials'][0]['access_status'] = 'login_required'
            with self.subTest(change=change): self.assertFalse(self.inspect(doc)['ready'])

    def test_scope_and_transformation_do_not_inherit_ready(self):
        self.assertFalse(self.inspect(account='different')['ready'])
        for key, value in [('universe', 'TOP1000'), ('delay', 0)]:
            policy = copy.deepcopy(self.p); policy['settings'][key] = value
            self.assertFalse(self.inspect(policy=policy)['ready'])
        policy = copy.deepcopy(self.p); policy['bindings']['news']['expression'] = 'reverse(fixture_news)'
        self.assertFalse(self.inspect(policy=policy)['ready'])

    def test_required_assertions_cannot_be_deleted_or_duplicated(self):
        doc = copy.deepcopy(self.doc); doc['required_assertions'] = []
        self.assertFalse(self.inspect(doc)['ready'])
        doc = copy.deepcopy(self.doc); doc['assertions'].append(copy.deepcopy(doc['assertions'][0]))
        self.assertFalse(self.inspect(doc)['ready'])

    def test_time_boundaries_and_permission_renewal_do_not_refresh_evidence(self):
        for key, value in [('verified_at', '2026-10-02T00:00:00Z'), ('verified_at', '2026-09-29T00:00:00'),
                           ('valid_until', '2026-09-30T00:00:00Z')]:
            doc = copy.deepcopy(self.doc); doc['assertions'][0][key] = value
            self.assertFalse(self.inspect(doc)['ready'])
        self.doc['assertions'][0]['valid_until'] = '2026-09-30T00:00:00Z'
        self.p['valid_until'] = '2026-10-10T00:00:00+08:00'
        self.assertEqual(self.inspect()['state'], 'expired')

    def test_unknown_values_and_unmatched_subject_rejected(self):
        for field, value in [('value', None), ('value', ''), ('subject_ref', 'wrong')]:
            doc = copy.deepcopy(self.doc); doc['assertions'][0][field] = value
            self.assertFalse(self.inspect(doc)['ready'])


    def test_single_node_edit_does_not_prove_common_effective_sample(self):
        isolation=next(a for a in self.doc['assertions'] if a['predicate']=='measurement_isolation')
        for basis in ('posthoc_intersection','same_pnl_dates',None):
            isolation['value']['common_sample_basis']=basis
            self.assertIn('MEASUREMENT_NOT_ISOLATED',[r['code'] for r in self.inspect()['reasons']])
        isolation['value']['common_sample_basis']='contract_identical_effective_set'
        isolation['value']['missing_semantics']=''
        self.assertFalse(self.inspect()['ready'])
