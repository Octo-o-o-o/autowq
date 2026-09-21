"""业务去重：精确去重覆盖完整模拟配置；参数微调归回原假设族。

两级键：
- config_hash（精确）：账号别名 + 规范化表达式 + 完整模拟配置 + 数据版本。
  改空格、改大小写命中同一键；UNIQUE 约束兜底。
- family_key（族）：表达式中全部数字字面量归一为 # 后取 hash。
  只改参数 → 同一族，计为变体而非新假设。
"""
from __future__ import annotations

import re

from .util import sha256_json, sha256_text, canonical_json

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def normalize_expr(expr: str) -> str:
    # 移除全部空白并小写化：rank( x ) 与 RANK(x) 视为同一表达式
    return _WS.sub("", expr.strip().lower())


def family_key(expr: str) -> str:
    """参数无关的族键。"""
    return sha256_text("family:" + _NUM.sub("#", normalize_expr(expr)))


def config_hash(account: str, expr: str, config: dict) -> str:
    return sha256_json({
        "account": account,
        "expression": normalize_expr(expr),
        "config": config,
    })


def describe_hash(expr: str, config: dict) -> str:
    """调试辅助：返回规范化后的指纹输入，便于人工核对。"""
    return canonical_json({"expr": normalize_expr(expr), "config": config})
