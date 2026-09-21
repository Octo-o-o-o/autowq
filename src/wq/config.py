"""配置加载。默认全关：manual adapter、预算未知、模型禁用。

config.json 不含凭证；凭证放 private_dir（默认 ~/.worldquant-pilot，0700）。
注意：0700 与同 uid 子进程的 cwd 限定都只是卫生措施，不是沙箱——模型子进程
与父进程同 uid，仍可读 private_dir；不将凭证写入工作区/argv/日志只能降低泄漏风险；隔离需 OS 沙箱等实际访问控制。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os

from . import util

from .errors import INVALID, USAGE, WqExit

DEFAULTS: dict = {
    "account_alias": "default",
    "paths": {
        "db": "var/wq.db",
        "run_dir": "var/run",
        "private_dir": "~/.worldquant-pilot",
    },
    "limits": {
        "sims_per_week": 24,
        "sim_concurrency": 1,
        "grok_calls_per_week": 3,
        "devin_tickets_per_week": 1,
        "max_repair_attempts_per_incident": 2,
        "families_max": 3,
        "configs_per_family_max": 4,
        "rate_limit_max_wait_s": 900,
    },
    "budgets": {
        "simulation": {"enabled": False, "remaining": None, "unit": "unknown"},
        "grok": {"enabled": False, "remaining": None, "unit": "unknown"},
        "devin": {"enabled": False, "remaining": None, "unit": "unknown"},
    },
    "adapters": {
        "brain": {"mode": "manual", "endpoint": None, "verified": False},
    },
    "models": {
        "grok": {"bin": "~/.grok/bin/grok", "enabled": False, "timeout_s": 900},
        "devin": {"bin": "~/.local/bin/devin", "enabled": False, "timeout_s": 1800},
    },
    # 首周调试授权窗口：enabled+窗口有效才豁免 listed agents 的 remaining/周上限；
    # 供应商真实余额仍未知。未配置/无效一律走原有预算闸门。
    "debug_authorization": {
        "enabled": False, "starts_at": None, "expires_at": None,
        "agents": [], "unlimited": False, "evidence": "",
    },
}

_VALID_STAGES = (
    "UNKNOWN", "REGISTERED", "RESEARCHING", "GOLD", "INVITED",
    "ONBOARDING", "CONSULTANT_ACTIVE", "COMPENSATION_ELIGIBLE", "PAID",
)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict, root: str, path: str | None):
        self.data = data
        self.root = root            # 项目根：相对路径相对它解析
        self.path = path            # 配置文件实际路径（None 表示全默认）

    @classmethod
    def load(cls, path: str | None, cwd: str) -> "Config":
        if path is None:
            default_path = os.path.join(cwd, "config", "config.json")
            path = default_path if os.path.exists(default_path) else None
        if path is None:
            return cls(copy.deepcopy(DEFAULTS), root=cwd, path=None)
        try:
            with open(path, "r", encoding="utf-8") as f:
                over = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise WqExit(INVALID, f"config 读取失败 {path}: {e}")
        root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
        return cls(_merge(DEFAULTS, over), root=root, path=path)

    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def resolve(self, p: str) -> str:
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.join(self.root, p)

    @property
    def db_path(self) -> str:
        return self.resolve(self.get("paths", "db"))

    @property
    def run_dir(self) -> str:
        return self.resolve(self.get("paths", "run_dir"))

    @property
    def private_dir(self) -> str:
        return self.resolve(self.get("paths", "private_dir"))

    def ensure_private_dir(self) -> str:
        d = self.private_dir
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
        return d

    def budget(self, name: str) -> dict:
        return self.get("budgets", name, default={}) or {}

    def model(self, name: str) -> dict:
        m = dict(self.get("models", name, default={}) or {})
        if "bin" in m:
            m["bin"] = os.path.expanduser(m["bin"])
        return m

    def adapter_mode(self) -> str:
        return self.get("adapters", "brain", "mode", default="manual")

    def debug_window(self, agent: str, now: dt.datetime | None = None) -> tuple[str, str]:
        """首周 debug 授权窗口判定。返回 (state, info)：

        - "off"：未启用，或窗口不覆盖该 agent → 走原有预算逻辑（info 为空）；
        - "ok"：窗口有效且覆盖 → 该 agent 免 remaining 与周调用上限（info=到期 ISO）；
        - "invalid"：窗口已启用但不可用（过期/未开始/>7天/日期无效/缺证据/
          agents 形态错误）→ fail closed，阻断新调用（info=原因）。
        """
        w = self.get("debug_authorization", default=None)
        if not isinstance(w, dict) or not w.get("enabled"):
            return "off", ""
        agents = w.get("agents")
        if not isinstance(agents, list) or not all(isinstance(a, str) for a in agents):
            return "invalid", "agents 须为字符串数组"
        if agent not in agents:
            return "off", ""
        if not w.get("unlimited"):
            return "off", ""          # unlimited=false：窗口不豁免任何限制
        if not str(w.get("evidence") or "").strip():
            return "invalid", "缺 evidence（需明确授权证据文本）"
        try:
            start = _parse_window_ts(w.get("starts_at"), "starts_at")
            end = _parse_window_ts(w.get("expires_at"), "expires_at")
        except ValueError as e:
            return "invalid", str(e)
        if end <= start:
            return "invalid", "expires_at 必须晚于 starts_at"
        if end - start > dt.timedelta(days=7):
            return "invalid", "授权窗口超过 7 天上限"
        n = now or util.now()
        if n < start:
            return "invalid", f"窗口未开始（starts_at={w['starts_at']}）"
        if n >= end:
            return "invalid", f"窗口已过期（expires_at={w['expires_at']}）"
        return "ok", end.isoformat()


def _parse_window_ts(s, key: str) -> dt.datetime:
    if not isinstance(s, str) or not s.strip():
        raise ValueError(f"{key} 缺失或非字符串")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{key} 非法 ISO8601: {s!r}")
    if d.tzinfo is None:
        raise ValueError(f"{key} 缺时区，无法判定: {s!r}")
    return d.astimezone(util.UTC)


def validate_stage(stage: str) -> str:
    if stage not in _VALID_STAGES:
        raise WqExit(USAGE, f"未知账号阶段 {stage!r}，可选: {', '.join(_VALID_STAGES)}")
    return stage
