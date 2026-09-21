"""数据契约（无 jsonschema 依赖的手写校验器）。

三种文档：
- 研究卡 wq.research-card/v1：机制先行的假设族，可全抽象（无字段目录时不得写真表达式）。
- 导入结果 wq.imported-result/v1：manual-import 模式入口；synthetic=true 的 fixture
  与真实结果分开存放、分开统计。
- 模拟配置：research card variant / import 内嵌的 config 对象，键集合固定。

校验返回错误列表；空列表 = 通过。
"""
from __future__ import annotations

import datetime as dt
import math

CARD_SCHEMA = "wq.research-card/v1"
RESULT_SCHEMA = "wq.imported-result/v1"

# 模拟配置允许的键。平台真实字段目录未核验前，expression 可以是抽象描述，
# 但 enqueue 真实模拟要求 expression 非空且 catalog_verified=true。
CONFIG_KEYS = {
    "region", "universe", "delay", "neutralization", "decay", "truncation",
    "data_version", "fields", "catalog_verified", "extra",
}


def _type_err(errs: list[str], obj, key: str, typ, required: bool):
    if key not in obj or obj[key] is None:
        if required:
            errs.append(f"缺少必需字段 {key}")
        return None
    v = obj[key]
    if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
        errs.append(f"字段 {key} 类型应为 {getattr(typ, '__name__', typ)}")
        return None
    return v


def validate_sim_config(cfg, errs: list[str], prefix: str = "config") -> None:
    if not isinstance(cfg, dict):
        errs.append(f"{prefix} 必须是对象")
        return
    for k in cfg:
        if k not in CONFIG_KEYS:
            errs.append(f"{prefix}.{k}: 未知配置键")
    for k in ("region", "universe", "neutralization", "data_version"):
        if k in cfg and cfg[k] is not None and not isinstance(cfg[k], str):
            errs.append(f"{prefix}.{k} 应为字符串")
    for k in ("delay", "decay"):
        if k in cfg and cfg[k] is not None:
            if not isinstance(cfg[k], int) or isinstance(cfg[k], bool) or cfg[k] < 0:
                errs.append(f"{prefix}.{k} 应为非负整数")
    if "truncation" in cfg and cfg["truncation"] is not None:
        t = cfg["truncation"]
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not (0 <= t <= 1):
            errs.append(f"{prefix}.truncation 应为 [0,1] 数值")
    if "fields" in cfg and cfg["fields"] is not None:
        if not isinstance(cfg["fields"], list) or not all(isinstance(x, str) for x in cfg["fields"]):
            errs.append(f"{prefix}.fields 应为字符串数组")
    if "catalog_verified" in cfg and cfg["catalog_verified"] is not None:
        if not isinstance(cfg["catalog_verified"], bool):
            errs.append(f"{prefix}.catalog_verified 应为布尔")
    if "extra" in cfg and cfg["extra"] is not None and not isinstance(cfg["extra"], dict):
        errs.append(f"{prefix}.extra 应为对象")


def validate_research_card(obj) -> list[str]:
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["文档必须是 JSON 对象"]
    if obj.get("schema") != CARD_SCHEMA:
        errs.append(f"schema 应为 {CARD_SCHEMA}")
    _type_err(errs, obj, "hypothesis_id", str, True)
    _type_err(errs, obj, "family_id", str, True)
    _type_err(errs, obj, "mechanism", str, True)
    if "sources" in obj and obj["sources"] is not None:
        if not isinstance(obj["sources"], list):
            errs.append("sources 应为数组")
    variants = _type_err(errs, obj, "variants", list, True) or []
    if isinstance(variants, list) and len(variants) > 4:
        errs.append("variants 超过每族 4 个初筛配置上限")
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            errs.append(f"variants[{i}] 应为对象")
            continue
        _type_err(errs, v, "variant_id", str, True)
        if "expression" in v and v["expression"] is not None:
            if not isinstance(v["expression"], str):
                errs.append(f"variants[{i}].expression 应为字符串")
            elif v["expression"].strip():
                cfg = v.get("config") or {}
                if not isinstance(cfg, dict) or cfg.get("catalog_verified") is not True:
                    errs.append(
                        f"variants[{i}] 写了表达式但 config.catalog_verified!=true："
                        "无获准字段目录时不得生成看似可执行的表达式"
                    )
        validate_sim_config(v.get("config", {}), errs, f"variants[{i}].config")
    prov = obj.get("provenance")
    if not isinstance(prov, dict):
        errs.append("缺少 provenance 对象")
    else:
        if not isinstance(prov.get("synthetic"), bool):
            errs.append("provenance.synthetic 必填布尔（研究卡也必须显式标记合成）")
        _type_err(errs, prov, "origin", str, True)
    return errs


def validate_pnl(pnl, errs: list[str]) -> None:
    if not isinstance(pnl, dict):
        errs.append("pnl 应为对象")
        return
    if pnl.get("kind") not in ("cumulative", "daily"):
        errs.append("pnl.kind 应为 cumulative|daily（语义必须显式，禁止二次差分）")
    pts = pnl.get("points")
    if not isinstance(pts, list):
        errs.append("pnl.points 应为数组")
        return
    for i, p in enumerate(pts):
        if (not isinstance(p, dict) or not isinstance(p.get("date"), str)
                or type(p.get("value")) not in (int, float) or not math.isfinite(p['value'])):
            errs.append(f"pnl.points[{i}] 需 {{date: 'YYYY-MM-DD', value: number}}")
            continue
        try:
            dt.date.fromisoformat(p["date"])
        except ValueError:
            errs.append(f"pnl.points[{i}].date 非法: {p['date']}")


def validate_imported_result(obj) -> list[str]:
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["文档必须是 JSON 对象"]
    if obj.get("schema") != RESULT_SCHEMA:
        errs.append(f"schema 应为 {RESULT_SCHEMA}")
    if not isinstance(obj.get("synthetic"), bool):
        errs.append("synthetic 必填布尔；fixture 必须标 true，真实结果标 false 且需 --real 确认")
    src = obj.get("source")
    if src not in ("manual", "api", "fixture"):
        errs.append("source 应为 manual|api|fixture")
    if obj.get("synthetic") is False and src == "fixture":
        errs.append("source=fixture 但 synthetic=false：合成数据不得冒充真实结果")
    sim = obj.get("simulation")
    if not isinstance(sim, dict):
        errs.append("缺少 simulation 对象")
    else:
        _type_err(errs, sim, "expression", str, True)
        if isinstance(sim.get("expression"), str) and not sim["expression"].strip():
            errs.append("simulation.expression 不能为空串")
        validate_sim_config(sim.get("config", {}), errs, "simulation.config")
        if "stats" in sim and sim["stats"] is not None and not isinstance(sim["stats"], dict):
            errs.append("simulation.stats 应为对象")
        if "checks" in sim and sim["checks"] is not None and not isinstance(sim["checks"], dict):
            errs.append("simulation.checks 应为对象")
        if "pnl" in sim and sim["pnl"] is not None:
            validate_pnl(sim["pnl"], errs)
        _type_err(errs, sim, "remote_id", str, False)
        _type_err(errs, sim, "observed_at", str, False)
    q = obj.get("quality")
    if q is not None:
        if not isinstance(q, dict) or q.get("status") not in ("pass", "fail", "unknown"):
            errs.append("quality.status 应为 pass|fail|unknown")
    return errs
