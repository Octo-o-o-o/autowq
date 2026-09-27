import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import onboard


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'checkout';self.root.mkdir()
        source=SCRIPTS.parent
        shutil.copytree(source/'config',self.root/'config',ignore=shutil.ignore_patterns('config.json','profiles.json','autopilot-policy.json'))
        shutil.copytree(source/'scripts',self.root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        self.runtime=self.base/'runtime'
        self.models={'grok':'chosen-grok-model','devin':'chosen-devin-model'}
        self.bins={'grok':sys.executable,'devin':sys.executable}
        self.roles={'research':'grok','review':'devin','engineering':'devin'}

    def configure(self,platform='darwin'):
        return onboard.configure(self.root,self.runtime,['grok','devin'],self.models,self.bins,self.roles,{'grok':'xhigh'},platform)

    def test_mac_choice_is_pinned_with_no_side_effect_activation(self):
        cfg=self.configure();p=json.loads((self.root/'config/profiles.json').read_text())
        self.assertEqual(set(p['providers']),{'grok','devin'})
        self.assertEqual(p['providers']['grok']['model'],'chosen-grok-model')
        self.assertEqual(p['providers']['grok']['argv'][-4:],['--model','{model}','--reasoning-effort','xhigh'])
        self.assertEqual(p['presets']['local']['routes']['review'],['devin'])
        self.assertFalse(cfg['models']['grok']['enabled'])
        self.assertFalse(cfg['brain_api']['enabled']);self.assertFalse(cfg['brain_submission']['enabled'])
        self.assertFalse(cfg['autopilot']['enabled']);self.assertIsNone(cfg['routing']['authorized_until'])
        self.assertEqual(cfg['autopilot']['max_cycles_total'],4)
        self.assertIn(sys.executable,(self.runtime/'launchers/grok').read_text())
        self.assertTrue(cfg['models']['grok']['bin'].endswith('/launchers/grok'))
        before=(self.root/'config/config.json').read_bytes()
        with self.assertRaises(ValueError):self.configure()
        self.assertEqual(before,(self.root/'config/config.json').read_bytes())

    def test_linux_pins_inner_command_and_outer_profile(self):
        cfg=self.configure('linux')
        p=json.loads((self.root/'config/profiles.json').read_text())
        c=json.loads((self.runtime/'containers.json').read_text())
        self.assertEqual(set(c['providers']),{'grok','devin'})
        self.assertEqual(c['providers']['devin']['argv'][-2:],['--model','{model}'])
        self.assertEqual(p['providers']['devin']['model'],'chosen-devin-model')
        self.assertEqual(cfg['onboarding']['platform'],'linux')

    def test_invalid_choice_writes_nothing(self):
        self.roles['review']='grok'
        with self.assertRaises(ValueError):self.configure()
        self.assertFalse(self.runtime.exists());self.assertFalse((self.root/'config/config.json').exists())
        self.roles['review']='devin';self.models.pop('grok')
        with self.assertRaises(ValueError):self.configure()
        self.assertFalse(self.runtime.exists())

    def test_single_provider_remains_explicitly_limited(self):
        cfg=onboard.configure(self.root,self.runtime,['grok'],{'grok':'chosen'},self.bins,
            dict.fromkeys(('research','review','engineering'),'grok'),{},'darwin')
        self.assertTrue(cfg['onboarding']['single_provider']);self.assertFalse(cfg['autopilot']['enabled'])

    def test_noninteractive_entry_and_list_do_not_call_provider(self):
        args=[sys.executable,str(SCRIPTS/'onboard.py'),'--non-interactive','--lang','en',
              '--root',str(self.root),'--runtime',str(self.runtime),'--providers','grok,devin',
              '--model','grok=chosen-grok','--model','devin=chosen-devin',
              '--binary','grok='+sys.executable,'--binary','devin='+sys.executable]
        r=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('No paid inference',r.stdout)
        self.assertIn('https://platform.worldquantbrain.com/sign-up',r.stdout)
        self.assertIn('Login pending',r.stdout)
        before=(self.root/'config/config.json').read_bytes()
        r=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(before,(self.root/'config/config.json').read_bytes())

    def test_cli_help_forwards_options(self):
        import os
        env={**os.environ,'PYTHONPATH':str(SCRIPTS.parent/'src')}
        r=subprocess.run([sys.executable,'-m','wq','onboard','--help'],capture_output=True,text=True,timeout=10,env=env)
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('--reasoning-effort',r.stdout)

    def test_login_flow_records_pending_and_resume_without_overwrite(self):
        from unittest.mock import patch
        self.configure()
        with patch('builtins.input',side_effect=['n','n','n']),patch('subprocess.call') as call:
            code=onboard.login_flow(self.root,'en')
        self.assertEqual(code,3);call.assert_not_called()
        saved=json.loads((self.root/'config/config.json').read_text())
        self.assertFalse(saved['onboarding']['authentication_verified'])
        with patch('builtins.input',side_effect=['n','n','y']),patch('subprocess.call',return_value=0) as call:
            code=onboard.login_flow(self.root,'en')
        self.assertEqual(code,0)
        self.assertEqual(call.call_args.args[0][-2:],['brain','login'])
        saved=json.loads((self.root/'config/config.json').read_text())
        self.assertEqual(saved['onboarding']['login']['brain'],'authenticated')
        self.assertNotIn('password',json.dumps(saved).lower())
        self.assertFalse(saved['brain_api']['enabled'])

    def test_interactive_language_defaults_and_skip_login(self):
        from unittest.mock import patch
        args=['--root',str(self.root),'--runtime',str(self.runtime),'--providers','grok,devin',
              '--model','grok=chosen','--model','devin=review','--binary','grok='+sys.executable,
              '--binary','devin='+sys.executable,'--research','grok','--review','devin',
              '--engineering','devin','--reasoning-effort','grok=high','--skip-login']
        with patch.dict('os.environ',{'LC_ALL':'zh_CN.UTF-8'}),patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=['en','y']):
            self.assertEqual(onboard.main(args),0)
        self.assertEqual(json.loads((self.root/'config/config.json').read_text())['ui']['language'],'en')
