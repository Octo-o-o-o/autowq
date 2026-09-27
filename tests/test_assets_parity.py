"""包内资产与仓库开发正本的防漂移校验。"""
import unittest
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AssetsParityTests(unittest.TestCase):
    def test_config_examples_match_repo_masters(self):
        for name in ('config.example.json', 'profiles.example.json', 'autopilot-policy.example.json'):
            packaged = (files('wq.assets') / name).read_bytes()
            self.assertEqual(packaged, (ROOT / 'config' / name).read_bytes(), name)

    def test_docker_provider_matches_repo_master(self):
        packaged = (files('wq.assets') / 'docker_provider.py').read_bytes()
        self.assertEqual(packaged, (ROOT / 'scripts' / 'docker_provider.py').read_bytes())

    def test_cli_version_matches_pyproject(self):
        import re
        pyproject = (ROOT / 'pyproject.toml').read_text()
        version = re.search(r'^version = "(.+?)"', pyproject, re.M).group(1)
        from wq import cli, __version__
        import contextlib
        import io
        self.assertEqual(__version__, version)
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as done:
            cli.main(['--version'])
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), 'wq ' + version)


if __name__ == '__main__':
    unittest.main()
