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
        self.profile = {'default': 'test', 'providers': {'api': {'argv': [sys.executable, '-c', 'pass']}, 'review': {'argv': [sys.executable, '-c', 'pass  # review']}},
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
            with self.assertRaisesRegex(ValueError, '^API HTTP 401 limit=auth$'): runtime.api_generate(item, 'hello')

    def test_quota_429_reports_class_and_http_date_wait_without_body(self):
        import email.utils, time
        when = email.utils.formatdate(time.time() + 7200, usegmt=True)
        body = b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"secret-ish detail","details":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(429); self.send_header('Retry-After', when); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        item = providers.api_definition('openai', 'm', 'http://127.0.0.1:'+str(server.server_port)+'/v1', 'WQ_FIXTURE_KEY')
        with patch.dict(os.environ, {'WQ_FIXTURE_KEY': 'k'}):
            with self.assertRaises(ValueError) as caught: runtime.api_generate(item, 'hello')
        message = str(caught.exception)
        self.assertRegex(message, r'^API HTTP 429 limit=quota retry_after=(71\d\d|7200)$')
        self.assertNotIn('secret', message)

    def test_limit_classes_and_retry_after_forms(self):
        self.assertEqual(runtime.limit_kind(429, '{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}'), 'rate')
        gemini_minute = ('{"error":{"code":429,"message":"You exceeded your current quota, please check your plan and billing details.",'
                         '"status":"RESOURCE_EXHAUSTED","details":[{"violations":[{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},'
                         '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"37s"}]}}')
        self.assertEqual(runtime.limit_kind(429, gemini_minute), 'rate')
        self.assertEqual(runtime.limit_suffix(429, gemini_minute, {}), ' limit=rate retry_after=37')
        self.assertEqual(runtime.limit_kind(429, gemini_minute.replace('PerMinute', 'PerDay')), 'quota')
        self.assertEqual(runtime.limit_kind(429, '{"error":{"message":"Rate limit exceeded: free-models-per-day"}}'), 'quota')
        self.assertEqual(runtime.limit_kind(429, '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}'), 'quota')
        self.assertEqual(runtime.limit_kind(403, '{"code":"AllocationQuota.FreeTierOnly"}'), 'quota')
        self.assertEqual(runtime.limit_kind(429, '{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}'), 'quota')
        self.assertEqual(runtime.limit_kind(402, ''), 'quota')
        self.assertEqual(runtime.limit_kind(401, ''), 'auth')
        self.assertEqual(runtime.limit_kind(500, 'boom'), '')
        self.assertEqual(runtime.retry_after_seconds('30'), 30)
        self.assertIsNone(runtime.retry_after_seconds('soon'))
        self.assertEqual(runtime.limit_suffix(429, 'Rate limit reached', {'x-ratelimit-reset-requests': '6m0s'}), ' limit=rate retry_after=360')

    def test_cli_env_drops_unlisted_keys(self):
        source = {'PATH': '/bin', 'HOME': '/h', 'LC_ALL': 'C', 'OPENAI_API_KEY': 'paid', 'ANTHROPIC_API_KEY': 'paid',
                  'WQ_HOMELAB_API_KEY': 'custom', 'CODEX_HOME': '/c', 'GEMINI_API_KEY': 'g'}
        self.assertEqual(runtime.cli_env({'kind': 'codex'}, source), {'PATH': '/bin', 'HOME': '/h', 'LC_ALL': 'C', 'CODEX_HOME': '/c'})
        self.assertEqual(runtime.cli_env({'kind': 'gemini', 'env_passthrough': ['GEMINI_API_KEY']}, source)['GEMINI_API_KEY'], 'g')
        with self.assertRaises(ValueError): runtime.cli_env({'kind': 'gemini', 'env_passthrough': ['bad name']}, source)

    def test_free_preset_install_writes_reset_rule_and_rejects_same_service_review(self):
        self.save()
        providers.install_custom(self.cfg, {'name': 'or-a', 'preset': 'openrouter-free', 'roles': []}, key='fixture-provider-secret-one')
        providers.install_custom(self.cfg, {'name': 'or-b', 'preset': 'openrouter-free', 'model': 'google/gemma-4-31b-it:free'}, key='fixture-provider-secret-two')
        saved = json.loads((self.root/'config/profiles.json').read_text())
        entry = saved['providers']['or-b']
        self.assertEqual(entry['transport']['base_url'], 'https://openrouter.ai/api/v1')
        self.assertEqual(entry['transport']['model'], 'google/gemma-4-31b-it:free')
        self.assertEqual(entry['quota_reset'], {'tz': 'UTC', 'at': '00:00', 'period': 'daily'})
        self.assertNotIn('fixture-provider-secret-two', json.dumps(saved))
        providers.assign_role(self.cfg, self.conn, 'research', 'or-a')
        with self.assertRaisesRegex(ValueError, '同一服务'):
            providers.assign_role(self.cfg, self.conn, 'review', 'or-b')
        with self.assertRaises(ValueError):
            providers.install_custom(self.cfg, {'name': 'x', 'preset': 'no-such-preset'}, key='k')

    def test_every_free_preset_passes_transport_validation(self):
        from wq import free_apis
        for name, item in free_apis.PRESETS.items():
            with self.subTest(preset=name):
                api, reset = free_apis.api_spec(name)
                self.assertTrue(api['base_url'].startswith('https://'))
                self.assertIn(item['model'], item['models'])
                if reset:
                    from wq import routing
                    self.assertIsNotNone(routing.next_reset(reset))

    def test_api_rejects_incomplete_output(self):
        item=providers.api_definition('openai','fixture')
        with patch.object(runtime,'api_request',return_value={'choices':[{'finish_reason':'length','message':{'content':json.dumps(RESULT)}}]}):
            with self.assertRaisesRegex(ValueError,'incomplete'):runtime.api_generate(item,'hi')

    def test_url_and_private_key_rules(self):
        for url in ('https://user:key@host/v1', 'http://example.com/v1', 'https://example.com/v1?key=secret', 'http://8.8.8.8/v1'):
            with self.assertRaises(ValueError): providers.api_definition('openai','fixture',url)
        providers.api_definition('openai', 'fixture', 'http://192.168.1.20:8000/v1')
        providers.api_definition('anthropic', 'fixture', 'http://10.0.0.8/v1')
        # link-local（含云主机元数据地址）不是用户自己的服务，不给明文 HTTP
        for url in ('http://169.254.169.254/v1', 'http://[fe80::1]/v1'):
            with self.assertRaises(ValueError): providers.api_definition('openai', 'fixture', url)
    def test_custom_endpoint_stores_the_key_outside_profiles_and_leads_the_route(self):
        self.save()
        added = providers.install_custom(self.cfg, {
            'name': 'homelab', 'protocol': 'openai', 'model': 'qwen',
            'base_url': 'http://192.168.1.20:8000/v1', 'roles': ['research'],
        }, key='local-secret')
        self.assertEqual(added['name'], 'homelab')
        saved = json.loads((self.root/'config/profiles.json').read_text())
        self.assertNotIn('local-secret', json.dumps(saved))
        self.assertEqual(saved['providers']['homelab']['transport']['base_url'], 'http://192.168.1.20:8000/v1')
        self.assertTrue(saved['providers']['homelab']['transport']['api_key_file'])
        self.assertEqual(saved['presets']['test']['routes']['research'][0], 'api')
        assigned = providers.assign_role(self.cfg, self.conn, 'research', 'homelab')
        self.assertEqual(assigned['provider'], 'homelab')
        routed = json.loads((self.root/'config/profiles.json').read_text())
        self.assertEqual(routed['presets']['test']['routes']['research'][0], 'homelab')
        with self.assertRaisesRegex(ValueError, '不同'):
            providers.assign_role(self.cfg, self.conn, 'review', 'homelab')
        self.assertEqual(self.cfg.data['budgets']['homelab']['remaining'], 10000)
        self.assertTrue(self.cfg.data['models']['homelab']['enabled'])
        with self.assertRaises(ValueError):
            providers.install_custom(self.cfg, {
                'name': 'homelab', 'protocol': 'openai', 'model': 'qwen',
                'base_url': 'http://127.0.0.1:11434/v1', 'roles': ['research'], 'allow_no_key': True,
            })

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
        # qwen 0.18 的 json 输出是消息数组，终态为最后一条 type=result。
        qwen=[{'type':'system','subtype':'session_start'},{'type':'assistant','message':{}},
              {'type':'result','subtype':'success','is_error':False,'result':json.dumps(RESULT),'usage':{'input_tokens':3,'output_tokens':4}}]
        self.assertEqual(runtime.cli_response('qwen',json.dumps(qwen)),(json.dumps(RESULT),{'input_tokens':3,'output_tokens':4}))
        for bad in ([{'type':'assistant'}], [{'type':'result','subtype':'error_max_turns','is_error':True}], ['x']):
            with self.assertRaises(ValueError): runtime.cli_response('qwen',json.dumps(bad))
        with self.assertRaises(ValueError):
            runtime.cli_response('claude',json.dumps({'type':'result','subtype':'error_max_turns','result':''}))
        codex='\n'.join(json.dumps(e) for e in ({'type':'item.completed','item':{'type':'agent_message','text':'done'}},
                                                   {'type':'turn.completed','usage':{'input_tokens':5,'cached_input_tokens':1,'output_tokens':2}}))
        self.assertEqual(runtime.cli_response('codex',codex),('done',{'input_tokens':5,'output_tokens':2,'total_tokens':7}))
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
