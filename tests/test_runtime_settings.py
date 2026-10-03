"""Operating controls must reach dispatch gates, preserve usage and survive restarts."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from wq import runtime_settings as settings, store, util, research_meta as meta
from wq import research_evidence as evidence, research_framework as framework, research_lifecycle as lifecycle
from wq.config import Config
import test_research_dual_loop
import test_routing


class OperatingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.f=test_research_dual_loop.DualLoopTests('test_model_reservations_failures_do_not_refund_and_cap_persists')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c,self.cfg=self.f.c,self.f.cfg
        self.cfg.data['autopilot']['enabled']=True
        util.write_json(self.cfg.path,self.cfg.data)
        self.c.commit()

    def test_preferences_rebase_draft_without_baseline_drift(self):
        from wq import research_learning as learning
        before=learning.current_baseline(self.cfg)
        tid,_=store.enqueue_task(self.c,'agent_call',{},'pref-inflight');self.c.commit()
        settings.request(self.cfg,{'autopilot.concurrent_lanes':3})
        settings.preference_request(self.cfg,{'desktop.notifications':False,'ui.language':'en'})
        self.assertEqual(learning.current_baseline(self.cfg),before)
        self.assertEqual(settings.pending(self.c)['target']['autopilot']['concurrent_lanes'],3)
        store.finish_task(self.c,tid,'failed');self.c.commit()
        result=settings.apply_pending(self.c,self.cfg)
        self.assertEqual(result['state'],'applied')
        self.assertFalse(self.cfg.get('desktop','notifications'))
        self.assertEqual(self.cfg.get('ui','language'),'en')

    def test_settings_batch_validates_every_value_before_mutation(self):
        from wq import desktop_control
        original=util.read_json(self.cfg.path)
        with patch.object(desktop_control,'_cfg',return_value=self.cfg):
            with self.assertRaises(ValueError):
                desktop_control.control('settings-save',json.dumps({'research_dual_loop.max_model_starts':'256','autopilot.concurrent_lanes':'99'}))
            result=desktop_control.control('settings-save',json.dumps({'research_dual_loop.max_model_starts':'256','limits.model_spend_cap_usd':'20.25'}))
        self.assertEqual(result['state'],'applied')
        self.assertEqual(self.cfg.get('limits','model_spend_cap_usd'),20.25)
        self.assertEqual(self.cfg.get('limits','model_spend_as_of'),original.get('limits',{}).get('model_spend_as_of'))

    def test_experiment_request_change_waits_for_owned_work(self):
        tid,_=store.enqueue_task(self.c,'agent_call',{},'experiment-inflight');self.c.commit()
        before=lifecycle.document(self.c,'fixture-dual-epoch')['max_requests_per_arm']
        result=settings.request(self.cfg,{'experiment_requests_per_arm':30})
        self.assertEqual(result['state'],'pending')
        self.assertEqual(lifecycle.document(self.c,'fixture-dual-epoch')['max_requests_per_arm'],before)
        store.finish_task(self.c,tid,'failed');self.c.commit()
        self.assertEqual(settings.apply_pending(self.c,self.cfg)['state'],'applied')
        self.assertEqual(lifecycle.document(self.c,'fixture-dual-epoch')['max_requests_per_arm'],30)

    def test_stale_form_cannot_overwrite_newer_settings(self):
        revision=settings.snapshot(self.c,self.cfg)['revision']
        settings.preference_request(self.cfg,{'desktop.notifications':False})
        with self.assertRaisesRegex(ValueError,'changed since'):
            settings.request(self.cfg,{'autopilot.concurrent_lanes':3},expected_revision=revision)
        self.assertIsNone(settings.pending(self.c))

    def test_preference_crash_window_rebases_before_drained_apply(self):
        tid,_=store.enqueue_task(self.c,'agent_call',{},'pref-crash');self.c.commit()
        settings.request(self.cfg,{'autopilot.concurrent_lanes':3})
        raw=util.read_json(self.cfg.path);raw['desktop']={'notifications':False};util.write_json(self.cfg.path,raw)
        store.finish_task(self.c,tid,'failed');self.c.commit()
        self.assertEqual(settings.apply_pending(self.c,self.cfg)['state'],'applied')
        self.assertFalse(self.cfg.get('desktop','notifications'))
        self.assertEqual(self.cfg.get('autopilot','concurrent_lanes'),3)

    def test_apply_ends_old_supervisor_batch_to_reload_cached_limits(self):
        from wq import runner
        tid,_=store.enqueue_task(self.c,'agent_call',{},'batch-end');self.c.commit()
        settings.request(self.cfg,{'limits.max_agent_parallel':2})
        store.finish_task(self.c,tid,'failed');self.c.commit()
        self.assertTrue(runner._coordinate(self.c,self.cfg))
        self.assertEqual(runner.agent_pool_size(self.cfg),2)

    def test_pause_does_not_prevent_recovery_of_drained_settings(self):
        from wq import runner
        tid,_=store.enqueue_task(self.c,'agent_call',{},'pause-pending');self.c.commit()
        settings.request(self.cfg,{'autopilot.interval_s':1200})
        store.finish_task(self.c,tid,'failed');store.set_flag(self.c,'paused','1');store.set_flag(self.c,'pause_reason','menu:stop-after-cycle');self.c.commit()
        runner.run_once(self.c,self.cfg)
        self.assertEqual(self.cfg.get('autopilot','interval_s'),1200)
        self.assertTrue(store.is_paused(self.c))
        self.assertIsNone(settings.pending(self.c))

    def test_cancel_via_actual_control_keeps_current_values(self):
        from wq import desktop_control
        store.set_flag(self.c,settings.PENDING,json.dumps({'updates':{'research_dual_loop.max_model_starts':256}}));self.c.commit()
        with patch.object(desktop_control,'_cfg',return_value=self.cfg):
            result=desktop_control.control('operating-cancel')
        self.assertEqual(result['state'],'cancelled')
        self.assertIsNone(settings.pending(self.c))
        self.assertEqual(settings.integer(self.cfg,'research_dual_loop.max_model_starts'),64)

    def test_256_is_enforced_and_displayed_without_resetting_64_used(self):
        key='dual_model_reservations:fixture-dual'
        store.set_flag(self.c,key,'64');self.c.commit()
        before=lifecycle.remaining(self.c,'fixture-dual-epoch')
        result=settings.request(self.cfg,{'research_dual_loop.max_model_starts':256})
        self.assertEqual(result['state'],'applied',result)
        self.assertEqual(store.get_flag(self.c,key),'64')
        latest=self.c.execute('SELECT experiment_id FROM learning_experiments ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        self.assertEqual(lifecycle.remaining(self.c,latest),before)
        meta.reserve_model(self.c,self.cfg)
        self.assertEqual(store.get_flag(self.c,key),'65')
        store.set_flag(self.c,key,'255');self.c.commit()
        meta.reserve_model(self.c,self.cfg)
        with self.assertRaisesRegex(ValueError,'256'):meta.reserve_model(self.c,self.cfg)
        view=framework.progress(self.c,self.cfg)['dual_resources']['model_starts']
        self.assertEqual(view,{'used':256,'limit':256,'remaining':0})
        row=next(e for e in settings.snapshot(self.c,self.cfg)['entries'] if e['key']=='research_dual_loop.max_model_starts')
        self.assertEqual((row['value'],row['used']),(256,256))

    def test_pending_values_do_not_mutate_live_config_and_apply_after_drain(self):
        tid,_=store.enqueue_task(self.c,'agent_call',{},'settings-inflight');self.c.commit()
        result=settings.request(self.cfg,{'autopilot.concurrent_lanes':3,'limits.max_agent_parallel':2})
        self.assertEqual(result['state'],'pending')
        self.assertNotEqual(Config.load(self.cfg.path,self.cfg.root).get('autopilot','concurrent_lanes'),3)
        self.assertTrue(settings.snapshot(self.c,self.cfg)['pending'])
        store.finish_task(self.c,tid,'failed',error='fixture finished');self.c.commit()
        reloaded=Config.load(self.cfg.path,self.cfg.root)
        self.assertEqual(settings.apply_pending(self.c,reloaded)['state'],'applied')
        from wq import runner,autopilot
        self.assertEqual(autopilot.lane_limit(reloaded),3)
        self.assertEqual(runner.agent_pool_size(reloaded),2)
        self.assertIsNone(settings.pending(self.c))

    def test_external_config_edit_is_preserved_and_failed_apply_is_visible(self):
        tid,_=store.enqueue_task(self.c,'agent_call',{},'settings-busy');self.c.commit()
        settings.request(self.cfg,{'autopilot.interval_s':900})
        raw=util.read_json(self.cfg.path);raw.setdefault('autopilot',{})['error_cooldown_s']=1234;util.write_json(self.cfg.path,raw)
        store.finish_task(self.c,tid,'failed');self.c.commit()
        result=settings.apply_pending(self.c,self.cfg)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(util.read_json(self.cfg.path),raw)
        self.assertIsNone(settings.pending(self.c))
        self.assertEqual(settings.snapshot(self.c,self.cfg)['last']['state'],'failed')

    def test_transition_failure_rolls_back_file_and_database(self):
        raw=util.read_json(self.cfg.path)
        with patch('wq.research_lifecycle.apply_transition',side_effect=ValueError('fixture gate')):
            result=settings.request(self.cfg,{'autopilot.interval_s':900})
        self.assertEqual(result['state'],'failed')
        self.assertEqual(util.read_json(self.cfg.path),raw)
        self.assertIsNone(lifecycle.stop_kind(self.c,'fixture-dual-epoch'))

    def test_recovery_after_file_write_before_database_commit(self):
        tid,_=store.enqueue_task(self.c,'agent_call',{},'settings-crash');self.c.commit()
        settings.request(self.cfg,{'autopilot.interval_s':900})
        doc=settings.pending(self.c)
        util.write_json(self.cfg.path,doc['target'])
        store.set_flag(self.c,settings.PENDING,json.dumps({**doc,'state':'applying'}))
        store.finish_task(self.c,tid,'failed');self.c.commit()
        cfg=Config.load(self.cfg.path,self.cfg.root)
        self.assertEqual(settings.apply_pending(self.c,cfg)['state'],'applied')
        self.assertEqual(cfg.get('autopilot','interval_s'),900)
        self.assertEqual(settings.apply_pending(self.c,cfg)['state'],'none')

    def test_bad_values_do_not_persist_and_caps_cannot_drop_below_used(self):
        raw=util.read_json(self.cfg.path)
        for key,value in [('research_dual_loop.max_model_starts','-1'),('limits.max_agent_parallel','9'),('research_dual_loop.max_reads_per_source','6'),('routing.max_retries','4'),('routing.authorized_until','2099-01-01'),('routing.authorized_until','2000-01-01T00:00:00Z')]:
            with self.assertRaises(ValueError):settings.parse(self.cfg,key,value)
        store.set_flag(self.c,'dual_model_reservations:fixture-dual','64');self.c.commit()
        with self.assertRaises(ValueError):settings.request(self.cfg,{'research_dual_loop.max_model_starts':63})
        self.assertEqual(util.read_json(self.cfg.path),raw)

    def test_evidence_total_source_and_daily_caps_reach_actual_gates(self):
        self.cfg.data['research_dual_loop'].update(max_evidence_reads=2,max_reads_per_source=1,max_evidence_tasks_per_day=1)
        a={'adapter':'local_material_v1','source':{'fixture':'a'}}
        b={'adapter':'local_material_v1','source':{'fixture':'b'}}
        evidence.reserve_read(self.c,self.cfg,a)
        with self.assertRaises(ValueError):evidence.reserve_read(self.c,self.cfg,a)
        evidence.reserve_read(self.c,self.cfg,b)
        with self.assertRaises(ValueError):evidence.reserve_read(self.c,self.cfg,{'adapter':'local_material_v1','source':{'fixture':'c'}})
        self.assertTrue(evidence.daily_capacity(self.c,self.cfg))
        store.enqueue_task(self.c,'research_evidence_verify',{},'fixture-daily')
        self.assertFalse(evidence.daily_capacity(self.c,self.cfg))

    def test_discovery_cap_and_experiment_request_cap_are_independent(self):
        self.assertEqual(settings.request(self.cfg,{'research_dual_loop.max_empty_discoveries':3})['state'],'applied')
        for cid in (700,701):meta.record_discovery(self.c,self.cfg,cid,False,self.f.p)
        self.assertTrue(meta.discovery_permission(self.c,self.cfg,self.f.p)['allowed'])
        meta.record_discovery(self.c,self.cfg,702,False,self.f.p)
        self.assertFalse(meta.discovery_permission(self.c,self.cfg,self.f.p)['allowed'])
        result=lifecycle.set_experiment_request_limit(self.c,12)
        self.assertEqual(set(result['requests_by_arm'].values()),{12})
        self.assertEqual(result['cycles'],10)

    def test_snapshot_marks_waived_or_replaced_settings_inactive(self):
        self.cfg.data['history_research']={'enabled':True}
        self.cfg.data['debug_authorization']={'enabled':True,'starts_at':util.now_iso(),'expires_at':(util.now()+__import__('datetime').timedelta(days=1)).isoformat(),'agents':['grok'],'unlimited':True,'evidence':'fixture owner'}
        rows=settings.snapshot(self.c,self.cfg)['entries']
        history=next(e for e in rows if e['key']=='history_research.every_cycles')
        self.assertFalse(history['active'])
        self.assertIn('双环',history['note'])


class ProviderSettingsTests(unittest.TestCase):
    def test_model_role_changes_preserve_profile_and_wait_for_drain(self):
        from wq import providers,routing
        fixture=test_routing.RoutingTests('test_three_retries_then_fallback_and_preserve_evidence')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        cfg,c=fixture.cfg,fixture.conn
        path=Path(cfg.resolve(cfg.get('routing','profiles_file',default='config/profiles.json')))
        before=path.read_bytes();catalog=routing.catalog(cfg)
        name=next(iter(catalog['providers']))
        tid,_=store.enqueue_task(c,'agent_call',{},'role-pending');c.commit()
        result=providers.assign_role(cfg,c,'engineering',name)
        self.assertEqual(result['state'],'pending')
        self.assertEqual(path.read_bytes(),before)
        store.finish_task(c,tid,'failed');c.commit()
        self.assertEqual(settings.apply_pending(c,cfg)['state'],'applied')
        self.assertEqual(routing.catalog(cfg)['presets'][result['preset']]['routes']['engineering'][0],name)
        self.assertEqual(path.read_bytes(),before)


    def test_retry_and_timeout_values_are_frozen_into_real_dispatch(self):
        f=test_routing.RoutingTests('test_three_retries_then_fallback_and_preserve_evidence')
        f.setUp();self.addCleanup(f.doCleanups)
        f.cfg.data['routing'].update(max_retries=0,retry_delay_s=77,provider_timeouts={'a':13,'b':14})
        tid,_=f.enqueue()
        f.tick()
        frozen=json.loads(f.conn.execute('SELECT snapshot_json FROM task_routes WHERE task_id=?',(tid,)).fetchone()[0])
        self.assertEqual(frozen['retries'],0)
        self.assertEqual(frozen['delays'],[77,77,77])
        self.assertEqual(frozen['providers']['a']['timeout_s'],13)
        self.assertEqual(f.conn.execute('SELECT max_attempts FROM tasks WHERE task_id=?',(tid,)).fetchone()[0],2)
        f.tick()
        self.assertEqual(f.status(tid),'succeeded')
        self.assertEqual(f.conn.execute("SELECT COUNT(*) FROM agent_calls WHERE agent='a'").fetchone()[0],1)

    def test_pinned_source_is_used_by_desktop_children(self):
        from wq import desktop_control
        with patch.dict(os.environ,{'WQ_ENGINE_SOURCE':'/fixture/pinned','PYTHONPATH':'/old'}):
            self.assertEqual(desktop_control._child_env()['PYTHONPATH'],'/fixture/pinned')
