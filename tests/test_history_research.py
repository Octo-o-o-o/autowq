import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import autopilot,history_research as h,util,store

class HistoryResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg,self.conn=make_env(self.tmp.name)
        self.addCleanup(self.conn.close);autopilot.setup(self.conn);h.setup(self.conn)
        for i in range(45):
            self.conn.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,outcome,created_at,updated_at) VALUES('closed','{}','fixture',?,?,?)",('机制结构重复',util.now_iso(),util.now_iso()))
        self.doc=h.snapshot(self.conn)
        self.rec={'snapshot_hash':util.sha256_json(self.doc),'priorities':[{'rule':'novel_measurement','cycles':[1,45],'reason':'Repeated structures support changing measurements, not proof of improved performance.'}]}
    def test_all_cycles_and_sanitized_snapshot(self):
        self.assertEqual(self.doc['closed_cycles'],45)
        self.assertEqual(self.doc['counts']['novel_measurement'],45)
        self.assertNotIn('candidate_json',json.dumps(self.doc))
        self.assertNotIn('report_json',json.dumps(self.doc))
    def test_evidence_bound_recommendations(self):
        h.validate_recommendation({'recommendation':self.rec},self.doc)
        for key,value in [('cycles',[46]),('rule','turnover_control'),('rule','change_threshold')]:
            bad=copy.deepcopy(self.rec);bad['priorities'][0][key]=value
            with self.assertRaises(ValueError):h.validate_recommendation({'recommendation':bad},self.doc)
    def test_full_history_citations_over_twenty_are_valid(self):
        rec=copy.deepcopy(self.rec)
        rec['priorities'][0]['cycles']=list(range(1,46))
        self.assertEqual(h.validate_recommendation({'recommendation':rec},self.doc),rec)
        rec['priorities'][0]['cycles']=[1,1]
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            h.validate_recommendation({'recommendation':rec},self.doc)
        rec['priorities'][0]['cycles']=[True]
        with self.assertRaises(ValueError):
            h.validate_recommendation({'recommendation':rec},self.doc)

    def insert(self,state='researching'):
        now=util.now_iso();digest=util.sha256_json(self.doc)
        tid,_=store.enqueue_task(self.conn,'agent_call',{},'history-fixture')
        self.conn.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?",(tid,))
        self.conn.execute('INSERT INTO history_research VALUES(?,?,?,?,?,?,?,?,?)',(digest,json.dumps(self.doc),state,tid,tid,json.dumps(self.rec),None,now,now))
        return tid
    def test_only_accepted_fixed_guidance_enters_prompt(self):
        self.insert('reviewing');self.assertEqual(h.context(self.conn,self.cfg),'')
        self.conn.execute("UPDATE history_research SET state='accepted'")
        result=h.context(self.conn,self.cfg)
        self.assertIn(h.RULES['novel_measurement'],result)
        self.assertNotIn(self.rec['priorities'][0]['reason'],result)
    def test_reviewer_must_be_distinct_and_hash_bound(self):
        self.insert('reviewing')
        result={'history_review':{'recommendation_hash':util.sha256_json(self.rec),'accept':True,'reason':'Evidence supports bounded priorities without claims of causality.'}}
        with patch('wq.autopilot.artifact',return_value=result),patch('wq.autopilot.provider',side_effect=['grok','devin']):h.progress(self.conn,self.cfg)
        self.assertEqual(self.conn.execute('SELECT state FROM history_research').fetchone()[0],'accepted')
    def test_invalid_review_never_applied(self):
        self.insert('reviewing')
        with patch('wq.autopilot.artifact',return_value={'history_review':{}}),patch('wq.autopilot.provider',side_effect=['grok','devin']):h.progress(self.conn,self.cfg)
        self.assertEqual(self.conn.execute('SELECT state FROM history_research').fetchone()[0],'failed')
        self.assertEqual(h.context(self.conn,self.cfg),'')
    def test_same_snapshot_not_queued_twice(self):
        self.insert()
        with patch('wq.routing.enqueue_job') as enqueue:
            _,created=h.enqueue(self.conn,self.cfg,self.doc)
        self.assertFalse(created);enqueue.assert_not_called()
    def test_timer_off_and_no_new_cycles_no_calls(self):
        self.insert('accepted');self.cfg.data['history_research']={'enabled':True,'every_cycles':5,'min_interval_hours':24}
        with patch('wq.history_research.enqueue') as enqueue:h.tick(self.conn,self.cfg)
        enqueue.assert_not_called()
    def test_prompt_integration(self):
        self.insert('accepted')
        with patch('wq.routing.enqueue_job',return_value=('new-task','/tmp/job')) as queue,patch('wq.autopilot.task',return_value={'payload_json':'{}'}):
            autopilot.make_job(self.conn,self.cfg,46,'research','Original required task contract')
        prompt=Path(queue.call_args.args[3]).read_text()
        self.assertIn('Original required task contract',prompt);self.assertIn(h.RULES['novel_measurement'],prompt)
