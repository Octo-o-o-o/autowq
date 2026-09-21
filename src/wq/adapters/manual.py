"""manual-import adapter：API 不可用时的默认模式。

模拟/提交由用户在 BRAIN 官方 UI 手工完成，结果经 `wq import-results` 入库。
这里不做任何网络动作；simulate/submit 一律 POLICY 拒绝并指明人工步骤。
"""
from __future__ import annotations

from ..errors import AdapterError
from .base import SimAdapter


class ManualAdapter(SimAdapter):
    name = "manual"

    def status(self) -> dict:
        return {"adapter": self.name,
                "note": "人工模式：在 BRAIN UI 完成模拟后用 import-results 导入回执"}

    def simulate(self, payload: dict) -> dict:
        raise AdapterError(
            AdapterError.POLICY,
            "manual 模式不发请求：请在 BRAIN UI 运行该配置，再用 "
            "`wq import-results <file> --real` 导入真实结果")

    def submit(self, payload: dict) -> dict:
        raise AdapterError(
            AdapterError.POLICY,
            "manual 模式不提交：提交需本人确认后在官方入口完成，再用对账/导入登记状态")

    def reconcile(self, unknown: dict) -> dict:
        # 离线可完成的部分：列出需要人工核实的清单，不编造远端状态。
        items = []
        for t in unknown.get("tasks", []):
            items.append({"kind": "task", "id": t["task_id"],
                          "action": "在 BRAIN UI 核对该远端动作是否已被接受，"
                                    "然后 `wq reconcile --resolve <task_id> --outcome accepted|lost`"})
        for s in unknown.get("simulations", []):
            items.append({"kind": "simulation", "id": s["sim_id"],
                          "action": "核对 remote_id 对应模拟的最终状态并重新导入"})
        for s in unknown.get("submissions", []):
            items.append({"kind": "submission", "id": s["submission_id"],
                          "action": "核对提交最终有效性（accepted≠final_valid）并更新状态"})
        return {"adapter": self.name, "manual_actions": items}
