"""旧 simulation/submission adapter 保留为阻断入口。

真实单次模拟使用 brain_jobs 的 brain_simulation 状态机；此类不转发，
避免旧任务绕过新路径的证据、幂等与授权门禁。自动提交未实现。
"""
from __future__ import annotations

from ..errors import AdapterError
from .base import SimAdapter

_REASON = "旧adapter不发网络请求；真实模拟请用 wq brain enqueue，提交仍需本人处理"


class BrainAPIAdapter(SimAdapter):
    name = "brain-api"

    def __init__(self, endpoint: str | None = None, verified: bool = False):
        self.endpoint = endpoint
        self.verified = verified

    def status(self) -> dict:
        return {"adapter": self.name, "endpoint": self.endpoint,
                "verified": self.verified, "state": "not_implemented", "reason": _REASON}

    def _ni(self, op: str):
        raise AdapterError(AdapterError.NOT_IMPLEMENTED, f"{op}: {_REASON}")

    def simulate(self, payload: dict) -> dict:
        self._ni("simulate")

    def submit(self, payload: dict) -> dict:
        self._ni("submit")

    def reconcile(self, unknown: dict) -> dict:
        self._ni("reconcile")


def build_adapter(cfg) -> SimAdapter:
    from .manual import ManualAdapter
    if cfg.adapter_mode() == "api":
        return BrainAPIAdapter(endpoint=cfg.get("adapters", "brain", "endpoint"),
                               verified=bool(cfg.get("adapters", "brain", "verified")))
    return ManualAdapter()
