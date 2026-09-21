import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import make_env
from wq import runner, store
from wq.wrappers.agent import CallOutcome, _acquire_lock
import os


class AutomationDependencies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg, self.conn = make_env(self.tmp.name)
        self.addCleanup(self.conn.close)

    def enqueue(self, payload, attempts=1):
        tid, _ = store.enqueue_task(self.conn, 'agent_call', payload, max_attempts=attempts)
        self.conn.commit()
        return tid

    def test_pending_dependency_skipped_without_spending_attempt(self):
        parent = self.enqueue({})
        self.conn.execute("UPDATE tasks SET not_before='2999' WHERE task_id=?", (parent,))
        child = self.enqueue({'depends_on': [parent]})
        self.assertIsNone(store.claim_task(self.conn, 'test'))
        row = self.conn.execute('SELECT attempts FROM tasks WHERE task_id=?', (child,)).fetchone()
        self.assertEqual(row['attempts'], 0)

    def test_failed_dependency_blocks_and_unrelated_work_continues(self):
        parent = self.enqueue({})
        store.finish_task(self.conn, parent, 'failed')
        child = self.enqueue({'depends_on': [parent]})
        independent = self.enqueue({})
        self.assertEqual(store.claim_task(self.conn, 'test')['task_id'], independent)
        self.assertEqual(self.conn.execute('SELECT status FROM tasks WHERE task_id=?', (child,)).fetchone()[0], 'blocked')

    def test_succeeded_dependency_allows_child(self):
        parent = self.enqueue({})
        store.finish_task(self.conn, parent, 'succeeded')
        child = self.enqueue({'depends_on': [parent]})
        self.assertEqual(store.claim_task(self.conn, 'test')['task_id'], child)

    def test_attempt_cap_prevents_reclaimed_task_from_calling(self):
        tid = self.enqueue({})
        self.conn.execute('UPDATE tasks SET attempts=1 WHERE task_id=?', (tid,))
        self.conn.commit()
        self.assertIsNone(store.claim_task(self.conn, 'test'))
        self.assertEqual(store.list_tasks(self.conn)[0]['status'], 'blocked')

    def test_artifact_blocked_is_not_pipeline_success(self):
        payload = {'agent': 'grok', 'prompt_file': 'p', 'require_completed': True}
        for state, expected in [('blocked', 'blocked'), ('completed', 'succeeded'), (None, 'blocked')]:
            outcome = CallOutcome('succeeded', 0, 'verified', artifacts={'r.json': {'status': state}})
            with patch.object(runner.agent_runner, 'run_agent', return_value=outcome):
                self.assertEqual(runner._dispatch_agent(self.conn, self.cfg, payload)[0], expected)

    def test_runner_lock_prevents_parallel_dispatch(self):
        lock = _acquire_lock(self.cfg.run_dir, 'runner')
        try:
            with patch.object(runner, '_run_once') as dispatch:
                self.assertEqual(runner.run_once(self.conn, self.cfg)[0], 0)
                dispatch.assert_not_called()
        finally:
            os.close(lock)

    def test_idle_writes_progress_without_model(self):
        code, _ = runner.run_once(self.conn, self.cfg)
        self.assertEqual(code, 0)
        snapshot = json.loads((Path(self.cfg.run_dir) / 'progress.json').read_text())
        self.assertEqual(snapshot['tasks'], [])
        self.assertEqual(snapshot['live_agent_calls'], [])
