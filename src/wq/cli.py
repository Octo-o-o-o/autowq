"""wq CLI。约定：未接通能力走非零退出；对外操作默认关闭；synthetic 不入真实报告。

语言：`--lang zh|en|auto` 会话内覆盖 → config.json 的 ui.language 持久偏好
（`wq config language` 或首次向导设置）→ 环境自动检测（zh* 中文，其余英文）。
账本里由调度器写入的中文消息/结局在显示层翻译，深层报告（feedback/refine）仍为中文，
可用 --json 取结构化数据。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import contracts, dedup, importer, reconcile, report, runner, store, util
from . import __version__
from .config import Config, validate_stage
from .i18n import (default_language, preferred_language, stored_language,
                   text, translate, write_language)
from .errors import (AdapterError, BLOCKED, ContractError, DUPLICATE, ERR, INVALID,
                     NOT_IMPLEMENTED, OK, PAUSED, WqExit)
from .wrappers import agent as agent_runner


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _ctx(args):
    cfg = Config.load(args.config, os.getcwd())
    if getattr(args, "db", None):
        cfg.data["paths"]["db"] = args.db
    conn = store_conn(cfg)
    return cfg, conn


def store_conn(cfg):
    from .db import connect
    return connect(cfg.db_path)


# ---------- commands ----------

def cmd_doctor(args) -> int:
    lang = getattr(args, "lang", "zh")
    cfg = Config.load(args.config, os.getcwd())
    checks = []
    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("python>=3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    add("config", True, cfg.path or text(lang, "(默认内置值；可 cp config/config.example.json config/config.json)",
                                          "(built-in defaults; cp config/config.example.json config/config.json)"))
    db_ok = True
    try:
        conn = store_conn(cfg)
        conn.execute("SELECT 1")
    except Exception as e:
        db_ok, conn = False, None
        add("sqlite", False, str(e))
    if db_ok:
        add("sqlite", True, cfg.db_path)
    priv = cfg.private_dir
    exists = os.path.isdir(priv)
    # Windows 没有 POSIX 权限位（目录恒为 0o777），私有性由用户目录 ACL 保证。
    mode_ok = exists and (sys.platform == 'win32' or (os.stat(priv).st_mode & 0o777) == 0o700)
    if args.fix_private and not mode_ok:
        cfg.ensure_private_dir()
        exists, mode_ok = True, True
    add("private_dir 0700", exists and mode_ok,
        f"{priv} {'0700' if mode_ok else text(lang, '(缺失或权限不对；--fix-private 可创建)', '(missing or wrong mode; --fix-private creates it)')}")
    provider_defs = {}
    profile_path = cfg.resolve(cfg.get('routing','profiles_file',default='config/profiles.json'))
    if os.path.isfile(profile_path):
        provider_defs = util.read_json(profile_path).get('providers',{})
    for agent in (provider_defs or cfg.get('models',default={})):
        m = cfg.model(agent)
        transport = provider_defs.get(agent,{}).get('transport',{})
        b = transport.get('binary') or m.get('bin','')
        if transport.get('kind') == 'api':
            from .provider_runtime import validate_api
            try:
                validate_api(transport)
                key_file=transport.get('api_key_file')
                has_key=bool(os.environ.get(transport.get('api_key_env',''))) or bool(key_file and os.path.isfile(os.path.expanduser(key_file))) or transport.get('allow_no_key',False)
                add(f'{agent} API configuration',has_key,'Key reference present; access not verified' if has_key else 'API key missing')
            except ValueError as exc: add(f'{agent} API configuration',False,str(exc))
        else:
            add(f"{agent} {text(lang, 'bin 存在', 'binary present')}",
                os.path.isfile(b) and os.access(b, os.X_OK), b)
            if agent == "zcode":
                # ZCode 链路前置条件：node、应用内 CLI 包、bundled provider 配置、已核验版本。
                import shutil as _shutil
                from .assets.provider_entry import (ZCODE_CJS, ZCODE_PROVIDER_CONFIG,
                                                    zcode_version)
                node = os.environ.get("WQ_NODE_BIN") or _shutil.which("node")
                add("zcode node", bool(node), node or text(lang, "未找到 node；可设 WQ_NODE_BIN",
                                                                "node not found; set WQ_NODE_BIN"))
                add("zcode CLI bundle", os.path.isfile(ZCODE_CJS), ZCODE_CJS)
                add("zcode builtin provider config", os.path.isfile(ZCODE_PROVIDER_CONFIG), ZCODE_PROVIDER_CONFIG)
                verified = (cfg.get("onboarding", "verified_versions", default={}) or {}).get("zcode", "")
                if verified and node:
                    current = zcode_version(node)
                    add(f"zcode {text(lang, '版本与已核验一致', 'version matches verified')}",
                        current == verified,
                        f"{current or '?'} vs {verified}" + ("" if current == verified else
                        text(lang, "；应用已更新，需重新单轮核验后更新 verified_versions",
                              "; app updated, re-verify one real call then update verified_versions")))
        if args.probe and os.path.isfile(b):
            import subprocess
            # launcher 型渠道经沙箱启动：探活须用沙箱放行的 cwd（生产为 work_root 下的副本），
            # 否则沙箱禁读项目根会让 getcwd 失败、探活误报。
            work_root = cfg.resolve(cfg.get("routing", "work_root", default="var/jobs"))
            probe_cwd = work_root if os.path.isdir(work_root) else None
            try:
                r = subprocess.run([b, "--version"], capture_output=True, text=True,
                                   timeout=15, stdin=subprocess.DEVNULL, cwd=probe_cwd)
                add(f"{agent} --version", r.returncode == 0, (r.stdout or r.stderr).strip()[:80])
            except Exception as e:
                add(f"{agent} --version", False, str(e))
        b_ = cfg.budget(agent)
        wstate, winfo = cfg.debug_window(agent)
        confirmed = bool(m.get("enabled")) and bool(b_.get("enabled")) and \
            (wstate != "invalid" and (b_.get("remaining") is not None or wstate == "ok"))
        if wstate == "ok":
            det = (text(lang, f"授权窗口内（至 {winfo}）免 remaining/周上限；供应商余额仍未知，不等于额度无限",
                          f"Within the authorization window (until {winfo}): remaining/weekly caps waived; vendor balance still unknown — not unlimited quota"))
        elif wstate == "invalid":
            det = f"debug_authorization {text(lang, '无效', 'invalid')}: {winfo}"
        elif confirmed:
            det = f"remaining={b_.get('remaining')} {b_.get('unit', '')}".rstrip()
        else:
            det = text(lang, "未确认 → 模型调用一律阻断（默认安全）",
                              "Unconfirmed → every model call is blocked (safe default)")
        add(f"{agent} {text(lang, '预算已确认', 'budget confirmed')}", confirmed, det)
    sandbox = os.path.join(os.path.expanduser(cfg.get("onboarding", "runtime", default="") or ""), "agents.sb")
    if sys.platform == "darwin" and cfg.get("onboarding", "runtime") and os.path.isfile(sandbox):
        # 沙箱实测：每个任务目录须在 agents.sb 内可写，否则调用写不出 result.json。
        import subprocess
        from .providers import sandbox_work_dirs
        for d in dict.fromkeys(sandbox_work_dirs(cfg)):
            if not os.path.isdir(d):
                continue
            probe = os.path.join(d, f".wq-sandbox-probe-{os.getpid()}")
            try:
                r = subprocess.run(["/usr/bin/sandbox-exec", "-f", sandbox, "/usr/bin/touch", probe],
                                   capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, cwd=d)
                ok = r.returncode == 0 and os.path.isfile(probe)
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            finally:
                if os.path.lexists(probe):
                    os.remove(probe)
            add(text(lang, "沙箱可写任务目录", "sandbox can write work dir"), ok,
                d + ("" if ok else text(lang, "；运行 wq providers refresh-runtime，或把目录移出项目",
                                          "; run wq providers refresh-runtime, or move it outside the project")))
    if conn:
        stage = store.current_account_stage(conn)
        add(text(lang, "账号阶段已登记", "account stage recorded"), stage["stage"] not in ("UNKNOWN",), stage["stage"])
        if stage["stage"] in ("UNKNOWN", "REGISTERED"):
            checks[-1]["detail"] += text(lang, "（阶段登记不代表当前积分或API权限；用 wq brain check 核验会话）",
                                                 " (a recorded stage is not proof of current points or API access; verify the session with wq brain check)")
    for c in checks:
        print(f"[{'ok' if c['ok'] else 'XX'}] {c['check']}: {c['detail']}")
    hard_fail = [c for c in checks if not c["ok"]
                 and c["check"] in ("python>=3.11", "sqlite", "private_dir 0700")]
    soft = [c for c in checks if not c["ok"] and c not in hard_fail]
    if soft:
        print('\n' + text(lang, f'{len(soft)} 项未就绪（预算/模型/账号默认阻断属预期，不是故障）',
                          f'{len(soft)} items not ready (budget/model/account blocks are expected defaults, not failures)'))
    return ERR if hard_fail else OK


def cmd_validate(args) -> int:
    obj = util.read_json(args.file)
    fn = {"card": contracts.validate_research_card,
          "result": contracts.validate_imported_result,
          "config": None}[args.kind]
    if fn is None:
        errs = []
        contracts.validate_sim_config(obj, errs)
    else:
        errs = fn(obj)
    if errs:
        for e in errs:
            print(f"invalid: {e}")
        return INVALID
    print("valid")
    return OK


def cmd_ingest_card(args) -> int:
    cfg, conn = _ctx(args)
    obj = util.read_json(args.file)
    errs = contracts.validate_research_card(obj)
    if errs:
        raise ContractError(errs)
    synthetic = bool(obj["provenance"]["synthetic"])
    made = []
    for v in obj["variants"]:
        expr = v.get("expression") or f"ABSTRACT:{obj['hypothesis_id']}:{v['variant_id']}"
        cid, outcome = importer.ensure_candidate(
            conn, cfg, expr, v.get("config", {}), synthetic, card=obj)
        made.append({"variant_id": v["variant_id"], "candidate_id": cid, "outcome": outcome})
    conn.commit()
    _out({"card": obj["hypothesis_id"], "candidates": made})
    return OK


def cmd_import_results(args) -> int:
    cfg, conn = _ctx(args)
    res = importer.import_result_file(conn, cfg, args.file, allow_real=args.real)
    conn.commit()
    _out(res)
    return DUPLICATE if res["outcome"] == "duplicate" else OK


def cmd_enqueue(args) -> int:
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    payload = util.read_json(args.payload[1:]) if args.payload.startswith("@") \
        else json.loads(args.payload)
    kind = args.kind
    dedup_key = None
    if kind == "import":
        payload = {"document": payload, "real": bool(args.real)}
    if kind == "simulation":
        expr = payload.get("expression")
        if not expr or not expr.strip():
            raise WqExit(INVALID, text(lang, "simulation 任务需要非空 expression",
                                             "simulation tasks require a non-empty expression"))
        cfgd = payload.get("config", {})
        errs = []
        contracts.validate_sim_config(cfgd, errs)
        if errs:
            raise ContractError(errs)
        cid, c_out = importer.ensure_candidate(
            conn, cfg, expr, cfgd, bool(payload.get("synthetic")))
        payload["candidate_id"] = cid
        dedup_key = dedup.config_hash(cfg.get("account_alias"), expr, cfgd)
        print(f"candidate {cid} ({c_out})")
    elif kind == "agent_call":
        agent = payload.get("agent")
        if not isinstance(agent, str) or not agent:
            raise WqExit(INVALID, text(lang, "agent_call.payload.agent 须为非空字符串",
                                             "agent_call.payload.agent must be a non-empty string"))
        if agent not in (cfg.get("models", default={}) or {}):
            raise WqExit(INVALID, text(lang, f"未知渠道：{agent}", f"Unknown provider: {agent}"))
        if not payload.get("prompt_file"):
            raise WqExit(INVALID, text(lang, "agent_call 需要 prompt_file", "agent_call requires prompt_file"))
    tid, created = store.enqueue_task(conn, kind, payload, dedup_key,
                                      max_attempts=int(args.max_attempts))
    conn.commit()
    _out({"task_id": tid, "created": created})
    return OK if created else DUPLICATE


def cmd_run_once(args) -> int:
    cfg, conn = _ctx(args)
    code, lines = runner.run_once(conn, cfg, lease_s=args.lease)
    for l in lines:
        print(l)
    return code


def cmd_report(args) -> int:
    cfg, conn = _ctx(args)
    print(report.build_report(conn, cfg, include_synthetic=args.include_synthetic))
    return OK


def cmd_status(args) -> int:
    cfg, conn = _ctx(args)
    unk = store.unknown_items(conn)
    _out({
        "paused": store.is_paused(conn),
        "pause_reason": store.get_flag(conn, "pause_reason") if store.is_paused(conn) else None,
        "account_stage": store.current_account_stage(conn),
        "adapter_mode": cfg.adapter_mode(),
        "unknown_pending": sum(len(v) for v in unk.values()),
        "live_agent_calls": [dict(r) for r in store.live_agent_calls(conn)],
        "budgets": cfg.data["budgets"],
        "active_preset": store.get_flag(conn, "active_preset"),
        "local_call_remaining": {
            name: max(0, budget['remaining'] - conn.execute(
                'SELECT COUNT(*) FROM agent_calls WHERE agent=? AND pid IS NOT NULL AND started_at>=?',
                (name, budget.get('as_of', ''))).fetchone()[0])
            for name, budget in cfg.data['budgets'].items()
            if budget.get('unit') == 'calls' and budget.get('remaining') is not None
        },
    })
    return OK


def cmd_pause(args) -> int:
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    store.set_flag(conn, "paused", "1")
    store.set_flag(conn, "pause_origin", "manual")
    store.set_flag(conn, "pause_reason", args.reason or "manual pause")
    conn.commit()
    if getattr(args, "graceful", False):
        _out({"paused": True, "draining": True,
              "note": text(lang, "不再领取新任务；当前已领取任务允许收尾，远端请求不会被取消",
                                 "No new tasks are claimed; claimed tasks may finish; remote requests are not cancelled")})
        return OK
    # 在途任务清理：本地活调用整组终止，2s 宽限后强杀；远端未知项留 UNKNOWN 待对账。
    live = [r for r in store.live_agent_calls(conn) if r["pid"] and store.pid_alive(r["pid"])]
    aborted = [{"call_id": r["call_id"], "pid": r["pid"]} for r in live]
    for r in live:
        util.kill_tree(r["pid"], force=False)
    deadline = time.time() + 2
    for r in live:
        while store.pid_alive(r["pid"]) and time.time() < deadline:
            time.sleep(0.05)
        if store.pid_alive(r["pid"]):
            util.kill_tree(r["pid"], force=True)
        store.finish_agent_call(conn, r["call_id"], "aborted", None, detail="pause 终止")
    conn.commit()
    unk = store.unknown_items(conn)
    _out({"paused": True, "aborted_calls": aborted,
          "unknown_to_reconcile": sum(len(v) for v in unk.values())})
    return OK


def cmd_resume(args) -> int:
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    unk = store.unknown_items(conn)
    n = sum(len(v) for v in unk.values())
    if n and not args.force:
        _out({"resumed": False,
              "reason": text(lang, f"有 {n} 项 UNKNOWN 待对账；先 `wq reconcile` 逐项核实，确认后用 --force 恢复（不盲目重放）",
                                 f"{n} UNKNOWN items pending; verify each with `wq reconcile`, then resume with --force (no blind replay)")})
        return BLOCKED
    store.set_flag(conn, "paused", "0")
    store.set_flag(conn, "pause_origin", "")
    store.set_flag(conn, "pause_reason", "")
    conn.commit()
    _out({"resumed": True, "unknown_pending": n})
    return OK


def cmd_reconcile(args) -> int:
    cfg, conn = _ctx(args)
    if args.resolve:
        new = reconcile.resolve(conn, args.resolve, args.outcome, args.note or "", args.alpha_id)
        conn.commit()
        _out({"task_id": args.resolve, "new_status": new})
        return OK
    res = reconcile.collect(conn, cfg)
    _out(res)
    return OK


def cmd_add_payment(args) -> int:
    cfg, conn = _ctx(args)
    pid = store.add_payment(conn, args.submission, args.kind, args.amount,
                            args.currency, args.occurred_at or util.now_iso(), args.evidence)
    conn.commit()
    _out({"payment_id": pid})
    return OK


def cmd_add_expense(args) -> int:
    cfg, conn = _ctx(args)
    eid = store.add_expense(conn, args.kind, args.amount, args.unit,
                            args.occurred_at or util.now_iso(), args.note)
    conn.commit()
    _out({"expense_id": eid})
    return OK


def cmd_account_stage(args) -> int:
    cfg, conn = _ctx(args)
    stage = validate_stage(args.stage)
    store.set_account_stage(conn, stage, args.evidence)
    conn.commit()
    _out({"stage": stage})
    return OK


def cmd_budget(args) -> int:
    lang = getattr(args, "lang", "zh")
    cfg = Config.load(args.config, os.getcwd())
    if not cfg.path:
        raise WqExit(INVALID, text(lang, "无 config.json；先 cp config/config.example.json config/config.json",
                                         "No config.json; cp config/config.example.json config/config.json first"))
    from .providers import NAMES
    if args.name != "simulation" and args.name not in NAMES and args.name not in cfg.get("models", default={}):
        raise WqExit(INVALID, text(lang, f"未知渠道：{args.name}", f"Unknown provider: {args.name}"))
    b = cfg.data["budgets"].setdefault(args.name, {})
    if args.enable:
        b["enabled"] = True
    if args.disable:
        b["enabled"] = False
    if args.remaining is not None:
        b["remaining"] = args.remaining
    if args.unit:
        b["unit"] = args.unit
    if args.remaining is not None and b.get("unit") == "calls":
        b["as_of"] = util.now_iso()
    util.write_json(cfg.path, cfg.data)
    _out({"budgets": cfg.data["budgets"], "written": cfg.path})
    return OK


def cmd_config(args) -> int:
    lang = getattr(args, "lang", "zh")
    cfg = Config.load(args.config, os.getcwd())
    if args.action != "language":
        return INVALID
    stored = stored_language(cfg.path) if cfg.path else None
    if args.value is None:
        _out({"setting": stored or "auto", "effective": lang,
              "note": text(lang, "effective 为当前生效语言；auto 跟随系统（中文环境中文，其余英文）",
                                 "effective is the language in use; auto follows the system locale (Chinese for zh*, English otherwise)")})
        return OK
    if not cfg.path or not write_language(cfg.path, args.value):
        raise WqExit(INVALID, text(lang, "未找到 config/config.json；先 cp config/config.example.json config/config.json",
                                         "config/config.json not found; cp config/config.example.json config/config.json first"))
    effective = args.value if args.value in ("zh", "en") else default_language()
    names = {"zh": ("中文", "Chinese"), "en": ("英文", "English"), "auto": ("跟随系统", "follow the system")}
    print(text(lang, f"界面语言已保存为 {args.value}（{names[args.value][0]}），当前生效：{effective}。",
                     f"Interface language saved as {args.value} ({names[args.value][1]}); effective now: {effective}."))
    return OK


def cmd_invoke(args) -> int:
    cfg, conn = _ctx(args)
    try:
        spec = agent_runner.spec_for(cfg, args.agent, args.prompt_file,
                                     args.artifacts.split(",") if args.artifacts else [])
    except ValueError as e:
        raise WqExit(INVALID, str(e))
    out = agent_runner.run_agent(conn, cfg, spec, prompt_file=args.prompt_file,
                                 purpose=args.purpose, incident_id=args.incident,
                                 allow=args.allow)
    _out({"status": out.status, "exit_code": out.exit_code,
          "call_id": out.call_id, "detail": out.detail})
    if out.status == "succeeded":
        return OK
    if out.status.startswith("blocked"):
        return BLOCKED
    return ERR


def cmd_tasks(args) -> int:
    cfg, conn = _ctx(args)
    rows = store.list_tasks(conn, args.status)
    if args.json:
        _out([{k: r[k] for k in ("task_id", "kind", "status", "attempts",
                                "last_error", "created_at")} for r in rows])
    else:
        from .task_view import render_tasks
        print(render_tasks(conn, cfg, rows, getattr(args, "lang", "zh")))
    return OK


def cmd_preset(args) -> int:
    from . import routing
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    data = routing.catalog(cfg)
    if args.action == "use":
        cycles = getattr(args, "cycles", 1) or 1
        routing.choose_preset(conn, cfg, args.name, once=getattr(args, 'once', False), cycles=cycles)
        permanent = store.get_flag(conn, "active_preset") or data["default"]
        if getattr(args, 'once', False):
            if cycles <= 1:
                print(text(lang, f"已登记仅一轮预设 {args.name}：下一个新建的研究轮次使用它，该轮结束后自动回到永久预设 {permanent}。",
                                  f"One-cycle preset {args.name} registered: the next new research cycle uses it, then the permanent preset {permanent} resumes."))
            else:
                print(text(lang, f"已登记临时预设 {args.name}：接下来 {cycles} 个新建研究轮次使用它，用完后自动回到永久预设 {permanent}。",
                                  f"Temporary preset {args.name} registered: the next {cycles} new research cycles use it, then the permanent preset {permanent} resumes."))
        else:
            print(text(lang, f"已选择预设 {args.name}。下一项任务领取时生效；在途任务及其重试保持原预设。",
                              f"Preset {args.name} selected; applies to the next claimed task. In-flight tasks and their retries keep the original preset."))
        return OK
    if args.action == "cancel-once":
        pending = store.get_flag(conn, "preset_once") or ""
        routing.cancel_once(conn)
        if not pending:
            print(text(lang, "当前没有待生效的临时预设。", "No temporary preset is pending."))
        else:
            print(text(lang, f"已取消临时预设 {pending}；之后的新轮次使用永久预设，已绑定到进行中轮次的预设不变。",
                              f"Temporary preset {pending} cancelled; new cycles use the permanent preset. Presets already bound to in-flight cycles are unchanged."))
        return OK
    current = routing.active_preset(conn, cfg, data)
    print(text(lang, f"当前预设：{current}；每个渠道：首次 + 3 次重试，仅明确额度/容量故障才切备用渠道。",
                      f"Current preset: {current}; per provider: first attempt + 3 retries, switching to a fallback only on explicit quota/capacity failures."))
    for name, preset in data['presets'].items():
        print(f"\n{'*' if name == current else ' '} {name} — {preset.get('description', '')}")
        for role, zh, en in (('research', '研究', 'Research'), ('engineering', '工程', 'Engineering'), ('review', '审查', 'Review')):
            print(f"  {text(lang, zh, en)}：" + ' → '.join(preset['routes'][role]))
    print("\n" + text(lang, "渠道状态（本地判定；登录状态与实际余额以供应商为准）：",
                           "Provider status (local view; login state and actual balance are the vendor's word):"))
    for provider in data['providers']:
        print(f"  {provider}: {routing._unavailable(conn, cfg, provider) or text(lang, '本地配置可用', 'available per local config')}")
    print(text(lang, "配置文件：", "Configuration file: ") + cfg.resolve(cfg.get('routing', 'profiles_file', default='config/profiles.json')))
    return OK


def cmd_provider(args) -> int:
    from . import routing
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    if args.action == 'quota':
        pauses = routing.quota_pauses(conn, cfg)
        if not pauses:
            print(text(lang, "没有处于额度暂停的渠道。", "No provider is paused for quota."))
        for item in pauses:
            print(text(lang, f"{item['provider']}：{item['reason'] or '额度暂停'}，至 {item['until']} 自动恢复",
                              f"{item['provider']}: {item['reason'] or 'quota pause'}, resumes automatically at {item['until']}"))
        return OK
    if args.name not in routing.catalog(cfg)['providers']:
        raise WqExit(INVALID, text(lang, f"未知渠道：{args.name}", f"Unknown provider: {args.name}"))
    if args.action == 'resume':
        routing.clear_quota_pause(conn, args.name)
        conn.commit()
        print(text(lang, f"{args.name} 的额度暂停已解除；下一次调度会再试它，若供应商仍拒绝会重新暂停。",
                          f"Quota pause cleared for {args.name}; the next dispatch tries it again and re-pauses if the vendor still refuses."))
        return OK
    store.set_flag(conn, f'provider_disabled:{args.name}', '1' if args.action == 'disable' else '0')
    if args.action == 'disable':
        print(text(lang, f"{args.name} 已停用；不打断在途调用，后续任务不再路由到该渠道，预算闸门保留。",
                          f"{args.name} disabled; in-flight calls are unaffected, new tasks no longer route to it, budget gates remain."))
    else:
        print(text(lang, f"{args.name} 已恢复；后续任务恢复路由到该渠道。",
                          f"{args.name} re-enabled; new tasks may route to it again."))
    return OK


def cmd_job(args) -> int:
    from . import routing
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    tid, path = routing.enqueue_job(conn, cfg, args.role, args.prompt_file, args.input_dir, args.title)
    print(text(lang, f"已排队：{tid}\n冻结输入与产物：{path}\n首次领取时采用当前预设；定时器将自动执行。",
                      f"Queued: {tid}\nFrozen inputs and artifacts: {path}\nThe current preset applies at first claim; the timer executes it."))
    return OK


def cmd_brain(args):
    from .brain_client import BrainClient
    from . import brain_jobs
    lang = getattr(args, 'lang', default_language())
    cfg, conn = _ctx(args)
    try:
        if args.action == 'login':
            import getpass
            if not sys.stdin.isatty():
                raise ValueError(text(lang, '登录需本人在交互终端输入；不接受命令行明文密码',
                                           'Login requires your interactive terminal; plaintext passwords in arguments are not accepted'))
            print(text(lang, '没有账号？注册：', 'Need an account? Register: ') + 'https://platform.worldquantbrain.com/sign-up')
            username = input('BRAIN Email: ').strip()
            password = getpass.getpass(text(lang, 'BRAIN 密码（不保存）: ', 'BRAIN password (not saved): '))
            result = BrainClient(cfg.private_dir).login(username, password)
            from .autopilot import after_login
            store.set_flag(conn, 'brain_auto_auth_not_before', '')
            result['resumed_get_tasks'] = after_login(conn)
            conn.commit()
            _out(result)
            return OK
        if args.action == 'keychain-save':
            if not sys.stdin.isatty():
                raise ValueError(text(lang, 'Keychain 保存需本人在交互终端执行',
                                           'Keychain save requires your interactive terminal'))
            username = input('BRAIN Email: ').strip()
            service = (os.environ.get('WQ_BRAIN_KEYCHAIN_SERVICE', '').strip() or
                       cfg.get('brain_api', 'keychain_service', default='com.worldquant.wq.brain'))
            print(text(lang, '将由 macOS Keychain 安全提示输入密码；密码不会写入环境变量、文件或日志。',
                           'macOS Keychain will securely prompt for the password; it is never written to environment variables, files or logs.'))
            _out(BrainClient.save_keychain(username, service))
            return OK
        if args.action == 'check':
            client=BrainClient(cfg.private_dir)
            code, headers, data=client.preflight(cfg)
            util.write_json(os.path.join(cfg.private_dir,'brain-simulation-options.json'),data)
            _out({'http_status':code,'allow':headers.get('allow'),'metadata_saved_locally':True})
            return OK
        if args.action == 'account':
            from .brain_jobs import get_with_reauth
            from . import account
            snapshot = account.snapshot(get_with_reauth, BrainClient(cfg.private_dir), cfg)
            _out(account.summary(snapshot))
            return OK
        if args.action in ('submit-check', 'submit', 'reconcile-submit'):
            from . import brain_submission
            if args.action == 'submit-check':
                result = brain_submission.readiness(conn, args.alpha_id)
                _out(result)
                return OK if result['ready'] else BLOCKED
            if args.action == 'reconcile-submit':
                lock = agent_runner._acquire_lock(cfg.run_dir, 'runner')
                if lock is None:
                    _out({'blocked': text(lang, '调度器正在处理任务，请稍后对账', 'The scheduler is busy; reconcile later')})
                    return BLOCKED
                try:
                    _out(brain_submission.reconcile(conn, cfg, args.alpha_id))
                finally:
                    os.close(lock)
                return OK
            tid, created = brain_submission.enqueue(conn, cfg, args.alpha_id, util.read_json(args.review))
            _out({'task_id': tid, 'created': created,
                  'note': text(lang, '先重新检查，全部合格后才提交；由 run-once 执行',
                                    'Re-checks first and submits only if all pass; executed by run-once')})
            return OK if created else DUPLICATE
        if args.action == 'enqueue':
            tid,created=brain_jobs.enqueue(conn,cfg,util.read_json(args.file))
            _out({'task_id':tid,'created':created,
                  'note':text(lang,'仅模拟，不提交 Alpha','Simulation only; no Alpha submission')})
            return OK if created else DUPLICATE
        if args.action in ('fields', 'field-evidence'):
            from . import catalog, autopilot
            query = catalog.query_from_settings(autopilot.policy(cfg)['settings'])
            if args.action == 'field-evidence':
                client = BrainClient(cfg.private_dir); client.preflight(cfg)
                written = []
                policy_path = cfg.resolve(cfg.get('autopilot', 'policy_file', default='config/autopilot-policy.json'))
                for fid in args.field_id:
                    path = catalog.evidence_path(cfg.private_dir, fid, query)
                    util.write_json(path, catalog.field_snapshot(client, fid, query)); os.chmod(path, 0o600)
                    rehashed = catalog.refresh_evidence_hash(policy_path, path)
                    written.append({'field': fid, 'path': path, 'sha256': util.sha256_json(util.read_json(path)), 'policy_hash_refreshed': rehashed})
                _out({'query': query, 'evidence': written,
                      'note': text(lang, '证据只在私有目录；用 wq policy add-role 登记角色',
                                         'Evidence stays in the private directory; register roles with wq policy add-role')})
                return OK
            path = catalog.catalog_path(cfg.private_dir, query)
            if args.refresh or not os.path.exists(path):
                client = BrainClient(cfg.private_dir); client.preflight(cfg)
                doc = catalog.fetch_catalog(client, query)
                util.write_json(path, doc); os.chmod(path, 0o600)
            doc = catalog.load_catalog(cfg.private_dir, query)
            if args.datasets:
                _out({'query': query, 'count': doc['count'], 'complete': doc['complete'], 'datasets': catalog.datasets(doc)})
                return OK
            rows = catalog.search(doc, args.search, args.dataset, args.type, args.min_coverage, args.limit)
            _out({'query': query, 'count': doc['count'], 'queried_at': doc['queried_at'], 'shown': len(rows), 'fields': rows})
            return OK
    except AdapterError as exc:
        _out({'kind':exc.kind,'error':str(exc)});return BLOCKED


def cmd_policy(args):
    from . import catalog, autopilot, research_dsl
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    path = cfg.resolve(cfg.get('autopilot', 'policy_file', default='config/autopilot-policy.json'))
    if args.action == 'roles':
        p = autopilot.policy(cfg)
        _out({'roles': {k: {'fields': v['fields'], 'cluster': v.get('cluster'), 'description': v.get('description', research_dsl.public_contract()['roles'].get(k))} for k, v in p['bindings'].items()},
              'setting_variants': p.get('setting_variants', []), 'verified_at': p['verified_at'], 'valid_until': p['valid_until']})
        return OK
    if args.action == 'add-role':
        fields = [x.strip() for x in args.fields.split(',') if x.strip()]
        query = catalog.query_from_settings(autopilot.policy(cfg)['settings'])
        evidence = args.evidence or [catalog.evidence_path(cfg.private_dir, f, query) for f in fields]
        try:
            binding = catalog.add_role(path, args.name, args.expression, fields, args.description, evidence, args.cluster, args.group_field,
                                       check=autopilot.check_policy)
        except ValueError as exc:
            _out({'error': text(lang, '未登记：', 'Not registered: ') + str(exc)}); return INVALID
        _out({'role': args.name, 'binding': binding,
              'note': text(lang, '新角色只影响之后创建的研究轮次；活动轮次会因策略 hash 变化而结束',
                                 'New roles affect only cycles created afterwards; the active cycle ends because the policy hash changed')})
        return OK
    return INVALID


def cmd_autopilot(args):
    from . import autopilot
    lang = getattr(args, "lang", "zh")
    cfg, conn = _ctx(args)
    autopilot.setup(conn)
    if args.action in ('feedback','collect-feedback'):
        from . import feedback
        if args.action=='collect-feedback':
            queued=[]
            if not args.local_only:
                for row in conn.execute("SELECT remote_id FROM simulations WHERE synthetic=0 AND source='api'").fetchall():
                    try:
                        tid,created=feedback.enqueue(conn,cfg,row[0])
                        if created:queued.append(tid)
                    except ValueError:continue
            refreshed=feedback.refresh_local(conn,cfg)
            conn.commit();_out({'queued':queued,'refreshed_local':refreshed,
                'note':(text(lang,'仅本地重算已有资料，不入队、不联网','Recompute local data only; nothing enqueued, no network') if args.local_only
                        else text(lang,'缺资料的Alpha已入队只读收集，由本地队列执行（会联网）；已有资料按当前规则本地重算',
                                    'Alphas missing data are queued for read-only collection via the local queue (network); existing data is recomputed locally under current rules'))})
        else:
            result=feedback.report(conn)
            if args.json: _out(result)
            else:
                print('真实结果反馈：'+result['note'])
                for item in result['candidates']:
                    stage='资料齐全' if item.get('collection_status')=='complete' else '等待只读资料回填'
                    retained='保留互补性研究' if item['retain_for_complementarity'] else '不列入组合候选'
                    print(item['alpha_id']+'：'+('已提交且官方接收；本轮资料复核：' if item.get('platform_submission')=='accepted' else '')+'、'.join(item['diagnosis'])+'；'+retained+'；'+stage)
                    if item['validation_gaps']: print(('  资料回填缺口：' if item.get('platform_submission')=='accepted' else '  提交前缺口：')+'；'.join(item['validation_gaps']))
                for pair in result['pairs']:
                    if pair['worth_combination_review']:
                        print('互补候选 '+ ' + '.join(pair['parents'])+f"：日PnL相关性 {pair['value']:.3f}，共同观测 {pair['observations']}；仍须预登记与模型审查")
                if result.get('submission_order'):
                    print('提交排序建议（先提对已提交池相关最低者；官方 /check 仍是唯一放行条件）：')
                    for i,item in enumerate(result['submission_order'],1):
                        corr='未知' if item['local_corr_max'] is None else f"{item['local_corr_max']:.2f}"
                        print(f"  {i}. {item['alpha_id']}：本地相关 {corr}；{item['correlation_state']}；依据 {item['basis']}")
                shadow=result.get('shadow') or {}
                if shadow.get('dead_zones'):
                    print('影子死区（角色集合 ≥3 次真实结果全部无信号；不自动拒绝）：'+'；'.join('+'.join(g['roles'])+f"×{g['observations']}" for g in shadow['dead_zones']))
                early=shadow.get('early_stop') or {}
                if early.get('checkpoint'):
                    print(f"早停检查点：最近5个基础轮 {early['recent_cycles']} 全部失败，主诊断“{early['dominant_diagnosis']}”出现 {early['dominant_count']} 次；请人工复核，不自动暂停数据簇")
        return OK
    if args.action == 'refine-check':
        from .refinement import report
        result=report(conn)
        if args.json:
            _out(result)
        else:
            print('调优分诊：Sharpe/Fitness 均至少达到各自门槛的85%，其余检查通过（自相关待核验单列）。')
            print(result['note'])
            for item in result['candidates']:
                decision='值得进一步复核，尚未获准调优' if item['worth_reviewing'] else '暂不调优'
                print(f"Alpha {item['alpha_id']}（轮次 {item['cycle_id'] or '直接实验'}）：{decision}；"+'；'.join(item['blockers']+item['pending']))
            if not result['candidates']: print('暂无真实自动研究回测。')
        return OK
    if args.action == 'run-next':
        # 不占 runner 文件锁。调度器在模型调用期间会一直持有该锁；菜单若因此失败，
        # 点击「立刻运行下一轮」在最常见的时候等于没有生效。用数据库事务与 finish() 对齐。
        try:
            conn.execute('BEGIN IMMEDIATE')
            autopilot.request_run_next(conn, cfg)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if args.action in ('start','stop'):
        if args.action == 'start':
            autopilot.policy(cfg)
            store.set_flag(conn, 'autopilot_stop_after_cycle', '0')
        else:
            store.set_flag(conn,'autopilot_run_next','0')
        store.set_flag(conn,'autopilot_enabled','1' if args.action=='start' else '0')
        # 账本内消息保持中文规范文案；英文界面经 i18n.translate 在显示层翻译。
        autopilot.message(conn,'自动补充任务已启用，等待本地调度' if args.action=='start' else '已停止创建新轮次；现有任务继续执行，全部暂停用 wq pause')
    result=autopilot.status(conn,cfg)
    if args.json:
        _out(result)
    else:
        print(text(lang, '自动研究：', 'Automatic research: ') + (text(lang, '已启用', 'on') if result['enabled'] else text(lang, '已停用', 'off')))
        print(text(lang, '当前：', 'Current: ') + translate(result['message'], lang))
        if result.get('max_cycles_total') is not None:
            print(text(lang, f"累计轮次：{result['total_cycles']}/{result['max_cycles_total']}（包括拒绝、重复和失败轮次）",
                              f"Cycles: {result['total_cycles']}/{result['max_cycles_total']} (including rejected, duplicate and failed cycles)"))
        local=lambda value: util.parse_iso(value).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z') if value else text(lang, '由下一次本地调度检查', 'at the next scheduler check')
        if result.get('paused'):
            print(text(lang, '下一步：先恢复 BRAIN 会话并执行 `./wq resume`；恢复后由本地调度继续',
                              'Next: restore the BRAIN session and run `./wq resume`; the local scheduler then continues'))
        else:
            print(text(lang, '下一轮：', 'Next cycle: ') + local(result['next_cycle_at']))
        print(text(lang, '最近调度：', 'Last tick: ') + local(result['last_tick_at']))
        cycle=result['latest_cycle']
        if cycle:
            from .task_view import cycle_state
            label = cycle_state(cycle['state'], lang)
            print(text(lang, f"最近一轮：第{cycle['cycle_id']}轮，{label}；{translate(cycle['outcome'], lang) or text(lang, '按队列自动推进', 'advances automatically via the queue')}",
                              f"Latest cycle: #{cycle['cycle_id']}, {label}; {translate(cycle['outcome'], lang) or 'advances automatically via the queue'}"))
        print(text(lang, '仅探索回测；无自动提交。告警同步见 var/run/autopilot-status.json。',
                          'Exploratory backtests only; no automatic submission. Alert mirror: var/run/autopilot-status.json.'))
    return OK


def cmd_evidence(args):
    from .evidence_gate import main
    argv = ['--preregister', args.preregister, '--declarations', args.declarations]
    if args.now:
        argv += ['--now', args.now]
    return main(argv)


# ---------- parser ----------

def cmd_onboard(args):
    from . import onboard
    return onboard.main(args.onboard_args)


def cmd_export(args):
    from .exporter import write
    cfg,conn=_ctx(args)
    try:
        _out(write(conn,args.output,args.kind,args.format,args.include_synthetic))
    except FileExistsError:
        raise WqExit(INVALID,text(args.lang,"导出文件已存在；请选择新路径，不覆盖现有数据。",
                                        "Export file already exists; choose a new path. Existing data was not overwritten."))
    finally: conn.close()
    return OK


def build_parser(lang=None) -> argparse.ArgumentParser:
    lang = lang or default_language()
    p = argparse.ArgumentParser(prog="wq", description=text(
        lang, "WorldQuant 研究试点本地工具（离线优先）",
        "Local WorldQuant research workflow (offline first)"))
    p.add_argument("--lang", choices=["zh", "en", "auto"], default=lang,
                   help=text(lang, "界面语言（本次调用）：zh / en / auto", "Interface language (this invocation): zh / en / auto"))
    p.add_argument("--version", action="version", version="wq " + __version__)
    p.add_argument("--config", help=text(lang, "config.json 路径，默认 ./config/config.json",
                                              "Configuration path (default: ./config/config.json)"))
    p.add_argument("--db", help=text(lang, "覆盖 SQLite 路径（测试用）", "Override SQLite path (for testing)"))
    sub = p.add_subparsers(dest="cmd", required=True)
    from . import providers, workflow
    providers.add_parser(sub, lang)
    workflow.add_parser(sub, lang)
    from . import history_research
    history_research.add_parser(sub, lang)

    sub.add_parser('help', help=text(lang, '显示命令帮助：wq help [命令]', 'Show command help: wq help [command]'))
    s=sub.add_parser('login', help=text(lang, '登录 BRAIN（含注册链接）', 'Sign in to BRAIN (includes the registration URL)'))
    s.set_defaults(fn=cmd_brain, action='login')
    s=sub.add_parser('export', help=text(lang, '导出摘要/任务/结果为 JSON 或 CSV，不含凭据',
                                              'Export summary/tasks/results as JSON or CSV, without credentials'))
    s.add_argument('--kind', choices=['summary','tasks','results'], default='summary')
    s.add_argument('--format', choices=['json','csv'], default='json')
    s.add_argument('--output', required=True, help=text(lang, '目标文件（不覆盖）', 'Destination file (never overwritten)'))
    s.add_argument('--include-synthetic', action='store_true')
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser('onboard', help=text(lang, '首次使用：选择语言、本地 CLI、模型和角色',
                                                     'First-run wizard: language, local CLIs, models and roles'), add_help=False)
    s.add_argument('onboard_args', nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_onboard)

    s = sub.add_parser('brain', help=text(lang, 'BRAIN 本地会话、接口核验与单次模拟队列',
                                                    'BRAIN login, access checks, simulation and submission queues'))
    bs=s.add_subparsers(dest='action', required=True)
    ap = sub.add_parser('autopilot', help=text(lang, '自动研究：状态/启动/停止补充任务',
                                                       'Automatic research: inspect, start or stop supplemental cycles'))
    ap.add_argument('action', choices=['status','start','stop','run-next','refine-check','feedback','collect-feedback'], nargs='?', default='status')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--local-only', action='store_true', help=text(lang, 'collect-feedback：只用已有资料本地重算，不入队',
                                                                             'collect-feedback: recompute from local data only; nothing enqueued'))
    ap.set_defaults(fn=cmd_autopilot)
    bs.add_parser('login').set_defaults(fn=cmd_brain)
    bs.add_parser('keychain-save').set_defaults(fn=cmd_brain)
    bs.add_parser('check').set_defaults(fn=cmd_brain)
    bs.add_parser('account', help=text(lang, '只读账户快照：等级、pyramid 乘数/数量、连续提交（streak）；写私有目录，不含凭证',
                                                    'Read-only account snapshot: level, pyramid multipliers/counts, submission streak; written to the private directory, no credentials')
        ).set_defaults(fn=cmd_brain)
    for action in ('submit-check', 'submit', 'reconcile-submit'):
        b = bs.add_parser(action)
        b.add_argument('alpha_id')
        if action == 'submit':
            b.add_argument('--review', required=True, help=text(lang, '已完成的提交研究验收 JSON；不可使用模板占位',
                                                                        'Completed submission review JSON; placeholders are not accepted'))
        b.set_defaults(fn=cmd_brain)
    b=bs.add_parser('enqueue'); b.add_argument('file'); b.set_defaults(fn=cmd_brain)
    b=bs.add_parser('fields', help=text(lang, '只读字段目录快照与检索（私有目录缓存）',
                                                    'Read-only data-field catalog snapshot and search (cached in the private directory)'))
    b.add_argument('--refresh', action='store_true'); b.add_argument('--search'); b.add_argument('--dataset')
    b.add_argument('--datasets', action='store_true', help=text(lang, '按数据集汇总数量', 'Group counts by dataset'))
    b.add_argument('--type', default='MATRIX')
    b.add_argument('--min-coverage', type=float, default=0.0); b.add_argument('--limit', type=int, default=50); b.set_defaults(fn=cmd_brain)
    b=bs.add_parser('field-evidence', help=text(lang, '为字段生成当前设置下的证据快照', 'Capture evidence snapshots for fields under current settings'))
    b.add_argument('field_id', nargs='+'); b.set_defaults(fn=cmd_brain)

    s = sub.add_parser('policy', help=text(lang, '研究策略：查看角色、用已核验字段登记新角色',
                                                     'Research policy: inspect roles, register new roles from verified fields'))
    pl = s.add_subparsers(dest='action', required=True)
    pl.add_parser('roles').set_defaults(fn=cmd_policy)
    a = pl.add_parser('add-role')
    a.add_argument('name'); a.add_argument('--expression', required=True)
    a.add_argument('--fields', required=True, help=text(lang, '逗号分隔的平台字段 ID', 'Comma-separated platform field IDs'))
    a.add_argument('--description', required=True, help=text(lang, '给模型看的角色说明，写明它不代表什么',
                                                                    'Role description for the model; state what it does not represent'))
    a.add_argument('--cluster', help=text(lang, '数据簇标签，如 pv/fundamental/analyst', 'Data cluster label, e.g. pv/fundamental/analyst'))
    a.add_argument('--evidence', nargs='*', help=text(lang, '证据快照路径；默认按字段 ID 在私有目录查找',
                                                              'Evidence snapshot paths; defaults to private-dir lookup by field ID'))
    a.add_argument('--group-field', action='store_true')
    a.set_defaults(fn=cmd_policy)

    s = sub.add_parser('config', help=text(lang, '查看或设置本机偏好（界面语言）', 'Inspect or change local preferences (interface language)'))
    cs = s.add_subparsers(dest='action', required=True)
    cl = cs.add_parser('language', help=text(lang, '查看或设置界面语言', 'Show or set the interface language'))
    cl.add_argument('value', nargs='?', choices=['zh', 'en', 'auto'], help=text(lang, '不填显示当前值', 'omit to show the current value'))
    cl.set_defaults(fn=cmd_config)

    s = sub.add_parser('evidence-check', help=text(lang, '核验证据声明；不把声明完整视为平台就绪',
                                                            'Validate evidence declarations (not proof of platform readiness)'))
    s.add_argument('--preregister', required=True)
    s.add_argument('--declarations', required=True)
    s.add_argument('--now')
    s.set_defaults(fn=cmd_evidence)

    s = sub.add_parser('preset', help=text(lang, '查看或切换整套路由预设（下一项任务生效）',
                                                     'Inspect or select routing presets for new tasks'))
    ps = s.add_subparsers(dest='action', required=True)
    ps.add_parser('list').set_defaults(fn=cmd_preset)
    ps.add_parser('show').set_defaults(fn=cmd_preset)
    use = ps.add_parser('use'); use.add_argument('--once', action='store_true',
                                                 help=text(lang, '仅下一个新建轮次使用该预设，结束后自动恢复',
                                                                 'Use this preset for the next new cycle only, then revert'))
    use.add_argument('--cycles', type=int, default=1, metavar='N',
                     help=text(lang, '与 --once 搭配：临时预设覆盖的新建轮次数（1..100）',
                                     'With --once: how many new cycles use the temporary preset (1..100)'))
    use.add_argument('name')
    use.set_defaults(fn=cmd_preset)
    ps.add_parser('cancel-once', help=text(lang, '取消待生效的临时预设登记',
                                                     'Cancel a pending temporary preset registration')).set_defaults(fn=cmd_preset)

    s = sub.add_parser('provider', help=text(lang, '临时停用/恢复某个渠道，不修改套餐',
                                                      'Disable or enable a provider locally; does not change your subscription'))
    s.add_argument('action', choices=['enable', 'disable', 'resume', 'quota'],
                   help=text(lang, 'resume：额度已恢复（如已充值）时提前解除额度暂停；quota：查看额度暂停',
                                   'resume: lift a quota pause early (e.g. after topping up); quota: show quota pauses'))
    s.add_argument('name', nargs='?')
    s.set_defaults(fn=cmd_provider)

    s = sub.add_parser('job', help=text(lang, '创建按预设路由的本地任务', 'Create a locally routed task'))
    js = s.add_subparsers(dest='action', required=True)
    enqueue = js.add_parser('enqueue')
    enqueue.add_argument('--role', choices=['research', 'engineering', 'review'], required=True)
    enqueue.add_argument('--prompt-file', required=True)
    enqueue.add_argument('--input-dir', help=text(lang, '明确筛选的输入目录，将复制给模型',
                                                            'Explicitly selected input directory to copy to the model'))
    enqueue.add_argument('--title')
    enqueue.set_defaults(fn=cmd_job)

    s = sub.add_parser("doctor", help=text(lang, "环境/预算/账号就绪检查", "Check environment, budgets and account prerequisites"))
    s.add_argument("--probe", action="store_true", help=text(lang, "运行 bin --version（只读探测）", "Run binary --version (read-only probe)"))
    s.add_argument("--fix-private", action="store_true", help=text(lang, "创建 private_dir(0700)", "Create private_dir with mode 0700"))
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("validate", help=text(lang, "校验契约文件", "Validate a contract document"))
    s.add_argument("kind", choices=["card", "result", "config"])
    s.add_argument("file")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("ingest-card", help=text(lang, "导入研究卡 → 假设族与候选", "Import a research card into families and candidates"))
    s.add_argument("file")
    s.set_defaults(fn=cmd_ingest_card)

    s = sub.add_parser("import-results", help=text(lang, "导入模拟结果（manual-import）", "Import simulation results"))
    s.add_argument("file")
    s.add_argument("--real", action="store_true", help=text(lang, "声明确认为真实（非合成）结果", "Confirm these are real, non-synthetic results"))
    s.set_defaults(fn=cmd_import_results)

    s = sub.add_parser("enqueue", help=text(lang, "入队任务 simulation|submission|agent_call|reconcile|import",
                                                     "Enqueue a simulation, submission, agent call, reconciliation or import"))
    s.add_argument("kind", choices=["simulation", "submission", "agent_call", "reconcile", "import"])
    s.add_argument("payload", help=text(lang, "JSON 字符串或 @file", "JSON string or @file"))
    s.add_argument("--max-attempts", type=int, default=2)
    s.add_argument("--real", action="store_true",
                   help=text(lang, "kind=import 时确认文档为真实结果（非合成）",
                                    "For import tasks, confirm real rather than synthetic results"))
    s.set_defaults(fn=cmd_enqueue)

    s = sub.add_parser("run-once", help=text(lang, "领取并处理一个到期任务（单并发）", "Claim and process one due task (single concurrency)"))
    s.add_argument("--lease", type=int, default=300, help=text(lang, "租约秒数", "Lease duration in seconds"))
    s.set_defaults(fn=cmd_run_once)

    s = sub.add_parser("report", help=text(lang, "一页报告", "Generate a research and accounting report"))
    s.add_argument("--include-synthetic", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("status", help=text(lang, "机器可读状态摘要", "Machine-readable status summary"))
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("pause", help=text(lang, "暂停：禁止新任务并终止在途本地调用",
                                                    "Pause queue and terminate active local model calls"))
    s.add_argument("--reason")
    s.add_argument("--graceful", action="store_true", help=text(lang, "停止领取新任务，允许当前任务收尾",
                                                                        "Stop claiming new tasks; let the current task finish"))
    s.set_defaults(fn=cmd_pause)

    s = sub.add_parser("resume", help=text(lang, "恢复（存在 UNKNOWN 需先对账或 --force）",
                                                    "Resume; reconcile UNKNOWN tasks first or explicitly use --force"))
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("reconcile", help=text(lang, "列出 UNKNOWN 并对账", "List and reconcile UNKNOWN requests"))
    s.add_argument("--resolve", metavar="TASK_ID")
    s.add_argument("--outcome", choices=["accepted", "lost", "rejected"])
    s.add_argument("--note")
    s.add_argument("--alpha-id", help=text(lang, "UNKNOWN BRAIN 请求经本人核实的 Alpha ID；仅恢复 GET 并校验结果",
                                                             "Verified Alpha ID for an UNKNOWN BRAIN request; resume GET only"))
    s.set_defaults(fn=cmd_reconcile)

    s = sub.add_parser("add-payment", help=text(lang, "登记真实现金账本（唯一收入口径）",
                                                         "Record payable or received cash (not simulated income)"))
    s.add_argument("--submission")
    s.add_argument("--kind", choices=["confirmed_payable", "received"], required=True)
    s.add_argument("--amount", type=float, required=True)
    s.add_argument("--currency", required=True)
    s.add_argument("--occurred-at")
    s.add_argument("--evidence", required=True, help=text(lang, "必填：到账/应付证据说明", "Required evidence of received/payable cash"))
    s.set_defaults(fn=cmd_add_payment)

    s = sub.add_parser("add-expense", help=text(lang, "登记费用/额度消耗", "Record expenses or quota use"))
    s.add_argument("--kind", choices=["cash", "quota_points", "credit", "other"], required=True)
    s.add_argument("--amount", type=float, required=True)
    s.add_argument("--unit", required=True, help="USD|pct_week_pool|call|...")
    s.add_argument("--occurred-at")
    s.add_argument("--note", required=True)
    s.set_defaults(fn=cmd_add_expense)

    s = sub.add_parser("account-stage", help=text(lang, "登记账号阶段（需证据）", "Record account stage with evidence"))
    s.add_argument("stage")
    s.add_argument("--evidence", required=True)
    s.set_defaults(fn=cmd_account_stage)

    s = sub.add_parser("budget", help=text(lang, "设置预算闸门（写回 config.json）", "Set local budget controls in config.json"))
    s.add_argument("name", help=text(lang, "simulation 或已配置的渠道 ID", "simulation or a configured provider ID"))
    g = s.add_mutually_exclusive_group()
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    s.add_argument("--remaining", type=float)
    s.add_argument("--unit")
    s.set_defaults(fn=cmd_budget)

    s = sub.add_parser("invoke", help=text(lang, "单次 gated 模型调用（默认阻断）", "One gated model call (blocked by default)"))
    s.add_argument("agent", help=text(lang, "models.<agent> 已配置项，如 grok|devin", "Configured models.<agent> name, such as grok or devin"))
    s.add_argument("--prompt-file", required=True)
    s.add_argument("--purpose", required=True)
    s.add_argument("--incident")
    s.add_argument("--artifacts", help=text(lang, "逗号分隔的预期产物相对路径", "Comma-separated expected relative artifact paths"))
    s.add_argument("--allow", action="store_true", help=text(lang, "确认已获本次调用授权", "Confirm authorization for this invocation"))
    s.set_defaults(fn=cmd_invoke)

    s = sub.add_parser("tasks", help=text(lang, "任务进度（--json 输出原始数据）", "Task progress (--json for raw data)"))
    s.add_argument("--status")
    s.add_argument("--json", action="store_true", help=text(lang, "保留机器可读 JSON 格式", "Output machine-readable JSON"))
    s.set_defaults(fn=cmd_tasks)

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [part for item in argv for part in (item.split('=', 1) if item.startswith(('--lang=', '--config=')) else [item])]
    if argv and argv[0] == 'onboard':
        return cmd_onboard(argparse.Namespace(onboard_args=argv[1:]))
    config_path = argv[argv.index('--config') + 1] if '--config' in argv and argv.index('--config') + 1 < len(argv) else 'config/config.json'
    lang = preferred_language(config_path)
    lang_pref = None
    if '--lang' in argv:
        i = argv.index('--lang')
        if i + 1 >= len(argv) or argv[i + 1] not in ('zh', 'en', 'auto'):
            build_parser().error(text(lang, '--lang：请选择 zh、en 或 auto', '--lang: choose zh, en or auto'))
        lang_pref = argv[i + 1]
        lang = default_language() if lang_pref == 'auto' else lang_pref
        del argv[i:i + 2]
    if argv and argv[0] == 'help':
        argv = argv[1:] + ['--help']
    if argv and argv[0] == 'onboard':
        return cmd_onboard(argparse.Namespace(onboard_args=['--lang', lang_pref or lang, *argv[1:]]))
    args = build_parser(lang).parse_args(argv)
    try:
        return args.fn(args)
    except WqExit as e:
        print(f"error: {translate(str(e), lang)}", file=sys.stderr)
        return e.code
    except ContractError as e:
        for err in e.errors:
            print(f"invalid: {err}", file=sys.stderr)
        return INVALID
    except AdapterError as e:
        print(f"adapter[{e.kind}]: {e}", file=sys.stderr)
        return NOT_IMPLEMENTED if e.kind == AdapterError.NOT_IMPLEMENTED else BLOCKED
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return ERR
    except json.JSONDecodeError as e:
        print(f"error: {text(lang, 'JSON 解析失败', 'JSON parse error')} {e}", file=sys.stderr)
        return INVALID
    except (ValueError, TypeError) as e:
        # 各模块抛出的中文校验消息在显示层按已知文案表翻译（i18n.translate）。
        print(f"invalid: {translate(str(e), lang)}", file=sys.stderr)
        return INVALID


if __name__ == "__main__":
    raise SystemExit(main())
