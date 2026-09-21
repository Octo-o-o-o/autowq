import copy
import datetime as dt
import unittest
from wq.evidence_gate import evaluate

class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,9,21,tzinfo=dt.timezone.utc)
        self.spec=[{'id':'A','blocking':True},{'id':'B','blocking':False}]
        self.doc={'synthetic':True,'declarations':[{'evidence_id':'A','status':'verified','source_ref':'fixture://a','verified_at':'2026-09-20T00:00:00Z','note':'synthetic proof'}]}
    def run_gate(self):return evaluate(self.spec,self.doc,self.now)
    def test_complete_is_not_readiness(self):
        out,code=self.run_gate();self.assertEqual(code,0)
        self.assertFalse(out['platform_ready']);self.assertFalse(out['research_ready']);self.assertTrue(out['synthetic'])
        self.assertEqual(len(out['non_blocking']['gaps']),1)
    def test_invalid_requirements_fail_closed(self):
        for spec in [[],[{'id':'A','blocking':'false'}],[{'id':'A','blocking':False}],self.spec+self.spec]:
            with self.subTest(spec=spec):self.assertEqual(evaluate(spec,self.doc,self.now)[1],2)
    def test_unknown_unavailable_require_proof(self):
        item=self.doc['declarations'][0];item['status']='unavailable';del item['source_ref']
        self.assertEqual(self.run_gate()[0]['verdict'],'blocked_unknown')
        item['source_ref']='fixture://absent';self.assertEqual(self.run_gate()[0]['verdict'],'blocked_unavailable')
    def test_no_naive_or_future_verification_time(self):
        for value in ['2026-09-20','2026-09-20T00:00:00','2026-10-20T00:00:00Z']:
            self.doc['declarations'][0]['verified_at']=value
            self.assertEqual(self.run_gate()[0]['verdict'],'blocked_unknown')
    def test_missing_marker_rejected(self):
        del self.doc['synthetic'];self.assertEqual(self.run_gate()[1],2)
    def test_duplicate_and_unknown_declaration(self):
        self.doc['declarations']*=2;self.assertEqual(self.run_gate()[1],2)
        self.doc['declarations']=[{'evidence_id':'X','status':'unknown'}];self.assertEqual(self.run_gate()[1],2)
    def test_calendar_placeholder_case_rejected(self):
        self.spec=[{'id':'E2_filing_availability_timestamps','blocking':True}]
        self.doc['declarations'][0].update(evidence_id=self.spec[0]['id'],details={'calendar':{'kind':'PLUS_24H','source':'fixture'}})
        self.assertEqual(self.run_gate()[0]['verdict'],'blocked_unknown')
