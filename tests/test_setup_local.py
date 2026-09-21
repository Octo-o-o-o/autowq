import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('setup_local',ROOT/'scripts/setup_local.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'project with spaces'
        (self.root/'config').mkdir(parents=True);(self.root/'scripts').mkdir()
        for p in (ROOT/'config').glob('*.example.json'):shutil.copyfile(p,self.root/'config'/p.name)
        shutil.copyfile(ROOT/'scripts/provider_entry.py',self.root/'scripts/provider_entry.py')
        self.runtime=self.base/'runtime with spaces'

    def test_disabled_relocatable_config_and_refuse_overwrite(self):
        setup.render(self.root,self.runtime)
        cfg=json.loads((self.root/'config/config.json').read_text())
        self.assertFalse(cfg['brain_api']['enabled']);self.assertFalse(cfg['autopilot']['enabled'])
        self.assertTrue(all(not m['enabled'] for m in cfg['models'].values()))
        self.assertEqual(cfg['routing']['work_root'],str(self.runtime/'jobs'))
        with (self.runtime/'com.worldquant.wq-runner.plist').open('rb') as f:pl=plistlib.load(f)
        self.assertEqual(pl['ProgramArguments'][0],str(self.root/'wq'))
        before=(self.root/'config/config.json').read_bytes()
        with self.assertRaises(ValueError):setup.render(self.root,self.runtime)
        self.assertEqual((self.root/'config/config.json').read_bytes(),before)

    def test_runtime_cannot_be_inside_project(self):
        with self.assertRaises(ValueError):setup.render(self.root,self.root/'runtime')
        self.assertFalse((self.root/'config/config.json').exists())

    @unittest.skipUnless(sys.platform=='darwin','macOS sandbox only')
    def test_real_sandbox_denies_project_and_allows_job_files(self):
        setup.render(self.root,self.runtime)
        marker=self.root/'marker';marker.write_text('fixture')
        script="""from pathlib import Path
import sys
try: Path(sys.argv[1]).read_text()
except PermissionError: pass
else: raise SystemExit('project read was allowed')
try: Path(sys.argv[1]).write_text('bad')
except PermissionError: pass
else: raise SystemExit('project write was allowed')
Path(sys.argv[2]).write_text('ok')
"""
        dest=self.runtime/'jobs/output'
        r=subprocess.run(['/usr/bin/sandbox-exec','-f',str(self.runtime/'agents.sb'),sys.executable,'-c',script,str(marker),str(dest)],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(dest.read_text(),'ok');self.assertEqual(marker.read_text(),'fixture')
