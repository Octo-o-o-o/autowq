import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from helpers import make_env
from wq import cli, exporter, store
from wq.i18n import default_language


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
