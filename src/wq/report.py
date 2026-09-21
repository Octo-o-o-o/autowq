"""一页报告：默认只统计真实记录，synthetic 明确分列；PnL 只是研究指标。"""
from __future__ import annotations

import json

from . import store, util


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def build_report(conn, cfg, include_synthetic: bool = False) -> str:
    L: list[str] = []
    week = util.iso_week()
    stage = store.current_account_stage(conn)
    paused = store.is_paused(conn)

    L.append(f"# 周报 {week}（生成于 {util.now_iso()}）")
    L.append("")
    L.append(f"- 账号阶段: **{stage['stage']}**（证据: {stage['evidence']}）")
    L.append(f"- 状态: {'**已暂停** — ' + (store.get_flag(conn, 'pause_reason') or '') if paused else '运行中'}")
    L.append(f"- adapter: {cfg.adapter_mode()}；数据口径: {'含 synthetic' if include_synthetic else '仅真实记录'}")
    L.append("")

    # 假设族与候选
    fams = _rows(conn, "SELECT family_id, hypothesis_id, origin, synthetic, status FROM families")
    cands = _rows(conn, "SELECT family_id, status, synthetic FROM candidates")
    real_f = [f for f in fams if include_synthetic or not f["synthetic"]]
    L.append(f"## 研究\n- 假设族 {len(real_f)} / 上限 {cfg.get('limits','families_max')}；"
             f"候选 {len([c for c in cands if include_synthetic or not c['synthetic']])}")
    for f in real_f:
        n = len([c for c in cands if c["family_id"] == f["family_id"]])
        tag = " [synthetic]" if f["synthetic"] else ""
        L.append(f"  - {f['family_id']} ({f['origin']}) {f['status']}：{n} 个配置{tag}")
    L.append("")

    # 模拟漏斗：平台 checks 与研究质量分开
    sims = _rows(conn, "SELECT status, synthetic, source FROM simulations")
    sims = [s for s in sims if include_synthetic or not s["synthetic"]]
    by = {}
    for s in sims:
        by[s["status"]] = by.get(s["status"], 0) + 1
    week_n = sum(
        1 for r in _rows(conn, "SELECT imported_at, synthetic FROM simulations")
        if (include_synthetic or not r["synthetic"])
        and util.iso_week(util.parse_iso(r["imported_at"])) == week)
    L.append(f"## 模拟（本周 {week_n}/{cfg.get('limits','sims_per_week')}）")
    L.append("- 状态分布: " + (json.dumps(by, ensure_ascii=False) if by else "无"))
    L.append("- 口径: passed=官方 checks 通过；quality_failed=平台过线但研究质量失败；两者都不等于提交或收入")
    L.append("")

    subs = _rows(conn, "SELECT status FROM submissions")
    bys = {}
    for s in subs:
        bys[s["status"]] = bys.get(s["status"], 0) + 1
    L.append(f"## 提交: {json.dumps(bys, ensure_ascii=False) if bys else '无'}"
             "（accepted→final_valid→报酬资格→到账，逐级分开）")
    L.append("")

    # 现金账本 —— 只有 payments 算收入
    pays = _rows(conn, "SELECT kind, amount, currency FROM payments")
    agg = {}
    for p in pays:
        k = (p["kind"], p["currency"])
        agg[k] = agg.get(k, 0) + p["amount"]
    L.append("## 现金账本（PnL/积分/平台资金不是收入）")
    if agg:
        for (kind, cur), amt in sorted(agg.items()):
            L.append(f"- {kind}: {amt} {cur}")
        L.append("- 净现金 = received − 当期现金费用；详见 expenses")
    else:
        L.append("- 无记录 → 可依赖收入按 0 计")
    exps = _rows(conn, "SELECT kind, amount, unit, occurred_at, note FROM expenses ORDER BY occurred_at")
    if exps:
        L.append("- 费用:")
        for e in exps:
            L.append(f"  - {e['occurred_at']} {e['kind']} {e['amount']} {e['unit']} — {e['note']}")
    L.append("")

    # 模型使用与预算
    L.append("## 模型调用与预算")
    limit_keys = {"grok": "grok_calls_per_week", "devin": "devin_tickets_per_week"}
    for agent in cfg.data.get("models", {}):
        used = store.agent_calls_this_week(conn, agent)
        lim = cfg.get("limits", limit_keys.get(agent, f"{agent}_calls_per_week"), default="n/a")
        b = cfg.budget(agent)
        btxt = "disabled" if not b.get("enabled") else \
            ("unknown" if b.get("remaining") is None else f"{b['remaining']} {b.get('unit')}")
        if b.get('enabled') and b.get('unit') == 'calls' and b.get('remaining') is not None:
            consumed = conn.execute(
                'SELECT COUNT(*) FROM agent_calls WHERE agent=? AND pid IS NOT NULL AND started_at>=?',
                (agent, b.get('as_of', ''))).fetchone()[0]
            btxt = f"本地剩余 {max(0, b['remaining'] - consumed)}/{b['remaining']} 次；供应商 token/余额未核实"
        wstate, winfo = cfg.debug_window(agent)
        if wstate == "ok":
            L.append(f"- {agent}: 本周 {used} 次（debug 授权窗口内不限本地周上限，至 {winfo}）；"
                     f"预算 {btxt}（窗口豁免本地 remaining，供应商余额仍未知，非无限）；"
                     f"enabled={cfg.model(agent).get('enabled')}")
        else:
            line = (f"- {agent}: 本周 {used}/{lim} 次；预算 {btxt}；"
                    f"enabled={cfg.model(agent).get('enabled')}")
            if wstate == "invalid":
                line += f"；debug_authorization 无效: {winfo}"
            L.append(line)
    L.append("")

    unk = store.unknown_items(conn)
    n_unk = len(unk["tasks"]) + len(unk["simulations"]) + len(unk["submissions"])
    L.append(f"## 待对账: {n_unk} 项（UNKNOWN 状态下不得盲目重发）")
    incs = _rows(conn, "SELECT incident_id, attempts, status FROM incidents")
    if incs:
        L.append("- incidents: " + "; ".join(
            f"{i['incident_id']} {i['attempts']}次/{i['status']}" for i in incs))
    blocked = _rows(conn, "SELECT task_id, kind, last_error FROM tasks WHERE status='blocked'")
    for t in blocked:
        L.append(f"- blocked task {t['task_id']} ({t['kind']}): {t['last_error']}")
    return "\n".join(L) + "\n"
