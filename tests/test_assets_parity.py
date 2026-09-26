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


if __name__ == '__main__':
    unittest.main()
