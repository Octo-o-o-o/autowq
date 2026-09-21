"""测试公共件：临时目录配置 + DB。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wq.config import Config  # noqa: E402
from wq.db import connect  # noqa: E402


def make_cfg(tmp: str, overrides: dict | None = None) -> Config:
    data = {
        "account_alias": "testacct",
        "paths": {
            "db": os.path.join(tmp, "var", "wq.db"),
            "run_dir": os.path.join(tmp, "var", "run"),
            "private_dir": os.path.join(tmp, "private"),
        },
    }
    if overrides:
        def merge(a, b):
            for k, v in b.items():
                a[k] = merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
            return a
        merge(data, overrides)
    path = os.path.join(tmp, "config", "config.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    return Config.load(path, tmp)


def make_env(tmp: str, overrides: dict | None = None):
    cfg = make_cfg(tmp, overrides)
    conn = connect(cfg.db_path)
    return cfg, conn


RESULT_DOC = {
    "schema": "wq.imported-result/v1",
    "synthetic": True,
    "source": "fixture",
    "simulation": {
        "remote_id": "R1",
        "expression": "rank(x) * 2",
        "config": {"region": "USA", "delay": 1, "catalog_verified": False},
        "stats": {"sharpe": 1.0},
        "checks": {"passed": True},
    },
    "quality": {"status": "pass"},
}
