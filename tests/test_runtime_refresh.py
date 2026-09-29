"""refresh-runtime 幂等重建：修复部署 runtime 与源码漂移（entry/launchers/沙箱）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from wq import providers, setup_local  # noqa: E402
from wq.assets import path as asset_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class RuntimeRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base/'project'
        self.root.mkdir()
        self.runtime = self.base/'runtime'

    def test_refresh_restores_drifted_files(self):
        setup_local.render(self.root, self.runtime)
        entry = self.runtime/'launchers/provider_entry.py'
        entry.write_text('stale hand-edited copy\n')
        (self.runtime/'agents.sb').unlink()
        (self.runtime/'launchers/zcode').unlink()
        providers.install_runtime(self.runtime, {}, {}, root=self.root)
        self.assertEqual(entry.read_bytes(), Path(asset_path('provider_entry.py')).read_bytes())
        self.assertTrue((self.runtime/'agents.sb').is_file())
        zcode = (self.runtime/'launchers/zcode').read_text()
        self.assertIn('agents.sb', zcode)
        self.assertNotIn('multi-provider.sb', zcode)
        self.assertEqual((self.runtime/'launchers/provider_runtime.py').read_bytes(),
                         (ROOT/'src/wq/provider_runtime.py').read_bytes())
        # 再次刷新幂等，不产生差异。
        before = sorted((p.relative_to(self.runtime), p.read_bytes())
                        for p in self.runtime.rglob('*') if p.is_file())
        providers.install_runtime(self.runtime, {}, {}, root=self.root)
        after = sorted((p.relative_to(self.runtime), p.read_bytes())
                       for p in self.runtime.rglob('*') if p.is_file())
        self.assertEqual(before, after)

    def test_verified_version_embedded_in_zcode_launcher_only(self):
        setup_local.render(self.root, self.runtime)
        providers.install_runtime(self.runtime, {}, {'zcode': '0.16.9'}, root=self.root)
        self.assertIn('WQ_ZCODE_VERIFIED_VERSION=0.16.9', (self.runtime/'launchers/zcode').read_text())
        self.assertNotIn('WQ_ZCODE_VERIFIED_VERSION', (self.runtime/'launchers/cursor').read_text())
        self.assertNotIn('WQ_ZCODE_VERIFIED_VERSION', (self.runtime/'launchers/grok').read_text())

    def test_sandbox_allows_jobs_but_not_launchers_or_project(self):
        setup_local.render(self.root, self.runtime)
        sb = (self.runtime/'agents.sb').read_text()
        self.assertIn(str(self.runtime/'jobs'), sb)
        self.assertNotIn(str(self.runtime/'launchers') + '"', sb)
        self.assertIn(str(self.root), sb)  # 项目根保持禁读禁写

    def test_configured_work_dirs_are_writable_but_not_runtime_or_project(self):
        # 回归：部署的 work_root 为 runtime/routed-jobs 时，沙箱只放行 jobs/ 会让所有调用写不出 result.json。
        setup_local.render(self.root, self.runtime)
        routed, grok = self.runtime/'routed-jobs', self.base/'grok-work'
        inside = self.root/'var/jobs'
        providers.install_runtime(self.runtime, {}, {}, root=self.root,
                                  work_dirs=[routed, grok, inside, self.runtime, self.base])
        sb = (self.runtime/'agents.sb').read_text()
        allowed = sb.split('(allow file-write*', 1)[1].split('\n', 1)[0]
        self.assertIn(f'"{routed}"', allowed)
        self.assertIn(f'"{grok}"', allowed)
        for rejected in (inside, self.runtime, self.base):
            self.assertNotIn(f'"{rejected}"', allowed)

    def test_non_macos_refresh_does_not_write_sandbox_launchers(self):
        self.runtime.mkdir()
        (self.runtime/'docker_provider.py').write_text('stale')
        with patch.object(providers.sys, 'platform', 'linux'):
            providers.install_runtime(self.runtime, {}, {}, root=self.root)
        self.assertFalse((self.runtime/'agents.sb').exists())
        self.assertFalse((self.runtime/'launchers/zcode').exists())
        self.assertEqual((self.runtime/'docker_provider.py').read_bytes(),
                         Path(asset_path('docker_provider.py')).read_bytes())

    def test_missing_runtime_is_readable_error(self):
        with self.assertRaises(ValueError):
            providers.install_runtime(None, {}, {})

    def test_sandbox_work_dirs_reads_routing_and_model_workdirs(self):
        from helpers import make_env
        cfg, conn = make_env(str(self.base/'env'), {
            'routing': {'work_root': str(self.base/'routed')},
            'models': {'grok': {'workdir': str(self.base/'gw')}, 'devin': {}}})
        self.addCleanup(conn.close)
        self.assertEqual(providers.sandbox_work_dirs(cfg), [str(self.base/'routed'), str(self.base/'gw')])

    @unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').exists(), 'macOS sandbox only')
    def test_doctor_sandbox_probe_reports_unwritable_work_root(self):
        # 临时目录位于恒放行的 /private/var/folders，用项目根内的 work_root 触发禁写。
        import contextlib
        import io
        from helpers import make_env
        from wq import cli
        setup_local.render(self.root, self.runtime)
        blocked, ok = self.root/'var/jobs', self.runtime/'jobs'
        blocked.mkdir(parents=True)
        cfg, conn = make_env(str(self.base/'env'), {
            'routing': {'work_root': str(blocked)},
            'models': {'grok': {'workdir': str(ok)}},
            'onboarding': {'runtime': str(self.runtime)}})
        self.addCleanup(conn.close)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(['--config', cfg.path, 'doctor'])
        rows = {d: [l for l in out.getvalue().splitlines() if l.endswith(str(d)) or str(d) + '；' in l
                    or str(d) + ';' in l] for d in (blocked, ok)}
        self.assertTrue(rows[blocked] and rows[blocked][0].startswith('[XX]'), rows)
        self.assertTrue(rows[ok] and rows[ok][0].startswith('[ok]'), rows)
        self.assertEqual(list(blocked.iterdir()) + list(ok.iterdir()), [])  # 探针文件已清理


if __name__ == '__main__':
    unittest.main()
