"""manual-import：导入获准的真实模拟结果；synthetic fixture 严格分流。

- synthetic=false 必须 --real 显式确认；
- 精确去重（config_hash）命中 → duplicate，不重复建记录；
- 参数微调命中 family_key → 归入同一假设族，计为该族变体；
- 每族候选数上限（默认 4，对应首周每族最多 4 个初筛配置）。
"""
from __future__ import annotations

from . import contracts, dedup, store, util
from .errors import ContractError, INVALID, WqExit


def ensure_candidate(conn, cfg, expression: str, config: dict, synthetic: bool,
                     card: dict | None = None) -> tuple[str, str]:
    """返回 (candidate_id, outcome)：outcome ∈ new|exact_dup|family_variant。"""
    chash = dedup.config_hash(cfg.get("account_alias"), expression, config)
    existing = store.find_candidate_by_hash(conn, chash)
    if existing:
        if bool(existing['synthetic']) != synthetic:
            raise WqExit(INVALID, '同配置已有不同 synthetic 类型的候选，拒绝混合真实与合成证据')
        return existing["candidate_id"], "exact_dup"

    fam = None
    # 研究卡显式声明 family_id 时优先按卡归族（一张卡的多变体同属一族）；
    # 无卡（如手工导入）则按参数无关指纹 family_key 归族。
    if card and card.get("family_id"):
        fam = conn.execute("SELECT * FROM families WHERE family_id=?",
                           (card["family_id"],)).fetchone()
    fkey = dedup.family_key(expression)
    if fam is None:
        fam = store.find_family(conn, fkey)
    if fam:
        cap = int(cfg.get("limits", "configs_per_family_max", default=4))
        n = store.count_family_candidates(conn, fam["family_id"])
        if n >= cap:
            raise WqExit(
                INVALID,
                f"假设族 {fam['family_id']} 已有 {n}/{cap} 个配置；参数微调不产生新族，"
                "达到上限后需先评审再决定是否扩展")
        fid = fam["family_id"]
        outcome = "family_variant"
    else:
        hypothesis_id = (card or {}).get("hypothesis_id") or "imported-adhoc"
        origin = (card or {}).get("provenance", {}).get("origin") or \
            ("fixture" if synthetic else "manual")
        fid = store.insert_family(conn, (card or {}).get("family_id"), fkey,
                                  hypothesis_id, origin, synthetic, card)
        outcome = "new"
    cid = store.insert_candidate(conn, fid, expression, config, chash, synthetic)
    return cid, outcome


def _sim_status(sim: dict, quality: dict | None) -> str:
    checks = sim.get("checks") or {}
    if quality and quality.get("status") == "fail":
        return "quality_failed"          # 平台过线但研究质量失败 ≠ 成功
    if checks.get("passed") is True:
        return "passed"                  # 仅平台 checks 通过；提交/有效/到账另算
    if checks.get("passed") is False:
        return "failed"
    return "unchecked"                   # 没有官方 checks 信息，不猜


def import_result_obj(conn, cfg, obj: dict, allow_real: bool) -> dict:
    errs = contracts.validate_imported_result(obj)
    if errs:
        raise ContractError(errs)
    synthetic = bool(obj["synthetic"])
    if not synthetic and not allow_real:
        raise WqExit(INVALID, "synthetic=false 的真实结果需加 --real 确认，防止 fixture 混入账本")
    sim = obj["simulation"]
    remote = store.find_simulation_by_remote(conn, sim['remote_id']) if sim.get('remote_id') else None
    if remote:
        expected = store.find_candidate_by_hash(conn, dedup.config_hash(
            cfg.get('account_alias'), sim['expression'], sim.get('config', {})))
        if not expected or remote['candidate_id'] != expected['candidate_id'] or bool(remote['synthetic']) != synthetic:
            raise WqExit(INVALID, 'remote_id 已属于另一候选或证据类型，拒绝错误关联')
    cid, outcome = ensure_candidate(
        conn, cfg, sim["expression"], sim.get("config", {}), synthetic)
    if outcome == "exact_dup":
        prior = conn.execute('SELECT sim_id FROM simulations WHERE candidate_id=?', (cid,)).fetchone()
        if prior:
            return {"outcome": "duplicate", "candidate_id": cid, "sim_id": prior['sim_id'],
                    "detail": "同配置已有模拟结果，未新增记录"}
        # 已登记候选不等于已有结果：首个真实回执必须入账。
    status = _sim_status(sim, obj.get("quality"))
    sid = store.insert_simulation(
        conn, cid, sim.get("remote_id"), obj["source"], synthetic, status,
        sim, obj.get("quality"), obj.get("evidence"))
    return {"outcome": "imported", "candidate_id": cid, "sim_id": sid,
            "sim_status": status, "synthetic": synthetic,
            "family_outcome": outcome}


def import_result_file(conn, cfg, path: str, allow_real: bool) -> dict:
    return import_result_obj(conn, cfg, util.read_json(path), allow_real)
