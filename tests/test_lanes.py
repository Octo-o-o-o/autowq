"""多泳道并发：泳道独立推进、渠道串行槽 defer、池上限、UNKNOWN/停/取消语义。"""
import json
import os
import sqlite3
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpers import make_env
from wq import autopilot, brain_jobs, research_dsl, routing, runner, store, util
from wq.db import connect


def proposal(op='mean'):
    return {'title': '过去收益的可证伪探索假设',
            'hypothesis': '研究过去收益的结构是否和后续收益有关，窗口在观测前固定。',
            'counterexample': '交易成本或风险暴露可能完全解释表面关联，因此不能宣称盈利。',
            'ast': {'op': 'neg', 'arg': {'op': op, 'arg': {'op': 'field', 'name': 'daily_return'}, 'window': 20}}}


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg, self.c = make_env(self.temp.name, {
            'brain_api': {'enabled': True, 'min_post_interval_s': 0, 'authorized_until': '2099-01-01T00:00:00Z'},
            'routing': {'authorized_until': '2099-01-01T00:00:00Z'},
            'autopilot': {'enabled': True, 'policy_file': 'config/policy.json', 'interval_s': 60,
                          'max_cycles_per_day': 40, 'max_simulations_per_week': 30,
                          'concurrent_lanes': 2}})
        self.addCleanup(lambda: self.c.close())
        self.root = Path(self.temp.name)
        proof = self.root / 'evidence.json'
        util.write_json(str(proof), {'fixture': True})
        settings = {'region': 'USA', 'universe': 'TOP3000', 'delay': 1, 'decay': 0,
                    'truncation': 0.08, 'neutralization': 'INDUSTRY'}
        self.p = {'version': 1, 'scope': 'exploratory_only_no_submission',
                  'valid_until': '2099-01-01T00:00:00Z', 'verified_at': '2026-01-01T00:00:00Z',
                  'settings': settings, 'source': 'fixture://source',
                  'bindings': {n: {'expression': 'returns', 'fields': ['returns'], 'source': 'fixture://field'}
                               for n in research_dsl.ROLES},
                  'evidence_files': [{'path': str(proof), 'sha256': util.sha256_json({'fixture': True})}]}
        util.write_json(str(self.root / 'config/policy.json'), self.p)
        autopilot.setup(self.c)
        brain_jobs.setup(self.c)
        self.counter = 0
        self.posts = 0
        self.lock = threading.Lock()
        self.inflight = set()
        self.overlap = threading.Event()
        self.add_patch('wq.routing.catalog', return_value={
            'default': 'steady',
            'providers': {'a': {'argv': ['/bin/true'], 'timeout_s': 60},
                          'b': {'argv': ['/bin/false'], 'timeout_s': 60}},
            'presets': {'steady': {'routes': {'research': ['a', 'b'], 'review': ['b', 'a']}}}})
        self.add_patch('wq.routing._unavailable', return_value=None)
        self.real_enqueue = routing.enqueue_job
        self.add_patch('wq.routing.enqueue_job', side_effect=self.job)
        client = self.add_patch('wq.brain_client.BrainClient').return_value
        client.jar = [True]
        client.preflight.return_value = (200, {}, {})
        client = self.add_patch('wq.brain_jobs.BrainClient').return_value
        client.jar = [True]
        client.preflight.return_value = (200, {}, {})
        client.request.side_effect = self.api
        self.real_dispatch = runner.dispatch_task
        self.add_patch('wq.runner.dispatch_task', side_effect=self.dispatch)

    def add_patch(self, *a, **k):
        p = patch(*a, **k)
        obj = p.start()
        self.addCleanup(p.stop)
        return obj

    def job(self, conn, cfg, role, prompt_file, input_dir=None, title=None):
        with self.lock:
            self.counter += 1
            n = self.counter
        path = self.root / f'job-{n}'
        path.mkdir()
        (path / 'prompt.txt').write_text(Path(prompt_file).read_text())
        (path / 'packet').mkdir()
        (path / 'packet' / 'request.md').write_text(Path(prompt_file).read_text())
        payload = {'job_dir': str(path), 'role': role, 'routing': True}
        return store.enqueue_task(conn, 'agent_call', payload)[0], str(path)

    def preferred(self, conn, cid, role):
        ev = conn.execute(
            "SELECT detail FROM research_events WHERE cycle_id=? AND kind='route_plan'", (cid,)).fetchone()
        # 默认泳道对不登记 route_plan：此时首选即预设头（research=a / review=b）。
        if ev:
            return json.loads(ev[0])[role + '_preferred']
        return 'a' if role == 'research' else 'b'

    def dispatch(self, conn, cfg, t):
        if t['kind'] != 'agent_call':
            return self.real_dispatch(conn, cfg, t)
        payload = json.loads(t['payload_json'])
        role = payload['role']
        cid = payload.get('autopilot_cycle')
        if cid is None:
            return self.real_dispatch(conn, cfg, t)
        row = conn.execute('SELECT * FROM research_cycles WHERE cycle_id=?', (cid,)).fetchone()
        with self.lock:
            self.inflight.add(t['task_id'])
            if len(self.inflight) >= 2:
                self.overlap.set()
        self.overlap.wait(10)
        if role == 'research':
            obj = {'status': 'completed', 'candidate': proposal('mean' if cid and cid % 2 else 'std')}
        else:
            obj = {'status': 'completed',
                   'review': {'candidate_hash': row['candidate_hash'], 'accept': True,
                              'checks': {k: True for k in autopilot.REVIEW_CHECKS},
                              'reason': '这是明确受限的探索，不能当作经济机制已获证明。',
                              'blocking_evidence': []}}
        util.write_json(str(Path(payload['job_dir']) / 'result.json'), obj)
        conn.execute('INSERT OR IGNORE INTO task_routes(task_id,snapshot_json,phase,updated_at) VALUES(?,?,?,?)',
                     (t['task_id'], json.dumps({'chain': [self.preferred(conn, cid, role)], 'preset': 'steady'}),
                      'complete', util.now_iso()))
        with self.lock:
            self.inflight.discard(t['task_id'])
        return 'succeeded', {}, None

    def api(self, method, path, body=None):
        if method == 'POST':
            self.posts += 1
            self.last_request = body
            return 201, {'location': '/simulations/test'}, {}
        if path.endswith('/test'):
            return 200, {}, {'alpha': f'alpha{self.posts}'}
        return 200, {}, {'id': f'alpha{self.posts}', 'regular': {'code': self.last_request['regular']},
                         'settings': self.last_request['settings'],
                         'is': {'sharpe': 0.1, 'checks': [{'name': 'LOW_SHARPE', 'result': 'FAIL'}]}}

    def tick(self):
        self.c.execute("UPDATE tasks SET not_before='2000' WHERE status='queued'")
        self.c.commit()
        return runner.run_once(self.c, self.cfg)

    def cycles(self):
        return [dict(r) for r in self.c.execute(
            "SELECT * FROM research_cycles WHERE state!='closed' ORDER BY lane").fetchall()]

    def full_cycle(self, rounds=8):
        for _ in range(rounds):
            self.tick()

    # ---------- 泳道创建与路由 ----------

    def test_two_lanes_open_with_staggered_route_heads(self):
        self.tick()
        rows = self.cycles()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['lane'] for r in rows}, {0, 1})
        # 两泳道首选渠道错开，减少同渠道争用；各自研究/审查仍不同渠道。
        plans = {}
        for r in rows:
            ev = self.c.execute("SELECT detail FROM research_events WHERE cycle_id=? AND kind='route_plan'",
                                (r['cycle_id'],)).fetchone()
            # 默认泳道对（=预设头）不登记 route_plan：语义上首选就是预设头。
            plans[r['lane']] = json.loads(ev[0]) if ev else {'research_preferred': 'a', 'review_preferred': 'b'}
            self.assertNotEqual(plans[r['lane']]['research_preferred'], plans[r['lane']]['review_preferred'])
        self.assertNotEqual(plans[0]['research_preferred'], plans[1]['research_preferred'])
        self.assertNotEqual(plans[0]['review_preferred'], plans[1]['review_preferred'])
        # 单轮上限语义不变：unique index 保证每泳道最多一条未关闭轮次。
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO research_cycles(state,policy_json,policy_hash,lane,created_at,updated_at)"
                           " VALUES('researching','{}','x',0,?,?)", (util.now_iso(), util.now_iso()))

    def test_agent_calls_run_in_parallel_across_lanes(self):
        self.tick()
        self.assertTrue(self.overlap.is_set())
        statuses = [r[0] for r in self.c.execute(
            "SELECT t.status FROM tasks t JOIN research_cycles c ON c.research_task=t.task_id ORDER BY c.lane")]
        self.assertEqual(statuses, ['succeeded', 'succeeded'])

    def test_lanes_progress_independently_to_simulation(self):
        self.full_cycle()
        done = self.c.execute("SELECT COUNT(*) FROM research_cycles WHERE state='closed'").fetchone()[0]
        self.assertEqual(done, 2)
        self.assertEqual(self.posts, 2)
        self.assertEqual(self.counter, 4)  # 每泳道 研究+审查 各一次

    def test_single_lane_remains_serial(self):
        self.cfg.data['autopilot']['concurrent_lanes'] = 1
        self.tick()
        rows = self.cycles()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['lane'], 0)
        self.assertFalse(self.overlap.is_set())

    # ---------- 渠道串行槽 ----------

    def test_provider_busy_defers_without_burning_attempt(self):
        prompt = self.root / 'p.md'
        prompt.write_text('x')
        tid, _job = self.real_enqueue(self.c, self.cfg, 'research', str(prompt))
        self.c.commit()
        store.start_agent_call(self.c, 'a', 'test', None, os.getpid(), 'x', 'x')
        self.c.commit()
        task = store.claim_task(self.c, owner='t', kinds={'agent_call'})
        self.c.commit()
        self.assertEqual(task['task_id'], tid)
        outcome, detail, error = runner.dispatch_task(self.c, self.cfg, task)
        self.c.commit()
        self.assertEqual(outcome, 'retry_scheduled')
        row = self.c.execute('SELECT status,attempts,not_before FROM tasks WHERE task_id=?', (tid,)).fetchone()
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['attempts'], 0)  # claim 的 +1 被 defer 退回
        self.assertGreater(util.parse_iso(row['not_before']), util.now())
        route = self.c.execute('SELECT provider_index,phase FROM task_routes WHERE task_id=?', (tid,)).fetchone()
        self.assertEqual(route['provider_index'], 0)
        self.assertEqual(route['phase'], 'ready')
        # b 渠道没有活调用：另一个任务的队首选 b 时不 defer。run_agent 打桩只验证走到分派层。
        tid2, _job2 = self.real_enqueue(self.c, self.cfg, 'review', str(prompt))
        self.c.commit()
        p2 = json.loads(self.c.execute('SELECT payload_json FROM tasks WHERE task_id=?', (tid2,)).fetchone()[0])
        p2['provider_order'] = ['b']
        self.c.execute('UPDATE tasks SET payload_json=? WHERE task_id=?', (json.dumps(p2), tid2))
        self.c.commit()
        task2 = store.claim_task(self.c, owner='t', kinds={'agent_call'})
        self.c.commit()
        self.assertEqual(task2['task_id'], tid2)
        from unittest.mock import Mock
        with patch('wq.wrappers.agent.run_agent',
                   return_value=Mock(status='blocked', detail='stub', call_id='c0', artifacts={})):
            outcome2, _d2, _e2 = runner.dispatch_task(self.c, self.cfg, task2)
        self.c.commit()
        self.assertEqual(outcome2, 'blocked')
        route2 = self.c.execute('SELECT provider_index,snapshot_json FROM task_routes WHERE task_id=?', (tid2,)).fetchone()
        self.assertEqual(json.loads(route2['snapshot_json'])['chain'][route2['provider_index']], 'b')

    def test_serial_segment_excludes_agent_calls(self):
        prompt = self.root / 'p.md'
        prompt.write_text('x')
        tid, _ = self.real_enqueue(self.c, self.cfg, 'research', str(prompt))
        store.enqueue_task(self.c, 'reconcile', {})
        self.c.commit()
        serial = store.claim_task(self.c, owner='s', exclude={'agent_call'})
        self.assertIsNotNone(serial)
        self.assertNotEqual(serial['kind'], 'agent_call')
        self.c.commit()
        agent = store.claim_task(self.c, owner='w', kinds={'agent_call'})
        self.assertIsNotNone(agent)
        self.assertEqual(agent['task_id'], tid)
        self.c.commit()

    # ---------- 全局闸门 ----------

    def test_unknown_freezes_lane_and_blocks_new_cycle(self):
        autopilot.tick(self.c, self.cfg)
        self.c.commit()
        rows = self.cycles()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.counter, 2)
        self.c.execute("UPDATE tasks SET status='unknown' WHERE task_id=?", (rows[0]['research_task'],))
        self.c.commit()
        self.cfg.data['autopilot']['concurrent_lanes'] = 3  # 让出一个空泳道
        autopilot.tick(self.c, self.cfg)
        self.c.commit()
        open_rows = self.cycles()
        self.assertEqual(len(open_rows), 2)          # UNKNOWN 冻结一切新轮次
        self.assertEqual([r['state'] for r in open_rows], ['researching', 'researching'])
        self.assertEqual(self.counter, 2)            # 不再登记新模型调用

    def test_pool_bound_and_validation(self):
        self.assertEqual(runner.agent_pool_size(self.cfg), 2)
        self.cfg.data['limits'] = {'max_agent_parallel': 4}
        self.assertEqual(runner.agent_pool_size(self.cfg), 4)
        for bad in (0, 9, 1.5, 'x'):
            self.cfg.data['limits']['max_agent_parallel'] = bad
            with self.assertRaises(ValueError):
                runner.agent_pool_size(self.cfg)
        del self.cfg.data['limits']
        self.cfg.data['autopilot']['concurrent_lanes'] = 0
        with self.assertRaises(ValueError):
            autopilot.lane_limit(self.cfg)

    # ---------- 停/取消 ----------

    def test_cancel_one_lane_only(self):
        # 直接用 autopilot.tick 建轮：任务停在 queued，取消路径不受已派发任务影响。
        autopilot.tick(self.c, self.cfg)
        self.c.commit()
        rows = self.cycles()
        self.assertEqual(len(rows), 2)
        victim = rows[0]['cycle_id']
        other = rows[1]['cycle_id']
        result = autopilot.cancel_open_cycle(self.c, self.cfg, victim)
        self.c.commit()
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['cycle_id'], victim)
        remaining = self.cycles()
        self.assertEqual([r['cycle_id'] for r in remaining], [other])
        dead = self.c.execute('SELECT status FROM tasks WHERE task_id=?',
                              (rows[0]['research_task'],)).fetchone()
        self.assertEqual(dead['status'], 'aborted')
        alive = self.c.execute('SELECT status FROM tasks WHERE task_id=?',
                               (rows[1]['research_task'],)).fetchone()
        self.assertEqual(alive['status'], 'queued')
        # 目标泳道的闸门已记账；未指定 cid 时 cancel 默认取最新开放轮次（另一个泳道）。
        result2 = autopilot.cancel_open_cycle(self.c, self.cfg)
        self.c.commit()
        self.assertTrue(result2['cancelled'])
        self.assertEqual(result2['cycle_id'], other)
        self.assertEqual(self.cycles(), [])

    def test_stop_after_cycle_waits_for_all_lanes(self):
        autopilot.tick(self.c, self.cfg)
        self.c.commit()
        self.assertEqual(len(self.cycles()), 2)
        result = autopilot.stop_after_cycle(self.c)
        self.c.commit()
        self.assertTrue(result['deferred'])
        self.assertFalse(store.is_paused(self.c))
        autopilot.cancel_open_cycle(self.c, self.cfg, self.cycles()[0]['cycle_id'])
        self.c.commit()
        autopilot.tick(self.c, self.cfg)  # 另一条泳道仍在跑：延迟停止不落 paused
        self.c.commit()
        self.assertFalse(store.is_paused(self.c))
        autopilot.cancel_open_cycle(self.c, self.cfg, self.cycles()[0]['cycle_id'])
        self.c.commit()
        autopilot.tick(self.c, self.cfg)  # 全部泳道结束 → 应用延迟停止
        self.c.commit()
        self.assertTrue(store.is_paused(self.c))
        self.assertEqual(store.get_flag(self.c, 'pause_origin'), 'manual')

    def test_per_lane_gate_is_independent(self):
        autopilot.tick(self.c, self.cfg)
        self.c.commit()
        rows = self.cycles()
        autopilot.cancel_open_cycle(self.c, self.cfg, rows[0]['cycle_id'])
        self.c.commit()
        gate0 = store.get_flag(self.c, 'autopilot_next_at:0')
        self.assertIsNotNone(gate0)
        self.assertGreater(util.parse_iso(gate0), util.now())
        autopilot.cancel_open_cycle(self.c, self.cfg, self.cycles()[0]['cycle_id'])
        self.c.commit()
        # lane0 仍在轮间间隔内；lane1 的闸门拨到过去 → 只有 lane1 能开新轮。
        store.set_flag(self.c, 'autopilot_next_at:1', '2000-01-01T00:00:00Z')
        self.c.commit()
        autopilot.tick(self.c, self.cfg)
        self.c.commit()
        new_rows = self.cycles()
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0]['lane'], 1)

    def test_cycle_preset_stays_frozen_per_cycle(self):
        store.set_flag(self.c, 'preset_once', 'alt')
        store.set_flag(self.c, 'preset_once_cycles', '1')
        data = routing.catalog(self.cfg)
        data['presets']['alt'] = {'routes': {'research': ['b'], 'review': ['a']}, 'solo': True}
        self.tick()
        rows = self.cycles()
        self.assertEqual(len(rows), 2)
        bound = [r['cycle_id'] for r in rows
                 if store.get_flag(self.c, f"cycle_preset_{r['cycle_id']}")]
        # 临时预设只绑定第一个新建的轮次；另一个回退永久预设。
        self.assertEqual(len(bound), 1)
        cid = bound[0]
        self.assertEqual(routing.active_preset(self.c, self.cfg, data, cid), 'alt')
        other = [r['cycle_id'] for r in rows if r['cycle_id'] != cid][0]
        self.assertEqual(routing.active_preset(self.c, self.cfg, data, other), 'steady')


if __name__ == '__main__':
    unittest.main()
