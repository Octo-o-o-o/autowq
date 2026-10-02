"""普通程序生成当前队列快照，不调用模型、不包含研究正文或凭证。"""
import json
import os
from pathlib import Path

from . import store, util


def write_progress(conn, cfg):
    tasks = []
    for row in store.list_tasks(conn):
        payload = json.loads(row["payload_json"])
        tasks.append({key: row[key] for key in
                      ("task_id", "kind", "status", "not_before", "attempts", "last_error")}
                     | {"agent": payload.get("agent"), "purpose": payload.get("purpose"),
                        "depends_on": payload.get("depends_on", [])})
    if cfg.get('autopilot'):
        from .autopilot import status
        auto = status(conn,cfg)
        util.write_json(str(Path(cfg.run_dir)/'autopilot-status.json'), auto)
    snapshot = {
        "generated_at": util.now_iso(), "paused": store.is_paused(conn),
        "authorization_expires_at": cfg.get("debug_authorization", "expires_at"),
        "model_windows": {a: cfg.debug_window(a)[0] for a in ("grok", "devin")},
        "brain_adapter": cfg.adapter_mode(),
        "tasks": tasks,
        "live_agent_calls": [{k: r[k] for k in ("call_id", "agent", "pid", "status")}
                             for r in store.live_agent_calls(conn)],
        "note": "Task completion is not platform validation or income. Refreshes after each runner tick.",
    }
    snapshot['active_preset'] = store.get_flag(conn, 'active_preset')
    if cfg.get('autopilot'):
        snapshot['lanes'] = [
            {'lane': r.get('lane'), 'cycle_id': r.get('cycle_id'), 'state': r.get('state')}
            for r in auto.get('open_cycles') or []]
    snapshot['routes'] = [
        {'task_id': r['task_id'], 'preset': json.loads(r['snapshot_json'])['preset'],
         'provider_index': r['provider_index'], 'retry_index': r['retry_index'], 'phase': r['phase']}
        for r in conn.execute('SELECT * FROM task_routes')]
    dest = Path(cfg.run_dir) / "progress.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(".json.tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, dest)
