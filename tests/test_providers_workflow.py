import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from wq import db, providers, provider_runtime as runtime, routing, store, workflow, runner, usage
from wq.config import Config


RESULT = {'status': 'completed', 'summary': 'fixture result', 'findings': []}


def write_fake_cli(path, code):
    # Application Support 等含空格的解释器路径不能直接用作 shebang。
    path.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(code) + ' "$@"\n')
    path.chmod(0o700)


class ProviderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'config').mkdir()
        self.cfg = Config({'paths': {'db': 'var/test.db', 'run_dir': 'var/run', 'private_dir': str(self.root/'private')},
            'models': {'api': {'enabled': True}, 'review': {'enabled': True}},
            'budgets': {'api': {'enabled': True, 'remaining': 3, 'unit': 'calls'}},
            'routing': {'profiles_file': 'config/profiles.json', 'work_root': str(self.root/'jobs')}}, str(self.root), str(self.root/'config/config.json'))
        self.conn = db.connect(self.cfg.db_path); self.addCleanup(self.conn.close)
        self.doc = workflow.template({'research': ['api'], 'review': ['review'], 'engineering': ['api']})
        self.profile = {'default': 'test', 'providers': {'api': {'argv': [sys.executable, '-c', 'pass']}, 'review': {'argv': [sys.executable, '-c', 'pass']}},
                        'presets': {'test': {'routes': self.doc['routes']}}}
        self.save()

    def save(self):
        (self.root/'config/config.json').write_text(json.dumps(self.cfg.data))
        (self.root/'config/profiles.json').write_text(json.dumps(self.profile))

    def server(self, mode='openai', status=200):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append((self.path, dict(self.headers), None))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[{"id":"fixture-model"}]}')
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, dict(self.headers), body))
                self.send_response(status); self.end_headers()
                if status != 200: self.wfile.write(b'secret echoed by bad endpoint'); return
                if mode == 'anthropic': obj = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(RESULT)}], 'usage': {'input_tokens': 11, 'output_tokens': 7}}
                elif mode == 'openai-responses': obj = {'status': 'completed', 'output': [{'content': [{'type':'output_text','text':json.dumps(RESULT)}]}], 'usage': {'input_tokens':11,'output_tokens':7}}
                else: obj = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(RESULT)}}], 'usage': {'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}}
                self.wfile.write(json.dumps(obj).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        item = providers.api_definition(mode, 'fixture-model', 'http://127.0.0.1:'+str(server.server_port)+'/v1', 'WQ_FIXTURE_KEY')
        return item, calls

    def test_three_api_protocols_headers_paths_tokens_and_model_listing(self):
        for protocol, path in [('openai','/chat/completions'),('openai-responses','/responses'),('anthropic','/messages')]:
            with self.subTest(protocol=protocol):
                item, calls = self.server(protocol)
                with patch.dict(os.environ, {'WQ_FIXTURE_KEY': 'fixture-key'}):
                    content, meter = runtime.api_generate(item, 'public research')
                    listing = providers.models('custom', item)
                self.assertEqual(json.loads(content), RESULT)
                self.assertEqual(calls[0][0], '/v1'+path)
                headers = {k.lower():v for k,v in calls[0][1].items()}
                self.assertEqual(headers.get('x-api-key' if protocol=='anthropic' else 'authorization'), 'fixture-key' if protocol=='anthropic' else 'Bearer fixture-key')
                self.assertEqual(meter['total_tokens'], 18); self.assertIsNone(meter['cost_usd'])
                self.assertEqual(listing['models'], ['fixture-model'])

    def test_http_failure_never_echoes_credentials_or_response(self):
        item, _ = self.server(status=401)
        with patch.dict(os.environ, {'WQ_FIXTURE_KEY':'do-not-log'}):
            with self.assertRaisesRegex(ValueError, '^API HTTP 401$'): runtime.api_generate(item, 'hello')

    def test_api_rejects_incomplete_output(self):
        item=providers.api_definition('openai','fixture')
        with patch.object(runtime,'api_request',return_value={'choices':[{'finish_reason':'length','message':{'content':json.dumps(RESULT)}}]}):
            with self.assertRaisesRegex(ValueError,'incomplete'):runtime.api_generate(item,'hi')

    def test_url_and_private_key_rules(self):
        for url in ('https://user:key@host/v1', 'http://example.com/v1', 'https://example.com/v1?key=secret'):
            with self.assertRaises(ValueError): providers.api_definition('openai','fixture',url)
        path = providers.save_key(str(self.root/'private'), 'example', 'test-secret')
        self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): providers.save_key(str(self.root/'private'), 'example', 'new')

    def test_routed_api_subprocess_end_to_end_and_budget(self):
        item, calls = self.server()
        self.profile['providers']['api'] = providers.definition('openai','fixture-model',api=item)
        self.save()
        prompt=self.root/'request.md';prompt.write_text('Return fixture JSON')
        tid, job = routing.enqueue_job(self.conn,self.cfg,'research',str(prompt))
        with patch.dict(os.environ, {'WQ_FIXTURE_KEY':'fixture-key'}):
            code, _ = runner.run_once(self.conn,self.cfg)
        task=dict(self.conn.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone())
        self.assertEqual(task['status'],'succeeded',task['last_error'])
        self.assertEqual(json.loads((Path(job)/'result.json').read_text()), RESULT)
        call=dict(self.conn.execute('SELECT * FROM agent_calls').fetchone())
        self.assertEqual(usage.for_call(call)['total_tokens'],18)
        self.assertEqual(len(calls),1)
        self.cfg.data['budgets']['api']['remaining']=1
        routing.enqueue_job(self.conn,self.cfg,'research',str(prompt))
        runner.run_once(self.conn,self.cfg)
        self.assertEqual(len(calls),1)

    def test_all_new_cli_parsers_and_model_pins(self):
        for kind in ('claude','codex','gemini','copilot','qwen','opencode'):
            command=runtime.cli_command(kind,'/bin/fake','exact-model','input')
            self.assertIn('exact-model',command);self.assertNotIn('--yolo',command)
        self.assertEqual(runtime.cli_response('claude',json.dumps({'result':json.dumps(RESULT)}))[0],json.dumps(RESULT))
        with self.assertRaises(ValueError): runtime.cli_response('codex','{}')
        self.assertEqual(runtime.parse_result('```json\n'+json.dumps(RESULT)+'\n```'),RESULT)
        with self.assertRaises(ValueError):runtime.parse_result('{"status":"completed"}')

    def test_discovery_does_not_run_cli_and_manual_catalog_is_honest(self):
        with patch('subprocess.run') as run:
            inventory=providers.inventory(); providers.models('claude')
        run.assert_not_called()
        self.assertEqual(len(inventory),13)
        self.assertTrue(all(r['model_access']=='not_verified' for r in inventory))

    def test_workflow_rejects_bypass_unknown_fields_and_bad_route(self):
        for mutation in (lambda d:d.update(shell='evil'),lambda d:d['stages'][1].update(enabled=False),lambda d:d['stages'].reverse(),lambda d:d['routes'].update(review=['api'])):
            doc=copy.deepcopy(self.doc);mutation(doc)
            with self.assertRaises(ValueError):workflow.validate(doc,self.profile['providers'])
        workflow.validate(self.doc,self.profile['providers'])

    def test_workflow_apply_routes_prompts_and_simulation_switch(self):
        self.doc['stages'][0]['prompt']='Focus on falsifiability'
        self.doc['stages'][2]['enabled']=False
        path=self.root/'draft.json';workflow.write_new(path,self.doc)
        args=argparse.Namespace(config=self.cfg.path,action='apply',file=str(path),lang='en')
        with contextlib.redirect_stdout(io.StringIO()):workflow.command(args)
        cfg=Config.load(self.cfg.path,str(self.root))
        self.assertFalse(workflow.stage_enabled(cfg,'simulate'))
        self.assertIn('Focus on falsifiability',workflow.customize(cfg,'research','contract'))
        self.assertEqual(routing.active_preset(self.conn,cfg),'advanced')
        self.assertEqual(routing.catalog(cfg)['presets']['advanced']['routes'],self.doc['routes'])
        self.assertEqual(cfg.budget('api')['remaining'],3)

    def test_workflow_apply_rejects_active_queue_and_ai_edit_only_queues(self):
        path=self.root/'draft.json';workflow.write_new(path,self.doc)
        args=argparse.Namespace(config=self.cfg.path,action='ai-edit',file=str(path),instruction='Improve hypothesis prompt',lang='en')
        with contextlib.redirect_stdout(io.StringIO()):workflow.command(args)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
        self.assertNotIn('workflow',json.loads(Path(self.cfg.path).read_text()))
        args.action='apply'
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'active queue'):workflow.command(args)

    def test_new_user_api_and_cli_configuration(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import onboard
        source=Path(__file__).resolve().parents[1]
        for platform, selected in [('darwin',['claude','codex']),('linux',['openai','anthropic'])]:
            root=self.root/platform;root.mkdir()
            shutil.copytree(source/'config',root/'config',ignore=shutil.ignore_patterns('config.json','profiles.json','autopilot-policy.json'))
            shutil.copytree(source/'scripts',root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
            cfg=onboard.configure(root,self.root/(platform+'-runtime'),selected,dict.fromkeys(selected,'fixture-model'),dict.fromkeys(selected,sys.executable),
                {'research':selected[0],'review':selected[1],'engineering':selected[1]}, {}, platform)
            self.assertTrue(all(not cfg['models'][n]['enabled'] for n in selected))
            profile=json.loads((root/'config/profiles.json').read_text())
            self.assertEqual(set(profile['providers']),set(selected))
            self.assertTrue(all('transport' in d for d in profile['providers'].values()))

    def test_every_new_cli_adapter_runs_to_verified_result_without_real_inference(self):
        for kind in ('claude','codex','gemini','copilot','qwen','opencode'):
            with self.subTest(kind=kind):
                work=self.root/kind;work.mkdir()
                prompt=work/'prompt.md';prompt.write_text('Fixture task')
                text=json.dumps(RESULT)
                if kind=='copilot': output=text
                elif kind=='codex': output=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}})+'\n'+json.dumps({'type':'turn.completed'})
                elif kind=='opencode': output=json.dumps({'type':'text','part':{'text':text}})
                else: output=json.dumps({'result' if kind=='claude' else 'response':text})
                binary=work/'fake-cli'
                write_fake_cli(binary, 'import sys\nassert "chosen" in sys.argv\nprint('+repr(output)+')\n')
                import subprocess
                r=subprocess.run([sys.executable,runtime.__file__,json.dumps({'kind':kind,'model':'chosen','binary':str(binary)}),str(prompt)],cwd=work,capture_output=True,text=True,timeout=5)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(json.loads((work/'result.json').read_text()),RESULT)

    def test_workflow_interactive_edit_and_ai_proposal_validation(self):
        path=self.root/'draft.json';workflow.write_new(path,self.doc)
        target=self.root/'edited.json'
        args=argparse.Namespace(config=self.cfg.path,action='edit',file=str(path),output=str(target),lang='en')
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=['new name','','','','','','n','y','n','0']),contextlib.redirect_stdout(io.StringIO()):
            workflow.command(args)
        edited=workflow.read_document(target)
        self.assertEqual(edited['name'],'new name');self.assertFalse(edited['stages'][2]['enabled'])
        self.assertFalse(edited['combinations']['enabled'])
        proposal=self.root/'result.json';proposal.write_text(json.dumps(RESULT|{'workflow':edited}))
        self.assertEqual(workflow.read_document(proposal),edited)
        self.assertNotIn('workflow',json.loads(Path(self.cfg.path).read_text()))

    def test_unknown_usage_is_not_zero_or_format_error(self):
        rendered=usage.display({'input_tokens':11,'output_tokens':7,'total_tokens':18,'cache_read_tokens':None,'cost_usd':None})
        self.assertIn('未知',rendered);self.assertNotIn('$0',rendered)
        self.assertIn('未知',usage.display({'cost_usd':None}))

    @unittest.skipUnless(sys.platform=='darwin','macOS sandbox integration')
    def test_new_cli_runs_inside_actual_macos_sandbox(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import onboard
        source=Path(__file__).resolve().parents[1]
        root=self.root/'checkout';root.mkdir()
        shutil.copytree(source/'config',root/'config',ignore=shutil.ignore_patterns('config.json','profiles.json','autopilot-policy.json'))
        shutil.copytree(source/'scripts',root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        binary=self.root/'fake-claude'
        write_fake_cli(binary, 'from pathlib import Path\nimport json\ntry:\n Path('+repr(str(root/'config/config.json'))+').read_text()\n raise SystemExit("sandbox failed")\nexcept PermissionError: pass\nprint('+repr(json.dumps({'result':json.dumps(RESULT)}))+')\n')
        run=self.root/'mac-runtime'
        onboard.configure(root,run,['claude','codex'],{'claude':'fixture','codex':'fixture'},{'claude':str(binary),'codex':str(binary)},
            {'research':'claude','review':'codex','engineering':'codex'}, {}, 'darwin')
        cfg=Config.load(str(root/'config/config.json'),str(root))
        cfg.data['paths']['private_dir']=str(self.root/'private');cfg.data['models']['claude']['enabled']=True
        cfg.data['budgets']['claude']={'enabled':True,'remaining':1,'unit':'calls'}
        conn=db.connect(cfg.db_path)
        try:
            prompt=self.root/'mac-prompt.md';prompt.write_text('fixture')
            tid,job=routing.enqueue_job(conn,cfg,'research',str(prompt))
            runner.run_once(conn,cfg)
            row=conn.execute('SELECT status,last_error FROM tasks WHERE task_id=?',(tid,)).fetchone()
            self.assertEqual(row['status'],'succeeded',row['last_error'])
            self.assertEqual(json.loads((Path(job)/'result.json').read_text()),RESULT)
        finally:conn.close()
