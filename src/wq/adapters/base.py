"""Adapter 协议与错误分类。真实 adapter 必须遵守：

- 401/403 → AdapterError(AUTH)，上层暂停并标记身份/权限问题；
- 429 → AdapterError(RATE_LIMIT, retry_after=官方 Retry-After)，上层退避且总等待有上限；
- 超时/连接中断 → AdapterError(UNKNOWN_REMOTE)：远端可能已接受，先对账，绝不盲目重发；
- 没有官方幂等键就不要伪造；dedup_key 只做本地去重，不构成远端幂等。

模拟通过 / 提交接受 / 最终有效 / 报酬资格 / 实际到账是五个不同状态。
"""
from __future__ import annotations

from ..errors import AdapterError  # re-export


class SimAdapter:
    name = "abstract"

    def status(self) -> dict:
        return {"adapter": self.name}

    def simulate(self, payload: dict) -> dict:
        """返回 {remote_id: str} 回执。抛 AdapterError 表达失败类别。"""
        raise AdapterError(AdapterError.NOT_IMPLEMENTED, "simulate 未实现")

    def submit(self, payload: dict) -> dict:
        raise AdapterError(AdapterError.NOT_IMPLEMENTED, "submit 未实现")

    def reconcile(self, unknown: dict) -> dict:
        """对账 unknown 任务/模拟/提交。返回每项的可核实结论或仍需人工。"""
        raise AdapterError(AdapterError.NOT_IMPLEMENTED, "reconcile 未实现")
