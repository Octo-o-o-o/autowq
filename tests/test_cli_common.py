import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import cli, exporter, store
from wq.i18n import default_language, translate


class LanguagePreferenceTests(unittest.TestCase):
    def test_config_equals_reads_saved_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp); c.close()
            data=json.loads(Path(cfg.path).read_text()); data['ui']={'language':'en'}
            Path(cfg.path).write_text(json.dumps(data))
            with patch.dict('os.environ',{'LC_ALL':'zh_CN.UTF-8'}), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(['--config='+cfg.path,'config','language']),0)
            self.assertEqual(json.loads(out.getvalue())['effective'],'en')

    def test_global_auto_reaches_onboard_without_becoming_fixed_language(self):
        with patch('wq.onboard.main',return_value=0) as onboard:
            self.assertEqual(cli.main(['--lang','auto','onboard','--skip-login']),0)
            self.assertEqual(onboard.call_args.args[0],['--lang','auto','--skip-login'])

    def test_version_matches_distribution_source(self):
        import tomllib
        from wq import __version__
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(__version__,tomllib.loads((root/'pyproject.toml').read_text())['project']['version'])
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as done:
            cli.main(['--version'])
        self.assertEqual(done.exception.code,0)
        self.assertEqual(out.getvalue().strip(),'wq '+__version__)
    def test_config_language_roundtrip_and_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp); c.close()
            for args, expect in ((['en'], 'en'), (['zh'], 'zh'), (['auto'], 'auto')):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(['--config', cfg.path, 'config', 'language', *args]), 0)
                self.assertEqual(json.loads(Path(cfg.path).read_text())['ui']['language'], expect)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(['--config', cfg.path, 'config', 'language']), 0)
            shown = json.loads(out.getvalue())
            self.assertEqual(shown['setting'], 'auto')
            self.assertIn(shown['effective'], ('zh', 'en'))
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(['--config', cfg.path, 'config', 'language', 'fr'])

    def test_english_runtime_output_when_saved_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp)
            data = json.loads(Path(cfg.path).read_text()); data['ui'] = {'language': 'en'}
            Path(cfg.path).write_text(json.dumps(data))
            for args in (['doctor'], ['tasks'], ['autopilot', 'status']):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main(['--config', cfg.path, *args])
                self.assertIn(code, (0, 1), args)   # doctor 可能因本机 private_dir 模式报 1，与语言无关
                self.assertFalse(any('\u4e00' <= ch <= '\u9fff' for ch in out.getvalue()), args)
            c.close()

    def test_ledger_message_translation(self):
        self.assertEqual(translate('等待现有任务完成', 'en'), 'Waiting for existing tasks to finish')
        self.assertEqual(translate('本轮结束：模型审查拒绝，不回测；下一轮由本地调度自动领取', 'en'),
                         'Cycle finished: Model review rejected; no backtest; the next cycle will be claimed by the local scheduler')
        self.assertEqual(translate('自由文本保持原样', 'en'), '自由文本保持原样')
        self.assertEqual(translate('等待现有任务完成', 'zh'), '等待现有任务完成')

    def test_error_boundary_translates_known_validation_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp)
            data = json.loads(Path(cfg.path).read_text()); data['ui'] = {'language': 'en'}
            Path(cfg.path).write_text(json.dumps(data)); c.close()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main(['--config', cfg.path, 'brain', 'submit-check', 'BAD ID!'])
            self.assertEqual(code, 5)
            self.assertIn('Invalid Alpha ID format', err.getvalue())
            self.assertFalse(any('\u4e00' <= ch <= '\u9fff' for ch in err.getvalue()))

    def test_workflow_init_output_follows_language(self):
        from unittest.mock import patch as _patch
        catalog = {'default': 'p', 'providers': {'a': {'model': 'A'}, 'b': {'model': 'B'}},
                   'presets': {'p': {'routes': {'research': ['a'], 'review': ['b'], 'engineering': ['a']}}}}
        with tempfile.TemporaryDirectory() as tmp:
            cfg, c = make_env(tmp)
            data = json.loads(Path(cfg.path).read_text()); data['ui'] = {'language': 'en'}
            Path(cfg.path).write_text(json.dumps(data)); c.close()
            out = io.StringIO()
            with _patch('wq.routing.catalog', return_value=catalog), contextlib.redirect_stdout(out):
                code = cli.main(['--config', cfg.path, 'workflow', 'init', '--output', str(Path(tmp) / 'draft.json')])
            self.assertEqual(code, 0)
            self.assertIn('Created:', out.getvalue())
            self.assertFalse(any('\u4e00' <= ch <= '\u9fff' for ch in out.getvalue()))


class CommonTests(unittest.TestCase):
    def test_environment_language_priority(self):
        self.assertEqual(default_language({'LANG':'zh_CN.UTF-8'}),'zh')
        self.assertEqual(default_language({'LANG':'zh-TW.UTF-8'}),'zh')
        self.assertEqual(default_language({'LANG':'zh_CN','LC_ALL':'C'}),'en')
        self.assertEqual(default_language({'LANG':'de_DE'}),'en')
        self.assertEqual(default_language({'LANGUAGE':'en:zh_CN'}),'en')

    def test_help_alias_and_english_nested_help(self):
        for args in (['help','export','--lang','en'],['--lang','en','help','brain','submit'],['--lang=en','help','export']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as result:
                cli.main(args)
            self.assertEqual(result.exception.code,0)
            self.assertFalse(any('\u4e00'<=x<='\u9fff' for x in out.getvalue()))

    def test_export_omits_payload_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp)
            store.enqueue_task(c,'agent_call',{'password':'DO_NOT_EXPORT','prompt':'PRIVATE_PROMPT'},'key')
            path=Path(tmp)/'tasks.json';exporter.write(c,path,'tasks')
            data=path.read_text();self.assertNotIn('DO_NOT_EXPORT',data);self.assertNotIn('PRIVATE_PROMPT',data)
            self.assertEqual(len(json.loads(data)['rows']),1)
            with self.assertRaises(FileExistsError):exporter.write(c,path,'tasks')
            self.assertEqual(data,path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            c.close()

    def test_csv_formula_escaping_and_empty_header(self):
        self.assertEqual(exporter.csv_value(' =1+1'),"' =1+1")
        self.assertEqual(exporter.csv_value(-2.5),-2.5)
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp);p=Path(tmp)/'results.csv'
            exporter.write(c,p,'results','csv')
            self.assertTrue(p.read_text().startswith('sim_id,remote_id,'));c.close()

    def test_budget_accepts_other_builtin_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,c=make_env(tmp);c.close()
            with contextlib.redirect_stdout(io.StringIO()):
                code=cli.main(['--config',cfg.path,'budget','cursor','--enable','--remaining','2','--unit','calls'])
            self.assertEqual(code,0)
            self.assertEqual(json.loads(Path(cfg.path).read_text())['budgets']['cursor']['remaining'],2)
