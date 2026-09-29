"""provider_entry 命令行构造契约：zcode/cursor 的旗标、deny 列表、护栏与探活转发。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from wq.assets import provider_entry as pe  # noqa: E402


class ProviderEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prompt = self.root/'prompt.md'; self.prompt.write_text('task body')
        self.fake_node = self.root/'node'; self.fake_node.write_text('#!/bin/sh\n')
        self.fake_cjs = self.root/'zcode.cjs'; self.fake_cjs.write_text('#!/usr/bin/env node\n')
        self.fake_cfg = self.root/'zcode-builtin.json'; self.fake_cfg.write_text('{}')
        for var in ('WQ_NODE_BIN', 'WQ_CURSOR_BIN', 'WQ_ZCODE_VERIFIED_VERSION',
                    'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE'):
            os.environ.pop(var, None)
        os.environ['WQ_NODE_BIN'] = str(self.fake_node)
        self.addCleanup(lambda: os.environ.pop('WQ_NODE_BIN', None))
        for target, value in ((pe, 'ZCODE_CJS'), (pe, 'ZCODE_PROVIDER_CONFIG')):
            stop = patch.object(target, value, str(self.root / ('zcode.cjs' if value == 'ZCODE_CJS' else 'zcode-builtin.json')))
            stop.start(); self.addCleanup(stop.stop)

    def test_zcode_argv_contract(self):
        binary, argv = pe.build_argv('zcode', '/tmp/w', str(self.prompt), 'app-configured')
        self.assertEqual(binary, str(self.fake_node))
        self.assertEqual(argv[:8], [str(self.fake_node), str(self.root/'zcode.cjs'),
                                    '--cwd', '/tmp/w', '--mode', 'yolo', '--json', '--prompt'])
        self.assertEqual(argv[8], 'task body')
        self.assertEqual(argv[9], '--disallowed-tools')
        deny = argv[10]
        for tool in ('Agent', 'Task', 'BrowserUse', 'WebSearch', 'WebFetch', 'Skill',
                     'CronCreate', 'CronUpdate', 'CronDelete', 'OffPeakCreate',
                     'SendMessage', 'ReadSessionContext', 'AskUserQuestion'):
            self.assertIn(tool, deny.split(','))
        self.assertEqual(os.environ.get('ZCODE_BUILTIN_PROVIDER_CONFIG_FILE'),
                         str(self.root/'zcode-builtin.json'))
        os.environ.pop('ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', None)

    def test_cursor_argv_contract(self):
        os.environ['WQ_CURSOR_BIN'] = str(self.fake_cjs)
        binary, argv = pe.build_argv('cursor', '/tmp/w', str(self.prompt), 'm1')
        self.assertEqual(binary, str(self.fake_cjs))
        self.assertEqual(argv, [str(self.fake_cjs), '--workspace', '/tmp/w', '--model', 'm1',
                                '--print', '--force', '--trust', '--output-format', 'json', 'task body'])

    def test_unknown_provider_rejected(self):
        with self.assertRaises(SystemExit):
            pe.build_argv('grok', '/tmp/w', str(self.prompt), 'm')

    def test_missing_app_bundle_rejected_before_spawn(self):
        with patch.object(pe, 'ZCODE_CJS', str(self.root/'missing.cjs')):
            with self.assertRaises(SystemExit) as ctx:
                pe.build_argv('zcode', '/tmp/w', str(self.prompt), 'm')
            self.assertIn('missing', str(ctx.exception))

    def test_oversized_prompt_rejected(self):
        big = self.root/'big.md'; big.write_text('x'*200_001)
        with self.assertRaises(SystemExit):
            pe.build_argv('zcode', '/tmp/w', str(big), 'm')

    def test_version_guard_blocks_on_mismatch(self):
        os.environ['WQ_ZCODE_VERIFIED_VERSION'] = '0.16.9'
        with patch.object(pe, 'zcode_version', return_value='0.17.0'):
            with self.assertRaises(SystemExit) as ctx:
                pe.build_argv('zcode', '/tmp/w', str(self.prompt), 'm')
            self.assertIn('0.16.9', str(ctx.exception))
        with patch.object(pe, 'zcode_version', return_value='0.16.9'):
            pe.build_argv('zcode', '/tmp/w', str(self.prompt), 'm')

    def test_doctor_version_passthrough(self):
        with patch.object(pe.os, 'execv') as ex, patch.object(sys, 'argv', ['x', 'zcode', '--version']):
            pe.main()
        self.assertEqual(ex.call_args[0][1], [str(self.fake_node), str(self.root/'zcode.cjs'), '--version'])
        with patch.object(pe.os, 'execv') as ex, patch.object(sys, 'argv', ['x', 'cursor', '-v']):
            os.environ['WQ_CURSOR_BIN'] = str(self.fake_node)
            pe.main()
        self.assertEqual(ex.call_args[0][1], [str(self.fake_node), '--version'])

    def test_doctor_reports_zcode_prerequisites(self):
        import contextlib
        import io
        from helpers import make_env
        from wq import cli
        os.chmod(self.fake_node, 0o755)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, conn = make_env(tmp, {
                'models': {'zcode': {'enabled': False, 'bin': str(self.fake_node)}},
                'onboarding': {'verified_versions': {'zcode': '0.16.9'}}})
            self.addCleanup(conn.close)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(['--config', cfg.path, 'doctor'])
            rendered = out.getvalue()
            for row in ('[ok] zcode node', '[ok] zcode CLI bundle',
                        '[ok] zcode builtin provider config'):
                self.assertIn(row, rendered)
            self.assertIn('0.16.9', rendered)  # 已核验版本出现在版本比对行


if __name__ == '__main__':
    unittest.main()
