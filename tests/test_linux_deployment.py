import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
linux=module('setup_linux');docker=module('docker_provider')

class LinuxDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'project with spaces';self.runtime=self.base/'runtime'
        (self.root/'config').mkdir(parents=True);(self.root/'scripts').mkdir()
        for f in (ROOT/'config').glob('*.example.json'):shutil.copyfile(f,self.root/'config'/f.name)
        shutil.copyfile(ROOT/'scripts/docker_provider.py',self.root/'scripts/docker_provider.py')
        linux.render(self.root,self.runtime)
        self.config=json.loads((self.runtime/'containers.json').read_text())
        self.work=self.runtime/'jobs/job/attempt';self.work.mkdir(parents=True)
        self.prompt=self.work/'prompt.md';self.prompt.write_text('public knowledge only')

    def test_units_and_config_disabled(self):
        cfg=json.loads((self.root/'config/config.json').read_text())
        self.assertFalse(cfg['autopilot']['enabled']);self.assertFalse(cfg['brain_api']['enabled'])
        profiles=json.loads((self.root/'config/profiles.json').read_text())
        self.assertNotIn('zcode',profiles['providers']);self.assertEqual(profiles['default'],'core-only')
        self.assertIn('OnUnitInactiveSec=60',(self.runtime/'autowq.timer').read_text())
        self.assertIn('KillMode=control-group',(self.runtime/'autowq.service').read_text())
        with self.assertRaises(ValueError):linux.render(self.root,self.runtime)

    def test_container_mounts_only_attempt_and_one_home(self):
        argv,name,ttl=docker.command(self.config,'grok',self.work,self.prompt,'')
        mounts=[argv[i+1] for i,a in enumerate(argv) if a=='--mount']
        self.assertEqual(len(mounts),2)
        self.assertIn('source='+str(self.work),mounts[0]);self.assertIn('target=/home/agent',mounts[1])
        self.assertNotIn(str(self.root),' '.join(argv));self.assertNotIn('docker.sock',' '.join(argv))
        self.assertIn('--read-only',argv);self.assertIn('--pull=never',argv)
        self.assertEqual(docker.command(self.config,'grok',self.work,self.prompt,'')[1],name)
        self.assertLess(ttl,900)

    def test_outside_prompt_and_symlinks_rejected(self):
        other=self.base/'secret';other.write_text('fixture')
        with self.assertRaises(ValueError):docker.command(self.config,'grok',self.work,other,'')
        (self.work/'link').symlink_to(other)
        with self.assertRaises(ValueError):docker.command(self.config,'grok',self.work,self.prompt,'')

    def test_whole_jobs_mount_rejected(self):
        with self.assertRaises(ValueError):docker.command(self.config,'grok',self.runtime/'jobs',self.prompt,'')

    @unittest.skipUnless(os.environ.get('WQ_DOCKER_TEST_IMAGE'),'explicit Docker integration image required')
    def test_real_container_artifact_and_no_host_project_access(self):
        secret=self.root/'secret';secret.write_text('host-only-fixture')
        item=self.config['providers']['grok'];item['image']=os.environ['WQ_DOCKER_TEST_IMAGE'];item['home_volume']='autowq-test-'+os.urandom(8).hex();item['timeout_s']=10
        item['argv']=['python3','-c','import json;from pathlib import Path;assert not Path('+repr(str(secret))+').exists();assert not Path("/var/run/docker.sock").exists();Path("/work/result.json").write_text(json.dumps({"status":"completed","summary":"container fixture","findings":[]}))']
        try:
            result=docker.run(self.config,'grok',self.work,self.prompt)
            self.assertEqual(result,0)
            self.assertEqual(json.loads((self.work/'result.json').read_text())['status'],'completed')
            name=docker.command(self.config,'grok',self.work,self.prompt,'')[1]
            self.assertNotEqual(subprocess.run([self.config['docker_bin'],'inspect',name],capture_output=True).returncode,0)
        finally:subprocess.run([self.config['docker_bin'],'volume','rm',item['home_volume']],capture_output=True)

    @unittest.skipUnless(os.environ.get('WQ_DOCKER_TEST_IMAGE'),'explicit Docker integration image required')
    def test_real_container_timeout_removes_owned_container(self):
        item=self.config['providers']['grok'];item['image']=os.environ['WQ_DOCKER_TEST_IMAGE'];item['home_volume']='autowq-test-'+os.urandom(8).hex();item['timeout_s']=1
        item['argv']=['python3','-c','import time;time.sleep(60)']
        try:
            self.assertEqual(docker.run(self.config,'grok',self.work,self.prompt),124)
            name=docker.command(self.config,'grok',self.work,self.prompt,'')[1]
            self.assertNotEqual(subprocess.run([self.config['docker_bin'],'inspect',name],capture_output=True).returncode,0)
        finally:subprocess.run([self.config['docker_bin'],'volume','rm',item['home_volume']],capture_output=True)
