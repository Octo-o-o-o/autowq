import contextlib
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('mac_app_setup', Path(__file__).resolve().parents[1] / 'packaging/macos/app_setup.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class AppSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.resources = self.root / 'Resources'; self.resources.mkdir()
        self.support = self.root / 'Application Support'
        self.venv = self.support / 'venv'
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.wheel = self.resources / 'wq_pilot-0.2.4-py3-none-any.whl'
        self.wheel.write_bytes(b'fixture first build')
        for name, value in [('RESOURCES', self.resources), ('APP_SUPPORT', self.support), ('VENV', self.venv)]:
            p = patch.object(setup, name, value); p.start(); self.addCleanup(p.stop)
        self.support.mkdir()
        for name, value in [('find_python', '/fixture/python'), ('usable', True), ('ensure_venv', self.venv/'bin/python')]:
            p = patch.object(setup, name, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch.object(setup, 'run'); self.run = p.start(); self.addCleanup(p.stop)

    def install(self):
        with contextlib.redirect_stdout(io.StringIO()): setup.setup(str(self.workspace))

    def test_changed_wheel_same_version_reinstalls_and_identical_wheel_skips(self):
        self.install()
        command = self.run.call_args_list[0].args[0]
        self.assertIn('--force-reinstall', command)
        self.assertEqual(command[-1], str(self.wheel))
        self.run.reset_mock(); self.install(); self.run.assert_not_called()
        self.wheel.write_bytes(b'fixture repaired same version')
        self.install(); self.assertEqual(self.run.call_count, 2)

    def test_busy_engine_can_open_without_replacing_the_wheel(self):
        config = self.workspace/'config'; config.mkdir()
        (config/'config.json').write_text(json.dumps({'paths': {'run_dir': 'custom-run'}}))
        run = self.workspace/'custom-run'; run.mkdir()
        out = io.StringIO()
        with (run/'agent-runner.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with contextlib.redirect_stdout(out):
                setup.setup(str(self.workspace), allow_busy=True)
        self.run.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertTrue(payload['engine_pending'])
        self.assertFalse((self.support/'engine-wheel.sha256').exists())

    def test_stop_after_cycle_is_only_a_flag_while_a_cycle_is_open(self):
        config = self.workspace/'config'; config.mkdir()
        (config/'config.json').write_text(json.dumps({'paths': {'db': 'var/wq.db'}}))
        db = self.workspace/'var'; db.mkdir()
        import sqlite3
        conn = sqlite3.connect(db/'wq.db')
        conn.execute('CREATE TABLE state_flags(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)')
        conn.execute("CREATE TABLE research_cycles(cycle_id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
        conn.execute("INSERT INTO research_cycles(state) VALUES('researching')")
        conn.commit(); conn.close()
        self.assertTrue(setup.mark_stop_after_cycle(self.workspace))
        conn = sqlite3.connect(db/'wq.db')
        flags = dict(conn.execute('SELECT key, value FROM state_flags'))
        conn.close()
        self.assertEqual(flags['autopilot_stop_after_cycle'], '1')
        self.assertNotEqual(flags.get('paused'), '1')

    def test_running_queue_blocks_upgrade_without_changing_marker(self):
        config = self.workspace/'config'; config.mkdir()
        (config/'config.json').write_text(json.dumps({'paths': {'run_dir': 'custom-run'}}))
        run = self.workspace/'custom-run'; run.mkdir()
        with (run/'agent-runner.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SystemExit): self.install()
        self.run.assert_not_called()
        self.assertFalse((self.support/'engine-wheel.sha256').exists())

    def test_failed_install_never_marks_wheel_current(self):
        self.run.side_effect = RuntimeError('fixture install failure')
        with self.assertRaises(RuntimeError): self.install()
        self.assertFalse((self.support/'engine-wheel.sha256').exists())

    def test_activate_does_not_interrupt_loaded_runner_or_menu(self):
        app = self.root/'WorldQuant.app'
        binary = app/'Contents/MacOS/WorldQuantMenu'; binary.parent.mkdir(parents=True); binary.touch()
        with patch.object(setup.Path, 'home', return_value=self.root), patch.object(setup, 'loaded', return_value=True), patch.object(setup, 'bootout') as bootout, contextlib.redirect_stdout(io.StringIO()):
            setup.activate(str(self.workspace), str(app))
        bootout.assert_not_called(); self.run.assert_not_called()

    def test_activate_runner_only_skips_menu_agent(self):
        with patch.object(setup.Path, 'home', return_value=self.root), patch.object(setup, 'loaded', return_value=False), contextlib.redirect_stdout(io.StringIO()) as out:
            setup.activate(str(self.workspace), None, runner_only=True)
        agents = self.root/'Library/LaunchAgents'
        self.assertTrue((agents/f'{setup.RUNNER_LABEL}.plist').exists())
        self.assertFalse((agents/f'{setup.MENU_LABEL}.plist').exists())
        self.assertEqual(self.run.call_count, 1)
        self.assertNotIn('menubar', json.loads(out.getvalue()))


class BundledRuntimeTests(unittest.TestCase):
    """应用自带运行时：不找系统 Python，复制到 Application Support 后再建 venv。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.resources = self.root / 'Resources'
        self.support = self.root / 'Application Support'
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        (self.resources).mkdir()
        (self.resources/'wq_pilot-0.2.4-py3-none-any.whl').write_bytes(b'fixture')
        for arch in ('arm64', 'x86_64'):
            runtime = self.resources/'runtime'/arch
            (runtime/'bin').mkdir(parents=True)
            (runtime/'bin/python3').write_text(arch)
            (runtime/'WQ-RUNTIME-ID').write_text(f'cpython-fixture-{arch}\n')
        for name, value in [('RESOURCES', self.resources), ('APP_SUPPORT', self.support),
                            ('VENV', self.support/'venv'), ('STAGED_PYTHON', self.support/'python')]:
            p = patch.object(setup, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(setup, 'usable', side_effect=lambda python: Path(python).exists()); p.start(); self.addCleanup(p.stop)
        p = patch.object(setup, 'run'); self.run = p.start(); self.addCleanup(p.stop)
        self.system = patch.object(setup, 'find_python', return_value=None); self.system.start(); self.addCleanup(self.system.stop)

    def test_setup_uses_staged_runtime_without_system_python(self):
        created = []

        def fake_venv(base):
            created.append(base)
            python = self.support/'venv/bin/python'; python.parent.mkdir(parents=True, exist_ok=True); python.touch()
            return python
        with patch.object(setup, 'ensure_venv', side_effect=fake_venv), contextlib.redirect_stdout(io.StringIO()):
            setup.setup(str(self.workspace))
        staged = self.support/'python'
        self.assertEqual(created, [str(staged/'bin/python3')])
        self.assertIn((staged/'bin/python3').read_text(), ('arm64', 'x86_64'))
        marker = (self.support/'engine-wheel.sha256').read_text()
        self.assertIn('+cpython-fixture-', marker)

    def test_new_runtime_replaces_staged_copy_and_old_venv(self):
        runtime = setup.bundled_runtime()
        setup.stage_runtime(runtime)
        (self.support/'venv').mkdir(); (self.support/'venv/stale').touch()
        (runtime/'WQ-RUNTIME-ID').write_text('cpython-next\n')
        setup.stage_runtime(runtime)
        self.assertEqual((self.support/'python/WQ-RUNTIME-ID').read_text().strip(), 'cpython-next')
        self.assertFalse((self.support/'venv').exists())
