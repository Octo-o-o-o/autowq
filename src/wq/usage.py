"""从模型 CLI 的元数据中读取最小用量摘要，不读取回复正文或思维过程。"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def _first_json_object(text: str):
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char != "{":
            continue
        try:
            return decoder.raw_decode(text[i:])[0]
        except json.JSONDecodeError:
            continue
    return None


def _positive(value):
    return value if isinstance(value, int) and value >= 0 else None


def grok_log(path: str | None) -> dict | None:
    """Grok JSON 输出包含 usage/total_cost_usd；警告行会被跳过。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        obj = _first_json_object(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("usage"), dict):
        return None
    raw = obj["usage"]
    values = {
        "input_tokens": _positive(raw.get("input_tokens")),
        "cache_read_tokens": _positive(raw.get("cache_read_input_tokens")),
        "cache_write_tokens": _positive(raw.get("cache_creation_input_tokens")),
        "output_tokens": _positive(raw.get("output_tokens")),
        "reasoning_tokens": _positive(raw.get("reasoning_tokens")),
    }
    parts = [values[k] for k in
             ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")]
    if not all(v is not None for v in parts):
        return None
    values["total_tokens"] = _positive(raw.get("total_tokens")) or sum(parts)
    models = obj.get("modelUsage")
    if isinstance(models, dict) and models:
        model = next(iter(models))
    else:
        model = None
    cost = obj.get("total_cost_usd")
    return {**values, "model": model, "cost_usd": cost if isinstance(cost, (int, float)) else None,
            "cost_label": "CLI估算（不是供应商账单）" if isinstance(cost, (int, float)) else None,
            "source": "Grok CLI JSON"}


def devin_transcript(path: str | None) -> dict | None:
    """Devin ATIF transcript 的 final_metrics 只含计量字段，不读取 steps 正文。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    metrics = obj.get("final_metrics") if isinstance(obj, dict) else None
    if not isinstance(metrics, dict):
        return None
    prompt = _positive(metrics.get("total_prompt_tokens"))
    completion = _positive(metrics.get("total_completion_tokens"))
    cached = _positive(metrics.get("total_cached_tokens"))
    if prompt is None or completion is None:
        return None
    agent = obj.get("agent") if isinstance(obj, dict) else None
    model = agent.get("model_name") if isinstance(agent, dict) else None
    return {
        "input_tokens": prompt, "cache_read_tokens": cached or 0,
        "cache_write_tokens": None, "output_tokens": completion,
        "reasoning_tokens": None, "total_tokens": prompt + completion,
        "model": model, "cost_usd": None,
        "cost_label": "未知（Devin 当前没有可核对的美元单价）",
        "source": "Devin ATIF final_metrics",
    }


def _call_session_ids(call: dict, cli_dir: Path) -> list[str]:
    db_path = cli_dir / "sessions.db"
    if not db_path.is_file():
        return []
    try:
        prompt_path = Path(call["prompt_file"])
        # prompt 通常位于 workdir/jobs；同时支持直接放在 workdir 的配置。
        dirs = []
        for candidate in (prompt_path.parent, *prompt_path.parents):
            text = str(candidate)
            if text not in dirs:
                dirs.append(text)
            if len(dirs) >= 4:
                break
        placeholders = ",".join("?" for _ in dirs)
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1) as conn:
            rows = conn.execute(
                f"SELECT id FROM sessions WHERE working_directory IN ({placeholders}) "
                "AND created_at BETWEEN ? AND ?",
                (*dirs, _epoch_seconds(call["started_at"]) - 15,
                 _epoch_seconds(call.get("finished_at") or call["started_at"]) + 15),
            ).fetchall()
        return [row[0] for row in rows]
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError):
        return []


def _epoch_seconds(value: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def for_call(call: dict, usage_dirs: list[str] | None = None) -> dict | None:
    """按账本日志或已配置的 Devin workdir 寻找最小计量摘要。

    Devin 的 `--export` 文件写在其 workdir；该目录通常与本地日志目录
    不同，所以调用方可传入当前配置中的 workdir 作为额外候选路径。
    """
    log = call.get("log_path")
    if log and Path(log).is_file():
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith('{"wq_usage":'):
                    try: return json.loads(line)["wq_usage"]
                    except (ValueError, KeyError): pass
    if call.get("agent") == "grok":
        return grok_log(call.get("log_path"))
    if call.get("agent") != "devin":
        return None
    log_path = call.get("log_path")
    if log_path:
        log = Path(log_path)
        candidates = [log.parent.parent / ".usage" / f"{log.stem}.json"]
        for directory in usage_dirs or []:
            if directory:
                candidates.append(Path(directory) / ".usage" / f"{log.stem}.json")
        for export in candidates:
            result = devin_transcript(str(export))
            if result:
                return result
    cli_dir = Path(os.environ.get("DEVIN_CLI_DIR", str(Path.home() / ".local/share/devin/cli")))
    for session_id in _call_session_ids(call, cli_dir):
        result = devin_transcript(str(cli_dir / "transcripts" / f"{session_id}.json"))
        if result:
            return result
    return None


def zero() -> dict:
    return {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            "cost_label": "未调用", "source": "本地队列"}


def display(value: dict | None, running: bool = False, lang: str = 'zh') -> str:
    from .i18n import text, translate
    if value is None:
        if running:
            return text(lang, "消耗：token 暂未获得实时计量；折合金额：未知（供应商尚未写出可核对数据）",
                        "Cost: no live token metering yet; equivalent amount unknown (no verifiable data from the provider)")
        return text(lang, "消耗：token 未取到；折合金额：未知（没有可核对的调用计量）",
                    "Cost: no tokens retrieved; equivalent amount unknown (no verifiable call metering)")
    total = value.get("total_tokens")
    inp = value.get("input_tokens")
    cache = value.get("cache_read_tokens")
    out = value.get("output_tokens")
    unknown = text(lang, "未知", "unknown")
    fmt = lambda n: f"{n:,}" if isinstance(n, int) and n >= 0 else unknown
    if lang == 'zh':
        parts = f"总 {fmt(total)}（输入 {fmt(inp)}，缓存读 {fmt(cache)}，输出 {fmt(out)}）"
    else:
        parts = f"total {fmt(total)} (input {fmt(inp)}, cache read {fmt(cache)}, output {fmt(out)})"
    cost = value.get("cost_usd")
    if isinstance(cost, (int, float)):
        money = f"${cost:.4f}"
        if value.get("cost_label"):
            money += text(lang, f"，{value['cost_label']}", f", {translate(value['cost_label'], lang)}")
    else:
        money = translate(value.get("cost_label"), lang) or unknown
    sep = '；' if lang == 'zh' else '; '
    head = text(lang, "消耗：", "Cost: ")
    unit = " token" if lang == 'zh' else ""
    return f"{head}{parts}{unit}{sep}{text(lang, '折合', 'equivalent')} {money}"
