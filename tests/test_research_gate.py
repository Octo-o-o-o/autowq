import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from helpers import make_env
from wq import brain_jobs, research_gate, runner, util


class ResearchGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg, self.conn = make_env(self.tmp.name, {'brain_api': {'enabled': True, 'authorized_until': '2099-01-01T00:00:00Z'}})
        self.addCleanup(self.conn.close)
        self.root = Path(self.cfg.private_dir) / 'research-approvals'; self.root.mkdir(parents=True)
        self.protocol = {'status': 'accepted_for_simulation', 'platform_ready': True, 'required_evidence': [{'id':'PIT', 'blocking':True}]}
        self.decl = {'synthetic':False, 'declarations':[{'evidence_id':'PIT','status':'verified','source_ref':'fixture://source','note':'test only', 'verified_at':'2026-01-01T00:00:00Z'}]}
        settings = {'region':'USA','universe':'TOP3000','delay':1,'decay':0,'neutralization':'INDUSTRY','truncation':0.08}
        self.doc = {'purpose':'research_validation','request':{'type':'REGULAR','regular':'volume','settings':settings}, 'config':{**settings,'fields':['volume'],'catalog_verified':True},'evidence':{'source':'fixture://source','settings_verified':True,'research_review':str(self.root/'review.json')}}
        self.review = {'status':'accepted_for_simulation','synthetic':False,'reviewer':'test fixture','reviewed_at':'2026-01-01T00:00:00Z','allowed_request_hashes':[research_gate.request_hash(self.doc)]}
        self.save_inputs(); self.save_review()

    def save_inputs(self):
        for key, obj in [('protocol',self.protocol), ('declarations',self.decl)]:
            p=self.root/(key+'.json'); p.write_text(json.dumps(obj))
            self.review[key]={'path':str(p),'sha256':util.sha256_json(obj)}

    def save_review(self):
        (self.root/'review.json').write_text(json.dumps(self.review))

    def test_finite_accepted_request(self):
        tid,created=brain_jobs.enqueue(self.conn,self.cfg,self.doc)
        self.assertTrue(created)
        payload=json.loads(self.conn.execute('SELECT payload_json FROM tasks WHERE task_id=?',(tid,)).fetchone()[0])
        self.assertEqual(payload['evidence']['research_review_sha256'],util.sha256_json(self.review))
        self.assertNotIn('research_review_sha256',self.doc['evidence'])

    def test_boolean_only_cannot_bypass(self):
        self.doc['evidence']={'protocol_accepted':True,'source':'fixture','settings_verified':True}
        with self.assertRaisesRegex(ValueError,'缺本地研究验收'):brain_jobs.enqueue(self.conn,self.cfg,self.doc)

    def test_unknown_synthetic_and_blocked_are_rejected(self):
        for mutate in ['unknown','synthetic','blocked']:
            with self.subTest(mutate=mutate):
                original=(copy.deepcopy(self.decl),copy.deepcopy(self.protocol))
                if mutate=='unknown':self.decl['declarations'][0]['status']='unknown'
                elif mutate=='synthetic':self.decl['synthetic']=True
                else:self.protocol['platform_ready']=False
                self.save_inputs();self.save_review()
                with self.assertRaises(ValueError):research_gate.validate(self.cfg,self.doc)
                self.decl,self.protocol=original

    def test_changed_input_or_unlisted_request_rejected(self):
        (self.root/'protocol.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'已改变'):research_gate.validate(self.cfg,self.doc)
        self.save_inputs();self.save_review()
        self.doc['request']['regular']='-volume'
        with self.assertRaisesRegex(ValueError,'有限清单'):research_gate.validate(self.cfg,self.doc)

    def test_revoked_after_enqueue_no_post(self):
        tid,_=brain_jobs.enqueue(self.conn,self.cfg,self.doc)
        self.review['status']='revoked';self.save_review()
        with patch('wq.brain_jobs.BrainClient') as client:
            client.return_value.jar=[True]
            runner.run_once(self.conn,self.cfg)
            client.return_value.request.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT status FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],'blocked')

    def test_review_edit_requires_new_enqueue(self):
        doc=copy.deepcopy(self.doc);doc['evidence']['research_review_sha256']=research_gate.validate(self.cfg,doc)
        self.review['reviewer']='another';self.save_review()
        with self.assertRaisesRegex(ValueError,'入队后'):research_gate.validate(self.cfg,doc)

    def test_review_outside_private_directory_rejected(self):
        self.doc['evidence']['research_review']=str(Path(self.tmp.name)/'review.json')
        with self.assertRaisesRegex(ValueError,'私有'):research_gate.validate(self.cfg,self.doc)
