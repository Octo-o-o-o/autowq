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
